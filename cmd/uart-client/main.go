package main

import (
	"bufio"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"golang.org/x/term"
)

type message map[string]any

func str(m message, k string) string { s, _ := m[k].(string); return s }
func raw(m message, k string) ([]byte, error) {
	return base64.StdEncoding.Strict().DecodeString(str(m, k))
}
func number(m message, k string) float64 { n, _ := m[k].(float64); return n }

const maxFrame = 2 * 1024 * 1024

func read(r *bufio.Reader) (message, error) {
	var line []byte
	for {
		p, e := r.ReadSlice('\n')
		if len(line)+len(p) > maxFrame {
			return nil, errors.New("NDJSON 訊息過大")
		}
		line = append(line, p...)
		if e == bufio.ErrBufferFull {
			continue
		}
		if e != nil {
			return nil, e
		}
		break
	}
	var m message
	if !utf8.Valid(line) {
		return nil, errors.New("invalid UTF-8")
	}
	if e := json.Unmarshal(line, &m); e != nil {
		return nil, e
	}
	if m == nil {
		return nil, errors.New("NDJSON 必須為物件")
	}
	return m, nil
}
func send(c net.Conn, m message) error {
	b, e := json.Marshal(m)
	if e != nil {
		return e
	}
	b = append(b, '\n')
	if len(b) > maxFrame {
		return errors.New("NDJSON 訊息過大")
	}
	c.SetWriteDeadline(time.Now().Add(5 * time.Second))
	for len(b) > 0 {
		n, e := c.Write(b)
		if e != nil {
			return e
		}
		b = b[n:]
	}
	return nil
}
func show(w io.Writer, m message) { b, _ := json.Marshal(m); fmt.Fprintln(w, string(b)) }
func safe(b []byte) string {
	var s strings.Builder
	for len(b) > 0 {
		r, n := utf8.DecodeRune(b)
		if r == utf8.RuneError && n == 1 {
			fmt.Fprintf(&s, "\\x%02x", b[0])
		} else if unicode.IsPrint(r) || strings.ContainsRune("\n\r\t", r) {
			s.WriteRune(r)
		} else {
			fmt.Fprintf(&s, "\\x%02x", r)
		}
		b = b[n:]
	}
	return s.String()
}

type options struct {
	host, port, op, command, cwd string
	hasCwd, json, full, ready    bool
	timeout                      float64
	maximum                      int
}

func parse(args []string) (o options, e error) {
	o = options{host: "127.0.0.1", port: "18081", timeout: 10, maximum: 1048576}
	for i := 0; i < len(args); i++ {
		a := args[i]
		k, v, eq := strings.Cut(a, "=")
		value := func() (string, error) {
			if eq {
				return v, nil
			}
			i++
			if i >= len(args) {
				return "", fmt.Errorf("%s 缺少值", k)
			}
			return args[i], nil
		}
		switch k {
		case "--help", "-h":
			return o, errors.New("usage: uart-client [--host HOST] [--port PORT] {exec COMMAND [--cwd PATH] [--timeout SEC] [--max-output N] [--json]|monitor [--full|--json]|status|interrupt|terminal [--shell-ready]}")
		case "--host", "--port":
			if o.op != "" {
				return o, errors.New("--host/--port 必須放在子指令前")
			}
			s, err := value()
			if err != nil {
				return o, err
			}
			if k == "--host" {
				o.host = s
			} else {
				o.port = s
			}
		case "--cwd", "--timeout", "--max-output":
			if o.op != "exec" {
				return o, errors.New("exec option required")
			}
			s, err := value()
			if err != nil {
				return o, err
			}
			if k == "--cwd" {
				o.cwd = s
				o.hasCwd = true
			} else if k == "--timeout" {
				o.timeout, e = strconv.ParseFloat(s, 64)
			} else {
				o.maximum, e = strconv.Atoi(s)
			}
			if e != nil {
				return o, e
			}
		case "--json":
			if o.op != "exec" && o.op != "monitor" {
				return o, errors.New("invalid --json")
			}
			o.json = true
		case "--full":
			if o.op != "monitor" {
				return o, errors.New("invalid --full")
			}
			o.full = true
		case "--shell-ready":
			if o.op != "terminal" {
				return o, errors.New("invalid --shell-ready")
			}
			o.ready = true
		case "--":
			if o.op != "exec" || o.command != "" || i+2 != len(args) {
				return o, errors.New("expected one command")
			}
			o.command = args[i+1]
			i++
		default:
			if strings.HasPrefix(a, "-") {
				return o, fmt.Errorf("unknown option %s", a)
			}
			if o.op == "" {
				o.op = a
			} else if o.op == "exec" && o.command == "" {
				o.command = a
			} else {
				return o, errors.New("unexpected argument")
			}
		}
	}
	if o.full && o.json {
		return o, errors.New("--full 與 --json 互斥")
	}
	switch o.op {
	case "exec":
		valid := func(s string) bool {
			if strings.TrimSpace(s) == "" || len(s) > 4096 {
				return false
			}
			for _, r := range s {
				if !unicode.IsPrint(r) {
					return false
				}
			}
			return true
		}
		if !valid(o.command) || (o.hasCwd && !valid(o.cwd)) || math.IsNaN(o.timeout) || math.IsInf(o.timeout, 0) || o.timeout < 0.1 || o.timeout > 300 || o.maximum < 1 || o.maximum > 1048576 {
			return o, errors.New("command/cwd/timeout/max-output 不合法")
		}
	case "monitor", "status", "interrupt", "terminal":
	default:
		return o, errors.New("需要有效子指令，請用 --help")
	}
	return o, nil
}
func run(o options) (int, error) {
	restore, modeErr := outputMode()
	if modeErr != nil {
		return 2, modeErr
	}
	defer restore()
	if o.op == "terminal" && !o.ready && (!term.IsTerminal(int(os.Stdin.Fd())) || !term.IsTerminal(int(os.Stdout.Fd()))) {
		return 2, errors.New("terminal 需要互動終端，不能使用管線")
	}
	c, e := net.DialTimeout("tcp", net.JoinHostPort(o.host, o.port), 5*time.Second)
	if e != nil {
		return 2, e
	}
	defer c.Close()
	r := bufio.NewReader(c)
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt)
	defer signal.Stop(signals)
	done := make(chan struct{})
	defer close(done)
	interrupted := make(chan struct{})
	go func() {
		select {
		case <-signals:
			close(interrupted)
			c.Close()
		case <-done:
		}
	}()
	code, err := operate(c, r, o)
	select {
	case <-interrupted:
		return 130, errors.New("已停止等待；不會中止或重送 UART 命令")
	default:
	}
	return code, err
}
func operate(c net.Conn, r *bufio.Reader, o options) (int, error) {
	c.SetReadDeadline(time.Now().Add(5 * time.Second))
	req := message{"op": o.op}
	if o.op == "exec" {
		req["command"] = o.command
		req["timeout"] = o.timeout
		req["max_output"] = o.maximum
		if o.hasCwd {
			if e := send(c, message{"op": "hello"}); e != nil {
				return 2, e
			}
			h, e := read(r)
			if e != nil {
				return 2, e
			}
			ok := false
			if fs, yes := h["features"].([]any); yes && str(h, "type") == "hello" {
				for _, f := range fs {
					if f == "cwd" {
						ok = true
					}
				}
			}
			if !ok {
				m := message{"type": "error", "error": "cwd_unsupported", "message": "Server 不支援 --cwd，請重新啟動；命令尚未送出"}
				if o.json {
					show(os.Stdout, m)
				}
				return 2, errors.New(str(m, "message"))
			}
			req["cwd"] = o.cwd
		}
		c.SetReadDeadline(time.Now().Add(time.Duration((o.timeout + 7) * float64(time.Second))))
	}
	if o.ready {
		req = message{"op": "terminal_ready", "confirmed": true}
	}
	if e := send(c, req); e != nil {
		return 2, e
	}
	if o.op == "terminal" && !o.ready {
		return terminal(c, r)
	}
	if o.op == "monitor" {
		c.SetReadDeadline(time.Time{})
		return monitor(r, o)
	}
	m, e := read(r)
	if e != nil {
		return 2, e
	}
	id := ""
	if str(m, "type") == "accepted" {
		id = str(m, "id")
		m, e = read(r)
		if e != nil {
			return 2, fmt.Errorf("id=%s: %w", id, e)
		}
	}
	if o.op != "exec" || o.json {
		show(os.Stdout, m)
	}
	if str(m, "type") == "error" {
		if !o.json && o.op == "exec" {
			show(os.Stderr, m)
		}
		return 2, nil
	}
	if o.op != "exec" {
		if str(m, "state") == "unknown" {
			return 2, nil
		}
		return 0, nil
	}
	if !o.json {
		b, e := raw(m, "output_b64")
		if e != nil {
			return 2, e
		}
		if term.IsTerminal(int(os.Stdout.Fd())) {
			os.Stdout.Write(b)
		} else {
			fmt.Print(safe(b))
		}
	}
	if str(m, "state") != "completed" || m["output_complete"] != true {
		fmt.Fprintf(os.Stderr, "\n命令 %s：state=%s reason=%v；請用 status 查詢，不要重送。\n", str(m, "id"), str(m, "state"), m["reason"])
		return 2, nil
	}
	if number(m, "exit_code") != 0 {
		return 1, nil
	}
	return 0, nil
}
func main() {
	o, e := parse(os.Args[1:])
	if e != nil {
		fmt.Fprintln(os.Stderr, e)
		for _, a := range os.Args[1:] {
			if a == "--help" || a == "-h" {
				os.Exit(0)
			}
		}
		os.Exit(2)
	}
	code, e := run(o)
	if e != nil {
		fmt.Fprintln(os.Stderr, e, "；不會重送命令。")
	}
	os.Exit(code)
}
