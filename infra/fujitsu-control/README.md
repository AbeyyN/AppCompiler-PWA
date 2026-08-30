# FUJITSU-CONTROL

`FUJITSU-CONTROL` is the local control plane for FUJITSU-BUILD. It is deliberately **not** a GitHub Actions runner and is not counted in the GitHub runner fleet.

Responsibilities:

- supervise the live Fujitsu dashboard on port `9860`;
- expose local control-plane health on `127.0.0.1:9861`;
- recover the dashboard if it crashes;
- supervise the Hermes 986 CI Doctor runtime;
- start the Hermes Telegram gateway when Hermes + provider + Telegram configuration are ready;
- keep runtime state in `$HOME/fujitsu-control/state.json` without storing repository credentials.

Runtime sessions:

- `fujitsu-control` — the control-plane supervisor;
- `fujitsu-dashboard` — the existing animated dashboard;
- `hermes-gateway` — Hermes messaging gateway when configured.

The repository files are only a bootstrap/source-of-truth mechanism. Once installed, the control plane runs locally in WSL and does not consume a GitHub Actions runner slot.
