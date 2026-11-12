//go:build !linux

package client

import "errors"

func OperationLock(string, string) (func(), error) {
	return nil, errors.New("操作檔案鎖僅支援 Linux")
}
