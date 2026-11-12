package client

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func TestStrictJSON(t *testing.T) {
	bad := [][]byte{[]byte(`[]`), []byte(`{"a":1,"a":2}`), []byte(`{"a":1,"\u0061":2}`), []byte(`{"x":{"a":1,"a":2}}`), []byte(`{"a":NaN}`), []byte(`{"a":1e999}`), []byte(`{"a":"\ud800"}`), []byte(`{"a":"\udc00"}`), []byte(`{"a":"\ud800\u0041"}`), {'{', '"', 'a', '"', ':', '"', 255, '"', '}'}, []byte(`{} {}`), []byte(`{"a":[[[]]]}`)}
	for _, raw := range bad {
		if _, e := StrictJSON(raw, 3); e == nil {
			t.Fatalf("未拒絕非法訊框 %q", raw)
		}
	}
	v, e := StrictJSON([]byte(`{"emoji":"\ud83d\ude00","a":[1,true,null],"text":"\\ud800"}`), 3)
	if e != nil || String(v, "emoji") != "😀" {
		t.Fatalf("合法 Unicode：%v", e)
	}
}
func TestU64AndBinary(t *testing.T) {
	if n, e := Uint("18446744073709551615"); e != nil || n != ^uint64(0) {
		t.Fatal(n, e)
	}
	for _, s := range []string{"01", "-1", "18446744073709551616", "1.0", ""} {
		if _, e := Uint(s); e == nil {
			t.Fatal(s)
		}
	}
	for _, s := range []string{"YQ==\n", "YR==", "YQ", "===="} {
		if _, e := Binary(s, 4); e == nil {
			t.Fatal(s)
		}
	}
	if b, e := Binary("AAH/", 3); e != nil || !bytes.Equal(b, []byte{0, 1, 255}) {
		t.Fatal(b, e)
	}
}
func TestRendererAndMarkersBounded(t *testing.T) {
	r := Renderer{Terminal: true}
	first := []byte("開機")
	if got := r.Feed(first[:1]); got != "" {
		t.Fatal(got)
	}
	got := r.Feed(append(first[1:], []byte("\x1b[31mred\x1b[0m\r\n\x1b]52;c;SECRETS\aend\x1b[2J")...))
	if got != "開機\x1b[31mred\x1b[0m\r\nend" {
		t.Fatalf("終端過濾：%q", got)
	}
	r.Feed(append([]byte("\x1b]"), bytes.Repeat([]byte("x"), 100000)...))
	if len(r.pending) > 4 || len(r.escape) > 256 {
		t.Fatal("控制字串未受限")
	}
	r.Feed([]byte{7})
	if r.Feed([]byte("OK")) != "OK" {
		t.Fatal("OSC 復原失敗")
	}
	id := strings.Repeat("a", 32)
	p, _ := NewMarkers(id, 4)
	wire := []byte("echo __UART_BEGIN_\n\n__UART_BEGIN_" + id + "__\r\nhello\r\n\n__UART_END_" + id + "__:7\n")
	for _, b := range wire {
		p.Feed([]byte{b})
	}
	if !p.Done || p.ExitCode == nil || *p.ExitCode != 7 || string(p.Output()) != "hell" || !p.Truncated() || p.Observed() != 7 {
		t.Fatalf("標記結果：%+v／%q", p, p.Output())
	}
}
func TestShellPayloadCannotEchoCompletion(t *testing.T) {
	id := strings.Repeat("b", 32)
	payload, e := ShellPayload("printf '%s' \"hello\"", id, "cr", 4096)
	if e != nil {
		t.Fatal(e)
	}
	if !bytes.HasSuffix(payload, []byte("\r")) || bytes.Contains(payload, []byte("__UART_BEGIN_"+id+"__")) || !bytes.Contains(payload, []byte("</dev/null")) {
		t.Fatalf("包裝不符：%s", payload)
	}
	for _, command := range []string{"", "echo x\nreboot", "echo\x1b"} {
		if _, e := ShellPayload(command, id, "lf", 4096); e == nil {
			t.Fatal("未拒絕控制命令")
		}
	}
}
func TestRXCursorChecksEpochOffsetReplayAndGap(t *testing.T) {
	s := Subscription{"sub", "a", "1", "epoch", "0", "3", "0"}
	cursor, _ := NewCursor(s, 4096)
	event := Event{Kind: "rx", Channel: "a", Revision: "1", Epoch: "epoch", Data: Object{"subscription_id": "sub", "seq": "0", "chunk_offset": "0", "offset": "0", "next_offset": "3", "delivery": "replay", "data_b64": "YWJj"}}
	if b, e := cursor.Receive(event); e != nil || string(b) != "abc" {
		t.Fatal(b, e)
	}
	if _, e := cursor.Receive(event); e == nil {
		t.Fatal("未拒絕重複位移")
	}
	event.Data["offset"] = "3"
	event.Data["next_offset"] = "6"
	if _, e := cursor.Receive(event); e == nil {
		t.Fatal("未拒絕跨重播界線")
	}
	event.Data["delivery"] = "live"
	event.Epoch = "other"
	if _, e := cursor.Receive(event); e == nil {
		t.Fatal("未拒絕錯世代")
	}
	event.Epoch = "epoch"
	if _, e := cursor.Receive(event); e != nil {
		t.Fatal(e)
	}
	event.Kind = "gap"
	if _, e := cursor.Receive(event); e == nil {
		t.Fatal("未拒絕缺口")
	}
}
func TestTxResultRejectsWrongIdentityAndMalformedSuccess(t *testing.T) {
	v := Object{"tx_id": "id", "channel_id": "a", "gateway_instance_id": "g", "state": "written_to_driver", "submitted_bytes": json.Number("3"), "accepted_bytes": json.Number("3"), "io_started": true, "reason_code": nil, "completed_at": "2026-11-13T00:00:00.000Z", "retained_for_ms": json.Number("10"), "status_origin": "record"}
	if e := ValidateTx(v, "id", "a", "g"); e != nil {
		t.Fatal(e)
	}
	if e := ValidateTx(v, "other", "a", "g"); e == nil {
		t.Fatal("未拒絕不同操作")
	}
	v["accepted_bytes"] = json.Number("2")
	if e := ValidateTx(v, "id", "a", "g"); e == nil {
		t.Fatal("未拒絕部分成功")
	}
}
