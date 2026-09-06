# Protocol boundaries

SMEP service UUID: `f8620674-a1ed-41ab-a8b9-de9ad655729d`.
MDE_VERSION opcode: `0x0B`. The version-only asVer=2 frame is:

```text
FC 0B 00 01 43 04 03 04 00 00 0B 02 1E AF CC
```

Discovery must match the exact service. An HFP channel is never a fallback.
The last valid state notification must confirm asVer=2 or 3. Opening a socket
alone does not verify the setting. The write can temporarily interrupt PC audio.

Regular noise-control UUID: `2e73a4ad-332d-41fc-90e2-16bef06523f2`.
This is separate from SMEP and numeric channels must not be interchanged.
The current regular-status decoder is locally tested on Buds4 Pro only.

Source: [GalaxyBudsClient PR #729](https://github.com/timschneeb/GalaxyBudsClient/pull/729)
and the [upstream project](https://github.com/timschneeb/GalaxyBudsClient).
The upstream volatile-state explanation is based on Buds2 Pro reverse
engineering; do not generalize it into a universal hardware guarantee.
