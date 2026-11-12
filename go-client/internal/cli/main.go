// Package cli 只組合共用 Client，不直接操作 UART 或自動重送。
package cli

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"uart-gateway/go-client/client"
)

const Version = "0.2.0-go"

type app struct {
	options   client.ConnectionOptions
	interrupt chan os.Signal
}

func flags(name string) *flag.FlagSet {
	f := flag.NewFlagSet(name, flag.ContinueOnError)
	f.SetOutput(io.Discard)
	return f
}
func parse(f *flag.FlagSet, args []string) ([]string, error) {
	options, pos := []string{}, []string{}
	for i := 0; i < len(args); i++ {
		s := args[i]
		if s == "--" {
			pos = append(pos, args[i+1:]...)
			break
		}
		if !strings.HasPrefix(s, "-") || s == "-" {
			pos = append(pos, s)
			continue
		}
		name := strings.TrimLeft(strings.SplitN(s, "=", 2)[0], "-")
		v := f.Lookup(name)
		if v == nil {
			return nil, fmt.Errorf("未知選項 --%s", client.Label(name))
		}
		options = append(options, s)
		boolFlag, ok := v.Value.(interface{ IsBoolFlag() bool })
		if !strings.Contains(s, "=") && (!ok || !boolFlag.IsBoolFlag()) {
			i++
			if i >= len(args) {
				return nil, errors.New("選項缺少值")
			}
			options = append(options, args[i])
		}
	}
	if e := f.Parse(options); e != nil {
		return nil, errors.New("參數不合法，請執行 uart --help")
	}
	return pos, nil
}
func output(v any) error {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	return encoder.Encode(v)
}
func help() {
	fmt.Fprintln(os.Stdout, "UART Linux Client "+Version+"\n用法：uart [--connection 名稱] [--access read|manage|tx] [命令]\n\n不帶命令：遠端工作台；不需要 Python、Go 或虛擬環境。\nchannels　列通道\nwatch <通道...> [--duration 秒] [--replay] [--text|--terminal]\nreopen <通道>　人工 RX 重開（manage）\nconfirm <通道>／rebind <通道>　人工綁定管理\nsend <通道> <文字> [--newline none|lf|cr|crlf] [--hex]　預覽後傳一次\nstatus <通道> <TX_ID>　只查原收據\nshell-ready <通道> --newline lf|cr|crlf　人工核對已登入 Shell\nexec <通道> <單行命令> [--timeout 秒] [--max-output bytes]\nexec-status <ID> [--wait 秒]　只查原命令，不重送\nauth import --stdin|--file 路徑 [--name 名稱] [--port Port]\nauth use <名稱>\nversion　顯示版本")
}
func Main(args []string) int {
	a := &app{interrupt: make(chan os.Signal, 1)}
	signal.Notify(a.interrupt, os.Interrupt)
	defer signal.Stop(a.interrupt)
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM)
	defer cancel()
	code, e := a.run(ctx, args)
	if e != nil {
		if errors.Is(e, context.Canceled) {
			return 130
		}
		fmt.Fprintln(os.Stderr, "操作失敗："+client.Label(e.Error()))
		return 1
	}
	return code
}
func (a *app) settings() (client.Settings, error) { return a.options.Resolve() }
func (a *app) ask(ctx context.Context, prompt string) (string, error) {
	if !terminal(os.Stdin) || !terminal(os.Stdout) {
		return "", errors.New("此操作需要互動終端，由人核對目標；不提供非互動自動 Confirm")
	}
	fmt.Fprintln(os.Stderr, prompt)
	s, e := readLine(ctx, a.interrupt)
	return strings.TrimSpace(s), e
}
func (a *app) run(ctx context.Context, args []string) (int, error) {
	root := flags("uart")
	root.StringVar(&a.options.Root, "connections-dir", "", "私有連線根目錄")
	root.StringVar(&a.options.Name, "connection", "", "連線名稱")
	root.StringVar(&a.options.Access, "access", "", "角色")
	root.StringVar(&a.options.Host, "host", "", "Loopback 位址")
	root.IntVar(&a.options.Port, "port", 0, "轉送 Port")
	root.StringVar(&a.options.TokenFile, "token-file", "", "明確檔案來源")
	root.StringVar(&a.options.TokenEnv, "token-env", "", "明確環境來源")
	helpFlag := root.Bool("help", false, "")
	shortHelp := root.Bool("h", false, "")
	versionFlag := root.Bool("version", false, "")
	if e := root.Parse(args); e != nil {
		return 1, errors.New("參數不合法，請執行 uart --help")
	}
	if *helpFlag || *shortHelp {
		help()
		return 0, nil
	}
	if *versionFlag {
		fmt.Fprintln(os.Stdout, Version)
		return 0, nil
	}
	args = root.Args()
	command := "menu"
	if len(args) > 0 {
		command = args[0]
		args = args[1:]
	}
	if command == "version" || command == "--version" {
		fmt.Fprintln(os.Stdout, Version)
		return 0, nil
	}
	if command == "bootstrap" {
		return a.bootstrap(ctx, args)
	}
	if command == "auth" {
		return a.auth(ctx, args)
	}
	switch command {
	case "send", "status", "shell-ready", "exec", "exec-status":
		if a.options.Access == "" && a.options.TokenFile == "" && a.options.TokenEnv == "" {
			a.options.Access = "tx"
		}
	}
	settings, e := a.settings()
	if e != nil {
		return 1, e
	}
	switch command {
	case "menu", "client", "tui":
		if len(args) > 0 && !(len(args) == 1 && args[0] == "--text") {
			return 1, errors.New("工作台不接受此參數")
		}
		return a.menu(ctx, settings)
	case "watch":
		return a.watchCommand(ctx, settings, args)
	case "exec", "exec-status", "shell-ready":
		return a.shell(ctx, settings, command, args)
	}
	c, e := settings.Connect(ctx)
	if e != nil {
		return 1, e
	}
	defer c.Close()
	switch command {
	case "channels":
		if len(args) != 0 {
			return 1, errors.New("channels 不接受位置參數")
		}
		channels, e := c.Channels(ctx)
		if e != nil {
			return 1, e
		}
		return 0, output(channels)
	case "reopen", "confirm", "rebind":
		if len(args) != 1 {
			return 1, errors.New("請提供單一通道 ID")
		}
		ch, e := c.Channel(ctx, args[0])
		if e != nil {
			return 1, e
		}
		printTarget(c, ch)
		if command == "confirm" {
			fmt.Fprintln(os.Stderr, "確認會建立新 Epoch；不是登入或執行命令。")
		}
		answer, e := a.ask(ctx, "核對裝置與接線後，輸入 Y 執行本次管理操作；其他輸入取消：")
		if e != nil {
			return 1, e
		}
		if !strings.EqualFold(answer, "y") {
			return 0, nil
		}
		current, e := c.Channel(ctx, ch.ID)
		if e != nil {
			return 1, e
		}
		if !client.SameTarget(ch, current) {
			return 1, errors.New("核對期間目標已變更，未操作")
		}
		var result any
		if command == "reopen" {
			result, e = c.Reopen(ctx, current)
		} else if command == "confirm" {
			result, e = c.Confirm(ctx, current)
		} else {
			result, e = c.Request(ctx, "binding.rebind", client.Object{"channel_id": ch.ID, "expected": ch.Expected(c.Instance)})
		}
		if e != nil {
			return 1, fmt.Errorf("管理結果未確認，請先查詢通道，不直接重送：%w", e)
		}
		return 0, output(result)
	case "send":
		return a.send(ctx, settings, c, args)
	case "status":
		if len(args) != 2 {
			return 1, errors.New("請提供通道與原 TX_ID")
		}
		j := journal(settings)
		receipt, e := j.Load(args[1])
		if e != nil {
			return 1, e
		}
		if receipt.Channel != args[0] || receipt.Principal != c.Principal {
			return 1, errors.New("收據通道或身分不符")
		}
		v, e := c.Status(ctx, receipt.ID, receipt.Channel, receipt.Instance)
		if e != nil {
			return 1, e
		}
		if e = j.Update(receipt, v, true); e != nil {
			return 1, e
		}
		return txExit(receipt.Result), output(receipt)
	default:
		return 1, errors.New("未知命令，請執行 uart --help")
	}
}
func journal(s client.Settings) client.Journal {
	name := s.Name
	if name == "" {
		name = "direct"
	}
	return client.Journal{Directory: filepath.Join(s.Root, "go-receipts", name)}
}
func runner(s client.Settings) client.ShellRunner {
	name := s.Name
	if name == "" {
		name = "direct"
	}
	return client.ShellRunner{Settings: s, Journal: journal(s), Targets: filepath.Join(s.Root, "go-shell-targets", name)}
}
func printTarget(c *client.Client, ch client.Channel) {
	fmt.Fprintf(os.Stderr, "目標：%s／%s／%s\nGateway：%s；Revision：%s；Epoch：%v；狀態：%s\n", client.Label(ch.ID), client.Label(ch.Label), client.Label(client.String(ch.Adapter, "port")), c.Instance, ch.Binding.Revision, ch.Epoch, ch.State)
	serial, _ := json.Marshal(ch.Serial)
	fmt.Fprintf(os.Stderr, "目標備註：%s；UART：%s\n", client.Label(fmt.Sprint(ch.Note)), client.Label(string(serial)))
}
func txExit(result client.Object) int {
	switch client.String(result, "state") {
	case "written_to_driver":
		return 0
	case "rejected":
		return 1
	default:
		return 2
	}
}
func (a *app) send(ctx context.Context, s client.Settings, c *client.Client, args []string) (int, error) {
	f := flags("send")
	newline := f.String("newline", "none", "")
	hexMode := f.Bool("hex", false, "")
	pos, e := parse(f, args)
	if e != nil {
		return 1, e
	}
	if len(pos) != 2 {
		return 1, errors.New("send 需要通道與一段文字")
	}
	ch, e := c.Channel(ctx, pos[0])
	if e != nil {
		return 1, e
	}
	encoding := "text"
	if *hexMode {
		encoding = "hex"
	}
	payload, e := client.EncodePayload(pos[1], *newline, encoding, c.Limits["max_tx_bytes"])
	if e != nil {
		return 1, e
	}
	printTarget(c, ch)
	fmt.Fprintf(os.Stderr, "精確傳送內容：%s；%d bytes；換行：%s\n", hex.EncodeToString(payload), len(payload), *newline)
	answer, e := a.ask(ctx, "確認目標與完整 bytes 後輸入 Y，只送一次；其他輸入取消：")
	if e != nil {
		return 1, e
	}
	if !strings.EqualFold(answer, "y") {
		return 0, nil
	}
	if !c.Can(ch.ID, "configure") || !c.Can(ch.ID, "tx") {
		return 1, errors.New("缺少此通道 TX 權限")
	}
	ch, e = c.Check(ctx, ch, c.Instance)
	if e != nil {
		return 1, e
	}
	if ch.Binding.Status != "confirmed" {
		ch, e = c.Confirm(ctx, ch)
		if e != nil {
			return 1, e
		}
	}
	unlock, e := client.OperationLock(filepath.Join(s.Root, "go-locks", s.Name), ch.ID)
	if e != nil {
		return 1, e
	}
	defer unlock()
	lease, e := c.Acquire(ctx, ch)
	if e != nil {
		return 1, e
	}
	defer lease.Close()
	receipt := &client.Receipt{Version: 1, ID: client.OperationID(), Channel: ch.ID, Instance: c.Instance, Principal: c.Principal, Target: ch}
	j := journal(s)
	if e = j.Save(receipt, true); e != nil {
		return 1, e
	}
	wait, cancel := context.WithTimeout(lease.Context, 5*time.Second)
	defer cancel()
	v, e := lease.Write(wait, receipt.ID, payload)
	if e == nil {
		if e = j.Update(receipt, v, false); e == nil && client.String(v, "state") == "pending" {
			v, e = c.WaitTx(wait, receipt.ID, ch.ID)
			if e == nil {
				e = j.Update(receipt, v, false)
			}
		}
	}
	if e != nil {
		fmt.Fprintln(os.Stderr, "傳送結果未確認，只查原 TX_ID，不自動重送。")
		_ = output(receipt)
		return 2, nil
	}
	return txExit(receipt.Result), output(receipt)
}
func (a *app) shell(ctx context.Context, s client.Settings, command string, args []string) (int, error) {
	r := runner(s)
	if command == "shell-ready" {
		f := flags(command)
		newline := f.String("newline", "", "")
		pos, e := parse(f, args)
		if e != nil {
			return 1, e
		}
		if len(pos) != 1 || *newline != "lf" && *newline != "cr" && *newline != "crlf" {
			return 1, errors.New("請提供通道與明確的 --newline lf|cr|crlf")
		}
		c, e := s.Connect(ctx)
		if e != nil {
			return 1, e
		}
		defer c.Close()
		ch, e := c.Channel(ctx, pos[0])
		if e != nil {
			return 1, e
		}
		if !c.Can(ch.ID, "tx") || !c.Can(ch.ID, "configure") {
			return 1, errors.New("缺少 Shell 所需 TX 權限")
		}
		printTarget(c, ch)
		answer, e := a.ask(ctx, "請人工確認接線、目標已登入 POSIX Shell，且換行已知可用。輸入 Y 保存本次核對；不會自動登入或傳送探測命令：")
		if e != nil {
			return 1, e
		}
		if !strings.EqualFold(answer, "y") {
			return 0, nil
		}
		ch, e = c.Check(ctx, ch, c.Instance)
		if e != nil {
			return 1, e
		}
		if ch.Binding.Status != "confirmed" {
			ch, e = c.Confirm(ctx, ch)
			if e != nil {
				return 1, e
			}
		}
		if e = r.Authorize(ch, c.Instance, c.Principal, *newline); e != nil {
			return 1, e
		}
		fmt.Fprintln(os.Stdout, "已保存本次 Shell 目標核對；exec 不會自動 Confirm。")
		return 0, nil
	}
	f := flags(command)
	timeout := f.Float64("timeout", 10, "")
	wait := f.Float64("wait", 0, "")
	maximum := f.Int("max-output", 65536, "")
	text := f.Bool("text", false, "")
	pos, e := parse(f, args)
	if e != nil {
		return 1, e
	}
	if math.IsNaN(*timeout) || math.IsInf(*timeout, 0) || *timeout <= 0 || *timeout > 300 || math.IsNaN(*wait) || math.IsInf(*wait, 0) || *wait < 0 || *wait > 300 {
		return 1, errors.New("命令期限須為 0～300 秒內的有限數值")
	}
	operation, cancel := context.WithCancel(ctx)
	defer cancel()
	go func() {
		select {
		case <-a.interrupt:
			cancel()
		case <-operation.Done():
		}
	}()
	var result client.ExecutionResult
	if command == "exec" {
		if len(pos) != 2 {
			return 1, errors.New("exec 需要通道與一條單行命令")
		}
		result, e = r.Execute(operation, pos[0], pos[1], time.Duration(*timeout*float64(time.Second)), *maximum)
	} else {
		if len(pos) != 1 {
			return 1, errors.New("exec-status 需要原 execution_id")
		}
		result, e = r.Query(operation, pos[0], time.Duration(*wait*float64(time.Second)))
	}
	if e != nil {
		return 1, e
	}
	if *text {
		v := result.Object()
		fmt.Fprintf(os.Stdout, "execution_id=%s｜%s｜exit_code=%v｜output_complete=%v\n", v["execution_id"], v["state"], v["exit_code"], v["output_complete"])
		if text, ok := v["output_text"].(string); ok {
			fmt.Fprintln(os.Stdout, client.SafeText([]byte(text), false))
		}
	} else if e = output(result.Object()); e != nil {
		return 1, e
	}
	return result.Exit(), nil
}
func (a *app) auth(ctx context.Context, args []string) (int, error) {
	root := a.options.Root
	if root == "" {
		root = client.ConfigDirectory()
	}
	if a.options.Name != "" || a.options.Access != "" || a.options.Host != "" || a.options.Port != 0 || a.options.TokenFile != "" || a.options.TokenEnv != "" {
		return 1, errors.New("認證安裝不混用連線或角色覆寫")
	}
	if len(args) == 0 {
		return 1, errors.New("auth 需要 import 或 use")
	}
	if args[0] == "use" && len(args) == 2 {
		return 0, client.SelectDefault(root, args[1])
	}
	if args[0] != "import" {
		return 1, errors.New("Go Client 支援 auth import／use，Gateway 初始化請在 Windows 操作")
	}
	f := flags("auth import")
	stdin := f.Bool("stdin", false, "")
	file := f.String("file", "", "")
	name := f.String("name", "default", "")
	port := f.Int("port", 0, "")
	pos, e := parse(f, args[1:])
	if e != nil {
		return 1, e
	}
	if len(pos) != 0 || *stdin == (*file != "") {
		return 1, errors.New("請選 --stdin 或 --file 其中一種來源")
	}
	var data []byte
	if *stdin {
		if terminal(os.Stdin) {
			return 1, errors.New("請使用受保護管線，不在終端貼上憑證")
		}
		data, e = io.ReadAll(io.LimitReader(os.Stdin, 16385))
	} else {
		data, e = client.ReadPrivate(*file, 16384)
	}
	if e != nil {
		return 1, e
	}
	if e = client.ImportCredential(root, *name, *port, data); e != nil {
		return 1, e
	}
	fmt.Fprintln(os.Stdout, "已安裝所選角色憑證；未操作 UART。")
	return 0, nil
}
