//go:build windows

package main

// Windows has no umask: access is governed by ACLs, which the user profile already restricts.
func restrictUmask() int { return 0 }

func restoreUmask(int) {}

func restoreAndSetUmask(int) int { return 0 }
