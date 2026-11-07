package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"slices"
)

// ui_settings.json holds the variables the web interface saved. It only fills in a variable that is
// missing from the environment, so the environment always wins and every module keeps reading its
// variables as before. The list is the same as EDITABLE_ENV in whatsapp-mcp-server/uisettings.py (a test
// compares them): never a secret, a key, the configuration directory, a path to another file or the
// opt-in to a remote bind.
const uiSettingsFile = "ui_settings.json"

var uiEditableEnv = []string{
	"WHATSAPP_AUTO_DOWNLOAD",
	"WHATSAPP_AUTO_DOWNLOAD_MAX_MB",
	"WHATSAPP_AUTO_DOWNLOAD_TYPES",
	"WHATSAPP_MEDIA_RETENTION_DAYS",
	"WHATSAPP_KEEP_DELETED_CONTENT",
	"WHATSAPP_LOG_MESSAGES",
	"WHATSAPP_TIMEZONE",
	"WHATSAPP_MCP_MODE",
	"WHATSAPP_SESSION_TAINT",
	"WHATSAPP_MONITOR_DRAFT_TTL_HOURS",
	"WHATSAPP_UI",
	"WHATSAPP_UI_HOST",
	"WHATSAPP_UI_PORT",
}

type uiSettingsResult struct {
	Applied     []string
	IgnoredKeys []string
	Problem     string // why the whole file was ignored; never carries its content
}

// applyUISettings fills the editable variables that lookup does not know from the file in dir.
func applyUISettings(dir string, lookup func(string) (string, bool), set func(string, string) error) uiSettingsResult {
	var res uiSettingsResult
	path := filepath.Join(dir, uiSettingsFile)
	info, err := os.Stat(path)
	if os.IsNotExist(err) {
		return res
	}
	if err != nil {
		res.Problem = uiSettingsFile + " cannot be read"
		return res
	}
	if runtime.GOOS != "windows" && info.Mode().Perm()&0o077 != 0 {
		res.Problem = fmt.Sprintf("%s can be read by other users (mode %04o); run chmod 600 %s", uiSettingsFile, info.Mode().Perm(), path)
		return res
	}
	data, err := os.ReadFile(path)
	if err != nil {
		res.Problem = uiSettingsFile + " cannot be read"
		return res
	}
	var raw any
	if json.Unmarshal(data, &raw) != nil {
		res.Problem = uiSettingsFile + " is not valid JSON"
		return res
	}
	object, ok := raw.(map[string]any)
	if !ok {
		res.Problem = uiSettingsFile + " must be a JSON object"
		return res
	}
	var values map[string]any
	if env, present := object["env"]; present {
		var isObject bool
		if values, isObject = env.(map[string]any); !isObject {
			res.Problem = uiSettingsFile + `: "env" must be an object`
			return res
		}
	}
	names := make([]string, 0, len(values))
	for name := range values {
		names = append(names, name)
	}
	slices.Sort(names)
	for _, name := range names {
		value, isText := values[name].(string)
		if !slices.Contains(uiEditableEnv, name) || !isText {
			res.IgnoredKeys = append(res.IgnoredKeys, name)
			continue
		}
		if _, already := lookup(name); already {
			continue
		}
		if err := set(name, value); err == nil {
			res.Applied = append(res.Applied, name)
		}
	}
	return res
}

// applyUISettingsAtStart applies the file to the process environment and reports what it ignored. The
// settings read once at package initialisation are read again by main afterwards.
func applyUISettingsAtStart(logf func(format string, args ...any)) {
	dir, err := configDir()
	if err != nil {
		return
	}
	res := applyUISettings(dir, os.LookupEnv, os.Setenv)
	if res.Problem != "" {
		logf("%s: ignored: %s", uiSettingsFile, res.Problem)
	}
	if len(res.IgnoredKeys) > 0 {
		logf("%s: ignored keys: %v", uiSettingsFile, res.IgnoredKeys)
	}
}

// applyStartupSettings applies the file and then reads again what the package read before main ran:
// the auto-download settings and whether message text goes to the log.
func applyStartupSettings(logf func(format string, args ...any)) {
	applyUISettingsAtStart(logf)
	autoDownloadCfg = loadAutoDownloadConfig(os.Getenv)
	includeTextInLog = logMessagesEnabled()
}
