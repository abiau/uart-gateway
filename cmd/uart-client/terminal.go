package main

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"errors"
	"fmt"
	"golang.org/x/term"
	"net"
	"os"
	"time"
)

type received struct {
	m message
	e error
}

func terminal(c net.Conn, r *bufio.Reader) (int, error) {
	state, e := term.MakeRaw(int(os.Stdin.Fd()))
	if e != nil {
		return 2, e
	}
	defer term.Restore(int(os.Stdin.Fd()), state)
	defer flushInput()
	keys, stopKeyboard := keyboardInput()
	defer stopKeyboard()
	return terminalSession(c, r, keys)
}

func terminalSession(c net.Conn, r *bufio.Reader, keys <-chan []byte) (int, error) {
	events := make(chan received)
	stop := make(chan struct{})
	defer close(stop)
	go func() {
		for {
			m, e := read(r)
			select {
			case events <- received{m, e}:
			case <-stop:
				return
			}
			if e != nil {
				return
			}
		}
	}()
	c.SetReadDeadline(time.Now().Add(5 * time.Second))
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	ready, closing := false, false
	newline := []byte("\r")
	var queue []byte
	expected := 0
	var sent, closed time.Time
	opened := time.Now()
	for {
		input := keys
		if !ready || closing {
			input = nil
		}
		select {
		case ev := <-events:
			if ev.e != nil {
				return 2, ev.e
			}
			m := ev.m
			switch str(m, "type") {
			case "terminal":
				switch str(m, "newline") {
				case "cr":
					newline = []byte("\r")
				case "lf":
					newline = []byte("\n")
				case "crlf":
					newline = []byte("\r\n")
				default:
					return 2, errors.New("invalid newline")
				}
				ready = true
				c.SetReadDeadline(time.Time{})
				fmt.Fprint(os.Stderr, "已進入 UART 終端：Ctrl+C 傳至設備，Ctrl+] 離開。\r\n")
			case "rx":
				b, e := raw(m, "data_b64")
				if e != nil {
					return 2, e
				}
				if _, e = os.Stdout.Write(b); e != nil {
					return 2, e
				}
			case "terminal_tx":
				if expected == 0 || number(m, "accepted_bytes") != float64(expected) {
					return 2, errors.New("互動 TX 回覆不符；結果不明")
				}
				expected = 0
			case "terminal_closed":
				if closing {
					fmt.Fprint(os.Stderr, "\r\n已離開終端；確認 Shell 後以 terminal --shell-ready 恢復 exec。\r\n")
					return 0, nil
				}
			case "error":
				return 2, fmt.Errorf("terminal: %v", m)
			}
		case b := <-input:
			if !ready || closing {
				continue
			}
			if bytes.ContainsRune(b, 0x1d) {
				closing = true
				queue = nil
				closed = time.Now()
				if e := send(c, message{"op": "terminal_close"}); e != nil {
					return 2, e
				}
			} else {
				b = bytes.ReplaceAll(b, []byte("\r"), newline)
				if len(queue)+len(b) > 4096 {
					return 2, errors.New("互動輸入佇列已滿；不會重送")
				}
				queue = append(queue, b...)
			}
		case <-ticker.C:
		}
		if !ready && time.Since(opened) > 5*time.Second {
			return 2, errors.New("終端確認逾時")
		}
		if expected > 0 && time.Since(sent) > 5*time.Second {
			return 2, errors.New("互動 TX 逾時；結果不明")
		}
		if closing && time.Since(closed) > 2*time.Second {
			return 2, errors.New("終端關閉逾時，請用 status 確認")
		}
		if ready && !closing && expected == 0 && len(queue) > 0 {
			expected = len(queue)
			sent = time.Now()
			if e := send(c, message{"op": "terminal_write", "data_b64": base64.StdEncoding.EncodeToString(queue)}); e != nil {
				return 2, e
			}
			queue = nil
		}
	}
}
