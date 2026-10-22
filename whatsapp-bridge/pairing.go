package main

import (
	"fmt"
	"io"

	"github.com/mdp/qrterminal"
)

// showPairingQR prints the pairing QR code. The code is a pairing secret: pass the terminal, never the log.
func showPairingQR(terminal io.Writer, code string) {
	fmt.Fprintln(terminal, "\nScan this QR code with your WhatsApp app:")
	qrterminal.GenerateHalfBlock(code, qrterminal.L, terminal)
}
