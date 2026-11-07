//go:build !windows

package main

import (
	"os"
	"os/signal"
	"syscall"
)

func ignoreTermination() { signal.Ignore(syscall.SIGTERM) }

func processExists(p *os.Process) bool { return p.Signal(syscall.Signal(0)) == nil }
