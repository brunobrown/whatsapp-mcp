//go:build windows

package main

import "os/exec"

func prepareUIChild(cmd *exec.Cmd) {}

// signalUIChild ends the process: Windows has no polite request for a console-less child.
func signalUIChild(cmd *exec.Cmd, force bool) {
	if cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
}
