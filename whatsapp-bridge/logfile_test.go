package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func readFile(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestRotationKeepsEveryLineAndTheLimit(t *testing.T) { // LOG-02
	path := filepath.Join(t.TempDir(), "bridge.log")
	log, err := newRotatingFile(path, 100, 3)
	if err != nil {
		t.Fatal(err)
	}
	var all []string
	for i := 0; i < 12; i++ {
		line := fmt.Sprintf("linha %02d %s\n", i, strings.Repeat("x", 30)) // 40 bytes: 2 por arquivo
		all = append(all, line)
		if _, err := log.Write([]byte(line)); err != nil {
			t.Fatal(err)
		}
	}
	log.Close()

	// do mais novo para o mais antigo: atual, .1, .2, .3
	var got []string
	for _, p := range []string{path + ".3", path + ".2", path + ".1", path} {
		got = append(got, readFile(t, p))
	}
	if _, err := os.Stat(path + ".4"); err == nil {
		t.Error("more than 3 old files kept")
	}
	joined := strings.Join(got, "")
	if !strings.HasSuffix(strings.Join(all, ""), joined) {
		t.Errorf("kept text is not the most recent lines in order:\n%s", joined)
	}
	if !strings.Contains(got[3], all[11]) {
		t.Error("newest line is not in the current file")
	}
	for _, p := range []string{path, path + ".1", path + ".2", path + ".3"} {
		if info, _ := os.Stat(p); info.Size() > 100 {
			t.Errorf("%s has %d bytes, limit is 100", p, info.Size())
		}
	}
	if len(strings.Split(strings.TrimSpace(joined), "\n")) != 8 { // 4 arquivos x 2 linhas; as 4 mais antigas foram descartadas
		t.Errorf("unexpected number of lines kept:\n%s", joined)
	}
}

func TestRotationContinuesAnExistingFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "bridge.log")
	os.WriteFile(path, []byte(strings.Repeat("a", 90)), 0o600)
	log, err := newRotatingFile(path, 100, 3)
	if err != nil {
		t.Fatal(err)
	}
	defer log.Close()

	log.Write([]byte(strings.Repeat("b", 20)))

	if readFile(t, path+".1") != strings.Repeat("a", 90) || readFile(t, path) != strings.Repeat("b", 20) {
		t.Error("a file left by an earlier run must count toward the limit")
	}
}

func TestLogFileIsPrivate(t *testing.T) { // LOG-03
	path := filepath.Join(t.TempDir(), "sub", "bridge.log")
	log, err := newRotatingFile(path, 10, 2)
	if err != nil {
		t.Fatal(err)
	}
	log.Write([]byte("0123456789abc")) // força uma rotação
	log.Write([]byte("xyz"))
	log.Close()

	for _, p := range []string{path, path + ".1"} {
		info, err := os.Stat(p)
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm() != 0o600 {
			t.Errorf("%s mode = %v, want 0600", p, info.Mode().Perm())
		}
	}
}

func TestTeeStdoutWritesToTerminalAndFileButNotWhatGoesStraightToTerminal(t *testing.T) { // LOG-01, LOG-04
	dir := t.TempDir()
	fakeTerminal, err := os.Create(filepath.Join(dir, "terminal.txt"))
	if err != nil {
		t.Fatal(err)
	}
	real := os.Stdout
	os.Stdout = fakeTerminal
	defer func() { os.Stdout = real }()
	logPath := filepath.Join(dir, "bridge.log")
	log, _ := newRotatingFile(logPath, 1<<20, 3)

	terminal, stop, err := teeStdout(log)
	if err != nil {
		t.Fatal(err)
	}
	fmt.Println("evento normal")
	fmt.Fprintln(terminal, "QR-SECRETO")
	stop()
	log.Close()
	fakeTerminal.Close()

	if os.Stdout != fakeTerminal {
		t.Error("stop must put stdout back")
	}
	shown := readFile(t, filepath.Join(dir, "terminal.txt"))
	if !strings.Contains(shown, "evento normal") || !strings.Contains(shown, "QR-SECRETO") {
		t.Errorf("terminal lost output: %q", shown)
	}
	logged := readFile(t, logPath)
	if !strings.Contains(logged, "evento normal") {
		t.Errorf("log lost output: %q", logged)
	}
	if strings.Contains(logged, "QR-SECRETO") {
		t.Error("what goes straight to the terminal must not reach the log")
	}
}
