# Session renewal fix

The recurring failure was reproduced in logs: after a verified session ended,
subsequent connections explicitly skipped activation because AUTO_PATCH was false.

For the locally validated device, automatic patching is now opted in while host
auto-reconnect remains off. The daemon attempts once per connection trigger,
suppresses connection echoes and enforces a 60-second minimum write interval.
It checks that the device is still connected after waiting before writing.

On 2026-09-07 the daemon autonomously applied MDE_VERSION and verified asVer=2.
The user confirmed simultaneous phone and PC connections. This is one successful
renewal test. Repeated case cycles, resume and reboot still require acceptance.
The public configuration keeps this experimental behavior opt-in.
