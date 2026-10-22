package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func makeOutput(t *testing.T, args ...string) string {
	t.Helper()
	if _, err := exec.LookPath("make"); err != nil {
		t.Skip("make is not installed")
	}
	out, err := exec.Command("make", args...).CombinedOutput()
	if err != nil {
		t.Fatalf("make %v: %v\n%s", args, err, out)
	}
	return string(out)
}

func TestMakeRunBuildsABinaryAndRunsIt(t *testing.T) { // RUN-01
	out := makeOutput(t, "-n", "run")

	if !strings.Contains(out, "go build -o bin/whatsapp-bridge") {
		t.Errorf("run must build bin/whatsapp-bridge:\n%s", out)
	}
	if !strings.Contains(out, "exec ./bin/whatsapp-bridge") {
		t.Errorf("run must execute the binary:\n%s", out)
	}
	if strings.Contains(out, "go run") {
		t.Errorf("run must not use go run:\n%s", out)
	}
}

func TestInstallServiceWritesAUnitForThisDirectory(t *testing.T) { // RUN-02
	dir := t.TempDir()
	here, _ := os.Getwd()
	// -o build: the unit does not need a fresh compile to be rendered
	makeOutput(t, "-o", "build", "install-service", "SERVICE_DIR="+dir, "CONFIG_DIR="+filepath.Join(t.TempDir(), "cfg"))

	unit, err := os.ReadFile(filepath.Join(dir, "whatsapp-bridge.service"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(unit)
	for _, want := range []string{"WorkingDirectory=" + here + "\n", "ExecStart=" + here + "/bin/whatsapp-bridge\n", "Restart=on-failure"} {
		if !strings.Contains(text, want) {
			t.Errorf("unit lacks %q:\n%s", want, text)
		}
	}
	if strings.Contains(text, "@DIR@") {
		t.Errorf("placeholder left in the unit:\n%s", text)
	}
}

func TestBuildOutputsAreIgnoredByGit(t *testing.T) { // RUN-03
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
	for _, path := range []string{"bin/whatsapp-bridge", "whatsapp-client"} {
		if out, err := exec.Command("git", "check-ignore", "-q", path).CombinedOutput(); err != nil {
			t.Errorf("%s is not ignored by git: %v %s", path, err, out)
		}
	}
}

func TestInstallServiceRefusesAPathThatWouldBreakTheUnit(t *testing.T) { // RUN-02
	if _, err := exec.LookPath("make"); err != nil {
		t.Skip("make is not installed")
	}
	for _, name := range []string{"with space", "a&b", "a|b", "100%"} {
		t.Run(name, func(t *testing.T) {
			work := filepath.Join(t.TempDir(), name)
			os.MkdirAll(work, 0o755)
			for _, f := range []string{"Makefile", "whatsapp-bridge.service.in"} {
				data, _ := os.ReadFile(f)
				os.WriteFile(filepath.Join(work, f), data, 0o644)
			}
			dest := t.TempDir()

			cmd := exec.Command("make", "-o", "build", "install-service", "SERVICE_DIR="+dest)
			cmd.Dir = work
			out, err := cmd.CombinedOutput()

			if err == nil {
				t.Errorf("must refuse the path, got success:\n%s", out)
			}
			if _, statErr := os.Stat(filepath.Join(dest, "whatsapp-bridge.service")); statErr == nil {
				t.Error("no unit may be written for a path that breaks it")
			}
		})
	}
}

func TestUnitDoesNotDependOnSystemTargets(t *testing.T) { // RUN-02 (network-online.target não tem efeito em unidade --user)
	unit, err := os.ReadFile("whatsapp-bridge.service.in")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(unit), "network-online.target") {
		t.Error("a --user unit cannot wait for network-online.target")
	}
}

// renderedUnit builds the unit in a throwaway project directory with a fake executable, so that
// nothing of the real checkout or the real home is touched.
func renderedUnit(t *testing.T) (unit, project, config string) {
	t.Helper()
	if _, err := exec.LookPath("make"); err != nil {
		t.Skip("make is not installed")
	}
	project = filepath.Join(t.TempDir(), "proj")
	config = filepath.Join(t.TempDir(), "cfg", "whatsapp-mcp")
	os.MkdirAll(filepath.Join(project, "bin"), 0o755)
	for _, f := range []string{"Makefile", "whatsapp-bridge.service.in"} {
		data, _ := os.ReadFile(f)
		os.WriteFile(filepath.Join(project, f), data, 0o644)
	}
	os.WriteFile(filepath.Join(project, "bin", "whatsapp-bridge"), []byte("#!/bin/sh\nexit 0\n"), 0o755)
	dest := t.TempDir()
	cmd := exec.Command("make", "-o", "build", "install-service", "SERVICE_DIR="+dest, "CONFIG_DIR="+config)
	cmd.Dir = project
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("make install-service: %v\n%s", err, out)
	}
	data, err := os.ReadFile(filepath.Join(dest, "whatsapp-bridge.service"))
	if err != nil {
		t.Fatal(err)
	}
	return string(data), project, config
}

func TestUnitIsSandboxed(t *testing.T) { // SVC-01
	unit, project, config := renderedUnit(t)

	for _, want := range []string{
		"UMask=0077", "NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=read-only",
		"ReadWritePaths=" + project + "/store " + config,
		"RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX", "CapabilityBoundingSet=\n", "ProtectKernelTunables=yes",
		"Environment=WHATSAPP_CONFIG_DIR=" + config,
	} {
		if !strings.Contains(unit, want) {
			t.Errorf("unit lacks %q:\n%s", want, unit)
		}
	}
	if strings.Contains(unit, "@DIR@") || strings.Contains(unit, "@CONFIG@") {
		t.Errorf("placeholder left in the unit:\n%s", unit)
	}
	if strings.Contains(unit, "PrivateTmp=yes") {
		t.Error("PrivateTmp would hide the audio files the MCP server converts into /tmp")
	}
}

func TestInstallServiceCreatesThePrivateDirectoriesTheUnitWritesTo(t *testing.T) { // SVC-01
	skipOnWindows(t)
	_, project, config := renderedUnit(t)

	if got := mode(t, config); got != 0o700 {
		t.Errorf("config dir mode = %v, want 0700", got)
	}
	if got := mode(t, filepath.Join(project, "store")); got != 0o700 {
		t.Errorf("store mode = %v, want 0700", got)
	}
}

func TestGeneratedUnitPassesSystemdVerify(t *testing.T) { // SVC-01
	if _, err := exec.LookPath("systemd-analyze"); err != nil {
		t.Skip("systemd-analyze is not installed")
	}
	unit, _, _ := renderedUnit(t)
	path := filepath.Join(t.TempDir(), "whatsapp-bridge.service")
	os.WriteFile(path, []byte(unit), 0o644)

	out, err := exec.Command("systemd-analyze", "--user", "verify", path).CombinedOutput()

	if err != nil || strings.TrimSpace(string(out)) != "" {
		t.Errorf("systemd-analyze verify: %v\n%s", err, out)
	}
}

func TestUserManagerAcceptsTheSandboxingOptions(t *testing.T) { // SVC-01
	if _, err := exec.LookPath("systemd-run"); err != nil {
		t.Skip("systemd-run is not installed")
	}
	if out, err := exec.Command("systemd-run", "--user", "--wait", "--collect", "-q", "/bin/true").CombinedOutput(); err != nil {
		t.Skipf("no systemd user manager here: %v %s", err, out)
	}
	unit, _, _ := renderedUnit(t)
	args := []string{"--user", "--wait", "--collect", "-q"}
	for _, line := range strings.Split(unit, "\n") {
		key, value, ok := strings.Cut(line, "=")
		switch key {
		case "UMask", "NoNewPrivileges", "ProtectSystem", "ProtectHome", "ReadWritePaths", "ProtectKernelTunables",
			"ProtectKernelModules", "ProtectControlGroups", "RestrictAddressFamilies", "RestrictNamespaces",
			"RestrictRealtime", "RestrictSUIDSGID", "LockPersonality", "SystemCallArchitectures", "CapabilityBoundingSet":
			if ok {
				args = append(args, "-p", key+"="+value)
			}
		}
	}
	args = append(args, "/bin/true")

	if out, err := exec.Command("systemd-run", args...).CombinedOutput(); err != nil {
		t.Errorf("the user manager refused the options: %v\n%s", err, out)
	}
}
