package client

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"strings"
	"testing"
	"time"
)

func testHello() Object {
	limits := Object{}
	for _, k := range []string{"max_pending_requests", "max_subscriptions", "max_channel_list_items", "max_tx_queue_items", "max_tx_queue_bytes", "rx_ring_bytes", "tx_result_retention_ms", "tx_result_cache_entries"} {
		limits[k] = 16
	}
	limits["max_frame_bytes"] = 65536
	limits["max_json_depth"] = 16
	limits["max_rx_chunk_bytes"] = 4096
	limits["max_tx_bytes"] = 4096
	limits["handshake_timeout_ms"] = 5000
	limits["heartbeat_interval_ms"] = 10000
	limits["idle_timeout_ms"] = 30000
	limits["lease_ttl_ms"] = 15000
	limits["lease_renew_interval_ms"] = 5000
	return Object{"version": Object{"major": 1, "minor": 0}, "gateway_instance_id": "gateway", "limits": limits, "auth_methods": []string{"token"}, "capabilities": []string{"rx.replay", "tx.status", "binding.confirm"}}
}

type observedContext struct {
	context.Context
	checked chan struct{}
}

func (c observedContext) Err() error {
	e := c.Context.Err()
	select {
	case c.checked <- struct{}{}:
	default:
	}
	return e
}
func TestCanceledRequestWaitingForWriterDoesNotSendOrFailConnection(t *testing.T) {
	local, remote := net.Pipe()
	defer remote.Close()
	transport := newTransport(local)
	defer transport.Close()
	transport.write.Lock()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	observed := observedContext{ctx, make(chan struct{}, 1)}
	go func() { _, e := transport.Request(observed, "ping", Object{}); done <- e }()
	<-observed.checked
	cancel()
	transport.write.Unlock()
	if e := <-done; !errors.Is(e, context.Canceled) {
		t.Fatal(e)
	}
	if transport.Err() != nil || transport.counter != 0 {
		t.Fatal("取消等待不應送出請求或破壞連線")
	}
}
func connectFixture(t *testing.T, permissions []string, after func(net.Conn, *bufio.Reader)) (*Client, error) {
	t.Helper()
	listener, e := net.Listen("tcp", "127.0.0.1:0")
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { listener.Close() })
	go func() {
		conn, e := listener.Accept()
		if e != nil {
			return
		}
		defer conn.Close()
		reader := bufio.NewReader(conn)
		encoder := json.NewEncoder(conn)
		for n := 0; n < 2; n++ {
			raw, e := reader.ReadBytes('\n')
			if e != nil {
				return
			}
			var request Object
			json.Unmarshal(raw, &request)
			var result any
			if n == 0 {
				result = testHello()
			} else {
				result = Object{"session_id": "session", "principal_id": "operator", "grants": []any{Object{"channel_id": "a", "permissions": permissions}}}
			}
			encoder.Encode(Object{"type": "response", "id": request["id"], "ok": true, "result": result})
		}
		after(conn, reader)
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	return Connect(ctx, "127.0.0.1", listener.Addr().(*net.TCPAddr).Port, strings.Repeat("a", 43), "read")
}
func TestTransportOutOfOrderRepliesAreMatched(t *testing.T) {
	c, e := connectFixture(t, []string{"read"}, func(conn net.Conn, r *bufio.Reader) {
		requests := []Object{}
		for i := 0; i < 2; i++ {
			raw, e := r.ReadBytes('\n')
			if e != nil {
				return
			}
			var m Object
			json.Unmarshal(raw, &m)
			requests = append(requests, m)
		}
		encoder := json.NewEncoder(conn)
		for i := 1; i >= 0; i-- {
			encoder.Encode(Object{"type": "response", "id": requests[i]["id"], "ok": true, "result": Object{"method": requests[i]["method"]}})
		}
		<-time.After(100 * time.Millisecond)
	})
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	errors := make(chan error, 2)
	for _, method := range []string{"ping", "channel.list"} {
		go func(method string) {
			value, e := c.Request(context.Background(), method, Object{})
			if e == nil && String(value, "method") != method {
				e = fmt.Errorf("回覆配對錯誤")
			}
			errors <- e
		}(method)
	}
	for i := 0; i < 2; i++ {
		if e := <-errors; e != nil {
			t.Fatal(e)
		}
	}
}
func TestWrongReadCredentialDoesNotUpgrade(t *testing.T) {
	c, e := connectFixture(t, []string{"read", "configure"}, func(conn net.Conn, r *bufio.Reader) { r.ReadByte() })
	if e == nil {
		c.Close()
		t.Fatal("讀取模式接受了管理權限")
	}
}
func TestMalformedResponseAndEventOverflowCloseConnection(t *testing.T) {
	for _, overflow := range []bool{false, true} {
		t.Run(fmt.Sprint(overflow), func(t *testing.T) {
			c, e := connectFixture(t, []string{"read"}, func(conn net.Conn, r *bufio.Reader) {
				time.Sleep(10 * time.Millisecond)
				if !overflow {
					fmt.Fprintln(conn, `{"type":"response","id":"999","ok":true,"result":{}}`)
				} else {
					enc := json.NewEncoder(conn)
					for i := 0; i < 300; i++ {
						if e := enc.Encode(Object{"type": "event", "event": "channel_state", "channel_id": "a", "binding_revision": "1", "stream_epoch": "epoch", "timestamp_utc": "2026-11-13T00:00:00.000Z", "data": Object{}}); e != nil {
							return
						}
					}
				}
				r.ReadByte()
			})
			if e != nil {
				t.Fatal(e)
			}
			defer c.Close()
			deadline := time.Now().Add(2 * time.Second)
			for c.T.Err() == nil && time.Now().Before(deadline) {
				time.Sleep(time.Millisecond)
			}
			if c.T.Err() == nil {
				t.Fatal("未關閉非法／滿載連線")
			}
		})
	}
}
func TestRequestTimeoutIsNotRetried(t *testing.T) {
	count := make(chan int, 1)
	c, e := connectFixture(t, []string{"read"}, func(conn net.Conn, r *bufio.Reader) {
		raw, e := r.ReadBytes('\n')
		if e != nil {
			return
		}
		var m Object
		json.Unmarshal(raw, &m)
		time.Sleep(100 * time.Millisecond)
		json.NewEncoder(conn).Encode(Object{"type": "response", "id": m["id"], "ok": true, "result": Object{}})
		conn.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
		_, e = r.ReadBytes('\n')
		n := 1
		if e == nil {
			n++
		}
		count <- n
	})
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	_, e = c.Request(ctx, "tx.write", Object{})
	if !errors.Is(e, context.DeadlineExceeded) {
		t.Fatal(e)
	}
	if n := <-count; n != 1 {
		t.Fatal("逾時後自動重送")
	}
}
