package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

type compact struct {
	phase        string
	pending      []byte
	skip         int
	begins, ends [][]byte
}

var validID = regexp.MustCompile(`^[0-9a-f]{32}$`)
var ending = regexp.MustCompile(`^([0-9]{1,3})\r?\n$`)
var partial = regexp.MustCompile(`^[0-9]{0,3}\r?$`)

func (c *compact) start(m message, started bool) error {
	id := str(m, "id")
	if !validID.MatchString(id) {
		return errors.New("命令 ID 不合法")
	}
	c.begins = [][]byte{[]byte("\n__UART_BEGIN_" + id + "__\n"), []byte("\n__UART_BEGIN_" + id + "__\r\n")}
	c.ends = [][]byte{[]byte("\n__UART_END_" + id + "__:"), []byte("\r\n__UART_END_" + id + "__:")}
	c.phase = "echo"
	if started {
		c.phase = "body"
	}
	p, e := raw(m, "prefix_b64")
	c.pending = p
	c.skip = len(p)
	return e
}
func (c *compact) feed(data []byte) []byte {
	var out []byte
	for _, b := range data {
		switch c.phase {
		case "echo":
			c.pending = append(c.pending, b)
			found := false
			for _, begin := range c.begins {
				if bytes.HasSuffix(c.pending, begin) {
					found = true
				}
			}
			if found {
				c.phase = "body"
				c.pending = nil
				c.skip = 0
			} else if len(c.pending) > len(c.begins[1]) {
				c.pending = c.pending[1:]
			}
		case "body":
			c.pending = append(c.pending, b)
			for len(c.pending) > 0 {
				possible, done := false, false
				for _, end := range c.ends {
					if bytes.HasPrefix(end, c.pending) {
						possible = true
					} else if bytes.HasPrefix(c.pending, end) {
						suffix := c.pending[len(end):]
						if match := ending.FindSubmatch(suffix); match != nil {
							n, _ := strconv.Atoi(string(match[1]))
							if n <= 255 {
								done = true
								break
							}
						}
						possible = possible || partial.Match(suffix)
					}
				}
				if done {
					c.phase = "idle"
					c.pending = nil
					c.skip = 0
					break
				}
				if possible {
					break
				}
				if c.skip > 0 {
					c.skip--
				} else {
					out = append(out, c.pending[0])
				}
				c.pending = c.pending[1:]
			}
		default:
			out = append(out, b)
		}
	}
	return out
}
func (c *compact) finish() []byte {
	var b []byte
	if c.phase == "body" && c.skip <= len(c.pending) {
		b = c.pending[c.skip:]
	}
	c.pending = nil
	return b
}
func (c *compact) event(m message) ([]byte, error) {
	switch str(m, "type") {
	case "monitor":
		if ex, ok := m["execution"].(map[string]any); ok {
			return c.command(message(ex), ex["started"] == true)
		}
	case "tx":
		if str(m, "action") == "interrupt" && c.phase == "echo" {
			c.phase = "idle"
			c.pending = nil
		} else if str(m, "id") != "" && str(m, "action") != "terminal" && str(m, "action") != "interrupt" {
			return c.command(m, false)
		}
	case "rx":
		b, e := raw(m, "data_b64")
		if e != nil {
			return nil, e
		}
		return c.feed(b), nil
	case "completed":
		c.phase = "idle"
		c.pending = nil
	case "terminal_opened":
		b := c.finish()
		c.phase = "idle"
		return b, nil
	}
	return nil, nil
}
func (c *compact) command(m message, started bool) ([]byte, error) {
	if e := c.start(m, started); e != nil {
		return nil, e
	}
	s := str(m, "command")
	if s == "" {
		return nil, nil
	}
	if cwd, ok := m["cwd"].(string); ok {
		s = "[cwd=" + cwd + "] " + s
	}
	return []byte(s + "\r\n"), nil
}

type full struct {
	line, cr bool
	escape   string
	stamp    string
	pending  []byte
}

var sgr = regexp.MustCompile("^\x1b\\[[0-9;:]*m$")

func (f *full) text(s string) {
	if !f.line {
		fmt.Printf("[%s] RX  ", f.stamp)
		f.line = true
	}
	fmt.Print(s)
	if s == "\n" {
		f.line = false
	}
}
func (f *full) esc() {
	if sgr.MatchString(f.escape) {
		f.text(f.escape)
	} else {
		f.text("<" + strings.NewReplacer("\\x1b", "ESC", "\r", "\\r", "\n", "\\n", "\t", "\\t").Replace(safe([]byte(f.escape))) + ">")
	}
	f.escape = ""
}
func (f *full) rx(b []byte, final bool) {
	f.pending = append(f.pending, b...)
	for len(f.pending) > 0 {
		if !final && !utf8.FullRune(f.pending) {
			break
		}
		first := f.pending[0]
		r, n := utf8.DecodeRune(f.pending)
		f.pending = f.pending[n:]
		s := string(r)
		if r == '�' && n == 1 {
			s = fmt.Sprintf("\\x%02x", first)
		}
		if f.cr {
			f.cr = false
			if r != '\n' {
				f.text("<CR>")
			}
		}
		if f.escape != "" {
			f.escape += s
			ended := false
			if strings.HasPrefix(f.escape, "\x1b[") {
				ended = len(f.escape) >= 3 && r >= '@' && r <= '~'
			} else if strings.ContainsAny(f.escape[1:2], "]PX^_") {
				ended = strings.HasSuffix(f.escape, "\x1b\\") || r == 7
			} else {
				ended = len(f.escape) >= 2
			}
			if ended || len(f.escape) >= 256 {
				f.esc()
			}
		} else if r == 27 {
			f.escape = s
		} else if r == '\r' {
			f.cr = true
		} else if r == '\b' {
			f.text("<BS>")
		} else if r == '\n' || r == '\t' || unicode.IsPrint(r) {
			f.text(s)
		} else {
			f.text(fmt.Sprintf("<0x%02x>", r))
		}
	}
}
func (f *full) event(m message) error {
	now := time.Now()
	if t, e := time.Parse(time.RFC3339Nano, str(m, "time")); e == nil {
		now = t
	}
	stamp := now.Local().Format("15:04:05.000")
	kind := str(m, "type")
	if kind == "rx" {
		b, e := raw(m, "data_b64")
		if e != nil {
			return e
		}
		if !f.line {
			f.stamp = stamp
		}
		f.rx(b, false)
		return nil
	}
	if f.line {
		fmt.Println()
		f.line = false
	}
	label := strings.ToUpper(kind)
	if kind == "completed" {
		label = "DONE"
	}
	text := ""
	if kind == "tx" {
		if m["redacted"] == true {
			text = fmt.Sprintf("<互動輸入已隱去，%v bytes>", m["bytes"])
		} else if str(m, "action") == "interrupt" {
			text = "Ctrl+C (0x03)"
		} else {
			b, e := raw(m, "data_b64")
			if e != nil {
				return e
			}
			text = strings.NewReplacer("\r", "\\r", "\n", "\\n").Replace(safe(b))
		}
	} else {
		b, _ := json.Marshal(m)
		text = string(b)
	}
	fmt.Printf("[%s] %s  %s\n", stamp, label, text)
	return nil
}
func (f *full) finish() {
	f.rx(nil, true)
	if f.escape != "" {
		f.esc()
	}
	if f.cr {
		f.text("<CR>")
	}
	if f.line {
		fmt.Println()
	}
}
func monitor(r *bufio.Reader, o options) (int, error) {
	c := &compact{}
	f := &full{}
	defer func() {
		if o.full {
			f.finish()
		} else if !o.json {
			os.Stdout.Write(c.finish())
		}
	}()
	for {
		m, e := read(r)
		if e != nil {
			return 2, e
		}
		if o.json {
			show(os.Stdout, m)
		} else if o.full {
			e = f.event(m)
		} else {
			var b []byte
			b, e = c.event(m)
			os.Stdout.Write(b)
		}
		if e != nil {
			return 2, e
		}
		if str(m, "type") == "error" {
			if !o.json && !o.full {
				show(os.Stderr, m)
			}
			return 2, nil
		}
	}
}
