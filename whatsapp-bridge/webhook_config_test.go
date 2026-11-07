package main

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

const goodSecret = "0123456789abcdef0123456789abcdef-test"

func goodConfig(extra string) string {
	return `{"endpoints":[{"name":"local","url":"http://127.0.0.1:9000/hook","secret":"` + goodSecret + `","events":["message"]` + extra + `}]}`
}

func TestConfigAcceptsAMinimalEndpointAndDefaultsAreSafe(t *testing.T) { // HOOK-03, HOOK-05, HOOK-06
	cfg, err := parseWebhookConfig([]byte(goodConfig("")), defaultRESTPort)
	if err != nil {
		t.Fatal(err)
	}
	e := cfg.Endpoints[0]
	if e.IncludeContent || e.IncludeOwn || e.AllowPrivateNetwork {
		t.Errorf("defaults = %+v, want content, own messages and private networks all off", e)
	}
}

func TestConfigRefusesWhatItShould(t *testing.T) { // HOOK-02, HOOK-22
	cases := map[string]string{
		"not JSON":           `{`,
		"unknown field":      `{"endpoints":[],"enable":true}`,
		"no name":            `{"endpoints":[{"url":"http://127.0.0.1:1/x","secret":"` + goodSecret + `","events":["message"]}]}`,
		"no url":             `{"endpoints":[{"name":"a","secret":"` + goodSecret + `","events":["message"]}]}`,
		"no secret":          `{"endpoints":[{"name":"a","url":"http://127.0.0.1:1/x","events":["message"]}]}`,
		"short secret":       `{"endpoints":[{"name":"a","url":"http://127.0.0.1:1/x","secret":"short","events":["message"]}]}`,
		"placeholder secret": `{"endpoints":[{"name":"a","url":"http://127.0.0.1:1/x","secret":"` + webhookExampleSecret + `","events":["message"]}]}`,
		"no events":          `{"endpoints":[{"name":"a","url":"http://127.0.0.1:1/x","secret":"` + goodSecret + `"}]}`,
		"empty events":       `{"endpoints":[{"name":"a","url":"http://127.0.0.1:1/x","secret":"` + goodSecret + `","events":[]}]}`,
		"unknown event":      `{"endpoints":[{"name":"a","url":"http://127.0.0.1:1/x","secret":"` + goodSecret + `","events":["nope"]}]}`,
		"duplicate name": `{"endpoints":[{"name":"a","url":"http://127.0.0.1:1/x","secret":"` + goodSecret + `","events":["message"]},` +
			`{"name":"a","url":"http://127.0.0.1:2/x","secret":"` + goodSecret + `","events":["message"]}]}`,
		"plain http to a host": `{"endpoints":[{"name":"a","url":"http://example.com/x","secret":"` + goodSecret + `","events":["message"]}]}`,
		"credentials in url":   `{"endpoints":[{"name":"a","url":"https://user:pass@example.com/x","secret":"` + goodSecret + `","events":["message"]}]}`,
		"not http":             `{"endpoints":[{"name":"a","url":"ftp://example.com/x","secret":"` + goodSecret + `","events":["message"]}]}`,
		"no host":              `{"endpoints":[{"name":"a","url":"https:///x","secret":"` + goodSecret + `","events":["message"]}]}`,
		"the bridge itself":    `{"endpoints":[{"name":"a","url":"http://127.0.0.1:8080/api/send","secret":"` + goodSecret + `","events":["message"]}]}`,
		"the bridge by name":   `{"endpoints":[{"name":"a","url":"http://localhost:8080/api/send","secret":"` + goodSecret + `","events":["message"]}]}`,
		"the bridge over IPv6": `{"endpoints":[{"name":"a","url":"http://[::1]:8080/api/send","secret":"` + goodSecret + `","events":["message"]}]}`,
		"no endpoints at all":  `{"endpoints":[]}`,
	}
	for name, body := range cases {
		if _, err := parseWebhookConfig([]byte(body), defaultRESTPort); err == nil {
			t.Errorf("%s: accepted, want an error", name)
		} else if strings.Contains(err.Error(), goodSecret) {
			t.Errorf("%s: the error repeats the secret: %v", name, err)
		}
	}
}

func TestConfigAcceptsLoopbackOverPlainHTTPAndAnyHostOverHTTPS(t *testing.T) { // HOOK-02
	for _, url := range []string{"http://127.0.0.1:9000/x", "http://localhost:9000/x", "http://[::1]:9000/x", "https://example.com/hook", "https://127.0.0.1:9000/x"} {
		body := `{"endpoints":[{"name":"a","url":"` + url + `","secret":"` + goodSecret + `","events":["*"]}]}`
		if _, err := parseWebhookConfig([]byte(body), defaultRESTPort); err != nil {
			t.Errorf("%s: %v", url, err)
		}
	}
}

func TestConfigAcceptsAnotherPortOnLoopbackAndTheBridgePortElsewhere(t *testing.T) { // HOOK-22
	body := `{"endpoints":[{"name":"a","url":"https://example.com:8080/x","secret":"` + goodSecret + `","events":["message"]}]}`
	if _, err := parseWebhookConfig([]byte(body), defaultRESTPort); err != nil {
		t.Errorf("port 8080 on another host must be fine: %v", err)
	}
}

func TestDisabledConfigStartsNothing(t *testing.T) { // HOOK-01
	cfg, err := parseWebhookConfig([]byte(`{"enabled":false,"endpoints":[]}`), defaultRESTPort)
	if err != nil || cfg.Enabled() {
		t.Errorf("cfg, err = %+v, %v; want a disabled config without error", cfg, err)
	}
}

func writeConfig(t *testing.T, dir, body string, mode os.FileMode) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, webhookConfigFile), []byte(body), mode); err != nil {
		t.Fatal(err)
	}
	os.Chmod(filepath.Join(dir, webhookConfigFile), mode)
}

func TestNoFileMeansDisabledWithoutError(t *testing.T) { // HOOK-01
	d := loadWebhooks(t.TempDir(), nil)
	if st := d.Status(); st.Enabled || st.Error != "" || len(st.Endpoints) != 0 {
		t.Errorf("status = %+v, want disabled and no error", st)
	}
}

func TestFileReadableByOthersIsRefusedAndNothingStarts(t *testing.T) { // HOOK-02
	if runtime.GOOS == "windows" {
		t.Skip("POSIX modes")
	}
	dir := t.TempDir()
	writeConfig(t, dir, goodConfig(""), 0o644)
	d := loadWebhooks(dir, nil)
	if st := d.Status(); st.Enabled || st.Error == "" || len(st.Endpoints) != 0 {
		t.Errorf("status = %+v, want disabled with an error", st)
	}
	if strings.Contains(d.Status().Error, goodSecret) {
		t.Error("the status error repeats the secret")
	}
}

func TestInvalidFileLeavesTheDispatcherDisabledWithTheReason(t *testing.T) { // HOOK-02
	dir := t.TempDir()
	writeConfig(t, dir, `{"endpoints":[{"name":"a","url":"http://example.com/x","secret":"`+goodSecret+`","events":["message"]}]}`, 0o600)
	st := loadWebhooks(dir, nil).Status()
	if st.Enabled || st.Error == "" || strings.Contains(st.Error, goodSecret) {
		t.Errorf("status = %+v, want disabled with a reason that does not repeat the secret", st)
	}
}

func TestValidFileEnablesTheEndpoints(t *testing.T) { // HOOK-03
	dir := t.TempDir()
	writeConfig(t, dir, goodConfig(""), 0o600)
	d := loadWebhooks(dir, nil)
	defer d.Close(0)
	if st := d.Status(); !st.Enabled || len(st.Endpoints) != 1 || st.Endpoints[0].Name != "local" {
		t.Errorf("status = %+v, want one enabled endpoint named local", st)
	}
}
