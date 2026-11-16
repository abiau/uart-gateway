#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p dist
for arch in amd64 arm64; do
    CGO_ENABLED=0 GOOS=linux GOARCH="$arch" GOAMD64=v1 go build -trimpath -o "dist/uart-client-linux-$arch" ./cmd/uart-client
done
