//go:build windows

package main

import (
	"golang.org/x/sys/windows"
	"golang.org/x/term"
	"os"
	"syscall"
)

func flushInput() {
	syscall.NewLazyDLL("kernel32.dll").NewProc("FlushConsoleInputBuffer").Call(os.Stdin.Fd())
}

func outputMode() (func(), error) {
	if !term.IsTerminal(int(os.Stdout.Fd())) {
		return func() {}, nil
	}
	h := windows.Handle(os.Stdout.Fd())
	var original uint32
	if e := windows.GetConsoleMode(h, &original); e != nil {
		return nil, e
	}
	if e := windows.SetConsoleMode(h, original|windows.ENABLE_PROCESSED_OUTPUT|windows.ENABLE_VIRTUAL_TERMINAL_PROCESSING); e != nil {
		return nil, e
	}
	return func() { windows.SetConsoleMode(h, original) }, nil
}

func keyboardInput() (<-chan []byte, func()) {
	keys := make(chan []byte)
	stop := make(chan struct{})
	go func() {
		for {
			b := make([]byte, 4096)
			n, e := os.Stdin.Read(b)
			if e != nil {
				return
			}
			select {
			case keys <- b[:n]:
			case <-stop:
				return
			}
		}
	}()
	return keys, func() { close(stop) }
}
