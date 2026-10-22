package main

import (
	"io/fs"
	"os"
	"path/filepath"
)

const (
	privateDirMode  fs.FileMode = 0o700
	privateFileMode fs.FileMode = 0o600
)

// chmodPath is a variable so that a test can make it fail.
var chmodPath = os.Chmod

// hardenResult says what hardenStore did.
type hardenResult struct {
	Changed int      // paths whose permissions were tightened
	Failed  []string // paths that could not be changed
}

// hardenStore makes everything under root private to the owner: directories 0700, regular files 0600.
// Symbolic links are never followed or changed, so a link planted in the store cannot make this
// touch a file elsewhere. A path that cannot be changed is reported, not fatal. It is safe to run
// again: a tree that is already private changes nothing.
func hardenStore(root string) (hardenResult, error) {
	var result hardenResult
	if err := os.MkdirAll(root, privateDirMode); err != nil {
		return result, err
	}
	walkErr := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			result.Failed = append(result.Failed, path)
			if entry != nil && entry.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		info, err := entry.Info() // does not follow links
		if err != nil {
			result.Failed = append(result.Failed, path)
			return nil
		}
		var want fs.FileMode
		switch {
		case info.Mode()&fs.ModeSymlink != 0:
			return nil
		case info.IsDir():
			want = privateDirMode
		case info.Mode().IsRegular():
			want = privateFileMode
		default:
			return nil // sockets, devices and the like
		}
		if info.Mode().Perm() == want {
			return nil
		}
		if err := chmodPath(path, want); err != nil {
			result.Failed = append(result.Failed, path)
			return nil
		}
		result.Changed++
		return nil
	})
	return result, walkErr
}
