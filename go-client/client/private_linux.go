//go:build linux

package client

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"syscall"
)

func ReadPrivate(path string, maximum int) ([]byte, error) {
	fd, e := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if e != nil {
		return nil, errors.New("無法讀取私有檔案，請先設定連線並確認權限")
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	info, e := f.Stat()
	if e != nil {
		return nil, e
	}
	owner, ok := info.Sys().(*syscall.Stat_t)
	if !ok || owner.Uid != uint32(os.Getuid()) || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return nil, errors.New("私有檔案須屬目前使用者，且僅擁有者可讀寫")
	}
	data, e := io.ReadAll(io.LimitReader(f, int64(maximum)+1))
	if e != nil || len(data) > maximum {
		return nil, errors.New("私有檔案讀取失敗或超過上限")
	}
	return data, nil
}
func PrivateDir(path string) error {
	info, e := os.Lstat(path)
	if os.IsNotExist(e) {
		parent := filepath.Dir(path)
		if parent != path {
			if e = PrivateDir(parent); e != nil {
				return e
			}
		}
		e = os.Mkdir(path, 0700)
		if e != nil && !os.IsExist(e) {
			return e
		}
		info, e = os.Lstat(path)
	}
	if e != nil {
		return e
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok || st.Uid != uint32(os.Getuid()) || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0022 != 0 {
		return errors.New("私有目錄擁有者、符號連結或寫入權限不安全")
	}
	return nil
}
func WritePrivate(path string, data []byte, replace bool) error {
	if e := PrivateDir(filepath.Dir(path)); e != nil {
		return e
	}
	if !replace {
		fd, e := syscall.Open(path, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
		if e != nil {
			return errors.New("檔案已存在或無法安全建立；未覆蓋")
		}
		f := os.NewFile(uintptr(fd), path)
		good := false
		defer func() {
			f.Close()
			if !good {
				os.Remove(path)
			}
		}()
		if _, e = f.Write(data); e != nil {
			return e
		}
		if e = f.Sync(); e != nil {
			return e
		}
		good = true
		return f.Close()
	}
	if _, e := os.Lstat(path); e == nil {
		if _, e = ReadPrivate(path, 65536); e != nil {
			return e
		}
	} else if !os.IsNotExist(e) {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".uart-*")
	if e != nil {
		return e
	}
	temp := f.Name()
	defer os.Remove(temp)
	if _, e = f.Write(data); e != nil {
		f.Close()
		return e
	}
	if e = f.Sync(); e != nil {
		f.Close()
		return e
	}
	if e = f.Close(); e != nil {
		return e
	}
	return os.Rename(temp, path)
}
