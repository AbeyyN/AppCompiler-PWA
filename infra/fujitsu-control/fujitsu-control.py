#!/usr/bin/env python3
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

HOST = "127.0.0.1"
PORT = 9861
HOME = Path.home()
CONTROL_HOME = HOME / "fujitsu-control"
STATE_FILE = CONTROL_HOME / "state.json"
LOG_FILE = CONTROL_HOME / "control.log"
DASHBOARD_HOME = HOME / "fujitsu-dashboard"
HERMES_HOME = HOME / ".hermes"
HERMES_WORKSPACE = HOME / "hermes-workspaces" / "986-ci-doctor"
PATH = f"{HOME}/.local/bin:{HOME}/bin:" + os.environ.get("PATH", "")
STOP = threading.Event()
STATE_LOCK = threading.Lock()
STATE = {}


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg):
    CONTROL_HOME.mkdir(parents=True, exist_ok=True)
    line = f"[{now_iso()}] {msg}\n"
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)


def run(cmd, timeout=15, cwd=None):
    env = os.environ.copy()
    env["PATH"] = PATH
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    try:
        return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, cwd=cwd, env=env)
    except Exception:
        return None


def tmux_has(name):
    p = run(["tmux", "has-session", "-t", name], timeout=3)
    return bool(p and p.returncode == 0)


def http_ok(url, expected=None, timeout=3):
    try:
        with urlopen(url, timeout=timeout) as r:
            body = r.read(512).decode("utf-8", "replace")
            if r.status != 200:
                return False
            return expected in body if expected else True
    except Exception:
        return False


def dashboard_health():
    return http_ok("http://127.0.0.1:9860/healthz", "ok", 3)


def restart_dashboard():
    server = DASHBOARD_HOME / "dashboard_server.py"
    if not server.exists():
        log("dashboard restart skipped: dashboard_server.py missing")
        return False
    log("dashboard unhealthy; starting local dashboard service")
    run(["tmux", "kill-session", "-t", "fujitsu-dashboard"], timeout=3)
    p = run(["pgrep", "-f", "dashboard_server\\.py"], timeout=3)
    if p and p.returncode == 0:
        for raw in p.stdout.split():
            try:
                pid = int(raw)
                if pid != os.getpid():
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
    time.sleep(1)
    cmd = (
        f"export PATH='{PATH}'; cd '{DASHBOARD_HOME}'; "
        f"exec python3 dashboard_server.py >> '{DASHBOARD_HOME}/dashboard.log' 2>&1"
    )
    p = run(["tmux", "new-session", "-d", "-s", "fujitsu-dashboard", cmd], timeout=5)
    if not p or p.returncode != 0:
        log("dashboard tmux start failed")
        return False
    for _ in range(20):
        if dashboard_health():
            log("dashboard recovered")
            return True
        time.sleep(1)
    log("dashboard recovery timed out")
    return False


def read_env_keys(path):
    keys = set()
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            keys.add(line.split("=", 1)[0].strip())
    except Exception:
        pass
    return keys


def hermes_command():
    candidates = [HOME / ".local/bin/hermes", HERMES_HOME / "hermes-agent/venv/bin/hermes"]
    for p in candidates:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which("hermes", path=PATH)


def hermes_config_get(key):
    h = hermes_command()
    if not h:
        return ""
    p = run([h, "config", "get", key], timeout=8)
    return p.stdout.strip() if p and p.returncode == 0 else ""


def hermes_snapshot():
    h = hermes_command()
    env_keys = read_env_keys(HERMES_HOME / ".env")
    version = ""
    if h:
        p = run([h, "--version"], timeout=8)
        if p and p.returncode == 0:
            version = (p.stdout.strip().splitlines() or [""])[0]
    provider = hermes_config_get("model.provider")
    model = hermes_config_get("model.default")
    telegram = "TELEGRAM_BOT_TOKEN" in env_keys and (
        "TELEGRAM_ALLOWED_USERS" in env_keys or "GATEWAY_ALLOWED_USERS" in env_keys
    )
    provider_keys = {
        "OPENROUTER_API_KEY", "FIREWORKS_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY", "KIMI_API_KEY", "KIMI_CODING_API_KEY",
        "MINIMAX_API_KEY", "DASHSCOPE_API_KEY", "HF_TOKEN", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "XAI_API_KEY", "NOVITA_API_KEY", "DEEPSEEK_API_KEY", "NVIDIA_API_KEY", "XIAOMI_API_KEY",
        "AI_GATEWAY_API_KEY", "OLLAMA_API_KEY", "COPILOT_GITHUB_TOKEN"
    }
    auth_present = (HERMES_HOME / "auth.json").exists()
    provider_ready = bool((provider and model) or (env_keys & provider_keys) or auth_present)
    gateway_alive = tmux_has("hermes-gateway") or bool(
        (lambda p: p and p.returncode == 0 and p.stdout.strip())(
            run(["pgrep", "-f", "hermes.*gateway"], timeout=3)
        )
    )
    return {
        "installed": bool(h and version),
        "version": version or "not installed",
        "workspace_ready": HERMES_WORKSPACE.exists(),
        "provider": provider or "auto/env/oauth",
        "model": model or "auto/default",
        "provider_ready": provider_ready,
        "telegram_configured": telegram,
        "gateway_alive": gateway_alive,
        "runtime_ready": bool(h and version and HERMES_WORKSPACE.exists() and provider_ready),
    }


def start_hermes_gateway(snapshot):
    if not snapshot.get("runtime_ready") or not snapshot.get("telegram_configured"):
        return False
    if snapshot.get("gateway_alive"):
        return True
    h = hermes_command()
    if not h:
        return False
    logs = HERMES_HOME / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    cmd = (
        f"export PATH='{PATH}'; cd '{HERMES_WORKSPACE}'; "
        f"exec '{h}' gateway run >> '{logs}/gateway.log' 2>&1"
    )
    p = run(["tmux", "new-session", "-d", "-s", "hermes-gateway", cmd], timeout=5)
    if p and p.returncode == 0:
        log("Hermes Telegram gateway started under FUJITSU-CONTROL")
        time.sleep(3)
        return True
    log("Hermes gateway start failed")
    return False


def update_state():
    dash = dashboard_health()
    if not dash:
        dash = restart_dashboard()
    hermes = hermes_snapshot()
    if hermes.get("runtime_ready") and hermes.get("telegram_configured") and not hermes.get("gateway_alive"):
        start_hermes_gateway(hermes)
        hermes = hermes_snapshot()
    state = {
        "name": "FUJITSU-CONTROL",
        "version": "1.0.0",
        "updated": now_iso(),
        "pid": os.getpid(),
        "dashboard": {"healthy": dash, "port": 9860, "tmux": tmux_has("fujitsu-dashboard")},
        "hermes": hermes,
        "control": {"healthy": True, "port": PORT, "tmux": tmux_has("fujitsu-control")},
    }
    with STATE_LOCK:
        STATE.clear()
        STATE.update(state)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def monitor_loop():
    last_dash = None
    last_gateway = None
    while not STOP.is_set():
        try:
            update_state()
            with STATE_LOCK:
                dash = STATE.get("dashboard", {}).get("healthy")
                gateway = STATE.get("hermes", {}).get("gateway_alive")
            if dash != last_dash:
                log(f"dashboard healthy={dash}")
                last_dash = dash
            if gateway != last_gateway:
                log(f"Hermes gateway alive={gateway}")
                last_gateway = gateway
        except Exception as e:
            log(f"monitor error: {type(e).__name__}: {e}")
        STOP.wait(10)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if self.path in ("/", "/state", "/api/state"):
            with STATE_LOCK:
                data = json.loads(json.dumps(STATE))
            self.send_json(data)
            return
        self.send_json({"error": "not found"}, 404)


def main():
    CONTROL_HOME.mkdir(parents=True, exist_ok=True)
    log("FUJITSU-CONTROL starting; local control-plane is not a GitHub runner")
    update_state()
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    finally:
        STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
