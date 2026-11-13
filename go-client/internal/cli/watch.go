package cli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"uart-gateway/go-client/client"
)

type display struct {
	data       []byte
	diagnostic bool
}
type sink struct {
	queue    chan display
	mu       sync.Mutex
	used     int
	done     chan struct{}
	failure  error
	finished chan struct{}
}

func newSink() *sink {
	s := &sink{queue: make(chan display, 256), done: make(chan struct{}), finished: make(chan struct{})}
	go func() {
		defer close(s.finished)
		for {
			select {
			case <-s.done:
				return
			case item, ok := <-s.queue:
				if !ok {
					return
				}
				stream := os.Stdout
				if item.diagnostic {
					stream = os.Stderr
				}
				_ = stream.SetWriteDeadline(time.Now().Add(2 * time.Second))
				_, e := stream.Write(item.data)
				_ = stream.SetWriteDeadline(time.Time{})
				s.mu.Lock()
				s.used -= len(item.data)
				if e != nil && s.failure == nil {
					s.failure = errors.New("終端輸出失敗，未重送資料")
					close(s.done)
				}
				s.mu.Unlock()
				if e != nil {
					return
				}
			}
		}
	}()
	return s
}
func (s *sink) put(b []byte, diagnostic bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.failure != nil {
		return s.failure
	}
	if len(s.queue) == cap(s.queue) || s.used+len(b) > 262144 {
		s.failure = errors.New("終端顯示佇列已滿，資料連續性中斷")
		close(s.done)
		return s.failure
	}
	s.used += len(b)
	s.queue <- display{append([]byte{}, b...), diagnostic}
	return nil
}
func (s *sink) close() { close(s.queue); <-s.finished }
func (a *app) watchCommand(ctx context.Context, s client.Settings, args []string) (int, error) {
	operation, cancel := context.WithCancel(ctx)
	defer cancel()
	go func() {
		select {
		case <-a.interrupt:
			cancel()
		case <-operation.Done():
		}
	}()
	ctx = operation
	f := flags("watch")
	duration := f.Float64("duration", 0, "")
	replay := f.Bool("replay", false, "")
	text := f.Bool("text", false, "")
	term := f.Bool("terminal", false, "")
	ids, e := parse(f, args)
	if e != nil {
		return 1, e
	}
	if len(ids) < 1 || len(ids) > 4 || *text && *term || math.IsNaN(*duration) || math.IsInf(*duration, 0) || *duration < 0 || *duration > 86400 {
		return 1, errors.New("watch 需一至四路；文字／終端模式互斥，時間須為 0～86400 秒")
	}
	if *term && !terminal(os.Stdout) {
		return 1, errors.New("終端模式需要互動終端，管線請使用 --text 或預設 JSON")
	}
	c, e := s.Connect(ctx)
	if e != nil {
		if ctx.Err() != nil {
			return 130, ctx.Err()
		}
		return 1, e
	}
	channels := []client.Channel{}
	seen := map[string]bool{}
	for _, id := range ids {
		if seen[id] {
			c.Close()
			return 1, errors.New("通道不可重複")
		}
		seen[id] = true
		ch, e := c.Channel(ctx, id)
		if e != nil {
			c.Close()
			return 1, e
		}
		channels = append(channels, ch)
	}
	instance := c.Instance
	c.Close()
	return a.watch(ctx, s, channels, instance, *replay, *text || *term, *term, time.Duration(*duration*float64(time.Second)))
}
func (a *app) watch(ctx context.Context, s client.Settings, channels []client.Channel, instance string, replay, text, term bool, duration time.Duration) (int, error) {
	run, cancel := context.WithCancel(ctx)
	defer cancel()
	if duration > 0 {
		var done context.CancelFunc
		run, done = context.WithTimeout(run, duration)
		defer done()
	}
	go func() {
		select {
		case <-a.interrupt:
			cancel()
		case <-run.Done():
		}
	}()
	sink := newSink()
	var workers sync.WaitGroup
	failures := make(chan error, len(channels))
	for _, ch := range channels {
		workers.Add(1)
		go func(ch client.Channel) {
			defer workers.Done()
			renderer := client.Renderer{Terminal: term, Multi: len(channels) > 1}
			prefix := "[" + client.Label(ch.ID) + "／" + client.Label(ch.Label) + "] "
			defer func() {
				if tail := renderer.Finish(); text && tail != "" {
					if len(channels) > 1 {
						tail = prefix + tail + "\n"
					}
					_ = sink.put([]byte(tail), false)
				}
			}()
			diagnostic := func(message string) { _ = sink.put([]byte(prefix+message+"\n"), true) }
			c, e := s.Connect(run)
			if e != nil {
				if run.Err() == nil {
					diagnostic(e.Error())
					failures <- e
				}
				return
			}
			defer c.Close()
			sub, e := c.Subscribe(run, ch, instance, replay, "")
			if e != nil {
				if run.Err() == nil {
					diagnostic(e.Error())
					failures <- e
				}
				return
			}
			cursor, e := client.NewCursor(sub, c.Limits["max_rx_chunk_bytes"])
			if e != nil {
				failures <- e
				return
			}
			diagnostic("訂閱已就緒；資料缺口與世代變更會停止此路。")
			delivery := ""
			for {
				event, e := c.T.Next(run)
				if e != nil {
					if run.Err() == nil {
						diagnostic(e.Error())
						failures <- e
					}
					return
				}
				raw, e := cursor.Receive(event)
				if e != nil {
					diagnostic(e.Error())
					failures <- e
					return
				}
				if text {
					if raw != nil {
						mode := client.String(event.Data, "delivery")
						if mode != delivery {
							if mode == "replay" {
								diagnostic("歷史重播")
							} else {
								diagnostic("即時接收")
							}
							delivery = mode
						}
						rendered := renderer.Feed(raw)
						if len(channels) > 1 && rendered != "" {
							rendered = prefix + strings.ReplaceAll(rendered, "\n", "\n"+prefix)
							rendered = strings.TrimSuffix(rendered, prefix)
							if !strings.HasSuffix(rendered, "\n") {
								rendered += "\n"
							}
						}
						if e = sink.put([]byte(rendered), false); e != nil {
							failures <- e
							cancel()
							return
						}
					} else if event.Kind == "recording_state" {
						diagnostic("錄製狀態：" + client.Label(client.String(event.Data, "state")))
					}
				} else {
					b, _ := json.Marshal(event.Raw)
					if e = sink.put(append(b, '\n'), false); e != nil {
						failures <- e
						cancel()
						return
					}
				}
				if event.Kind == "subscription_state" && client.String(event.Data, "subscription_id") == sub.ID && client.String(event.Data, "state") == "ended" {
					diagnostic("資料流已停止：" + client.Label(client.String(event.Data, "reason")))
					failures <- errors.New("訂閱已停止")
					return
				}
				if event.Kind == "channel_state" {
					current := client.Map(event.Data, "channel")
					binding := client.Map(current, "binding")
					if client.String(current, "state") != "ready" || client.String(binding, "revision") != sub.Revision || current["stream_epoch"] != sub.Epoch {
						diagnostic("目標或資料流已變更")
						failures <- errors.New("資料流已變更")
						return
					}
				}
			}
		}(ch)
	}
	finished := make(chan struct{})
	go func() { workers.Wait(); close(finished) }()
	select {
	case <-finished:
	case <-sink.done:
		cancel()
		<-finished
	case <-run.Done():
		<-finished
	}
	sink.close()
	if sink.failure != nil {
		return 2, sink.failure
	}
	if term {
		fmt.Fprint(os.Stdout, "\x1b[0m")
	}
	if len(failures) > 0 {
		return 2, nil
	}
	if run.Err() != nil {
		if ctx.Err() != nil {
			return 130, ctx.Err()
		}
		if errors.Is(run.Err(), context.DeadlineExceeded) {
			return 0, nil
		}
		return 130, nil
	}
	return 0, nil
}
func (a *app) menu(ctx context.Context, s client.Settings) (int, error) {
	if !terminal(os.Stdin) || !terminal(os.Stdout) {
		return 1, errors.New("uart 工作台需要互動終端；腳本請用 uart channels／watch／exec")
	}
	var channels []client.Channel
	instance := ""
	refresh := true
	last := []string{}
	preference := ""
	if s.Deployment != "" {
		preference = filepathPreference(s)
		if _, e := os.Lstat(preference); !os.IsNotExist(e) {
			last, e = readPreference(preference)
			if e != nil {
				fmt.Fprintln(os.Stderr, "上次通道偏好無法讀取；保留原檔，請重新選擇。")
				preference = ""
				last = nil
			}
		}
	}
	for {
		if refresh {
			refresh = false
			c, e := s.Connect(ctx)
			if e != nil {
				fmt.Fprintln(os.Stderr, client.Label(e.Error()))
				channels = nil
			} else {
				channels, e = c.Channels(ctx)
				instance = c.Instance
				c.Close()
				if e != nil {
					fmt.Fprintln(os.Stderr, client.Label(e.Error()))
					channels = nil
				}
			}
		}
		fmt.Fprintf(os.Stderr, "\nUART 遠端工作台｜%s｜%s:%d｜%s\n", client.Label(s.Name), s.Host, s.Port, s.Access)
		for i, ch := range channels {
			fmt.Fprintf(os.Stderr, "%d　%s／%s｜%s｜%s\n", i+1, client.Label(ch.ID), client.Label(ch.Label), client.Label(client.String(ch.Adapter, "port")), ch.State)
		}
		suggested := []string{}
		for _, id := range last {
			for i, ch := range channels {
				if ch.ID == id && ch.State == "ready" {
					suggested = append(suggested, strconv.Itoa(i+1))
				}
			}
		}
		prompt := "輸入一至四個不同編號（如 1,2）；R 更新／重新連線；Q 離開："
		if len(last) > 0 && len(suggested) == len(last) {
			prompt = "Enter 沿用本次清單中的上次選擇 " + strings.Join(suggested, ",") + "；或輸入編號；R 更新；Q 離開："
		}
		answer, e := a.ask(ctx, prompt)
		if e != nil {
			return 0, e
		}
		choice := strings.ToLower(answer)
		if choice == "q" {
			return 0, nil
		}
		if choice == "r" {
			refresh = true
			continue
		}
		if choice == "" && len(last) > 0 && len(suggested) == len(last) {
			choice = strings.Join(suggested, ",")
		}
		parts := strings.Fields(strings.ReplaceAll(choice, ",", " "))
		selected := []client.Channel{}
		used := map[int]bool{}
		valid := len(parts) >= 1 && len(parts) <= 4
		for _, p := range parts {
			n, e := strconv.Atoi(p)
			if e != nil || n < 1 || n > len(channels) || used[n] {
				valid = false
				break
			}
			used[n] = true
			selected = append(selected, channels[n-1])
		}
		if !valid {
			fmt.Fprintln(os.Stderr, "請選擇目前清單中的不同編號。")
			continue
		}
		ready := true
		for _, ch := range selected {
			if ch.State != "ready" {
				ready = false
				fmt.Fprintln(os.Stderr, "所選 UART 尚未就緒。可由管理者在 Windows 恢復，或明確使用 --access manage reopen。")
			}
		}
		if !ready {
			continue
		}
		last = nil
		for _, ch := range selected {
			last = append(last, ch.ID)
		}
		sort.Strings(last)
		if preference != "" {
			b, _ := json.Marshal(client.Object{"version": 1, "channels": last})
			if e = client.WritePrivate(preference, b, true); e != nil {
				fmt.Fprintln(os.Stderr, "本次通道偏好未保存："+client.Label(e.Error()))
			}
		}
		fmt.Fprintln(os.Stderr, "開始歷史＋即時監看；Ctrl+C 返回清單。畫面有界，長期資料請由 Gateway 錄製。")
		code, e := a.watch(ctx, s, selected, instance, true, true, true, 0)
		if e != nil && ctx.Err() != nil {
			return 130, e
		}
		if code == 130 {
			fmt.Fprintln(os.Stderr, "監看已停止，Gateway 繼續執行；正在更新目前通道清單。")
			refresh = true
		} else {
			fmt.Fprintln(os.Stderr, "資料流已停止，Gateway 繼續執行；R 重新查詢後再選擇。")
			channels = nil
			last = nil
		}
	}
}
func filepathPreference(s client.Settings) string {
	return s.Root + string(os.PathSeparator) + "client-preferences" + string(os.PathSeparator) + s.Deployment + "-" + s.Access + ".json"
}
func readPreference(path string) ([]string, error) {
	bad := errors.New("通道偏好格式不合法")
	raw, e := client.ReadPrivate(path, 2048)
	if e != nil {
		return nil, e
	}
	v, e := client.StrictJSON(raw, 8)
	if e != nil || len(v) != 2 {
		return nil, bad
	}
	if _, e = client.Integer(v, "version", 1, 1); e != nil {
		return nil, e
	}
	ids, ok := v["channels"].([]any)
	if !ok || len(ids) < 1 || len(ids) > 4 {
		return nil, bad
	}
	seen := map[string]bool{}
	result := []string{}
	for _, v := range ids {
		id, ok := v.(string)
		if !ok || !client.ValidID(id) || seen[id] {
			return nil, bad
		}
		seen[id] = true
		result = append(result, id)
	}
	return result, nil
}
