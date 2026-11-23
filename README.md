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
gh release download v0.1.3 -R madou1217/aih-plugin-tailscale
shasum -a 256 -c SHA256SUMS
# 一步：登记公钥并安装；指纹必须与 Release 说明一致
aih plugin install ./tailscale-0.1.3.aih-plugin \
  --trust-key ./public.pem \
  --expect-fingerprint 3960e945419f8f267c3f47b1
aih plugin enable network.tailscale --accept-permissions
```

不想校验签名时直接 `aih plugin install ./tailscale-0.1.3.aih-plugin`。
需要强制策略时加 `--require-signed`：未签名或公钥未登记都会被拒绝。

### 直接用 Release URL 安装

```sh
aih plugin install \
  https://github.com/madou1217/aih-plugin-tailscale/releases/download/v0.1.3/tailscale-0.1.3.aih-plugin \
  --trust-key https://github.com/madou1217/aih-plugin-tailscale/releases/download/v0.1.3/public.pem \
  --expect-fingerprint 3960e945419f8f267c3f47b1
```

只允许 https（本机回环可用 http）。CLI 会先下载用于摘要校验；如果服务端看不到这个本地文件（远程 server 或 SSH 隧道），它会按同一 URL 自行下载并核对 sha256。服务端需要能直连公网 https，且不走系统代理。

CLI 完全无法访问外网时，加 `--server-download`：CLI 只把 URL 交给服务端，由服务端下载。此时本地摘要校验会被跳过，完整性依赖 https 与包内签名。

### 本地开发的插件

```sh
# 直接给源码目录：先自动打包，再安装
aih plugin install ./my-plugin

# 需要签名时
aih plugin install ./my-plugin \
  --signing-key keys/private.pem --key-id my-publisher \
  --trust-key keys/public.pem --expect-fingerprint <指纹>
```

本地 server 直接读文件；远程 server 看不到本地文件时会自动把包上传过去（带 sha256 校验），不需要先手动 scp。

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
