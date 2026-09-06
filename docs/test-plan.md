# Acceptance testing

## Software

Run `bash scripts/build.sh`. Tests must never access real Bluetooth hardware.
Install into isolated XDG directories to test packaging. Check both installer
flags and the wheel. Launch the optional panel in a graphical test session.

## Hardware (manual)

Record Linux distribution, kernel, BlueZ, audio stack, desktop, Buds model and
firmware version. Inspect capabilities first. Never guess a channel.

1. Pair normally; confirm ordinary PC audio.
2. With phone Bluetooth off, follow the multipoint wizard once.
3. Require verified asVer and restored PC audio before connecting the phone.
4. Verify phone remains connected and audio works in each direction.
5. Repeat after case power cycle; record whether manual phone intervention is needed.
6. Separately test suspend/resume, login/reboot, and phone call priority.
7. Test explicit noise reads/writes and confirm the phone remains usable.
8. Repeat on each claimed model and desktop. Do not infer results from another model.

No firmware reset or update is part of this procedure. Report failures and manual
steps alongside successes. A single successful session is not daily reliability.
