//go:build linux

package main

import (
	"golang.org/x/sys/unix"
	"os"
	"sync"
)

func flushInput() { unix.IoctlSetInt(int(os.Stdin.Fd()), unix.TCFLSH, unix.TCIFLUSH) }

func outputMode() (func(), error) { return func() {}, nil }

// Polling permits shutdown before restoring the terminal, so no reader can
// consume keystrokes intended for the local shell after this session ends.
func keyboardInput() (<-chan []byte, func()) {
	keys := make(chan []byte)
	stop := make(chan struct{})
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		for {
			select {
			case <-stop:
				return
			default:
			}
			fds := []unix.PollFd{{Fd: int32(os.Stdin.Fd()), Events: unix.POLLIN}}
			n, e := unix.Poll(fds, 20)
			if e == unix.EINTR {
				continue
			}
			if e != nil {
				return
			}
			if n == 0 {
				continue
			}
			b := make([]byte, 4096)
			n, e = unix.Read(int(os.Stdin.Fd()), b)
			if e != nil || n == 0 {
				return
			}
			select {
			case keys <- b[:n]:
			case <-stop:
				return
			}
		}
	}()
	return keys, func() { close(stop); wg.Wait() }
}
