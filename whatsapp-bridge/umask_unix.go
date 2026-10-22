//go:build !windows

package main

import "syscall"

// restrictUmask makes every file the process creates from now on private to the owner and returns
// the previous mask.
func restrictUmask() int { return syscall.Umask(0o077) }

func restoreUmask(previous int) { syscall.Umask(previous) }

// restoreAndSetUmask sets the mask to value (used by tests to simulate an open umask) and returns the previous one.
func restoreAndSetUmask(value int) int { return syscall.Umask(value) }
