//go:build !windows

package main

import (
	"os/exec"
	"syscall"
)

// prepareUIChild puts the interface in its own process group, so that stopping it also stops what it
// started (uv starts Python).
func prepareUIChild(cmd *exec.Cmd) { cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} }

// signalUIChild asks the whole group to end, or ends it when force is set.
func signalUIChild(cmd *exec.Cmd, force bool) {
	if cmd.Process == nil {
		return
	}
	sig := syscall.SIGTERM
	if force {
		sig = syscall.SIGKILL
	}
	if syscall.Kill(-cmd.Process.Pid, sig) != nil {
		_ = cmd.Process.Signal(sig)
	}
}
