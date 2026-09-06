# Architecture

BlueZ helpers provide standard Linux device operations. SMEP framing/discovery
and regular noise controls are separate transports. A per-user daemon serializes
IPC commands and publishes atomic cached status for desktop clients.

The Tk panel and optional GNOME Shell extension invoke the same CLI. The CLI
can perform basic operations independently; the multipoint wizard requires the
daemon so its state persists between commands. systemd is optional: a foreground
daemon provides the same IPC.

PipeWire/WirePlumber is only needed for audio-profile/default-sink controls.
The project contains no Samsung Pass, browser integration or cloud client.

Capability inspection reports advertised UUIDs separately from hardware testing.
Automatic dual-device reconnection is an unresolved feature, not an invariant.
