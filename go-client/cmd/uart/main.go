package main

import (
	"os"
	"uart-gateway/go-client/internal/cli"
)

func main() { os.Exit(cli.Main(os.Args[1:])) }
