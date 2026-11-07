package main

import (
	"fmt"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// The web interface runs as a child of the bridge, the only process that stays up: the MCP server starts
// once per client session and would fight over the port. The child is started once and never restarted.

const (
	uiDefaultCommand = "uv run --directory ../whatsapp-mcp-server --group ui python -m ui"
	uiMaxLogLine     = 1000
)

type uiConfig struct {
	Enabled bool
	Command []string
	Env     []string // the environment the child gets; nil means the bridge's own
}

// loadUIConfig reads WHATSAPP_UI (off, false or 0 turn the interface off) and WHATSAPP_UI_COMMAND (words
// separated by spaces, no quoting). environ is what the child will see.
func loadUIConfig(getenv func(string) string, environ []string) uiConfig {
	cfg := uiConfig{Enabled: true, Env: environ}
	switch strings.ToLower(strings.TrimSpace(getenv("WHATSAPP_UI"))) {
	case "off", "false", "0":
		cfg.Enabled = false
	}
	command := strings.TrimSpace(getenv("WHATSAPP_UI_COMMAND"))
	if command == "" {
		command = uiDefaultCommand
	}
	cfg.Command = strings.Fields(command)
	return cfg
}

// uiProcess is the running interface.
type uiProcess struct {
	cmd  *exec.Cmd
	done chan struct{}
}

// lineWriter sends what the interface prints to the bridge log, a line at a time and marked as its own.
type lineWriter struct {
	mu   sync.Mutex
	logf func(string, ...any)
	buf  []byte
}

func (w *lineWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.buf = append(w.buf, p...)
	for {
		i := strings.IndexByte(string(w.buf), '\n')
		if i < 0 {
			break
		}
		w.emit(string(w.buf[:i]))
		w.buf = w.buf[i+1:]
	}
	if len(w.buf) > uiMaxLogLine*4 { // a line that never ends
		w.emit(string(w.buf))
		w.buf = nil
	}
	return len(p), nil
}

func (w *lineWriter) flush() {
	w.mu.Lock()
	defer w.mu.Unlock()
	if len(w.buf) > 0 {
		w.emit(string(w.buf))
		w.buf = nil
	}
}

func (w *lineWriter) emit(line string) {
	line = strings.TrimRight(line, "\r")
	if len(line) > uiMaxLogLine {
		line = line[:uiMaxLogLine] + "..."
	}
	w.logf("[ui] %s", line)
}

// startUI starts the interface and returns at once. A nil process with a nil error means it is off.
// The error is for the caller to log: a bridge without its interface is still a bridge.
func startUI(cfg uiConfig, logf func(string, ...any)) (*uiProcess, error) {
	if !cfg.Enabled {
		return nil, nil
	}
	if len(cfg.Command) == 0 {
		return nil, fmt.Errorf("WHATSAPP_UI_COMMAND is empty")
	}
	cmd := exec.Command(cfg.Command[0], cfg.Command[1:]...)
	cmd.Env = cfg.Env
	out := &lineWriter{logf: logf}
	cmd.Stdout, cmd.Stderr = out, out
	prepareUIChild(cmd)
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("could not start the interface (%s): %v", cfg.Command[0], err)
	}
	u := &uiProcess{cmd: cmd, done: make(chan struct{})}
	go func() {
		err := cmd.Wait()
		out.flush()
		status := "exit status 0"
		if err != nil {
			status = err.Error()
		}
		logf("the interface stopped (%s)", status)
		close(u.done)
	}()
	return u, nil
}

func (u *uiProcess) pid() int { return u.cmd.Process.Pid }

func (u *uiProcess) exited() bool {
	select {
	case <-u.done:
		return true
	default:
		return false
	}
}

// stop asks the interface to end, waits up to grace, then ends it. It is safe to call more than once.
func (u *uiProcess) stop(grace time.Duration) {
	if u == nil || u.exited() {
		return
	}
	signalUIChild(u.cmd, false)
	select {
	case <-u.done:
		return
	case <-time.After(grace):
	}
	signalUIChild(u.cmd, true)
	select {
	case <-u.done:
	case <-time.After(2 * time.Second):
	}
}

// startUIFromEnvironment is what main calls: it reads the settings, starts the interface with the
// environment the bridge had before ui_settings.json was applied (so the interface can tell a variable
// from the environment from a value of the file) and logs why it did not start. nil means no interface.
func startUIFromEnvironment(startupEnv []string, getenv func(string) string, logf func(string, ...any)) *uiProcess {
	ui, err := startUI(loadUIConfig(getenv, startupEnv), logf)
	if err != nil {
		logf("The web interface is off: %v", err)
		return nil
	}
	return ui
}
