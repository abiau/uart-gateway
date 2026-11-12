package client

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

func QuoteShell(s string) string { return "'" + strings.ReplaceAll(s, "'", "'\"'\"'") + "'" }
func ShellPayload(command, id, newline string, maximum int) ([]byte, error) {
	if !deploymentPattern.MatchString(id) || !utf8.ValidString(command) || strings.TrimSpace(command) == "" || len(command) > 4096 {
		return nil, errors.New("Shell 命令或操作 ID 不合法")
	}
	for _, r := range command {
		if !unicode.IsPrint(r) {
			return nil, errors.New("Shell 命令必須為可顯示的單行文字")
		}
	}
	begin := "__UART_BEGIN_" + id + "__"
	end := "__UART_END_" + id + "__:"
	source := fmt.Sprintf("( printf '\\n%%s%%s\\n' '__UART_BEGIN_' '%s__'; if sh -c %s </dev/null 2>&1; then _uart_rc=0; else _uart_rc=$?; fi; printf '\\n%%s%%s:%%s\\n' '__UART_END_' '%s__' \"$_uart_rc\" )", id, QuoteShell(command), id)
	if strings.Contains(source, begin) || strings.Contains(source, end) {
		return nil, errors.New("命令含本次保留標記")
	}
	return EncodePayload(source, newline, "text", maximum)
}

type ShellMarkers struct {
	begin    []byte
	end      []byte
	maximum  int
	Started  bool
	Done     bool
	ExitCode *int
	pending  []byte
	buffer   []byte
	total    uint64
	tail     []byte
	line     bool
}

func NewMarkers(id string, maximum int) (*ShellMarkers, error) {
	if !deploymentPattern.MatchString(id) || maximum < 1 || maximum > 1048576 {
		return nil, invalid()
	}
	return &ShellMarkers{begin: []byte("__UART_BEGIN_" + id + "__"), end: []byte("__UART_END_" + id + "__:"), maximum: maximum}, nil
}
func (p *ShellMarkers) append(data []byte) {
	p.total += uint64(len(data))
	p.tail = append(p.tail, data...)
	if len(p.tail) > 2 {
		p.tail = p.tail[len(p.tail)-2:]
	}
	room := p.maximum - len(p.buffer)
	if room > len(data) {
		room = len(data)
	}
	p.buffer = append(p.buffer, data[:room]...)
}

var exitPattern = regexp.MustCompile(`^([0-9]{1,3})\r?\n$`)
var partialExit = regexp.MustCompile(`^[0-9]{0,3}\r?$`)

func (p *ShellMarkers) Feed(data []byte) {
	for _, b := range data {
		if p.Done {
			return
		}
		if !p.Started {
			if !p.line {
				if b == '\n' {
					p.line = true
				}
				continue
			}
			p.pending = append(p.pending, b)
			a := append(append([]byte{}, p.begin...), '\n')
			z := append(append([]byte{}, p.begin...), '\r', '\n')
			if bytes.Equal(p.pending, a) || bytes.Equal(p.pending, z) {
				p.Started = true
				p.line = true
				p.pending = nil
			} else if !bytes.HasPrefix(a, p.pending) && !bytes.HasPrefix(z, p.pending) {
				p.pending = nil
				p.line = b == '\n'
			}
			continue
		}
		if !p.line {
			p.append([]byte{b})
			p.line = b == '\n'
			continue
		}
		p.pending = append(p.pending, b)
		if len(p.pending) <= len(p.end) && bytes.HasPrefix(p.end, p.pending) {
			continue
		}
		if bytes.HasPrefix(p.pending, p.end) {
			suffix := p.pending[len(p.end):]
			if match := exitPattern.FindSubmatch(suffix); match != nil {
				n, _ := strconv.Atoi(string(match[1]))
				if n <= 255 {
					p.Done = true
					p.ExitCode = &n
					p.pending = nil
					delimiter := 0
					if bytes.Equal(p.tail, []byte("\r\n")) {
						delimiter = 2
					} else if bytes.HasSuffix(p.tail, []byte("\n")) {
						delimiter = 1
					}
					p.total -= uint64(delimiter)
					if uint64(len(p.buffer)) > p.total {
						p.buffer = p.buffer[:p.total]
					}
					continue
				}
			}
			if partialExit.Match(suffix) {
				continue
			}
		}
		p.append(p.pending)
		p.pending = nil
		p.line = b == '\n'
	}
}
func (p *ShellMarkers) Observed() uint64 {
	n := p.total
	if p.Started {
		n += uint64(len(p.pending))
	}
	return n
}
func (p *ShellMarkers) Output() []byte {
	b := append([]byte{}, p.buffer...)
	if p.Started {
		room := p.maximum - len(b)
		if room > len(p.pending) {
			room = len(p.pending)
		}
		b = append(b, p.pending[:room]...)
	}
	return b
}
func (p *ShellMarkers) Truncated() bool { return p.Observed() > uint64(p.maximum) }

type ShellAuthorization struct {
	Version    int     `json:"version"`
	Generation string  `json:"generation"`
	Instance   string  `json:"instance"`
	Principal  string  `json:"principal"`
	Channel    Channel `json:"channel"`
	Newline    string  `json:"newline"`
}
type ShellRunner struct {
	Settings Settings
	Journal  Journal
	Targets  string
}

func (r ShellRunner) authorizationPath(id string) (string, error) {
	if !ValidID(id) {
		return "", invalid()
	}
	return filepath.Join(r.Targets, id+".json"), nil
}
func (r ShellRunner) Authorize(ch Channel, instance, principal, newline string) error {
	if ch.Binding.Status != "confirmed" || ch.Epoch == nil {
		return errors.New("目標尚未確認")
	}
	if newline != "lf" && newline != "cr" && newline != "crlf" {
		return errors.New("請明確指定 Shell 換行")
	}
	if e := ch.Validate(); e != nil {
		return e
	}
	p, e := r.authorizationPath(ch.ID)
	if e != nil {
		return e
	}
	a := ShellAuthorization{1, OperationID(), instance, principal, ch, newline}
	b, _ := json.Marshal(a)
	if len(b) > 8192 {
		return invalid()
	}
	return WritePrivate(p, b, true)
}
func (r ShellRunner) Authorization(id string) (ShellAuthorization, error) {
	p, e := r.authorizationPath(id)
	if e != nil {
		return ShellAuthorization{}, e
	}
	b, e := ReadPrivate(p, 8192)
	if e != nil {
		return ShellAuthorization{}, errors.New("尚無有效 Shell 授權，請由人執行 uart shell-ready 核對")
	}
	v, e := StrictJSON(b, 16)
	if e != nil {
		return ShellAuthorization{}, e
	}
	raw, _ := json.Marshal(v)
	var a ShellAuthorization
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if e = d.Decode(&a); e != nil || a.Version != 1 || !deploymentPattern.MatchString(a.Generation) || !ValidID(a.Instance) || !ValidID(a.Principal) || a.Channel.ID != id || a.Channel.Binding.Status != "confirmed" || a.Newline != "lf" && a.Newline != "cr" && a.Newline != "crlf" {
		return a, invalid()
	}
	return a, a.Channel.Validate()
}
func (r ShellRunner) current(ctx context.Context, c *Client, a ShellAuthorization) (Channel, error) {
	if c.Principal != a.Principal || !c.Can(a.Channel.ID, "tx") || !c.Can(a.Channel.ID, "configure") {
		return Channel{}, errors.New("Shell 授權身分／權限已變更")
	}
	ch, e := c.Check(ctx, a.Channel, a.Instance)
	if e == nil && ch.Binding.Status != "confirmed" {
		e = errors.New("Shell 目標不再 Confirm，請重新核對")
	}
	return ch, e
}
func (r ShellRunner) unresolved(channel, generation string) error {
	if !exists(r.Journal.Directory) {
		return nil
	}
	dir, e := os.Open(r.Journal.Directory)
	if e != nil {
		return e
	}
	defer dir.Close()
	entries, e := dir.ReadDir(257)
	if e != nil && e != io.EOF {
		return e
	}
	if len(entries) > 256 {
		return errors.New("收據超過上限")
	}
	for _, f := range entries {
		if !strings.HasSuffix(f.Name(), ".json") {
			continue
		}
		receipt, e := r.Journal.Load(strings.TrimSuffix(f.Name(), ".json"))
		if e != nil {
			return e
		}
		m := receipt.Execution
		if receipt.Channel == channel && m != nil && m.Generation == generation && (m.State == "pending" || m.State == "unknown") {
			return errors.New("原 Shell 命令尚未結案，請用 exec-status 查詢；必要時由人確認 Shell 恢復後重新 shell-ready")
		}
	}
	return nil
}

type ExecutionResult struct {
	Receipt    *Receipt
	Parser     *ShellMarkers
	Continuous bool
	Saved      bool
	Reason     string
}

func (r ExecutionResult) Object() Object {
	m := r.Receipt.Execution
	var text, b64 any
	var returned int
	if r.Parser != nil && r.Parser.Started {
		output := r.Parser.Output()
		text = DecodedText(output)
		b64 = base64.StdEncoding.EncodeToString(output)
		returned = len(output)
	}
	return Object{"execution_id": r.Receipt.ID, "channel_id": r.Receipt.Channel, "gateway_instance_id": r.Receipt.Instance, "state": m.State, "exit_code": m.ExitCode, "output_text": text, "output_b64": b64, "output_complete": r.Parser != nil && r.Parser.Started && r.Parser.Done && r.Continuous && !r.Parser.Truncated(), "output_truncated": m.Truncated, "observed_output_bytes": m.Observed, "returned_bytes": returned, "output_scope": "uart_between_markers", "background_output_possible": true, "may_still_be_running": m.State == "pending" || m.State == "unknown", "reason_code": r.Reason, "receipt_saved": r.Saved, "tx_result": r.Receipt.Result, "tx_query": r.Receipt.Query}
}
func (r ExecutionResult) Exit() int {
	m := r.Receipt.Execution
	if m.State == "completed" && m.ExitCode != nil {
		if *m.ExitCode == 0 {
			return 0
		}
		return 1
	}
	return 2
}
func collect(ctx context.Context, c *Client, sub Subscription, p *ShellMarkers, replayOnly bool, lease *Lease) error {
	cursor, e := NewCursor(sub, c.Limits["max_rx_chunk_bytes"])
	if e != nil {
		return e
	}
	mark, _ := Uint(sub.Watermark)
	for {
		if replayOnly && cursor.Next >= mark {
			return nil
		}
		event, e := c.T.Next(ctx)
		if e != nil {
			return e
		}
		b, e := cursor.Receive(event)
		if e != nil {
			return e
		}
		if b != nil {
			p.Feed(b)
			if p.Done {
				return nil
			}
		} else {
			switch event.Kind {
			case "subscription_state":
				if String(event.Data, "subscription_id") == sub.ID {
					return errors.New("命令等待期間資料流已停止")
				}
			case "channel_state":
				ch, e := decodeChannel(event.Data["channel"])
				if e != nil || ch.State != "ready" || ch.Binding.Revision != sub.Revision || ch.Epoch != sub.Epoch {
					return errors.New("命令期間資料流已改變")
				}
			case "lease_revoked":
				if lease != nil && String(event.Data, "fencing_token") == lease.Token {
					return errors.New("命令期間租約已撤銷")
				}
			}
		}
	}
}
func (r ShellRunner) finish(receipt *Receipt, p *ShellMarkers, continuous bool, reason string) ExecutionResult {
	m := receipt.Execution
	if m.State != "completed" && m.State != "not_sent" {
		if receipt.Result != nil && String(receipt.Result, "state") == "rejected" {
			m.State = "not_sent"
		} else if p.Done && continuous {
			m.State = "completed"
			m.ExitCode = p.ExitCode
			reason = ""
		} else {
			m.State = "unknown"
		}
		m.Observed = p.Observed()
		m.Truncated = p.Truncated()
		m.Reason = reason
	}
	e := r.Journal.Save(receipt, false)
	if e != nil {
		reason = "RECEIPT_SAVE_FAILED"
	}
	return ExecutionResult{receipt, p, continuous, e == nil, reason}
}
func (r ShellRunner) Execute(ctx context.Context, id, command string, timeout time.Duration, maximum int) (ExecutionResult, error) {
	if timeout < 100*time.Millisecond || timeout > 300*time.Second {
		return ExecutionResult{}, errors.New("命令等待須為 0.1～300 秒")
	}
	a, e := r.Authorization(id)
	if e != nil {
		return ExecutionResult{}, e
	}
	unlock, e := OperationLock(filepath.Join(r.Settings.Root, "go-locks", r.Settings.Name), id)
	if e != nil {
		return ExecutionResult{}, e
	}
	defer unlock()
	if e = r.unresolved(id, a.Generation); e != nil {
		return ExecutionResult{}, e
	}
	op := OperationID()
	p, e := NewMarkers(op, maximum)
	if e != nil {
		return ExecutionResult{}, e
	}
	c, e := r.Settings.Connect(ctx)
	if e != nil {
		return ExecutionResult{}, e
	}
	defer c.Close()
	ch, e := r.current(ctx, c, a)
	if e != nil {
		return ExecutionResult{}, e
	}
	payload, e := ShellPayload(command, op, a.Newline, c.Limits["max_tx_bytes"])
	if e != nil {
		return ExecutionResult{}, e
	}
	lease, e := c.Acquire(ctx, ch)
	if e != nil {
		return ExecutionResult{}, e
	}
	defer lease.Close()
	sub, e := c.Subscribe(lease.Context, ch, c.Instance, false, "")
	if e != nil {
		return ExecutionResult{}, e
	}
	defer func() {
		q, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_, _ = c.Request(q, "rx.unsubscribe", Object{"subscription_id": sub.ID})
	}()
	receipt := &Receipt{Version: 1, ID: op, Channel: id, Instance: c.Instance, Principal: c.Principal, Target: ch, Execution: &ExecutionMeta{Generation: a.Generation, Cursor: sub.Start, Subscription: sub, State: "pending", Maximum: maximum}}
	if e = r.Journal.Save(receipt, true); e != nil {
		return ExecutionResult{}, e
	}
	wait, cancel := context.WithTimeout(lease.Context, timeout)
	defer cancel()
	tx, e := lease.Write(wait, op, payload)
	if e == nil {
		receipt.Result = tx
	} else {
		var remote *RemoteError
		if errors.As(e, &remote) && remote.Code != "INTERNAL_ERROR" {
			receipt.Execution.State = "not_sent"
			return r.finish(receipt, p, true, remote.Code), nil
		}
		return r.finish(receipt, p, false, "TX_RESULT_UNKNOWN"), nil
	}
	e = collect(wait, c, sub, p, false, lease)
	reason := ""
	if e != nil {
		reason = "COMPLETION_NOT_OBSERVED"
	}
	return r.finish(receipt, p, e == nil, reason), nil
}
func (r ShellRunner) Query(ctx context.Context, id string, wait time.Duration) (ExecutionResult, error) {
	if wait < 0 || wait > 300*time.Second {
		return ExecutionResult{}, invalid()
	}
	receipt, e := r.Journal.Load(id)
	if e != nil || receipt.Execution == nil {
		return ExecutionResult{}, errors.New("找不到有效的 Go Shell 收據")
	}
	unlock, e := OperationLock(filepath.Join(r.Settings.Root, "go-locks", r.Settings.Name), receipt.Channel)
	if e != nil {
		return ExecutionResult{}, e
	}
	defer unlock()
	a, e := r.Authorization(receipt.Channel)
	if e != nil || a.Generation != receipt.Execution.Generation {
		return ExecutionResult{}, errors.New("Shell 核對已更新，原命令保持不明；不重送")
	}
	c, e := r.Settings.Connect(ctx)
	if e != nil {
		return ExecutionResult{}, e
	}
	defer c.Close()
	p, _ := NewMarkers(receipt.ID, receipt.Execution.Maximum)
	if c.Principal != receipt.Principal {
		return ExecutionResult{}, errors.New("查詢身分與原收據不符")
	}
	if c.Instance != receipt.Instance {
		return r.finish(receipt, p, false, "SERVER_RESTARTED"), nil
	}
	tx, e := c.Status(ctx, receipt.ID, receipt.Channel, receipt.Instance)
	if e == nil {
		if e = r.Journal.Update(receipt, tx, true); e != nil {
			return ExecutionResult{}, e
		}
	}
	ch, e := r.current(ctx, c, a)
	if e != nil {
		return r.finish(receipt, p, false, "CONTEXT_CHANGED"), nil
	}
	sub, e := c.Subscribe(ctx, ch, receipt.Instance, true, receipt.Execution.Cursor)
	if e != nil {
		return r.finish(receipt, p, false, "HISTORY_UNAVAILABLE"), nil
	}
	duration := wait
	if duration == 0 {
		duration = 10 * time.Second
	}
	q, cancel := context.WithTimeout(ctx, duration)
	defer cancel()
	e = collect(q, c, sub, p, wait == 0, nil)
	reason := ""
	if e != nil {
		reason = "COMPLETION_NOT_OBSERVED"
	}
	return r.finish(receipt, p, e == nil, reason), nil
}
