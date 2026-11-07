package main

import (
	"fmt"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

// TestUIHelperProcess lets the test binary stand in for the interface process: with UI_HELPER set it
// behaves as the named fake and exits; without it the test does nothing.
func TestUIHelperProcess(t *testing.T) {
	switch os.Getenv("UI_HELPER") {
	case "serve": // prints its URL, then stays up until it is stopped
		fmt.Println("App URL: http://127.0.0.1:8765")
		fmt.Fprintln(os.Stderr, "to stderr")
		time.Sleep(time.Minute)
		os.Exit(0)
	case "fail": // the port is taken, say
		fmt.Fprintln(os.Stderr, "port 8765 is already in use")
		os.Exit(1)
	case "stubborn": // ignores the polite request to stop
		ignoreTermination()
		fmt.Println("up")
		time.Sleep(time.Minute)
		os.Exit(0)
	case "env": // reports whether a variable is set
		_, ok := os.LookupEnv("UI_PROBE")
		fmt.Printf("probe-set=%v\n", ok)
		os.Exit(0)
	}
}

type logCollector struct {
	mu    sync.Mutex
	lines []string
}

func (c *logCollector) logf(format string, args ...any) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.lines = append(c.lines, fmt.Sprintf(format, args...))
}

func (c *logCollector) text() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return strings.Join(c.lines, "\n")
}

func (c *logCollector) waitFor(t *testing.T, want string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if strings.Contains(c.text(), want) {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("the log never said %q:\n%s", want, c.text())
}

func helperConfig(mode string, extraEnv ...string) uiConfig {
	return uiConfig{Enabled: true, Command: []string{os.Args[0], "-test.run=^TestUIHelperProcess$"}, Env: append([]string{"UI_HELPER=" + mode}, extraEnv...)}
}

func processAlive(pid int) bool {
	p, err := os.FindProcess(pid)
	if err != nil {
		return false
	}
	return processExists(p)
}

func TestUIStartsWithBridgeAndLogsURL(t *testing.T) { // UI-01
	logs := &logCollector{}
	ui, err := startUI(helperConfig("serve"), logs.logf)
	if err != nil {
		t.Fatal(err)
	}
	defer ui.stop(2 * time.Second)

	logs.waitFor(t, "App URL: http://127.0.0.1:8765")
	logs.waitFor(t, "to stderr") // standard error reaches the log too
	for _, line := range strings.Split(logs.text(), "\n") {
		if strings.Contains(line, "App URL") && !strings.HasPrefix(line, "[ui] ") {
			t.Errorf("a line of the interface is not marked as such: %q", line)
		}
	}
}

func TestUIOffStartsNothing(t *testing.T) { // UI-01
	for _, value := range []string{"off", "OFF", "false", "0"} {
		cfg := loadUIConfig(func(k string) string {
			if k == "WHATSAPP_UI" {
				return value
			}
			return ""
		}, nil)
		logs := &logCollector{}

		ui, err := startUI(cfg, logs.logf)

		if ui != nil || err != nil || cfg.Enabled {
			t.Errorf("WHATSAPP_UI=%s: ui=%v err=%v enabled=%v", value, ui, err, cfg.Enabled)
		}
	}
}

func TestUIDefaultCommand(t *testing.T) {
	cfg := loadUIConfig(func(string) string { return "" }, nil)

	if !cfg.Enabled || cfg.Command[0] != "uv" || strings.Join(cfg.Command, " ") !=
		"uv run --directory ../whatsapp-mcp-server python -m ui" {
		t.Fatalf("%+v", cfg)
	}
	if strings.Contains(strings.Join(cfg.Command, " "), "--group") {
		t.Fatalf("the default command must not name a dependency group: %+v", cfg.Command)
	}
}

func TestUICommandCanBeReplaced(t *testing.T) {
	cfg := loadUIConfig(func(k string) string {
		if k == "WHATSAPP_UI_COMMAND" {
			return "  python3   -m  ui "
		}
		return ""
	}, nil)

	if strings.Join(cfg.Command, "|") != "python3|-m|ui" {
		t.Fatalf("%q", cfg.Command)
	}
}

func TestUIStartFailureDoesNotStopBridge(t *testing.T) { // UI-03
	logs := &logCollector{}

	ui, err := startUI(uiConfig{Enabled: true, Command: []string{"/nonexistent/ui-command"}}, logs.logf)

	if err == nil || ui != nil {
		t.Fatalf("ui=%v err=%v", ui, err)
	}
	// the caller logs the error and goes on; startUI itself must not exit or panic, and must not hang
}

func TestUIExitIsLoggedAndNotRestarted(t *testing.T) { // UI-07
	logs := &logCollector{}
	ui, err := startUI(helperConfig("fail"), logs.logf)
	if err != nil {
		t.Fatal(err)
	}

	logs.waitFor(t, "port 8765 is already in use")
	logs.waitFor(t, "the interface stopped")
	time.Sleep(300 * time.Millisecond)
	if n := strings.Count(logs.text(), "port 8765 is already in use"); n != 1 {
		t.Errorf("the interface ran %d times, want 1 (no restart):\n%s", n, logs.text())
	}
	if !ui.exited() {
		t.Error("exited() is false after the process ended")
	}
}

func TestUIStoppedWhenBridgeStops(t *testing.T) { // UI-02
	logs := &logCollector{}
	ui, err := startUI(helperConfig("serve"), logs.logf)
	if err != nil {
		t.Fatal(err)
	}
	logs.waitFor(t, "App URL")
	pid := ui.pid()

	ui.stop(2 * time.Second)

	if processAlive(pid) {
		t.Fatalf("process %d is still alive after stop", pid)
	}
}

func TestUIStopForcesAProcessThatIgnoresTheRequest(t *testing.T) { // UI-02
	logs := &logCollector{}
	ui, err := startUI(helperConfig("stubborn"), logs.logf)
	if err != nil {
		t.Fatal(err)
	}
	logs.waitFor(t, "up")
	pid := ui.pid()

	started := time.Now()
	ui.stop(300 * time.Millisecond)

	if processAlive(pid) {
		t.Fatalf("process %d survived stop", pid)
	}
	if time.Since(started) > 3*time.Second {
		t.Errorf("stop took %v", time.Since(started))
	}
}

func TestUIStopAfterTheProcessEndedIsHarmless(t *testing.T) {
	logs := &logCollector{}
	ui, _ := startUI(helperConfig("fail"), logs.logf)
	logs.waitFor(t, "the interface stopped")

	ui.stop(time.Second) // must neither hang nor panic
	ui.stop(time.Second)
}

func TestUIChildGetsOnlyTheEnvironmentItIsGiven(t *testing.T) {
	t.Setenv("UI_PROBE", "set-in-the-bridge-process")
	logs := &logCollector{}

	ui, err := startUI(helperConfig("env"), logs.logf) // Env lists no UI_PROBE
	if err != nil {
		t.Fatal(err)
	}
	defer ui.stop(time.Second)

	logs.waitFor(t, "probe-set=false")
}

func helperEnv(mode string) func(string) string {
	return func(k string) string {
		if k == "WHATSAPP_UI_COMMAND" {
			return os.Args[0] + " -test.run=^TestUIHelperProcess$"
		}
		return ""
	}
}

func TestUIStartedFromTheEnvironmentRunsAndStopsWithTheBridge(t *testing.T) { // UI-01, UI-02
	logs := &logCollector{}

	ui := startUIFromEnvironment([]string{"UI_HELPER=serve"}, helperEnv("serve"), logs.logf)

	if ui == nil {
		t.Fatalf("the interface did not start:\n%s", logs.text())
	}
	logs.waitFor(t, "App URL: http://127.0.0.1:8765")
	pid := ui.pid()
	ui.stop(2 * time.Second)
	if processAlive(pid) || !ui.exited() {
		t.Fatalf("process %d is still alive after stop", pid)
	}
}

func TestUIChildSeesTheEnvironmentOfBeforeTheSavedSettings(t *testing.T) { // CFG-04
	logs := &logCollector{}
	// the bridge applied ui_settings.json to its own environment after taking this snapshot
	t.Setenv("UI_PROBE", "filled-in-from-the-file")

	ui := startUIFromEnvironment([]string{"UI_HELPER=env"}, helperEnv("env"), logs.logf)
	defer ui.stop(time.Second)

	logs.waitFor(t, "probe-set=false")
}

func TestUIThatCannotStartIsReportedAndTheBridgeGoesOn(t *testing.T) { // UI-03
	logs := &logCollector{}
	getenv := func(k string) string {
		if k == "WHATSAPP_UI_COMMAND" {
			return "/nonexistent/ui-command"
		}
		return ""
	}

	ui := startUIFromEnvironment(nil, getenv, logs.logf)

	if ui != nil || !strings.Contains(logs.text(), "web interface is off") ||
		!strings.Contains(logs.text(), "/nonexistent/ui-command") {
		t.Fatalf("ui=%v log:\n%s", ui, logs.text())
	}
}

func TestUIThatIsTurnedOffSaysNothingAndStartsNothing(t *testing.T) {
	logs := &logCollector{}
	getenv := func(k string) string {
		if k == "WHATSAPP_UI" {
			return "off"
		}
		return ""
	}

	ui := startUIFromEnvironment(nil, getenv, logs.logf)

	if ui != nil || logs.text() != "" {
		t.Fatalf("ui=%v log:\n%s", ui, logs.text())
	}
}
