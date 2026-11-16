package main

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"fmt"
	"net"
	"strings"
	"testing"
	"time"
)

func TestCompactEverySplit(t *testing.T) {
	id := strings.Repeat("a", 32)
	wire := []byte("echo\r\n__UART_BEGIN_" + id + "__\r\n\x1b[32m中文\x1b[0m\r\n\r\n__UART_END_" + id + "__:130\r\n~ # ")
	for split := 0; split <= len(wire); split++ {
		c := &compact{}
		if e := c.start(message{"id": id}, false); e != nil {
			t.Fatal(e)
		}
		out := append(c.feed(wire[:split]), c.feed(wire[split:])...)
		out = append(out, c.finish()...)
		if string(out) != "\x1b[32m中文\x1b[0m\r\n~ # " {
			t.Fatalf("split %d: %q", split, out)
		}
	}
}
func TestFrames(t *testing.T) {
	for _, s := range []string{"{}", "null\n", "[]\n", "{\"x\":NaN}\n", strings.Repeat("x", maxFrame) + "\n"} {
		if _, e := read(bufio.NewReader(strings.NewReader(s))); e == nil {
			t.Fatalf("accepted invalid frame %q", s[:min(20, len(s))])
		}
	}
	m, e := read(bufio.NewReader(strings.NewReader("{\"type\":\"status\"}\n")))
	if e != nil || str(m, "type") != "status" {
		t.Fatal(m, e)
	}
}
func TestCLI(t *testing.T) {
	for _, args := range [][]string{{"exec", "ls", "--timeout", "NaN"}, {"exec", "ls\nreboot"}, {"monitor", "--json", "--full"}, {"exec", "ls", "--cwd", ""}, {"exec", "ls", "--max-output", "1048577"}} {
		if _, e := parse(args); e == nil {
			t.Fatal(args)
		}
	}
	o, e := parse([]string{"--port", "19000", "exec", "ls", "--cwd", "/a b", "--json"})
	if e != nil || o.cwd != "/a b" || !o.json {
		t.Fatal(o, e)
	}
}
func mock(t *testing.T, serve func(net.Conn, *bufio.Reader)) net.Conn {
	t.Helper()
	a, b := net.Pipe()
	t.Cleanup(func() { a.Close(); b.Close() })
	go func() { defer b.Close(); serve(b, bufio.NewReader(b)) }()
	return a
}
func TestCwdRejectedBeforeExec(t *testing.T) {
	c := mock(t, func(c net.Conn, r *bufio.Reader) {
		m, e := read(r)
		if e != nil || str(m, "op") != "hello" {
			t.Error(m, e)
		}
		send(c, message{"type": "hello", "features": []string{}})
		c.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
		if m, e = read(r); e == nil {
			t.Errorf("unexpected write %v", m)
		}
	})
	o, _ := parse([]string{"exec", "reboot", "--cwd", "/tmp"})
	code, e := operate(c, bufio.NewReader(c), o)
	if code != 2 || e == nil {
		t.Fatal(code, e)
	}
}
func TestExecHandshakeAndResult(t *testing.T) {
	for _, tc := range []struct {
		exit     int
		complete bool
		want     int
	}{{0, true, 0}, {7, true, 1}, {0, false, 2}} {
		t.Run(fmt.Sprint(tc), func(t *testing.T) {
			c := mock(t, func(c net.Conn, r *bufio.Reader) {
				read(r)
				send(c, message{"type": "hello", "features": []string{"cwd"}})
				m, e := read(r)
				if e != nil || str(m, "cwd") != "/tmp" || str(m, "command") != "pwd" {
					t.Error(m, e)
				}
				send(c, message{"type": "accepted", "id": "test"})
				send(c, message{"type": "result", "id": "test", "state": "completed", "exit_code": tc.exit, "output_complete": tc.complete, "output_b64": base64.StdEncoding.EncodeToString([]byte("ok\n"))})
			})
			o, _ := parse([]string{"exec", "pwd", "--cwd", "/tmp", "--json"})
			code, e := operate(c, bufio.NewReader(c), o)
			if e != nil || code != tc.want {
				t.Fatal(code, e)
			}
		})
	}
}
func TestSafe(t *testing.T) {
	if got := safe([]byte{27, 0xff, '\n'}); got != "\\x1b\\xff\n" {
		t.Fatal(got)
	}
}
func TestCompactJoinAndTruncated(t *testing.T) {
	c := &compact{}
	id := strings.Repeat("b", 32)
	prefix := []byte("\r\n__UART_END_")
	e := c.start(message{"id": id, "prefix_b64": base64.StdEncoding.EncodeToString(prefix)}, true)
	if e != nil {
		t.Fatal(e)
	}
	out := c.feed([]byte("other\nhello"))
	out = append(out, c.finish()...)
	if !bytes.Equal(out, []byte("other\nhello")) {
		t.Fatalf("%q", out)
	}
}
func TestTerminalAckAndClose(t *testing.T) {
	keys := make(chan []byte)
	c := mock(t, func(c net.Conn, r *bufio.Reader) {
		send(c, message{"type": "terminal", "newline": "crlf"})
		keys <- []byte("pwd\r\x03")
		m, e := read(r)
		if e != nil || str(m, "op") != "terminal_write" {
			t.Error(m, e)
			return
		}
		b, e := raw(m, "data_b64")
		if e != nil || string(b) != "pwd\r\n\x03" {
			t.Error(b, e)
		}
		send(c, message{"type": "terminal_tx", "accepted_bytes": len(b)})
		keys <- []byte("ignored\x1dignored")
		m, e = read(r)
		if e != nil || str(m, "op") != "terminal_close" {
			t.Error(m, e)
			return
		}
		send(c, message{"type": "terminal_closed"})
	})
	code, e := terminalSession(c, bufio.NewReader(c), keys)
	if code != 0 || e != nil {
		t.Fatal(code, e)
	}
}
func TestTerminalBadAck(t *testing.T) {
	keys := make(chan []byte)
	c := mock(t, func(c net.Conn, r *bufio.Reader) {
		send(c, message{"type": "terminal", "newline": "cr"})
		keys <- []byte("x")
		read(r)
		send(c, message{"type": "terminal_tx", "accepted_bytes": 0})
	})
	code, e := terminalSession(c, bufio.NewReader(c), keys)
	if code != 2 || e == nil {
		t.Fatal(code, e)
	}
}
