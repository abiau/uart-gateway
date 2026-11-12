package cli

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"uart-gateway/go-client/client"
)

const maxBinary = 16 * 1024 * 1024
const launcherMarker = "# UART Gateway 管理的 Client 入口 v2"

func (a *app) bootstrap(ctx context.Context, args []string) (code int, err error) {
	// 部署 stdin 含秘密，錯誤僅回固定階段，避免輸出原始輸入或 traceback。
	stage := "FILES"
	defer func() {
		if err != nil {
			fmt.Fprintln(os.Stdout, "UART_REMOTE_FAILED_"+stage)
			err = nil
			code = 1
		}
	}()
	if runtime.GOOS != "linux" || runtime.GOARCH != "amd64" {
		return 1, errors.New("目標平台不符")
	}
	f := flags("bootstrap")
	hash := f.String("sha256", "", "")
	if _, e := parse(f, args); e != nil {
		return 1, e
	}
	if len(*hash) != 64 {
		return 1, errors.New("缺少安裝雜湊")
	}
	reader := bufio.NewReaderSize(os.Stdin, 16385)
	line, e := reader.ReadSlice('\n')
	if e != nil || len(line) > 16384 {
		return 1, errors.New("部署參數超過上限")
	}
	v, e := client.StrictJSON(line, 16)
	if e != nil || len(v) != 5 {
		return 1, errors.New("部署參數不合法")
	}
	if _, e = client.Integer(v, "version", 1, 1); e != nil {
		return 1, e
	}
	name := client.String(v, "name")
	if !client.ValidID(name) {
		return 1, errors.New("連線名稱不合法")
	}
	port, e := client.Integer(v, "port", 1, 65535)
	if e != nil {
		return 1, e
	}
	hold, ok := v["hold"].(bool)
	if !ok {
		return 1, errors.New("部署模式不合法")
	}
	bundles, ok := v["credentials"].([]any)
	if !ok || len(bundles) < 1 || len(bundles) > 3 {
		return 1, errors.New("部署角色不合法")
	}
	for k := range v {
		switch k {
		case "version", "name", "port", "hold", "credentials":
		default:
			return 1, errors.New("未知部署欄位")
		}
	}
	executable, e := os.Executable()
	if e != nil {
		return 1, e
	}
	binary, e := client.ReadPrivate(executable, maxBinary)
	if e != nil {
		return 1, e
	}
	sum := sha256.Sum256(binary)
	actual := hex.EncodeToString(sum[:])
	if actual != *hash {
		return 1, errors.New("安裝雜湊不符")
	}
	home, e := os.UserHomeDir()
	if e != nil {
		return 1, e
	}
	directory := filepath.Join(home, ".local", "share", "uart-gateway", "client-go")
	if e = client.PrivateDir(directory); e != nil {
		return 1, e
	}
	dir, e := os.Open(directory)
	if e != nil {
		return 1, e
	}
	entries, re := dir.ReadDir(9)
	dir.Close()
	if re != nil && re != io.EOF {
		return 1, re
	}
	target := filepath.Join(directory, actual)
	if len(entries) > 8 || len(entries) >= 8 && !fileExists(target) {
		stage = "VERSIONS"
		return 1, errors.New("安裝版本達上限")
	}
	launcher := filepath.Join(home, ".local", "bin", "uart")
	if fileExists(launcher) {
		prior, e := client.ReadPrivate(launcher, 4096)
		if e != nil || !strings.Contains(string(prior), launcherMarker) && !strings.Contains(string(prior), "# UART Gateway 管理的 Client 入口 v1") {
			stage = "ENTRY"
			return 1, errors.New("既有入口不屬本工具")
		}
	}
	if fileExists(target) {
		old, e := client.ReadPrivate(target, maxBinary)
		if e != nil {
			return 1, e
		}
		other := sha256.Sum256(old)
		if other != sum {
			return 1, errors.New("既有版本雜湊不符")
		}
	} else if e = client.WritePrivate(target, binary, false); e != nil {
		return 1, e
	}
	if e = os.Chmod(target, 0700); e != nil {
		return 1, e
	}
	root := a.options.Root
	if root == "" {
		root = client.ConfigDirectory()
	}
	stage = "CREDENTIALS"
	roles := map[string]bool{}
	for _, bundle := range bundles {
		m, ok := bundle.(map[string]any)
		if !ok {
			return 1, errors.New("角色格式不合法")
		}
		role := client.String(m, "access")
		if roles[role] {
			return 1, errors.New("部署角色重複")
		}
		roles[role] = true
		data, _ := json.Marshal(m)
		if e = client.ImportCredential(root, name, port, data); e != nil {
			return 1, e
		}
	}
	if !roles["read"] {
		return 1, errors.New("部署須包含明確讀取角色")
	}
	if e = client.SelectDefault(root, name); e != nil {
		return 1, e
	}
	script := []byte("#!/bin/sh\n" + launcherMarker + "\nexec " + client.QuoteShell(target) + " \"$@\"\n")
	stage = "FILES"
	if e = client.WritePrivate(launcher, script, true); e != nil {
		return 1, e
	}
	if e = os.Chmod(launcher, 0700); e != nil {
		return 1, e
	}
	if hold {
		stage = "AUTH"
		settings, e := (client.ConnectionOptions{Root: root, Name: name}).Resolve()
		if e != nil {
			return 1, e
		}
		c, e := settings.Connect(ctx)
		if e != nil {
			return 1, e
		}
		_, e = c.Channels(ctx)
		c.Close()
		if e != nil {
			return 1, e
		}
	}
	inPath := false
	for _, p := range filepath.SplitList(os.Getenv("PATH")) {
		if p == filepath.Dir(launcher) {
			inPath = true
		}
	}
	fmt.Fprintf(os.Stdout, "UART_REMOTE_READY {\"path_ready\":%t}\n", inPath)
	if hold {
		_, e = io.Copy(io.Discard, reader)
		if e != nil {
			return 1, e
		}
	}
	return 0, nil
}
func fileExists(path string) bool { _, e := os.Lstat(path); return e == nil || !os.IsNotExist(e) }
