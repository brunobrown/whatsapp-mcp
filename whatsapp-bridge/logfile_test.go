package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
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

type failingWriter struct{}

func (failingWriter) Write([]byte) (int, error) { return 0, errors.New("disk full") }

func TestTeeStdoutKeepsDrainingWhenTheLogFails(t *testing.T) { // LOG-01 (um destino que falha não trava quem imprime)
	dir := t.TempDir()
	fakeTerminal, _ := os.Create(filepath.Join(dir, "terminal.txt"))
	real := os.Stdout
	os.Stdout = fakeTerminal
	defer func() { os.Stdout = real }()

	_, stop, err := teeStdout(failingWriter{})
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		for i := 0; i < 4000; i++ { // bem mais que o buffer do pipe
			fmt.Println(strings.Repeat("x", 100))
		}
		fmt.Println("ultima linha")
	}()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("printing blocked after the log writer failed")
	}
	stop()
	fakeTerminal.Close()

	if !strings.Contains(readFile(t, filepath.Join(dir, "terminal.txt")), "ultima linha") {
		t.Error("the terminal must keep receiving output when the log fails")
	}
}

func TestExistingLogFilesBecomePrivate(t *testing.T) { // LOG-03
	path := filepath.Join(t.TempDir(), "bridge.log")
	os.WriteFile(path, []byte("antigo"), 0o644)
	os.Chmod(path, 0o644)

	log, err := newRotatingFile(path, 1<<20, 3)
	if err != nil {
		t.Fatal(err)
	}
	log.Close()

	if info, _ := os.Stat(path); info.Mode().Perm() != 0o600 {
		t.Errorf("mode = %v, want 0600", info.Mode().Perm())
	}
}

func TestLogKeepsWorkingWhenARenameFails(t *testing.T) { // LOG-02
	path := filepath.Join(t.TempDir(), "bridge.log")
	log, err := newRotatingFile(path, 20, 2)
	if err != nil {
		t.Fatal(err)
	}
	defer log.Close()
	// path.1 as a non-empty directory: moving the log onto it fails
	os.MkdirAll(filepath.Join(path+".1", "x"), 0o755)

	for i := 0; i < 5; i++ {
		if _, err := log.Write([]byte("0123456789ABCDEF\n")); err != nil {
			t.Fatalf("write %d failed after a rename error: %v", i, err)
		}
	}
	if got := strings.Count(readFile(t, path), "ABCDEF"); got == 0 {
		t.Error("lines were lost")
	}
}

func TestLogStillWorksWhenTheModeCannotBeSet(t *testing.T) { // LOG-03
	chmodFile = func(*os.File, os.FileMode) error { return errors.New("operation not permitted") }
	defer func() { chmodFile = func(f *os.File, m os.FileMode) error { return f.Chmod(m) } }()
	path := filepath.Join(t.TempDir(), "bridge.log")

	log, err := newRotatingFile(path, 1<<20, 3)
	if err != nil {
		t.Fatalf("the log must open anyway: %v", err)
	}
	log.Write([]byte("linha\n"))
	log.Close()

	if log.ModeErr() == nil || !strings.Contains(log.ModeErr().Error(), "not permitted") {
		t.Errorf("ModeErr = %v, want the chmod error", log.ModeErr())
	}
	if readFile(t, path) != "linha\n" {
		t.Error("nothing was logged")
	}
}
