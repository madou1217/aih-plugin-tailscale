#!/usr/bin/env python3
"""AIH network.connector plugin: Tailscale.

Reports direct P2P only when a peer exposes a current direct address; relay
paths are always reported as derp-relay, never as direct P2P.
Version is reported, never enforced.
"""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

PLUGIN_ID = "network.tailscale"
STATE_DIR = Path(os.environ.get("AIH_PLUGIN_STATE_DIR", Path.home() / ".ai_home" / "plugin-state" / PLUGIN_ID))
STATE_FILE = STATE_DIR / "state.json"
DEFAULT_STATE = {"enabled": False, "config": {}, "updatedAt": 0}
APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
AUTOSTART_LABEL = "com.aih.tailscale-userspace"


def log(message):
    print(f"[{PLUGIN_ID}] {message}", file=sys.stderr, flush=True)


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text())
        if isinstance(data, dict):
            merged = dict(DEFAULT_STATE)
            merged.update(data)
            if not isinstance(merged.get("config"), dict):
                merged["config"] = {}
            return merged
    except (OSError, ValueError):
        pass
    return dict(DEFAULT_STATE)


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    state["updatedAt"] = int(time.time() * 1000)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_FILE)


def daemon_env():
    # tailscaled 的控制面连接不等同于安装/登录命令；不能把给 brew/CLI 的
    # 临时 HTTP 代理注入常驻守护进程，否则代理入口停止后守护进程会持续重连失败。
    env = dict(os.environ)
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        env.pop(key, None)
    return env


def proxy_env(state):
    proxy = str((state.get("config") or {}).get("proxyUrl") or "").strip()
    if not proxy:
        return {}
    return {
        "HTTPS_PROXY": proxy,
        "HTTP_PROXY": proxy,
        "ALL_PROXY": proxy,
        "https_proxy": proxy,
        "http_proxy": proxy,
        "all_proxy": proxy,
    }


def on_linux():
    # tailscaled 在 Linux 上由 root 管理；改 prefs（up/down）需要 root 或 operator。
    # enable 直接走 sudo -n，避免无 key 时 `tailscale up` 阻塞等待登录 URL。
    return sys.platform.startswith("linux")


def sudo_available():
    return bool(shutil.which("sudo"))


def sudo_refused(text):
    return "sudo:" in (text or "")


def writable_auth_key_file(auth_key):
    """把 auth key 落到 0600 临时文件，供 `--auth-key=file:` 读取，避免出现在命令行。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    path = STATE_DIR / "authkey.tmp"
    path.write_text(auth_key)
    os.chmod(path, 0o600)
    return path


def run_with_sudo(args, timeout, env=None):
    """Linux 下以 `sudo -n` 执行；配置了代理时通过 `sudo env` 透传。"""
    prefix = [shutil.which("sudo") or "sudo", "-n"]
    if env:
        prefix += ["env"] + [f"{name}={value}" for name, value in env.items()]
    return run(prefix + args, timeout=timeout)


def command_output(out, err, limit=20):
    text = (err or out or "").strip()
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def binary_path():
    found = shutil.which("tailscale")
    if found:
        return found
    return APP_CLI if os.path.isfile(APP_CLI) and os.access(APP_CLI, os.X_OK) else ""


def run(cmd, timeout=20, env=None):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, **(env or {})})
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)


def effective_socket_path(state):
    path = str((state.get("config") or {}).get("socketPath") or "").strip()
    if path:
        return path
    if sys.platform == "darwin":
        return str(Path.home() / ".ai_home" / "run" / "tailscale" / "tailscaled.sock")
    return ""


def socket_args(state):
    path = effective_socket_path(state)
    return ["--socket", path] if path else []


def userspace_paths(state):
    socket_path = effective_socket_path(state) or str(Path.home() / ".ai_home" / "run" / "tailscale" / "tailscaled.sock")
    socket = Path(socket_path)
    state_dir = socket.parent
    return socket, state_dir / "tailscaled.state", state_dir


def tailscaled_binary():
    return shutil.which("tailscaled") or ""


def launchd_plist_path():
    return Path.home() / "Library" / "LaunchAgents" / (AUTOSTART_LABEL + ".plist")


def systemd_unit_path():
    return Path.home() / ".config" / "systemd" / "user" / "aih-tailscaled.service"


def windows_startup_path():
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "aih-tailscaled.cmd"


def launchd_loaded():
    if sys.platform != "darwin":
        return False
    code, _, _ = run(["launchctl", "print", f"gui/{os.getuid()}/{AUTOSTART_LABEL}"], timeout=5)
    return code == 0


def autostart_enabled(state):
    if sys.platform == "darwin":
        return launchd_plist_path().exists() and launchd_loaded()
    if sys.platform.startswith("linux"):
        if not systemd_unit_path().exists():
            return False
        code, out, _ = run(["systemctl", "--user", "is-enabled", "aih-tailscaled"], timeout=8)
        return code == 0 and (out or "").strip() == "enabled"
    if sys.platform.startswith("win"):
        return windows_startup_path().exists()
    return False


def userspace_command(state):
    tailscaled = tailscaled_binary()
    if not tailscaled:
        return []
    socket, state_file, state_dir = userspace_paths(state)
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    return [
        tailscaled,
        "--tun=userspace-networking",
        "--socket", str(socket),
        "--state", str(state_file),
        "--statedir", str(state_dir),
        "--port", "41641",
    ]


def daemon_status(state):
    binary = binary_path()
    if not binary:
        return False
    code, _, _ = run([binary, *socket_args(state), "status", "--json"], timeout=8)
    return code == 0


def stop_manual_userspace(state):
    socket, _, _ = userspace_paths(state)
    code, out, _ = run(["ps", "-axo", "pid=,command="], timeout=8)
    if code != 0:
        return
    for line in (out or "").splitlines():
        if "tailscaled" not in line or "userspace-networking" not in line or str(socket) not in line:
            continue
        try:
            pid = int(line.strip().split(None, 1)[0])
        except (ValueError, IndexError):
            continue
        run(["kill", "-TERM", str(pid)], timeout=5)
    time.sleep(1)


def wait_daemon(state, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if daemon_status(state):
            return True
        time.sleep(0.5)
    return False


def start_userspace_daemon(state):
    if daemon_status(state):
        return True, "守护进程已在运行"
    command = userspace_command(state)
    if not command:
        return False, "未找到 tailscaled，可先安装 tailscale"
    log_path = Path.home() / ".ai_home" / "logs" / "tailscaled-userspace.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab", buffering=0) as log_file:
        subprocess.Popen(
            command,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=daemon_env(),
        )
    if wait_daemon(state):
        return True, "本地守护进程已启动"
    return False, "本地守护进程启动超时，请查看 ~/.ai_home/logs/tailscaled-userspace.log"


def write_launch_agent(state):
    command = userspace_command(state)
    if not command:
        raise RuntimeError("未找到 tailscaled")
    log_path = Path.home() / ".ai_home" / "logs" / "tailscaled-userspace.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": AUTOSTART_LABEL,
        "ProgramArguments": command,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {"HOME": str(Path.home())},
    }
    path = launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        plistlib.dump(plist, handle)
    os.chmod(path, 0o644)
    return path


def enable_autostart(state):
    if not binary_path() or not tailscaled_binary():
        return {"ok": False, "changed": False, "message": "tailscale 尚未安装", "status": status_view(state)}
    try:
        if sys.platform == "darwin":
            path = write_launch_agent(state)
            if launchd_loaded():
                run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], timeout=10)
            stop_manual_userspace(state)
            code, out, err = run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], timeout=15)
            if code != 0:
                return {"ok": False, "changed": False, "message": "launchd 自启安装失败：" + (err or out)[-200:], "status": status_view(state)}
            run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{AUTOSTART_LABEL}"], timeout=10)
            if not wait_daemon(state, 15):
                return {"ok": False, "changed": True, "message": "自启已写入，但守护进程启动超时", "status": status_view(state)}
            return {"ok": True, "changed": True, "message": "已启用开机自启（用户级 LaunchAgent）", "status": status_view(state)}
        if sys.platform.startswith("linux"):
            command = userspace_command(state)
            if not command:
                return {"ok": False, "changed": False, "message": "未找到 tailscaled", "status": status_view(state)}
            unit = "[Unit]\nDescription=AIH Tailscale userspace daemon\n\n[Service]\nExecStart=" + " ".join(command) + "\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n"
            path = systemd_unit_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(unit)
            os.chmod(path, 0o644)
            run(["systemctl", "--user", "daemon-reload"], timeout=10)
            code, out, err = run(["systemctl", "--user", "enable", "--now", "aih-tailscaled"], timeout=20)
            if code != 0:
                return {"ok": False, "changed": False, "message": "systemd 自启安装失败：" + (err or out)[-200:], "status": status_view(state)}
            return {"ok": True, "changed": True, "message": "已启用用户级 systemd 自启", "status": status_view(state)}
        if sys.platform.startswith("win"):
            command = userspace_command(state)
            if not command:
                return {"ok": False, "changed": False, "message": "未找到 tailscaled", "status": status_view(state)}
            path = windows_startup_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("start \"\" /min " + " ".join('"' + part + '"' for part in command) + "\r\n")
            return {"ok": True, "changed": True, "message": "已写入 Windows 登录启动项；下次登录生效", "status": status_view(state)}
        return {"ok": False, "changed": False, "message": "当前平台不支持插件自启", "status": status_view(state)}
    except (OSError, RuntimeError) as exc:
        return {"ok": False, "changed": False, "message": "自启配置失败：" + str(exc)[:200], "status": status_view(state)}


def disable_autostart(state):
    if sys.platform == "darwin":
        path = launchd_plist_path()
        if launchd_loaded():
            run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], timeout=10)
        try:
            path.unlink()
        except OSError:
            pass
        return {"ok": True, "changed": True, "message": "已取消开机自启", "status": status_view(state)}
    if sys.platform.startswith("linux"):
        run(["systemctl", "--user", "disable", "--now", "aih-tailscaled"], timeout=15)
        try:
            systemd_unit_path().unlink()
        except OSError:
            pass
        run(["systemctl", "--user", "daemon-reload"], timeout=10)
        return {"ok": True, "changed": True, "message": "已取消用户级自启", "status": status_view(state)}
    if sys.platform.startswith("win"):
        try:
            windows_startup_path().unlink()
        except OSError:
            pass
        return {"ok": True, "changed": True, "message": "已移除 Windows 登录启动项", "status": status_view(state)}
    return {"ok": False, "changed": False, "message": "当前平台不支持插件自启", "status": status_view(state)}


def binary_version(binary, state):
    if not binary:
        return ""
    code, out, err = run([binary, *socket_args(state), "version"], timeout=10)
    text = out or err
    if code != 0 or not text:
        return ""
    return text.splitlines()[0].strip()[:96]


def read_status(binary, state):
    if not binary:
        return {}
    code, out, _ = run([binary, *socket_args(state), "status", "--json"], timeout=12)
    if code != 0 or not out:
        return {}
    try:
        data = json.loads(out)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def connectivity_from_status(data, running, state):
    if not running:
        return {"mode": "unavailable", "confidence": "observed"}
    peers = data.get("Peer") or {}
    if not isinstance(peers, dict):
        peers = {}
    direct = []
    relayed = []
    for peer in peers.values():
        if not isinstance(peer, dict) or not peer.get("Online"):
            continue
        cur = str(peer.get("CurAddr") or "").strip()
        relay = str(peer.get("Relay") or "").strip()
        if cur:
            direct.append((peer, cur))
        elif relay:
            relayed.append((peer, relay))
    observed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if direct:
        peer, addr = direct[0]
        return {
            "mode": "direct-p2p",
            "confidence": "observed",
            "observedAt": observed,
            "peerId": str(peer.get("ID") or peer.get("PublicKey") or ""),
            "latencyMs": int(peer.get("LatencyMs") or 0),
            "evidence": {"source": "tailscale status --json", "path": "Peer.CurAddr", "transport": "direct"},
        }
    # status 的 CurAddr 在部分版本/用户态 daemon 下不会及时刷新；
    # tailscale ping 直接报告本次探测走的路径，作为更直接的证据。
    ping_peer = None
    for peer in peers.values():
        if isinstance(peer, dict) and peer.get("Online"):
            ping_peer = peer.get("HostName") or peer.get("DNSName") or (peer.get("TailscaleIPs") or [None])[0]
            if ping_peer:
                break
    if ping_peer:
        binary = binary_path()
        code, out, _ = run([binary, *socket_args(state), "ping", "-c", "3", str(ping_peer)], timeout=20)
        if code == 0:
            lines = [line.strip() for line in (out or "").splitlines() if "pong from" in line]
            direct_line = next((line for line in lines if "via DERP" not in line and " via " in line), None)
            if direct_line:
                return {
                    "mode": "direct-p2p",
                    "confidence": "observed",
                    "observedAt": observed,
                    "peerId": str(ping_peer),
                    "evidence": {"source": "tailscale ping", "path": "non-DERP endpoint", "transport": "direct"},
                }
            if any("via DERP" in line for line in lines):
                return {
                    "mode": "derp-relay",
                    "confidence": "observed",
                    "observedAt": observed,
                    "peerId": str(ping_peer),
                    "evidence": {"source": "tailscale ping", "path": "via DERP", "transport": "derp"},
                }
    if relayed:
        peer, relay = relayed[0]
        return {
            "mode": "derp-relay",
            "confidence": "observed",
            "observedAt": observed,
            "peerId": str(peer.get("ID") or peer.get("PublicKey") or ""),
            "evidence": {"source": "tailscale status --json", "path": "Peer.Relay", "transport": "derp"},
        }
    return {"mode": "unknown", "confidence": "observed", "observedAt": observed}


def status_view(state=None):
    state = state or load_state()
    binary = binary_path()
    installed = bool(binary)
    data = read_status(binary, state) if installed else {}
    backend = str(data.get("BackendState") or "").strip()
    running = backend.lower() == "running"
    version = binary_version(binary, state)
    if not installed:
        label = "未安装"
    elif running:
        label = "运行中"
    elif backend:
        label = f"已停止（{backend}）"
    else:
        label = "已停止"
    message = ""
    if running and str((state.get("config") or {}).get("authKey") or "").strip():
        message = "已连接 Tailnet；Auth Key 已保存（不回显）"
    if not installed:
        message = "未找到 tailscale，可执行 install 通过 Homebrew 安装"
    elif not running:
        message = "Tailscale 未运行；可在配置 authKey 后执行 enable"
    return {
        "installed": installed,
        "enabled": running or bool(state.get("enabled")),
        "running": running,
        "state": label,
        "version": version,
        "message": message,
        "autostart": autostart_enabled(state),
        "connectivity": connectivity_from_status(data, running, state),
    }


def describe():
    return {
        "schemaVersion": 1,
        "kind": "describe",
        "connector": {
            "id": "tailscale",
            "name": "Tailscale",
            "description": "Tailnet mesh；直连与 DERP 中继分开报告",
            "transport": "mesh",
            "operations": [
                {"id": "install", "label": "安装 tailscale", "confirmation": "required"},
                {"id": "uninstall", "label": "卸载 tailscale", "confirmation": "required"},
                {"id": "configure", "label": "配置 Auth Key / 代理", "inputKind": "secret", "confirmation": "required"},
                {"id": "enable", "label": "连接 Tailnet", "confirmation": "required"},
                {"id": "disable", "label": "断开 Tailnet", "confirmation": "required"},
                {"id": "autostart-enable", "label": "开机自启（插件守护进程）", "confirmation": "required"},
                {"id": "autostart-disable", "label": "取消开机自启", "confirmation": "required"},
                {"id": "probe", "label": "探测连通路径", "confirmation": "none"},
            ],
            "config": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "authKey": {"type": "string", "description": "Tailscale Auth Key"},
                        "hostname": {"type": "string"},
                        "proxyUrl": {"type": "string", "description": "安装/连接使用的 HTTP(S) 或 SOCKS 代理", "x-aih-proxy": True},
                        "socketPath": {"type": "string", "description": "可选 tailscaled Unix socket 路径；空值使用系统默认 /var/run/tailscaled.socket"},
                    },
                },
                "secretFields": ["authKey", "proxyUrl"],
            },
            "setup": {
                "summary": "使用 Tailscale Auth Key 连接 Tailnet；密钥只提交给本地插件，状态和日志不回显。",
                "steps": [
                    "打开 Tailscale 管理台 → Settings → Keys（使用下方链接）。",
                    "Generate auth key：多台节点选 Reusable，按需设置过期时间和 tags；只需要短时节点可选 Ephemeral。",
                    "复制 tskey-auth-...，粘贴到下方 authKey 字段。",
                    "安装/连接命令连不上 Tailscale 时，在 proxyUrl 点「使用 AIH 本机代理」再执行配置；浏览器里的管理台链接仍使用浏览器自己的网络/系统代理。",
                    "依次点击「配置 Auth Key」→「安装 tailscale」→「连接 Tailnet」。",
                    "需要登录后自动恢复时，点击「开机自启（插件守护进程）」；插件会安装用户级 LaunchAgent/systemd 单元并管理 tailscaled。",
                    "连接后看状态：Peer.CurAddr 或 tailscale ping 的非 DERP endpoint 为直连 P2P；只有 Relay/DERP 时是中继，不会冒充直连。",
                ],
                "links": [
                    {"label": "打开 Tailscale Keys 页面", "url": "https://login.tailscale.com/admin/settings/keys"},
                    {"label": "Tailscale Auth Key 文档", "url": "https://tailscale.com/kb/1085/auth-keys"},
                ],
            },
            "connectivityModes": ["direct-p2p", "derp-relay", "unavailable", "unknown"],
        },
    }


def configure(state, value):
    config = dict(state.get("config") or {})
    if isinstance(value, dict):
        if isinstance(value.get("value"), str):
            config["authKey"] = value["value"].strip()
        for key in ("authKey", "hostname", "proxyUrl", "socketPath"):
            if isinstance(value.get(key), str):
                config[key] = value[key].strip()
    elif isinstance(value, str):
        config["authKey"] = value.strip()
    config = {key: val for key, val in config.items() if val}
    state["config"] = config
    save_state(state)
    return {"ok": True, "changed": bool(config), "message": "配置已保存" if config else "配置为空", "status": status_view(state)}


def install(state):
    if binary_path():
        return {"ok": True, "changed": False, "message": "tailscale 已安装", "status": status_view(state)}
    brew = shutil.which("brew")
    if not brew:
        if on_linux():
            return {"ok": False, "changed": False, "message": "Linux 请用发行版包管理器或官方静态包安装 tailscale 后重试"}
        return {"ok": False, "changed": False, "message": "未找到 Homebrew；请手动安装 tailscale 后重试"}
    code, out, err = run([brew, "install", "tailscale"], timeout=600, env=proxy_env(state))
    output = command_output(out, err)
    if code != 0:
        log(err or out)
        reason = output[-1] if output else "Homebrew 安装失败"
        return {"ok": False, "changed": False, "message": "Homebrew 安装失败：" + reason[:200], "output": output}
    return {"ok": True, "changed": True, "message": "tailscale 安装完成", "output": output, "status": status_view(state)}


def uninstall(state):
    binary = binary_path()
    if not binary:
        return {"ok": True, "changed": False, "message": "tailscale 未安装", "status": status_view(state)}
    brew = shutil.which("brew")
    if not brew:
        return {"ok": False, "changed": False, "message": "未找到 Homebrew；请手动卸载 tailscale"}
    code, out, err = run([brew, "uninstall", "tailscale"], timeout=300, env=proxy_env(state))
    output = command_output(out, err)
    if code != 0:
        log(err or out)
        reason = output[-1] if output else "Homebrew 卸载失败"
        return {"ok": False, "changed": False, "message": "Homebrew 卸载失败：" + reason[:200], "output": output}
    return {"ok": True, "changed": True, "message": "tailscale 已卸载", "output": output, "status": status_view(state)}


def enable(state):
    binary = binary_path()
    if not binary:
        return {"ok": False, "changed": False, "message": "tailscale 尚未安装", "status": status_view(state)}
    config = state.get("config") or {}
    args = [binary, *socket_args(state), "up"]
    hostname = str(config.get("hostname") or "").strip()
    if hostname:
        args += ["--hostname", hostname]
    env = proxy_env(state)
    auth_key = str(config.get("authKey") or "").strip()
    key_file = None
    if not daemon_status(state):
        if autostart_enabled(state):
            if sys.platform == "darwin":
                run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{AUTOSTART_LABEL}"], timeout=10)
            elif sys.platform.startswith("linux"):
                run(["systemctl", "--user", "start", "aih-tailscaled"], timeout=15)
            wait_daemon(state, 12)
        if not daemon_status(state):
            started, detail = start_userspace_daemon(state)
            if not started:
                return {"ok": False, "changed": False, "message": detail, "status": status_view(state)}
    if auth_key:
        # file: 形式让 key 不出现在进程命令行；--reset 让上一轮的 hostname 等设置可覆盖。
        key_file = writable_auth_key_file(auth_key)
        args += ["--auth-key=file:" + str(key_file), "--reset"]
    try:
        if on_linux():
            if not sudo_available():
                return {"ok": False, "changed": False, "message": "需要管理员权限：请在终端执行 sudo tailscale up 后重试", "status": status_view(state)}
            code, out, err = run_with_sudo(args, timeout=90, env=env)
        else:
            code, out, err = run(args, timeout=90, env=env)
    finally:
        if key_file is not None:
            try:
                key_file.unlink()
            except OSError:
                pass
    output = command_output(out, err)
    if code != 0:
        text = (err or out).strip()
        log(text)
        # Login URLs are safe to surface; keys never are.
        message = "连接失败；如需登录，请在终端执行 tailscale up 完成授权"
        links = []
        if on_linux() and sudo_refused(text):
            message = "需要管理员权限：请在终端执行 sudo tailscale up，或为当前用户配置免密 sudo 后重试"
        elif "https://" in text:
            last = text.splitlines()[-1].strip()
            url = last[last.find("https://"):].split()[0].strip("\"'")
            message = "需要登录授权：请打开下方登录链接完成授权后重试"
            if url:
                links = [{"label": "打开 Tailscale 登录授权", "url": url}]
        return {"ok": False, "changed": False, "message": message, "output": output, "links": links, "status": status_view(state)}
    state["enabled"] = True
    save_state(state)
    return {"ok": True, "changed": True, "message": "已连接 Tailnet", "status": status_view(state)}


def disable(state):
    binary = binary_path()
    if not binary:
        return {"ok": False, "changed": False, "message": "tailscale 尚未安装", "status": status_view(state)}
    before = status_view(state).get("running")
    env = proxy_env(state)
    code, out, err = run([binary, "down"], timeout=30, env=env)
    if code != 0 and on_linux() and sudo_available():
        # operator 已配置时普通用户即可 down；未配置时退回 sudo -n。
        code, out, err = run_with_sudo([binary, "down"], timeout=30, env=env)
    output = command_output(out, err)
    if code != 0:
        log(err or out)
        message = "断开失败"
        text = err or out
        if on_linux() and (sudo_refused(text) or "Access denied" in text):
            message = "需要管理员权限：请在终端执行 sudo tailscale down，或为当前用户配置免密 sudo 后重试"
        return {"ok": False, "changed": False, "message": message, "output": output, "status": status_view(state)}
    state["enabled"] = False
    save_state(state)
    return {"ok": True, "changed": bool(before), "message": "已断开 Tailnet", "status": status_view(state)}


def operate(action, value):
    state = load_state()
    if action == "install":
        return install(state)
    if action == "uninstall":
        return uninstall(state)
    if action == "configure":
        return configure(state, value)
    if action == "enable":
        return enable(state)
    if action == "disable":
        return disable(state)
    if action == "autostart-enable":
        return enable_autostart(state)
    if action == "autostart-disable":
        return disable_autostart(state)
    if action == "probe":
        return {"ok": True, "changed": False, "message": "探测完成", "status": status_view(state)}
    return {"ok": False, "changed": False, "message": f"未声明的操作：{action}"}


def handle_invoke(value):
    if not isinstance(value, dict):
        return describe()
    kind = value.get("kind")
    if kind == "describe":
        return describe()
    if kind == "status":
        return {"schemaVersion": 1, "kind": "status", "status": status_view()}
    if kind == "operate":
        return operate(str(value.get("action") or ""), value.get("input"))
    return describe()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if method == "invoke":
            result = handle_invoke(params.get("value"))
        elif method == "prepare":
            result = {"state": "prepared"}
        elif method == "activate":
            result = {"state": "active"}
        elif method == "dispose":
            result = {"state": "disposed"}
        elif method == "ping":
            result = {"ok": True}
        elif method == "shutdown":
            result = {"state": "disposed"}
        else:
            result = {"ok": False, "message": f"unknown method: {method}"}
        print(json.dumps({"id": request_id, "result": result}, ensure_ascii=False), flush=True)
        if method in ("dispose", "shutdown"):
            return


if __name__ == "__main__":
    main()
