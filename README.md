# aih-plugin-tailscale

AIH `network.connector` plugin for **Tailscale**.

- Connector id: `tailscale`
- Operations: `install`, `uninstall`, `configure`, `enable`, `disable`, `probe`
- Version is reported, never enforced. `engines.aih` is wide (`>=0.0.0`).

## Connectivity reporting

The plugin reads `tailscale status --json` and distinguishes:

| Mode | Evidence |
|---|---|
| `direct-p2p` | an online peer with a non-empty `CurAddr` |
| `derp-relay` | an online peer that only exposes a `Relay` |
| `unavailable` | Tailscale not running / not installed |
| `unknown` | running, but no peer evidence |

`direct-p2p` is only returned with `Peer.CurAddr` evidence. A DERP relay is
never relabeled as direct P2P.

## Install

```sh
aih plugin pack . tailscale-0.1.0.aih-plugin
aih plugin install ./tailscale-0.1.0.aih-plugin
aih plugin enable network.tailscale --accept-permissions
```

Signed distribution:

```sh
aih plugin keygen --out keys
aih plugin pack . tailscale-0.1.0.aih-plugin \
  --signing-key keys/private.pem --key-id aih-plugins
aih plugin trust keys/public.pem --key-id aih-plugins
aih plugin install ./tailscale-0.1.0.aih-plugin --require-signed
```

## Configure

```json
{ "authKey": "tskey-auth-...", "hostname": "aih-host" }
```

`authKey` is a secret field and is passed to `tailscale up` through
`TS_AUTHKEY`, not argv. State is stored at
`$HOME/.ai_home/plugin-state/network.tailscale/` with 0600 permissions.

## Boundaries

- `install` uses Homebrew when available; no `sudo` is attempted.
- `enable` runs `tailscale up`; if login is required, the returned message
  points the user to finish authorization in a terminal.
- No Electron/WebView/CDP.
