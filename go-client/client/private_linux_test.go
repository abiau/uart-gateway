//go:build linux

package client

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestShellPayloadRunsInRealPOSIXShell(t *testing.T) {
	// 僅執行固定測試字串，驗證拆分標記與單引號，沒有實體 UART。
	id := strings.Repeat("c", 32)
	payload, e := ShellPayload("printf '%s' \"quote ' 中文\"; exit 7", id, "lf", 4096)
	if e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	out, e := exec.CommandContext(ctx, "/bin/sh", "-c", string(payload)).CombinedOutput()
	if e != nil {
		t.Fatal(e)
	}
	parser, _ := NewMarkers(id, 1024)
	parser.Feed(out)
	if !parser.Done || parser.ExitCode == nil || *parser.ExitCode != 7 || string(parser.Output()) != "quote ' 中文" {
		t.Fatalf("實際 POSIX Shell 結果不符：%q", out)
	}
}

func TestPrivateFilesRejectLinksModesFIFOAndOverflow(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "secret")
	if e := WritePrivate(path, []byte("secret"), false); e != nil {
		t.Fatal(e)
	}
	if e := WritePrivate(path, []byte("other"), false); e == nil {
		t.Fatal("未拒絕覆寫")
	}
	if _, e := ReadPrivate(path, 3); e == nil {
		t.Fatal("未限制長度")
	}
	link := filepath.Join(root, "link")
	if e := os.Symlink(path, link); e != nil {
		t.Fatal(e)
	}
	if _, e := ReadPrivate(link, 100); e == nil {
		t.Fatal("未拒絕符號連結")
	}
	if e := WritePrivate(link, []byte("overwrite"), true); e == nil {
		t.Fatal("未保護連結")
	}
	os.Chmod(path, 0644)
	if _, e := ReadPrivate(path, 100); e == nil {
		t.Fatal("未拒絕公開讀取")
	}
	fifo := filepath.Join(root, "fifo")
	if e := syscall.Mkfifo(fifo, 0600); e != nil {
		t.Fatal(e)
	}
	if _, e := ReadPrivate(fifo, 100); e == nil {
		t.Fatal("未拒絕 FIFO")
	}
	os.Chmod(path, 0600)
	if e := WritePrivate(path, []byte("updated"), true); e != nil {
		t.Fatal(e)
	}
	if got, e := ReadPrivate(path, 100); e != nil || string(got) != "updated" {
		t.Fatal(string(got), e)
	}
}

func TestFixedCredentialsIdempotenceRoleConflictAndNoFallback(t *testing.T) {
	root := filepath.Join(t.TempDir(), "config")
	bundle := Object{"version": 1, "deployment_id": strings.Repeat("a", 32), "port": 18081, "access": "read", "token": strings.Repeat("x", 43)}
	raw, _ := json.Marshal(bundle)
	for range 2 {
		if e := ImportCredential(root, "company", 0, raw); e != nil {
			t.Fatal(e)
		}
	}
	opt := ConnectionOptions{Root: root}
	s, e := opt.Resolve()
	if e != nil || s.Access != "read" || s.Name != "company" {
		t.Fatal(s, e)
	}
	opt.Access = "tx"
	if _, e := opt.Resolve(); e == nil {
		t.Fatal("未拒絕缺少角色")
	}
	secret := filepath.Join(root, "connections/company/read.secret")
	before, _ := os.ReadFile(secret)
	bundle["token"] = strings.Repeat("y", 43)
	raw, _ = json.Marshal(bundle)
	if e := ImportCredential(root, "company", 0, raw); e == nil {
		t.Fatal("覆蓋衝突秘密")
	}
	after, _ := os.ReadFile(secret)
	if !bytes.Equal(before, after) {
		t.Fatal("秘密變動")
	}
	t.Setenv("UART_GATEWAY_TOKEN", strings.Repeat("z", 43))
	os.WriteFile(filepath.Join(root, "connections/company/client.json"), []byte("broken"), 0600)
	if _, e := (ConnectionOptions{Root: root}).Resolve(); e == nil {
		t.Fatal("損壞設定不應降級環境憑證")
	}
}

func TestOperationLockExcludesConcurrentWriter(t *testing.T) {
	root := t.TempDir()
	unlock, e := OperationLock(root, "a")
	if e != nil {
		t.Fatal(e)
	}
	if second, e := OperationLock(root, "a"); e == nil {
		second()
		t.Fatal("允許重疊操作")
	}
	unlock()
	second, e := OperationLock(root, "a")
	if e != nil {
		t.Fatal(e)
	}
	second()
}

func TestTerminalReceiptAllowsRetentionDecreaseButNotChangedResult(t *testing.T) {
	j := Journal{t.TempDir()}
	r := &Receipt{Version: 1, ID: "id", Channel: "a", Instance: "g", Principal: "p"}
	v := Object{"tx_id": "id", "channel_id": "a", "gateway_instance_id": "g", "state": "written_to_driver", "submitted_bytes": json.Number("3"), "accepted_bytes": json.Number("3"), "io_started": true, "reason_code": nil, "completed_at": "2026-11-13T00:00:00.000Z", "retained_for_ms": json.Number("10"), "status_origin": "record"}
	if e := j.Save(r, true); e != nil {
		t.Fatal(e)
	}
	if e := j.Update(r, v, false); e != nil {
		t.Fatal(e)
	}
	next := Object{}
	for k, value := range v {
		next[k] = value
	}
	next["retained_for_ms"] = json.Number("1")
	if e := j.Update(r, next, true); e != nil {
		t.Fatal(e)
	}
	next["accepted_bytes"] = json.Number("2")
	if e := j.Update(r, next, true); e == nil {
		t.Fatal("未拒絕矛盾終態")
	}
}
