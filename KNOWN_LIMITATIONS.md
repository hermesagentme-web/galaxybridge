# Known limitations

- Automatic phone + PC reconnect after charging-case power cycles is unresolved.
- Only Buds4 Pro controlled Linux sessions were tested locally.
- Standard Device1.Connected does not prove an A2DP stream or a phone link.
- Noise protocol payloads are not validated across the whole Buds family.
- The standalone panel is new; full graphical/hardware acceptance is pending.
- GNOME integration declares Shell 50 only and uses French labels.
- One configured headset per user daemon. Multi-headset routing is not supported.
- Call-mode expiry requires a live daemon; do not stop it mid-call-mode.
- Runtime markers are bookkeeping, not fresh hardware readback.
- Diagnostic media names and aliases need review before public sharing.
