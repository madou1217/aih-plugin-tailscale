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

## WebUI setup guide

The plugin declares a `setup` block in `describe`:

- exact steps for creating a Tailscale auth key (Settings → Keys);
- links to the Tailscale keys page and auth-key documentation;
- a note that browser links use the browser's own network, while install/connect
  commands can use the `proxyUrl` field.

The `proxyUrl` schema property is marked `x-aih-proxy`; the AIH connector panel
then offers **「使用 AIH 本机代理」**, which starts a local AIH proxy entry and
fills the authenticated proxy URL into the config without the user searching
for a proxy manually.

## Configure

```json
{ "authKey": "tskey-auth-...", "hostname": "aih-host", "proxyUrl": "http://aih:...@127.0.0.1:..." }
```

`proxyUrl` is used for Homebrew install/uninstall and the `tailscale up/down`
command environment. It does not transparently re-route an already-running
Tailscale daemon or a browser login page; those need system/browser proxy
configuration. Prefer the auth-key flow so no interactive browser is needed.

`authKey` is a secret field. On macOS it is passed to `tailscale up` through
`TS_AUTHKEY`; on Linux it is written to a 0600 temporary file and passed as
`--auth-key=file:<path>`, so it never appears in argv. State is stored at
`$HOME/.ai_home/plugin-state/network.tailscale/` with 0600 permissions.

## Linux notes

`tailscaled` is managed by root on Linux, so `enable`/`disable` use
`sudo -n tailscale up/down` when the unprivileged call is refused. A
passwordless sudo rule for the current user is required; without one the
plugin returns an explicit "需要管理员权限" message instead of claiming success.
Install `tailscale` with the distribution package manager or the official
static package first — `install` only automates Homebrew on macOS.

## Boundaries

- `install` uses Homebrew on macOS; on Linux it asks you to install the
  package yourself. `enable`/`disable` may use `sudo -n`, never an interactive
  password prompt.
- `enable` runs `tailscale up`; if login is required, the result returns a
  structured link so the WebUI can open it (the browser uses its own network).
- The WebUI never silently changes system/browser proxy settings.
- No Electron/WebView/CDP.
