//go:build linux

package cli

import (
	"context"
	"errors"
	"os"
	"syscall"
	"time"
	"unsafe"
)

func terminal(f *os.File) bool {
	var t syscall.Termios
	_, _, e := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), syscall.TCGETS, uintptr(unsafe.Pointer(&t)))
	return e == 0
}
func readLine(ctx context.Context, interrupt <-chan os.Signal) (string, error) {
	buffer := []byte{}
	overflow := false
	for {
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-interrupt:
			return "", context.Canceled
		default:
		}
		fd := int(os.Stdin.Fd())
		if fd >= 1024 {
			return "", errors.New("終端輸入描述元超出範圍")
		}
		set := syscall.FdSet{}
		set.Bits[fd/64] |= int64(1) << uint(fd%64)
		timeout := syscall.NsecToTimeval((50 * time.Millisecond).Nanoseconds())
		n, e := syscall.Select(fd+1, &set, nil, nil, &timeout)
		if e == syscall.EINTR {
			continue
		}
		if e != nil {
			return "", e
		}
		if n == 0 {
			continue
		}
		one := []byte{0}
		count, e := syscall.Read(fd, one)
		if e == syscall.EINTR {
			continue
		}
		if e != nil {
			return "", e
		}
		if count == 0 {
			return "", errors.New("輸入已結束")
		}
		if one[0] == '\n' {
			if overflow {
				return "", errors.New("輸入超過 4096 bytes")
			}
			return string(buffer), nil
		}
		if len(buffer) < 4096 {
			buffer = append(buffer, one[0])
		} else {
			overflow = true
		}
	}
}
