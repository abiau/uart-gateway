// Package client 提供 Protocol V1 的共用 Linux Client，不直接操作 UART。
package client

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"regexp"
	"strconv"
	"unicode/utf8"
)

type Object = map[string]any

var idPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
var tokenPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{43,128}$`)
var uintPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)$`)

func ValidID(s string) bool { return idPattern.MatchString(s) }
func Uint(s string) (uint64, error) {
	if !uintPattern.MatchString(s) {
		return 0, errors.New("U64 格式不合法")
	}
	n, e := strconv.ParseUint(s, 10, 64)
	if e != nil {
		return 0, errors.New("U64 超過上限")
	}
	return n, nil
}
func String(m Object, k string) string { s, _ := m[k].(string); return s }
func Map(m Object, k string) Object    { v, _ := m[k].(map[string]any); return v }
func Integer(m Object, k string, min, max int) (int, error) {
	s, ok := m[k].(json.Number)
	if !ok {
		return 0, fmt.Errorf("缺少合法整數欄位 %s", k)
	}
	n, e := strconv.Atoi(string(s))
	if e != nil || n < min || n > max {
		return 0, fmt.Errorf("整數欄位 %s 超出範圍", k)
	}
	return n, nil
}
func invalid() error { return errors.New("Protocol V1 訊框格式不合法") }

// StrictJSON 在解碼時拒絕重複 Key、過深容器及非法 Unicode，不回印資料。
func StrictJSON(raw []byte, depth int) (Object, error) {
	if !utf8.Valid(raw) || !validSurrogates(raw) {
		return nil, invalid()
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var read func(int) (any, error)
	read = func(level int) (any, error) {
		t, e := d.Token()
		if e != nil {
			return nil, invalid()
		}
		switch v := t.(type) {
		case json.Delim:
			if level > depth {
				return nil, invalid()
			}
			switch v {
			case '{':
				m := Object{}
				for d.More() {
					key, e := d.Token()
					if e != nil {
						return nil, invalid()
					}
					k, ok := key.(string)
					if !ok {
						return nil, invalid()
					}
					if _, found := m[k]; found {
						return nil, invalid()
					}
					x, e := read(level + 1)
					if e != nil {
						return nil, e
					}
					m[k] = x
				}
				end, e := d.Token()
				if e != nil || end != json.Delim('}') {
					return nil, invalid()
				}
				return m, nil
			case '[':
				a := []any{}
				for d.More() {
					x, e := read(level + 1)
					if e != nil {
						return nil, e
					}
					a = append(a, x)
				}
				end, e := d.Token()
				if e != nil || end != json.Delim(']') {
					return nil, invalid()
				}
				return a, nil
			}
			return nil, invalid()
		case json.Number:
			n, e := strconv.ParseFloat(string(v), 64)
			if e != nil || math.IsInf(n, 0) || math.IsNaN(n) {
				return nil, invalid()
			}
			return v, nil
		default:
			return t, nil
		}
	}
	value, e := read(1)
	if e != nil {
		return nil, e
	}
	if _, e = d.Token(); e != io.EOF {
		return nil, invalid()
	}
	m, ok := value.(map[string]any)
	if !ok {
		return nil, invalid()
	}
	return m, nil
}
func validSurrogates(b []byte) bool {
	for i := 0; i < len(b); i++ {
		if b[i] != '"' {
			continue
		}
		i++
		for i < len(b) && b[i] != '"' {
			if b[i] != '\\' {
				i++
				continue
			}
			i++
			if i >= len(b) {
				return false
			}
			if b[i] != 'u' {
				i++
				continue
			}
			if i+4 >= len(b) {
				return false
			}
			n, e := strconv.ParseUint(string(b[i+1:i+5]), 16, 16)
			if e != nil {
				return false
			}
			i += 5
			if n >= 0xd800 && n <= 0xdbff {
				if i+5 >= len(b) || b[i] != '\\' || b[i+1] != 'u' {
					return false
				}
				low, e := strconv.ParseUint(string(b[i+2:i+6]), 16, 16)
				if e != nil || low < 0xdc00 || low > 0xdfff {
					return false
				}
				i += 6
			} else if n >= 0xdc00 && n <= 0xdfff {
				return false
			}
		}
	}
	return true
}
func Binary(s string, maximum int) ([]byte, error) {
	if len(s) > ((maximum+2)/3)*4 {
		return nil, invalid()
	}
	b, e := base64.StdEncoding.Strict().DecodeString(s)
	if e != nil || len(b) > maximum || base64.StdEncoding.EncodeToString(b) != s {
		return nil, invalid()
	}
	return b, nil
}

type RemoteError struct {
	Code    string
	Message string
}

func (e *RemoteError) Error() string { return "Gateway 拒絕操作：" + e.Code }

type Context struct {
	Instance string `json:"gateway_instance_id"`
	Revision string `json:"binding_revision"`
	Epoch    any    `json:"stream_epoch"`
}
type Channel struct {
	ID      string `json:"channel_id"`
	Label   string `json:"label"`
	Note    any    `json:"target_note"`
	State   string `json:"state"`
	Binding struct {
		Revision string `json:"revision"`
		Status   string `json:"status"`
	} `json:"binding"`
	Epoch     any    `json:"stream_epoch"`
	Adapter   Object `json:"adapter"`
	Serial    Object `json:"serial"`
	RX        Object `json:"rx"`
	TX        Object `json:"tx"`
	Recording Object `json:"recording"`
	LastError any    `json:"last_error"`
}

func (c Channel) Expected(instance string) Context {
	return Context{instance, c.Binding.Revision, c.Epoch}
}
func (c Channel) Validate() error {
	if !ValidID(c.ID) || !utf8.ValidString(c.Label) || len(c.Label) < 1 || len(c.Label) > 128 || c.Adapter == nil || c.Serial == nil || c.RX == nil || c.TX == nil || c.Recording == nil {
		return invalid()
	}
	if _, e := Uint(c.Binding.Revision); e != nil {
		return e
	}
	switch c.State {
	case "closed", "opening", "ready", "draining", "lost", "faulted":
	default:
		return invalid()
	}
	switch c.Binding.Status {
	case "unconfirmed", "rebinding", "confirmed":
	default:
		return invalid()
	}
	if c.Epoch != nil {
		epoch, ok := c.Epoch.(string)
		if !ok || !ValidID(epoch) {
			return invalid()
		}
		a, e := Uint(String(c.RX, "earliest_offset"))
		if e != nil {
			return e
		}
		z, e := Uint(String(c.RX, "next_offset"))
		if e != nil || z < a {
			return invalid()
		}
	}
	return nil
}
func decodeChannel(v any) (Channel, error) {
	b, e := json.Marshal(v)
	if e != nil {
		return Channel{}, invalid()
	}
	var c Channel
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	e = d.Decode(&c)
	if e != nil {
		return c, invalid()
	}
	return c, c.Validate()
}

type Subscription struct {
	ID        string `json:"subscription_id"`
	Channel   string `json:"channel_id"`
	Revision  string `json:"binding_revision"`
	Epoch     string `json:"stream_epoch"`
	Start     string `json:"start_offset"`
	Watermark string `json:"replay_until_offset"`
	Earliest  string `json:"earliest_offset"`
}

func (s Subscription) Validate() error {
	if !ValidID(s.ID) || !ValidID(s.Channel) || !ValidID(s.Epoch) {
		return invalid()
	}
	if _, e := Uint(s.Revision); e != nil {
		return e
	}
	a, e := Uint(s.Earliest)
	if e != nil {
		return e
	}
	b, e := Uint(s.Start)
	if e != nil {
		return e
	}
	z, e := Uint(s.Watermark)
	if e != nil || a > b || b > z {
		return invalid()
	}
	return nil
}

type Event struct {
	Kind     string
	Channel  string
	Revision string
	Epoch    any
	Data     Object
	Raw      Object
}
