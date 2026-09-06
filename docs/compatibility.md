# Compatibility by feature

No device address or RFCOMM channel is baked into installation defaults.

| Models | Standard BlueZ A2DP | Experimental SMEP | Noise controls |
| --- | --- | --- | --- |
| Buds4 Pro | Locally tested | Controlled sessions only; case reconnect unresolved | Locally tested |
| Buds2 Pro | Generic BlueZ path, not tested here | Upstream Buds2 Pro/macOS evidence, not local Linux validation | Not validated here |
| Buds Core, FE, Buds2, Buds3, Buds3 FE, Buds3 Pro, Buds4 | Generic BlueZ path, not tested here | Upstream candidates; exact service discovery required | Not validated here |
| Buds (2019), Buds+, Buds Live, Buds Pro, unknown models | Generic BlueZ path, not tested here | Not claimed | Not claimed |

Run `galaxybridgectl buds capabilities --address YOUR_DEVICE_ADDRESS`.
The capability report distinguishes advertised services from hardware validation.
The control transport requires the exact regular-control UUID; sharing that UUID
does not establish matching payload layouts. Do not use experimental vendor
commands on an unvalidated model without reviewing the protocol first.

Contributions should add model-specific decoders, captured non-sensitive test
fixtures, and reproducible hardware acceptance evidence. Advertising all models
as fully supported would misrepresent the current implementation.
