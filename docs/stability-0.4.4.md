# Stability audit, 2026-09-06

The August test established a single working session, not reliable everyday
coexistence. Logs on subsequent days show unmarked connections with automatic
patch disabled. A Bluetooth connection alone does not establish multipoint.

0.4.4 removes automatic GNOME noise-control reads and closes the regular control
profile after each explicit noise command. The menu's Verify action refreshes
the cached mode. IPC survives a client leaving before a slow response, and the
wizard has a longer response deadline. Failed ConnectProfile calls no longer
count as success merely because Device1.Connected is true. Connection echoes
during the wizard cannot overwrite its progress with a ready state.

Automatic reconnect and patch remain off. The case-cycle restoration still
requires the explicit wizard; reliable automatic daily coexistence is unresolved.
No firmware reset, flash or pairing deletion was performed.

Acceptance still required: restore with phone Bluetooth off, confirm PC audio,
reconnect phone and verify both audio directions; then repeat after case closure,
and separately after suspend. Observe actual phone connection, not just PC state.
Do not label the feature stable until those sequences succeed repeatedly.
