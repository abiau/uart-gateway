//go:build !linux

package client

import "errors"

func ReadPrivate(string, int) ([]byte, error) {
	return nil, errors.New("獨立 Client 的私有檔案功能僅支援 Linux")
}
func WritePrivate(string, []byte, bool) error {
	return errors.New("獨立 Client 的私有檔案功能僅支援 Linux")
}
func PrivateDir(string) error {
	return errors.New("獨立 Client 的私有檔案功能僅支援 Linux")
}
