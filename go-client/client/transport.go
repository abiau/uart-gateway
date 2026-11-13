package client

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"runtime"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

type response struct {
	value Object
	err   error
}
type queuedEvent struct {
	event Event
	size  int
}
type Transport struct {
	conn         net.Conn
	mu           sync.Mutex
	write        sync.Mutex
	pending      map[string]chan response
	counter      uint64
	failure      error
	events       chan queuedEvent
	space        chan struct{}
	done         chan struct{}
	used         int
	maximum      atomic.Int64
	depth        atomic.Int64
	pendingLimit atomic.Int64
	workers      sync.WaitGroup
}

func newTransport(conn net.Conn) *Transport {
	t := &Transport{conn: conn, pending: map[string]chan response{}, events: make(chan queuedEvent, 256), space: make(chan struct{}, 1), done: make(chan struct{})}
	t.maximum.Store(65536)
	t.depth.Store(16)
	t.pendingLimit.Store(32)
	t.workers.Add(1)
	go t.receive()
	return t
}
func (t *Transport) fail(e error) {
	t.mu.Lock()
	if t.failure == nil {
		t.failure = e
		close(t.done)
		t.conn.Close()
	}
	t.mu.Unlock()
}
func (t *Transport) Err() error { t.mu.Lock(); defer t.mu.Unlock(); return t.failure }
func (t *Transport) Close()     { t.fail(errors.New("Client 已關閉連線")); t.workers.Wait() }
func (t *Transport) Request(ctx context.Context, method string, params any) (Object, error) {
	if e := ctx.Err(); e != nil {
		return nil, e
	}
	t.write.Lock()
	if e := ctx.Err(); e != nil {
		t.write.Unlock()
		return nil, e
	}
	t.mu.Lock()
	if t.failure != nil {
		e := t.failure
		t.mu.Unlock()
		t.write.Unlock()
		return nil, e
	}
	if len(t.pending) >= int(t.pendingLimit.Load()) || t.counter == ^uint64(0) {
		t.mu.Unlock()
		t.write.Unlock()
		return nil, errors.New("並行請求或請求序號已達上限")
	}
	t.counter++
	id := strconv.FormatUint(t.counter, 10)
	reply := make(chan response, 1)
	t.pending[id] = reply
	t.mu.Unlock()
	defer func() { t.mu.Lock(); delete(t.pending, id); t.mu.Unlock() }()
	raw, e := json.Marshal(Object{"type": "request", "id": id, "method": method, "params": params})
	if e == nil {
		raw = append(raw, '\n')
		if len(raw) > int(t.maximum.Load()) {
			e = errors.New("請求超過 Gateway 訊框上限")
		}
	}
	if e != nil {
		t.write.Unlock()
		return nil, e
	}
	deadline := time.Now().Add(5 * time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	t.conn.SetWriteDeadline(deadline)
	for len(raw) > 0 && e == nil {
		var n int
		n, e = t.conn.Write(raw)
		if n == 0 && e == nil {
			e = io.ErrShortWrite
		}
		raw = raw[n:]
	}
	t.write.Unlock()
	if e != nil {
		t.fail(errors.New("Gateway 寫入連線失敗，操作結果可能不明"))
		return nil, e
	}
	select {
	case r := <-reply:
		return r.value, r.err
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-t.done:
		return nil, t.Err()
	}
}
func (t *Transport) receive() {
	defer t.workers.Done()
	reader := bufio.NewReaderSize(t.conn, 65536)
	frames, received := 0, 0
	for {
		raw, e := reader.ReadSlice('\n')
		if e != nil {
			t.fail(errors.New("Gateway 連線中斷或訊框超過上限"))
			return
		}
		if len(raw) > int(t.maximum.Load()) || len(raw) == 1 || len(raw) > 1 && raw[len(raw)-2] == '\r' {
			t.fail(invalid())
			return
		}
		m, e := StrictJSON(raw, int(t.depth.Load()))
		if e != nil {
			t.fail(e)
			return
		}
		switch String(m, "type") {
		case "response":
			id := String(m, "id")
			n, e := Uint(id)
			ok, valid := m["ok"].(bool)
			t.mu.Lock()
			counter := t.counter
			reply := t.pending[id]
			t.mu.Unlock()
			if e != nil || n == 0 || n > counter || !valid {
				t.fail(invalid())
				return
			}
			r := response{}
			if ok {
				r.value = Map(m, "result")
				if r.value == nil || m["error"] != nil {
					t.fail(invalid())
					return
				}
			} else {
				v := Map(m, "error")
				if v == nil || String(v, "code") == "" || Map(v, "details") == nil || m["result"] != nil {
					t.fail(invalid())
					return
				}
				if _, ok := v["message"].(string); !ok {
					t.fail(invalid())
					return
				}
				r.err = &RemoteError{Code: String(v, "code"), Message: String(v, "message")}
			}
			if reply != nil {
				select {
				case reply <- r:
				default:
				}
			}
		case "event":
			kind := String(m, "event")
			switch kind {
			case "rx", "gap", "subscription_state", "channel_state", "lease_revoked", "tx_result", "recording_state":
			default:
				t.fail(invalid())
				return
			}
			data := Map(m, "data")
			cid := String(m, "channel_id")
			rev := String(m, "binding_revision")
			_, re := Uint(rev)
			if data == nil || !ValidID(cid) || re != nil || String(m, "timestamp_utc") == "" {
				t.fail(invalid())
				return
			}
			if epoch := m["stream_epoch"]; epoch != nil {
				v, ok := epoch.(string)
				if !ok || !ValidID(v) {
					t.fail(invalid())
					return
				}
			}
			item := queuedEvent{Event{kind, cid, rev, m["stream_epoch"], data, m}, len(raw)}
			if !t.enqueue(item) {
				return
			}
		default:
			t.fail(invalid())
			return
		}
		frames++
		received += len(raw)
		if frames >= 8 || received >= 16384 || len(t.events) >= 8 {
			frames, received = 0, 0
			// 已緩衝的重播也交回排程；不放大事件佇列或略過溢位檢查。
			runtime.Gosched()
		}
	}
}
func (t *Transport) enqueue(item queuedEvent) bool {
	replay := item.event.Kind == "rx" && String(item.event.Data, "delivery") == "replay"
	var deadline *time.Timer
	defer func() {
		if deadline != nil {
			deadline.Stop()
		}
	}()
	for {
		t.mu.Lock()
		if t.failure != nil {
			t.mu.Unlock()
			return false
		}
		if t.used+item.size <= 262144 && len(t.events) < cap(t.events) {
			t.used += item.size
			t.events <- item
			t.mu.Unlock()
			return true
		}
		t.mu.Unlock()
		if !replay {
			t.fail(errors.New("SDK 事件佇列已滿，資料連續性中斷"))
			return false
		}
		// 歷史突發先由 TCP 背壓等待消費者；即時資料及容量規則不放寬。
		if deadline == nil {
			deadline = time.NewTimer(time.Second)
		}
		select {
		case <-t.space:
		case <-t.done:
			return false
		case <-deadline.C:
			t.fail(errors.New("SDK 重播佇列持續無法排空，資料連續性中斷"))
			return false
		}
	}
}
func (t *Transport) Next(ctx context.Context) (Event, error) {
	if e := t.Err(); e != nil {
		return Event{}, e
	}
	select {
	case q := <-t.events:
		t.mu.Lock()
		t.used -= q.size
		t.mu.Unlock()
		select {
		case t.space <- struct{}{}:
		default:
		}
		if e := t.Err(); e != nil {
			return Event{}, e
		}
		return q.event, nil
	case <-t.done:
		return Event{}, t.Err()
	case <-ctx.Done():
		return Event{}, ctx.Err()
	}
}
func (t *Transport) heartbeat(interval time.Duration) {
	t.workers.Add(1)
	go func() {
		defer t.workers.Done()
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-t.done:
				return
			case <-ticker.C:
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				_, e := t.Request(ctx, "ping", Object{})
				cancel()
				if e != nil {
					t.fail(fmt.Errorf("Gateway 心跳失敗：%w", e))
					return
				}
			}
		}
	}()
}
