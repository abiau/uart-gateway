package client

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"
)

func OperationID() string {
	b := make([]byte, 16)
	if _, e := rand.Read(b); e != nil {
		panic("無法取得安全亂數")
	}
	return hex.EncodeToString(b)
}
func EncodePayload(text, newline, encoding string, maximum int) ([]byte, error) {
	if len(text) > 12288 {
		return nil, errors.New("輸入超過上限")
	}
	var b []byte
	var e error
	switch encoding {
	case "text":
		if !utf8.ValidString(text) {
			return nil, invalid()
		}
		for _, r := range text {
			if !unicode.IsPrint(r) {
				return nil, errors.New("文字不可含隱藏控制字元，請明確選擇換行")
			}
		}
		b = []byte(text)
	case "hex":
		filtered := []byte{}
		for _, r := range text {
			if r == ' ' || r == '\t' {
				continue
			}
			if r > 127 {
				return nil, invalid()
			}
			filtered = append(filtered, byte(r))
		}
		b, e = hex.DecodeString(string(filtered))
		if e != nil {
			return nil, errors.New("Hex 須為成對十六進位數字")
		}
	default:
		return nil, errors.New("編碼須為 text 或 hex")
	}
	switch newline {
	case "none":
	case "lf":
		b = append(b, '\n')
	case "cr":
		b = append(b, '\r')
	case "crlf":
		b = append(b, '\r', '\n')
	default:
		return nil, errors.New("換行須為 none、lf、cr 或 crlf")
	}
	if len(b) == 0 || len(b) > 4096 || len(b) > maximum {
		return nil, errors.New("單次 TX 含換行超過上限，不會拆分或傳送")
	}
	return b, nil
}

type Lease struct {
	Client  *Client
	Channel Channel
	Token   string
	Context context.Context
	cancel  context.CancelFunc
	done    chan struct{}
	mu      sync.Mutex
	failure error
}

func (c *Client) Acquire(ctx context.Context, ch Channel) (*Lease, error) {
	if !c.Can(ch.ID, "tx") || !c.Can(ch.ID, "configure") || ch.Binding.Status != "confirmed" || ch.State != "ready" {
		return nil, errors.New("缺少 TX 權限或目標尚未確認")
	}
	r, e := c.Request(ctx, "lease.acquire", Object{"channel_id": ch.ID, "expected": ch.Expected(c.Instance)})
	if e != nil {
		return nil, e
	}
	token := String(r, "fencing_token")
	if e = validateLease(r, ch.ID, token); e != nil {
		return nil, e
	}
	own, cancel := context.WithCancel(ctx)
	l := &Lease{Client: c, Channel: ch, Token: token, Context: own, cancel: cancel, done: make(chan struct{})}
	go func() {
		defer close(l.done)
		ticker := time.NewTicker(time.Duration(c.Limits["lease_renew_interval_ms"]) * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-own.Done():
				return
			case <-ticker.C:
				r, e := c.Request(own, "lease.renew", l.params())
				if e == nil {
					e = validateLease(r, ch.ID, token)
				}
				if e != nil {
					l.mu.Lock()
					l.failure = errors.New("租約續約失敗，不會重新取得或重送")
					l.mu.Unlock()
					cancel()
					return
				}
			}
		}
	}()
	return l, nil
}
func validateLease(v Object, channel, token string) error {
	if String(v, "channel_id") != channel || String(v, "fencing_token") != token {
		return invalid()
	}
	if _, e := Uint(token); e != nil {
		return e
	}
	ttl, e := Integer(v, "ttl_ms", 1, 3600000)
	if e != nil {
		return e
	}
	_, e = Integer(v, "remaining_ms", 0, ttl)
	return e
}
func (l *Lease) params() Object {
	return Object{"channel_id": l.Channel.ID, "expected": l.Channel.Expected(l.Client.Instance), "fencing_token": l.Token}
}
func (l *Lease) Err() error { l.mu.Lock(); defer l.mu.Unlock(); return l.failure }
func (l *Lease) Close() {
	l.cancel()
	<-l.done
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, _ = l.Client.Request(ctx, "lease.release", l.params())
}
func (l *Lease) Write(ctx context.Context, id string, payload []byte) (Object, error) {
	if !ValidID(id) || len(payload) == 0 || len(payload) > l.Client.Limits["max_tx_bytes"] {
		return nil, invalid()
	}
	if l.Context.Err() != nil {
		return nil, errors.New("租約已失效，未寫入")
	}
	p := l.params()
	p["tx_id"] = id
	p["data_b64"] = base64.StdEncoding.EncodeToString(payload)
	r, e := l.Client.Request(ctx, "tx.write", p)
	if e != nil {
		return nil, e
	}
	v := Map(r, "tx")
	if e = ValidateTx(v, id, l.Channel.ID, l.Client.Instance); e != nil {
		return nil, e
	}
	return v, nil
}
func ValidateTx(v Object, id, channel, instance string) error {
	if v == nil || String(v, "tx_id") != id || String(v, "channel_id") != channel || String(v, "gateway_instance_id") != instance {
		return errors.New("TX 結果識別不符，保留不明收據")
	}
	required := []string{"state", "submitted_bytes", "accepted_bytes", "io_started", "reason_code", "completed_at", "retained_for_ms", "status_origin"}
	for _, k := range required {
		if _, ok := v[k]; !ok {
			return invalid()
		}
	}
	state := String(v, "state")
	origin := String(v, "status_origin")
	if state != "pending" && state != "rejected" && state != "unknown" && state != "written_to_driver" {
		return invalid()
	}
	if origin != "record" && origin != "unavailable" {
		return invalid()
	}
	if v["reason_code"] != nil {
		if _, ok := v["reason_code"].(string); !ok {
			return invalid()
		}
	}
	if state != "pending" {
		if _, e := Integer(v, "retained_for_ms", 0, 2147483647); e != nil {
			return e
		}
	} else if v["retained_for_ms"] != nil {
		return invalid()
	}
	if origin == "unavailable" {
		if state != "unknown" || v["submitted_bytes"] != nil || v["accepted_bytes"] != nil || v["io_started"] != nil || v["completed_at"] != nil {
			return invalid()
		}
		return nil
	}
	n, e := Integer(v, "submitted_bytes", 1, 4096)
	if e != nil {
		return e
	}
	started, ok := v["io_started"].(bool)
	if !ok {
		return invalid()
	}
	if state == "pending" {
		if v["accepted_bytes"] != nil || v["completed_at"] != nil {
			return invalid()
		}
	} else if state == "written_to_driver" {
		a, e := Integer(v, "accepted_bytes", n, n)
		if e != nil || a != n || !started || String(v, "completed_at") == "" {
			return invalid()
		}
	} else if state == "rejected" {
		a, e := Integer(v, "accepted_bytes", 0, 0)
		if e != nil || a != 0 || started || String(v, "completed_at") == "" {
			return invalid()
		}
	} else if state == "unknown" {
		if !started || String(v, "completed_at") == "" {
			return invalid()
		}
		if v["accepted_bytes"] != nil {
			if _, e := Integer(v, "accepted_bytes", 0, n); e != nil {
				return e
			}
		}
	}
	return nil
}
func (c *Client) Status(ctx context.Context, id, channel, instance string) (Object, error) {
	r, e := c.Request(ctx, "tx.status", Object{"channel_id": channel, "gateway_instance_id": instance, "tx_id": id})
	if e != nil {
		return nil, e
	}
	v := Map(r, "tx")
	return v, ValidateTx(v, id, channel, instance)
}
func (c *Client) WaitTx(ctx context.Context, id, channel string) (Object, error) {
	for {
		v, e := c.Status(ctx, id, channel, c.Instance)
		if e != nil {
			return nil, e
		}
		if String(v, "state") != "pending" {
			return v, nil
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(50 * time.Millisecond):
		}
	}
}

type Receipt struct {
	Version   int            `json:"version"`
	ID        string         `json:"tx_id"`
	Channel   string         `json:"channel_id"`
	Instance  string         `json:"gateway_instance_id"`
	Principal string         `json:"principal_id"`
	Target    Channel        `json:"target"`
	Result    Object         `json:"tx_result"`
	Query     Object         `json:"tx_query"`
	Execution *ExecutionMeta `json:"execution,omitempty"`
}
type ExecutionMeta struct {
	Generation   string       `json:"generation"`
	Cursor       string       `json:"cursor"`
	Subscription Subscription `json:"subscription"`
	State        string       `json:"state"`
	ExitCode     *int         `json:"exit_code"`
	Maximum      int          `json:"maximum"`
	Observed     uint64       `json:"output_bytes"`
	Truncated    bool         `json:"output_truncated"`
	Reason       string       `json:"reason_code"`
}
type Journal struct{ Directory string }

func (j Journal) path(id string) (string, error) {
	if !ValidID(id) {
		return "", invalid()
	}
	return filepath.Join(j.Directory, id+".json"), nil
}
func (j Journal) Load(id string) (*Receipt, error) {
	p, e := j.path(id)
	if e != nil {
		return nil, e
	}
	raw, e := ReadPrivate(p, 16384)
	if e != nil {
		return nil, e
	}
	v, e := StrictJSON(raw, 24)
	if e != nil {
		return nil, e
	}
	b, _ := json.Marshal(v)
	var r Receipt
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	if e = d.Decode(&r); e != nil || r.Version != 1 || r.ID != id || !ValidID(r.Channel) || !ValidID(r.Instance) || !ValidID(r.Principal) {
		return nil, invalid()
	}
	if e = r.Target.Validate(); e != nil {
		return nil, e
	}
	if r.Target.ID != r.Channel {
		return nil, invalid()
	}
	if r.Result != nil {
		if e = ValidateTx(r.Result, r.ID, r.Channel, r.Instance); e != nil {
			return nil, e
		}
	}
	if r.Execution != nil {
		m := r.Execution
		if !deploymentPattern.MatchString(m.Generation) || m.Maximum < 1 || m.Maximum > 1048576 || m.Subscription.Channel != r.Channel || m.Subscription.Epoch != r.Target.Epoch || m.Subscription.Revision != r.Target.Binding.Revision {
			return nil, invalid()
		}
		if e = m.Subscription.Validate(); e != nil {
			return nil, e
		}
		if _, e = Uint(m.Cursor); e != nil {
			return nil, e
		}
		switch m.State {
		case "pending", "unknown", "completed", "not_sent":
		default:
			return nil, invalid()
		}
		if m.ExitCode != nil && (*m.ExitCode < 0 || *m.ExitCode > 255) {
			return nil, invalid()
		}
	}
	return &r, nil
}
func (j Journal) Save(r *Receipt, create bool) error {
	p, e := j.path(r.ID)
	if e != nil {
		return e
	}
	if create {
		if e = PrivateDir(j.Directory); e != nil {
			return e
		}
		dir, e := os.Open(j.Directory)
		if e != nil {
			return e
		}
		entries, re := dir.ReadDir(257)
		dir.Close()
		if re != nil && re != io.EOF {
			return re
		}
		if len(entries) >= 256 {
			return errors.New("Go 收據已達 256 份上限，請先封存舊收據")
		}
	}
	b, e := json.Marshal(r)
	if e != nil || len(b) > 16384 {
		return errors.New("收據超過上限")
	}
	return WritePrivate(p, append(b, '\n'), !create)
}
func (j Journal) Update(r *Receipt, result Object, query bool) error {
	if e := ValidateTx(result, r.ID, r.Channel, r.Instance); e != nil {
		return e
	}
	known := r.Result != nil && String(r.Result, "status_origin") == "record" && String(r.Result, "state") != "pending"
	if query {
		r.Query = result
	}
	if !(query && known && String(result, "status_origin") == "unavailable") {
		stable := func(v Object) Object {
			copy := Object{}
			for k, value := range v {
				if k != "retained_for_ms" {
					copy[k] = value
				}
			}
			return copy
		}
		if known && !reflect.DeepEqual(stable(r.Result), stable(result)) {
			return errors.New("TX 終態矛盾，保留原收據")
		}
		r.Result = result
	}
	return j.Save(r, false)
}
