package main

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"testing"
)

// uiFakeEnv is an environment a test can read and fill without touching the process.
type uiFakeEnv map[string]string

func (e uiFakeEnv) lookup(k string) (string, bool) { v, ok := e[k]; return v, ok }
func (e uiFakeEnv) set(k, v string) error          { e[k] = v; return nil }

func writeUISettings(t *testing.T, content string, mode os.FileMode) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, uiSettingsFile), []byte(content), mode); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(filepath.Join(dir, uiSettingsFile), mode); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestUISettingsFillsAVariableMissingFromTheEnvironment(t *testing.T) {
	dir := writeUISettings(t, `{"version":1,"env":{"WHATSAPP_AUTO_DOWNLOAD_MAX_MB":"50"}}`, 0o600)
	env := uiFakeEnv{}

	res := applyUISettings(dir, env.lookup, env.set)

	if env["WHATSAPP_AUTO_DOWNLOAD_MAX_MB"] != "50" || len(res.Applied) != 1 {
		t.Fatalf("env = %v, result = %+v", env, res)
	}
}

func TestUISettingsEnvironmentWinsEvenWhenEmpty(t *testing.T) {
	dir := writeUISettings(t, `{"env":{"WHATSAPP_AUTO_DOWNLOAD_MAX_MB":"50","WHATSAPP_TIMEZONE":"America/Recife"}}`, 0o600)
	env := uiFakeEnv{"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": ""} // set, though empty: it still wins

	res := applyUISettings(dir, env.lookup, env.set)

	if v, ok := env["WHATSAPP_AUTO_DOWNLOAD_MAX_MB"]; !ok || v != "" {
		t.Fatalf("the environment value was replaced: %v", env)
	}
	if env["WHATSAPP_TIMEZONE"] != "America/Recife" || len(res.Applied) != 1 {
		t.Fatalf("env = %v, result = %+v", env, res)
	}
}

func TestUISettingsNeverAppliesAVariableOutsideTheEditableList(t *testing.T) {
	for _, name := range []string{"WHATSAPP_DB_KEY", "WHATSAPP_DB_KEY_FILE", "WHATSAPP_CONFIG_DIR", "WHATSAPP_DB_ENCRYPTION",
		"WHATSAPP_SEND_POLICY", "WHATSAPP_MONITOR_CONFIG", "WHATSAPP_UI_ALLOW_REMOTE", "PATH", "HOME"} {
		dir := writeUISettings(t, `{"env":{"`+name+`":"x"}}`, 0o600)
		env := uiFakeEnv{}

		res := applyUISettings(dir, env.lookup, env.set)

		if len(env) != 0 || len(res.IgnoredKeys) != 1 || res.IgnoredKeys[0] != name {
			t.Errorf("%s: env = %v, result = %+v", name, env, res)
		}
	}
}

func TestUISettingsIgnoresAValueThatIsNotText(t *testing.T) {
	dir := writeUISettings(t, `{"env":{"WHATSAPP_AUTO_DOWNLOAD_MAX_MB":50,"WHATSAPP_TIMEZONE":["x"]}}`, 0o600)
	env := uiFakeEnv{}

	res := applyUISettings(dir, env.lookup, env.set)

	sort.Strings(res.IgnoredKeys)
	if len(env) != 0 || strings.Join(res.IgnoredKeys, ",") != "WHATSAPP_AUTO_DOWNLOAD_MAX_MB,WHATSAPP_TIMEZONE" {
		t.Fatalf("env = %v, result = %+v", env, res)
	}
}

func TestUISettingsWithoutAFileChangesNothing(t *testing.T) {
	env := uiFakeEnv{"A": "1"}

	res := applyUISettings(t.TempDir(), env.lookup, env.set)

	if len(env) != 1 || len(res.Applied) != 0 || res.Problem != "" {
		t.Fatalf("env = %v, result = %+v", env, res)
	}
}

func TestUISettingsIgnoresAFileThatCannotBeUsedAndSaysWhy(t *testing.T) {
	for _, content := range []string{"{not json", "[]", `{"env":[]}`} {
		dir := writeUISettings(t, content, 0o600)
		env := uiFakeEnv{}

		res := applyUISettings(dir, env.lookup, env.set)

		if len(env) != 0 || !strings.Contains(res.Problem, uiSettingsFile) {
			t.Errorf("%q: env = %v, problem = %q", content, env, res.Problem)
		}
	}
}

func TestUISettingsIgnoresAFileThatOtherUsersCanRead(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX modes")
	}
	dir := writeUISettings(t, `{"env":{"WHATSAPP_TIMEZONE":"America/Recife"}}`, 0o644)
	env := uiFakeEnv{}

	res := applyUISettings(dir, env.lookup, env.set)

	if len(env) != 0 || !strings.Contains(res.Problem, "chmod 600") {
		t.Fatalf("env = %v, problem = %q", env, res.Problem)
	}
}

func TestUISettingsProblemNeverCarriesTheContentOfTheFile(t *testing.T) {
	dir := writeUISettings(t, `{"env":{"WHATSAPP_TIMEZONE":"secret-value-123" `, 0o600)

	res := applyUISettings(dir, uiFakeEnv{}.lookup, uiFakeEnv{}.set)

	if res.Problem == "" || strings.Contains(res.Problem, "secret-value-123") {
		t.Fatalf("problem = %q", res.Problem)
	}
}

// The server (Python) and the bridge must accept the same variables from the file.
func TestUIEditableListMatchesTheServer(t *testing.T) {
	src, err := os.ReadFile(filepath.Join("..", "whatsapp-mcp-server", "uisettings.py"))
	if err != nil {
		t.Fatal(err)
	}
	block := regexp.MustCompile(`(?s)EDITABLE_ENV = \((.*?)\n\)`).FindSubmatch(src)
	if block == nil {
		t.Fatal("EDITABLE_ENV not found in uisettings.py")
	}
	var python []string
	for _, m := range regexp.MustCompile(`"([A-Z_]+)"`).FindAllSubmatch(block[1], -1) {
		python = append(python, string(m[1]))
	}
	want := append([]string(nil), uiEditableEnv...)
	sort.Strings(python)
	sort.Strings(want)
	if len(python) == 0 || strings.Join(python, ",") != strings.Join(want, ",") {
		t.Fatalf("python = %v\nbridge = %v", python, want)
	}
}

func TestStartupAppliesTheFileToTheProcessEnvironment(t *testing.T) {
	dir := writeUISettings(t, `{"env":{"WHATSAPP_AUTO_DOWNLOAD_MAX_MB":"50"}}`, 0o600)
	t.Setenv("WHATSAPP_CONFIG_DIR", dir)
	os.Unsetenv("WHATSAPP_AUTO_DOWNLOAD_MAX_MB")
	t.Cleanup(func() { os.Unsetenv("WHATSAPP_AUTO_DOWNLOAD_MAX_MB") })

	applyUISettingsAtStart(func(string, ...any) {})

	if got := loadAutoDownloadConfig(os.Getenv).MaxBytes; got != 50<<20 {
		t.Fatalf("auto-download limit = %d bytes", got)
	}
}

// Some settings are read once, when the package is initialised, which is before main applies the file.
// applyStartupSettings reads them again, so a value the interface saved reaches them.
func TestStartupSettingsReachTheValuesReadAtInitialisation(t *testing.T) {
	dir := writeUISettings(t, `{"env":{"WHATSAPP_AUTO_DOWNLOAD_MAX_MB":"50","WHATSAPP_LOG_MESSAGES":"true"}}`, 0o600)
	t.Setenv("WHATSAPP_CONFIG_DIR", dir)
	for _, name := range []string{"WHATSAPP_AUTO_DOWNLOAD_MAX_MB", "WHATSAPP_LOG_MESSAGES"} {
		os.Unsetenv(name)
	}
	previousDownload, previousLog := autoDownloadCfg, includeTextInLog
	t.Cleanup(func() {
		autoDownloadCfg, includeTextInLog = previousDownload, previousLog
		os.Unsetenv("WHATSAPP_AUTO_DOWNLOAD_MAX_MB")
		os.Unsetenv("WHATSAPP_LOG_MESSAGES")
	})
	autoDownloadCfg, includeTextInLog = loadAutoDownloadConfig(func(string) string { return "" }), false

	applyStartupSettings(func(string, ...any) {})

	if autoDownloadCfg.MaxBytes != 50<<20 {
		t.Errorf("auto-download limit = %d bytes, want 50 MB", autoDownloadCfg.MaxBytes)
	}
	if !includeTextInLog {
		t.Error("WHATSAPP_LOG_MESSAGES from the file did not reach includeTextInLog")
	}
}

func TestStartupSettingsDoNotOverrideTheEnvironment(t *testing.T) {
	dir := writeUISettings(t, `{"env":{"WHATSAPP_LOG_MESSAGES":"true"}}`, 0o600)
	t.Setenv("WHATSAPP_CONFIG_DIR", dir)
	t.Setenv("WHATSAPP_LOG_MESSAGES", "false")
	previous := includeTextInLog
	t.Cleanup(func() { includeTextInLog = previous })

	applyStartupSettings(func(string, ...any) {})

	if includeTextInLog {
		t.Error("the file overrode the environment")
	}
}
