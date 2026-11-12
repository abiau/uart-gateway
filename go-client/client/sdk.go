package client

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"reflect"
	"strconv"
	"time"
)

type Grant struct {
	Channel     string   `json:"channel_id"`
	Permissions []string `json:"permissions"`
}
type Client struct {
	T         *Transport
	Instance  string
	Principal string
	Session   string
	Hello     Object
	Grants    []Grant
	Limits    map[string]int
}

func Connect(ctx context.Context, host string, port int, token, access string) (*Client, error) {
	if !tokenPattern.MatchString(token) {
		return nil, errors.New("憑證格式不合法")
	}
	if host != "127.0.0.1" && host != "::1" {
		return nil, errors.New("Client 僅連 Loopback，遠端請使用 SSH 轉送")
	}
	if port < 1 || port > 65535 {
		return nil, errors.New("Port 超出範圍")
	}
	conn, e := (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp", net.JoinHostPort(host, strconv.Itoa(port)))
	if e != nil {
		return nil, errors.New("無法連到 Gateway，請先在 Windows 啟動遠端使用")
	}
	t := newTransport(conn)
	c := &Client{T: t, Limits: map[string]int{}}
	success := false
	defer func() {
		if !success {
			t.Close()
		}
	}()
	handshake, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	hello, e := t.Request(handshake, "hello", Object{"versions": []any{Object{"major": 1, "minor": 0}}, "client_name": "uart-go", "client_version": "0.2.0"})
	if e != nil {
		return nil, e
	}
	version := Map(hello, "version")
	major, e := Integer(version, "major", 1, 1)
	if e != nil || major != 1 {
		return nil, invalid()
	}
	if _, e = Integer(version, "minor", 0, 0); e != nil {
		return nil, e
	}
	c.Instance = String(hello, "gateway_instance_id")
	if !ValidID(c.Instance) {
		return nil, invalid()
	}
	limits := Map(hello, "limits")
	required := map[string][2]int{"max_frame_bytes": {16384, 65536}, "max_json_depth": {1, 32}, "max_rx_chunk_bytes": {1, 4096}, "max_tx_bytes": {1, 4096}, "max_pending_requests": {1, 32}, "max_subscriptions": {1, 16}, "max_channel_list_items": {1, 16}, "rx_ring_bytes": {1, 2147483647}, "max_tx_queue_items": {1, 2147483647}, "max_tx_queue_bytes": {1, 2147483647}, "handshake_timeout_ms": {1, 60000}, "heartbeat_interval_ms": {1, 3600000}, "idle_timeout_ms": {1, 3600000}, "lease_ttl_ms": {1, 3600000}, "lease_renew_interval_ms": {1, 3600000}, "tx_result_retention_ms": {1, 2147483647}, "tx_result_cache_entries": {1, 2147483647}}
	for k, r := range required {
		n, e := Integer(limits, k, r[0], r[1])
		if e != nil {
			return nil, e
		}
		c.Limits[k] = n
	}
	if c.Limits["heartbeat_interval_ms"] >= c.Limits["idle_timeout_ms"] || c.Limits["lease_renew_interval_ms"] >= c.Limits["lease_ttl_ms"] {
		return nil, invalid()
	}
	t.maximum.Store(int64(c.Limits["max_frame_bytes"]))
	t.depth.Store(int64(c.Limits["max_json_depth"]))
	t.pendingLimit.Store(int64(c.Limits["max_pending_requests"]))
	methods, ok := hello["auth_methods"].([]any)
	if !ok || len(methods) != 1 || methods[0] != "token" {
		return nil, invalid()
	}
	caps, ok := hello["capabilities"].([]any)
	if !ok || len(caps) > 64 {
		return nil, invalid()
	}
	for _, v := range caps {
		if _, ok := v.(string); !ok {
			return nil, invalid()
		}
	}
	c.Hello = hello
	auth, e := t.Request(handshake, "auth.authenticate", Object{"scheme": "token", "token": token})
	if e != nil {
		return nil, e
	}
	c.Principal = String(auth, "principal_id")
	c.Session = String(auth, "session_id")
	if !ValidID(c.Principal) || !ValidID(c.Session) {
		return nil, invalid()
	}
	raw, _ := json.Marshal(auth["grants"])
	if e = json.Unmarshal(raw, &c.Grants); e != nil || len(c.Grants) == 0 || len(c.Grants) > 64 {
		return nil, invalid()
	}
	allowed := map[string]bool{"read": true}
	switch access {
	case "manage":
		allowed["configure"] = true
	case "tx":
		allowed["configure"] = true
		allowed["tx"] = true
	case "read", "":
	default:
		return nil, errors.New("角色不合法")
	}
	matched := access == "read" || access == ""
	seen := map[string]bool{}
	for _, g := range c.Grants {
		if !ValidID(g.Channel) || seen[g.Channel] || len(g.Permissions) == 0 {
			return nil, invalid()
		}
		seen[g.Channel] = true
		p := map[string]bool{}
		for _, v := range g.Permissions {
			if p[v] || v != "read" && v != "configure" && v != "tx" {
				return nil, invalid()
			}
			p[v] = true
			if access != "" && !allowed[v] {
				return nil, errors.New("憑證實際權限與所選角色不符，已關閉連線")
			}
		}
		if !p["read"] {
			return nil, invalid()
		}
		if access == "manage" && p["configure"] || access == "tx" && p["configure"] && p["tx"] {
			matched = true
		}
	}
	if !matched {
		return nil, errors.New("憑證缺少所選角色的權限")
	}
	t.heartbeat(time.Duration(c.Limits["heartbeat_interval_ms"]) * time.Millisecond)
	success = true
	return c, nil
}
func (c *Client) Close() { c.T.Close() }
func (c *Client) Request(ctx context.Context, method string, params any) (Object, error) {
	q, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	return c.T.Request(q, method, params)
}
func (c *Client) Has(capability string) bool {
	a, _ := c.Hello["capabilities"].([]any)
	for _, v := range a {
		if v == capability {
			return true
		}
	}
	return false
}
func (c *Client) Can(id, permission string) bool {
	for _, g := range c.Grants {
		if g.Channel == id {
			for _, p := range g.Permissions {
				if p == permission {
					return true
				}
			}
		}
	}
	return false
}
func (c *Client) Channels(ctx context.Context) ([]Channel, error) {
	channels := []Channel{}
	var cursor any
	seen := map[string]bool{}
	pages := map[string]bool{}
	for n := 0; n < 64; n++ {
		r, e := c.Request(ctx, "channel.list", Object{"cursor": cursor})
		if e != nil {
			return nil, e
		}
		items, ok := r["channels"].([]any)
		if !ok || len(items) > 16 {
			return nil, invalid()
		}
		for _, v := range items {
			ch, e := decodeChannel(v)
			if e != nil {
				return nil, e
			}
			if seen[ch.ID] || !c.Can(ch.ID, "read") || len(channels) >= 64 {
				return nil, invalid()
			}
			seen[ch.ID] = true
			channels = append(channels, ch)
		}
		cursor = r["next_cursor"]
		if cursor == nil {
			return channels, nil
		}
		s, ok := cursor.(string)
		if !ok || len(s) > 256 || pages[s] {
			return nil, invalid()
		}
		pages[s] = true
	}
	return nil, errors.New("通道分頁超過上限")
}
func (c *Client) Channel(ctx context.Context, id string) (Channel, error) {
	if !ValidID(id) {
		return Channel{}, invalid()
	}
	r, e := c.Request(ctx, "channel.get", Object{"channel_id": id})
	if e != nil {
		return Channel{}, e
	}
	ch, e := decodeChannel(r["channel"])
	if e == nil && ch.ID != id {
		e = invalid()
	}
	return ch, e
}
func SameTarget(a, b Channel) bool {
	return a.ID == b.ID && a.Label == b.Label && reflect.DeepEqual(a.Note, b.Note) && a.Binding.Revision == b.Binding.Revision && reflect.DeepEqual(a.Epoch, b.Epoch) && reflect.DeepEqual(a.Adapter, b.Adapter) && reflect.DeepEqual(a.Serial, b.Serial)
}
func (c *Client) Check(ctx context.Context, old Channel, instance string) (Channel, error) {
	now, e := c.Channel(ctx, old.ID)
	if e != nil {
		return now, e
	}
	if c.Instance != instance || !SameTarget(old, now) || now.State != "ready" {
		return now, errors.New("目標／Gateway／資料流已變更，請重新核對")
	}
	return now, nil
}
func (c *Client) Confirm(ctx context.Context, old Channel) (Channel, error) {
	r, e := c.Request(ctx, "binding.confirm", Object{"channel_id": old.ID, "expected": old.Expected(c.Instance), "label": old.Label, "target_note": old.Note})
	if e != nil {
		return Channel{}, e
	}
	now, e := decodeChannel(r["channel"])
	if e != nil {
		return now, e
	}
	rev, re := Uint(old.Binding.Revision)
	nr, ne := Uint(now.Binding.Revision)
	if re != nil || ne != nil || rev == ^uint64(0) || nr != rev+1 || now.ID != old.ID || now.State != "ready" || now.Binding.Status != "confirmed" || now.Epoch == nil || now.Epoch == old.Epoch || now.Label != old.Label || !reflect.DeepEqual(now.Adapter, old.Adapter) || !reflect.DeepEqual(now.Serial, old.Serial) || !reflect.DeepEqual(now.Note, old.Note) {
		return now, invalid()
	}
	return now, nil
}
func (c *Client) Reopen(ctx context.Context, old Channel) (Channel, error) {
	if !c.Has("channel.reopen_rx") {
		return Channel{}, errors.New("Gateway 不支援 RX 重開")
	}
	r, e := c.Request(ctx, "channel.reopen_rx", Object{"channel_id": old.ID, "expected": old.Expected(c.Instance)})
	if e != nil {
		return Channel{}, e
	}
	now, e := decodeChannel(r["channel"])
	if e == nil && (now.ID != old.ID || now.Epoch == old.Epoch || now.Binding.Status != "unconfirmed") {
		e = invalid()
	}
	return now, e
}
func (c *Client) Subscribe(ctx context.Context, ch Channel, instance string, replay bool, offset string) (Subscription, error) {
	now, e := c.Check(ctx, ch, instance)
	if e != nil {
		return Subscription{}, e
	}
	p := Object{"channel_id": ch.ID, "mode": "live", "gap_policy": "error"}
	if replay || offset != "" {
		if offset == "" {
			offset = String(now.RX, "earliest_offset")
		}
		if _, e := Uint(offset); e != nil {
			return Subscription{}, e
		}
		p["mode"] = "resume"
		p["stream_epoch"] = now.Epoch
		p["from_offset"] = offset
	}
	r, e := c.Request(ctx, "rx.subscribe", p)
	if e != nil {
		return Subscription{}, e
	}
	raw, _ := json.Marshal(r)
	var s Subscription
	if e = json.Unmarshal(raw, &s); e != nil {
		return s, invalid()
	}
	if e = s.Validate(); e != nil {
		return s, e
	}
	if s.Channel != ch.ID || s.Revision != ch.Binding.Revision || s.Epoch != ch.Epoch {
		_, _ = c.Request(ctx, "rx.unsubscribe", Object{"subscription_id": s.ID})
		return s, errors.New("訂閱脈絡與所選快照不符，未顯示 RX")
	}
	return s, nil
}

type RXCursor struct {
	Sub     Subscription
	Next    uint64
	Maximum int
}

func NewCursor(s Subscription, maximum int) (*RXCursor, error) {
	if e := s.Validate(); e != nil {
		return nil, e
	}
	n, _ := Uint(s.Start)
	return &RXCursor{s, n, maximum}, nil
}
func (r *RXCursor) Receive(e Event) ([]byte, error) {
	if e.Channel != r.Sub.Channel {
		return nil, invalid()
	}
	if e.Kind != "rx" && e.Kind != "gap" {
		return nil, nil
	}
	if String(e.Data, "subscription_id") != r.Sub.ID {
		return nil, invalid()
	}
	if e.Kind == "gap" {
		return nil, errors.New("收到資料缺口，資料連續性中斷")
	}
	if e.Revision != r.Sub.Revision || e.Epoch != r.Sub.Epoch {
		return nil, errors.New("接收資料流已改變")
	}
	b, err := Binary(String(e.Data, "data_b64"), r.Maximum)
	if err != nil || len(b) == 0 {
		return nil, invalid()
	}
	start, se := Uint(String(e.Data, "offset"))
	end, ee := Uint(String(e.Data, "next_offset"))
	chunk, ce := Uint(String(e.Data, "chunk_offset"))
	_, qe := Uint(String(e.Data, "seq"))
	mark, _ := Uint(r.Sub.Watermark)
	delivery := String(e.Data, "delivery")
	if se != nil || ee != nil || ce != nil || qe != nil || chunk > start || start != r.Next || end < start || end-start != uint64(len(b)) || delivery != "live" && delivery != "replay" || delivery == "replay" && end > mark || delivery == "live" && start < mark {
		return nil, errors.New("接收位移、長度或重播界線不合法")
	}
	r.Next = end
	return b, nil
}
