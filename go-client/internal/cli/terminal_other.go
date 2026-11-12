//go:build !linux

package cli

import (
	"context"
	"errors"
	"os"
)

func terminal(*os.File) bool { return false }
func readLine(context.Context, <-chan os.Signal) (string, error) {
	return "", errors.New("互動終端僅支援 Linux")
}
