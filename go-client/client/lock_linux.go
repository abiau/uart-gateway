//go:build linux

package client

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
)

func OperationLock(directory, id string) (func(), error) {
	if !ValidID(id) {
		return nil, invalid()
	}
	if e := PrivateDir(directory); e != nil {
		return nil, e
	}
	p := filepath.Join(directory, id+".lock")
	fd, e := syscall.Open(p, syscall.O_RDWR|syscall.O_CREAT|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if e != nil {
		return nil, e
	}
	f := os.NewFile(uintptr(fd), p)
	if _, e = ReadPrivate(p, 64); e != nil {
		f.Close()
		return nil, e
	}
	if e = syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB); e != nil {
		f.Close()
		return nil, errors.New("此通道已有另一個本機操作，未排入第二次傳送")
	}
	return func() { syscall.Flock(fd, syscall.LOCK_UN); f.Close() }, nil
}
