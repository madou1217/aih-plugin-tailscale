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

签名是可选的：未签名包默认可以安装；带签名但公钥未登记时会以「签名未验证」状态安装，不会冒充「已验证」。

### 从 Release 安装（推荐）

```sh
gh release download v0.1.1 -R madou1217/aih-plugin-tailscale
shasum -a 256 -c SHA256SUMS
# 一步：登记公钥并安装；指纹必须与 Release 说明一致
aih plugin install ./tailscale-0.1.1.aih-plugin \
  --trust-key ./public.pem \
  --expect-fingerprint 3960e945419f8f267c3f47b1
aih plugin enable network.tailscale --accept-permissions
```

不想校验签名时直接 `aih plugin install ./tailscale-0.1.1.aih-plugin`。
需要强制策略时加 `--require-signed`：未签名或公钥未登记都会被拒绝。

### 本地开发打包

```sh
aih plugin pack . tailscale-0.1.0.aih-plugin
aih plugin install ./tailscale-0.1.0.aih-plugin
```

发布者签名：

```sh
aih plugin keygen --out keys
aih plugin pack . tailscale-0.1.0.aih-plugin \
  --signing-key keys/private.pem --key-id aih-plugins
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
