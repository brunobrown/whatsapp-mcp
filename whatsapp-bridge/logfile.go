package main

import (
	"io"
	"os"
	"path/filepath"
	"strconv"
	"sync"
)

const (
	logFileName  = "bridge.log"
	logMaxBytes  = 5 << 20
	logKeepOlder = 3
	logFileMode  = 0o600
	logStoreDir  = "store"
)

// chmodFile is a variable so that a test can make it fail, as it does for a file of another owner.
var chmodFile = func(f *os.File, mode os.FileMode) error { return f.Chmod(mode) }

// rotatingFile appends to path and, before a write that would pass maxBytes, moves it to
// path.1 (path.1 to path.2, and so on up to keep files; the oldest is dropped).
type rotatingFile struct {
	mu       sync.Mutex
	path     string
	maxBytes int64
	keep     int
	file     *os.File
	size     int64
	modeErr  error
}

func newRotatingFile(path string, maxBytes int64, keep int) (*rotatingFile, error) {
	r := &rotatingFile{path: path, maxBytes: maxBytes, keep: keep}
	if err := r.open(); err != nil {
		return nil, err
	}
	return r, nil
}

func (r *rotatingFile) open() error {
	if err := os.MkdirAll(filepath.Dir(r.path), privateDirMode); err != nil {
		return err
	}
	f, err := os.OpenFile(r.path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, logFileMode)
	if err != nil {
		return err
	}
	info, err := f.Stat()
	if err != nil {
		f.Close()
		return err
	}
	// the mode of OpenFile only applies to a file it creates; a file of someone else cannot be
	// changed, and logging anyway beats not logging
	r.modeErr = chmodFile(f, logFileMode)
	r.file, r.size = f, info.Size()
	return nil
}

// rotate shifts the old files. A rename that fails only leaves a file where it was: the log keeps
// working (the current file then grows past the limit) rather than stopping.
func (r *rotatingFile) rotate() error {
	r.file.Close()
	r.file = nil
	os.Remove(r.path + "." + strconv.Itoa(r.keep))
	for i := r.keep - 1; i >= 1; i-- {
		os.Rename(r.path+"."+strconv.Itoa(i), r.path+"."+strconv.Itoa(i+1))
	}
	os.Rename(r.path, r.path+".1")
	return r.open()
}

func (r *rotatingFile) Write(p []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.file != nil && r.size > 0 && r.size+int64(len(p)) > r.maxBytes {
		if err := r.rotate(); err != nil {
			return 0, err
		}
	}
	if r.file == nil { // an earlier rotation could not reopen the file
		if err := r.open(); err != nil {
			return 0, err
		}
	}
	n, err := r.file.Write(p)
	r.size += int64(n)
	return n, err
}

// ModeErr is why the log file could not be restricted to its owner, or nil.
func (r *rotatingFile) ModeErr() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.modeErr
}

func (r *rotatingFile) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.file == nil {
		return nil
	}
	return r.file.Close()
}

// teeStdout makes everything printed to os.Stdout also go to out. It returns the real stdout
// (for output that must not be logged, such as the pairing QR code) and a stop function that
// puts stdout back and waits until all captured output is written.
func teeStdout(out io.Writer) (terminal *os.File, stop func(), err error) {
	terminal = os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		return nil, nil, err
	}
	os.Stdout = w
	done := make(chan struct{})
	go func() {
		defer close(done)
		// Each destination fails on its own (full disk, closed terminal) and the pipe is always
		// drained: a reader that stops would block every goroutine that prints.
		buf := make([]byte, 32*1024)
		for {
			n, err := r.Read(buf)
			if n > 0 {
				terminal.Write(buf[:n])
				out.Write(buf[:n])
			}
			if err != nil {
				return
			}
		}
	}()
	return terminal, func() {
		os.Stdout = terminal
		w.Close()
		<-done
		r.Close()
	}, nil
}
