//go:build windows

package main

import "os"

func ignoreTermination() {}

func processExists(p *os.Process) bool {
	// FindProcess opens the process on Windows and fails when it is gone.
	return p != nil
}
