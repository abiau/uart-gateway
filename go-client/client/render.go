package client

import (
	"fmt"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

func DecodedText(b []byte) string {
	var out strings.Builder
	for len(b) > 0 {
		r, n := utf8.DecodeRune(b)
		if r == utf8.RuneError && n == 1 {
			fmt.Fprintf(&out, "\\x%02x", b[0])
		} else {
			out.WriteRune(r)
		}
		b = b[n:]
	}
	return out.String()
}
func SafeText(b []byte, terminal bool) string {
	r := Renderer{Terminal: terminal}
	return r.Feed(b) + r.Finish()
}
func Label(s string) string {
	if len(s) > 512 {
		s = s[:512]
	}
	text := SafeText([]byte(s), false)
	text = strings.ReplaceAll(text, "\n", "\\n")
	return strings.ReplaceAll(text, "\t", "\\t")
}

type Renderer struct {
	Terminal  bool
	Multi     bool
	pending   []byte
	escape    []byte
	state     byte
	oscEscape bool
}

func validSGR(s string) bool {
	if len(s) > 128 || !strings.HasPrefix(s, "\x1b[") || !strings.HasSuffix(s, "m") {
		return false
	}
	body := s[2 : len(s)-1]
	if body == "" {
		return true
	}
	parts := strings.Split(body, ";")
	if len(parts) > 32 {
		return false
	}
	for i := 0; i < len(parts); i++ {
		n, e := strconv.Atoi(parts[i])
		if e != nil {
			return false
		}
		if n == 38 || n == 48 {
			if i+2 >= len(parts) {
				return false
			}
			mode, e := strconv.Atoi(parts[i+1])
			if e != nil {
				return false
			}
			count := 1
			if mode == 2 {
				count = 3
			} else if mode != 5 {
				return false
			}
			if i+1+count >= len(parts) {
				return false
			}
			for j := 0; j < count; j++ {
				v, e := strconv.Atoi(parts[i+2+j])
				if e != nil || v < 0 || v > 255 {
					return false
				}
			}
			i += 1 + count
			continue
		}
		if !(n == 0 || n == 1 || n == 2 || n == 3 || n == 4 || n == 7 || n == 22 || n == 23 || n == 24 || n == 27 || n >= 30 && n <= 37 || n == 39 || n >= 40 && n <= 47 || n == 49 || n >= 90 && n <= 97 || n >= 100 && n <= 107) {
			return false
		}
	}
	return true
}
func (r *Renderer) Feed(b []byte) string {
	data := append(r.pending, b...)
	r.pending = nil
	var out strings.Builder
	for len(data) > 0 {
		if r.Terminal && r.state != 0 {
			ch := data[0]
			data = data[1:]
			switch r.state {
			case 'e':
				if ch == '[' {
					r.state = 'c'
					r.escape = []byte("\x1b[")
				} else if ch == ']' || ch == 'P' || ch == '^' || ch == '_' {
					r.state = 'o'
					r.escape = nil
				} else {
					r.state = 0
				}
			case 'c':
				if len(r.escape) < 256 {
					r.escape = append(r.escape, ch)
				}
				if ch >= 0x40 && ch <= 0x7e {
					if validSGR(string(r.escape)) {
						out.Write(r.escape)
					}
					r.escape = nil
					r.state = 0
				}
			case 'o':
				if ch == 7 || r.oscEscape && ch == '\\' {
					r.state = 0
				}
				r.oscEscape = ch == 27
			}
			continue
		}
		if !utf8.FullRune(data) {
			r.pending = append([]byte{}, data...)
			break
		}
		ch, n := utf8.DecodeRune(data)
		if ch == utf8.RuneError && n == 1 {
			fmt.Fprintf(&out, "\\x%02x", data[0])
			data = data[1:]
			continue
		}
		data = data[n:]
		if r.Terminal && ch == 27 {
			r.state = 'e'
			continue
		}
		if ch == '\n' || ch == '\t' || unicode.IsPrint(ch) {
			out.WriteRune(ch)
		} else if r.Terminal && !r.Multi && (ch == '\r' || ch == '\b') {
			out.WriteRune(ch)
		} else {
			switch ch {
			case '\r':
				out.WriteString("\\r")
			case '\b':
				out.WriteString("\\b")
			default:
				if ch < 256 {
					fmt.Fprintf(&out, "\\x%02x", ch)
				} else {
					fmt.Fprintf(&out, "\\u%04x", ch)
				}
			}
		}
	}
	return out.String()
}
func (r *Renderer) Finish() string {
	tail := r.pending
	r.pending = nil
	r.state = 0
	r.escape = nil
	r.oscEscape = false
	return SafeTail(tail)
}
func SafeTail(b []byte) string {
	var out strings.Builder
	for _, v := range b {
		fmt.Fprintf(&out, "\\x%02x", v)
	}
	return out.String()
}
