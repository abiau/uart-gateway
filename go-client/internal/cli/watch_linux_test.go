//go:build linux

package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
	"uart-gateway/go-client/client"
)

func TestBurstOutputLetsReadyWriterRunBeforeQueueOverflows(t *testing.T) {
	previous := runtime.GOMAXPROCS(1)
	defer runtime.GOMAXPROCS(previous)
	writer, e := os.CreateTemp(t.TempDir(), "output-")
	if e != nil {
		t.Fatal(e)
	}
	original := os.Stdout
	os.Stdout = writer
	s := newSink()
	closed := false
	defer func() {
		if !closed {
			s.close()
		}
		writer.Close()
		os.Stdout = original
	}()
	payload := bytes.Repeat([]byte("x"), 2048)
	for i := 0; i < 512; i++ {
		if e := s.put(payload, false); e != nil {
			t.Fatalf("可持續消費的 Writer 尚未取得執行機會即溢位：%v", e)
		}
	}
	s.close()
	closed = true
	info, e := writer.Stat()
	if e != nil {
		t.Fatal(e)
	}
	if info.Size() != int64(len(payload)*512) || s.failure != nil {
		t.Fatalf("輸出不完整：%d bytes，%v", info.Size(), s.failure)
	}
}

func TestBlockedOutputHasBoundedShutdown(t *testing.T) {
	reader, writer, e := os.Pipe()
	if e != nil {
		t.Fatal(e)
	}
	original := os.Stdout
	os.Stdout = writer
	defer func() { os.Stdout = original; reader.Close(); writer.Close() }()
	s := newSink()
	if e = s.put(bytes.Repeat([]byte("x"), 131072), false); e != nil {
		t.Fatal(e)
	}
	select {
	case <-s.done:
	case <-time.After(5 * time.Second):
		t.Fatal("慢管線未有界停止")
	}
	s.close()
	if s.failure == nil {
		t.Fatal("輸出失敗未回報")
	}
}
func TestCorruptPreferenceIsNotTreatedAsValid(t *testing.T) {
	path := filepath.Join(t.TempDir(), "preferences.json")
	for _, raw := range []string{`{"version":1,"channels":["a","a"]}`, `{"version":1,"channels":[]}`, `broken`} {
		if e := client.WritePrivate(path, []byte(raw), true); e != nil {
			t.Fatal(e)
		}
		if _, e := readPreference(path); e == nil {
			t.Fatal("損壞偏好未拒絕")
		}
		got, _ := os.ReadFile(path)
		if string(got) != raw {
			t.Fatal("損壞偏好被覆寫")
		}
	}
}
