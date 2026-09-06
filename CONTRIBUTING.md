# Contributing

Use Python 3.10 or newer and install `.[dev]` in a virtual environment.
Run `bash scripts/build.sh` before proposing changes. Add regression tests for
protocol parsers and state-machine changes without sending commands to hardware.

For a new model, report exact advertised services, model identifier, firmware,
host stack and acceptance results. Do not enable vendor writes based on a
user-editable device alias. Never add guessed RFCOMM channels or firmware actions.

Review logs for addresses, aliases, media titles, home paths and account data.
Keep protocol provenance and licensing explicit. Describe physical validation
separately from unit tests. File issues with reproducible steps and expected
versus observed behavior.
