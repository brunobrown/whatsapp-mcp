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
	makeOutput(t, "-o", "build", "install-service", "SERVICE_DIR="+dir)

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
