#!/usr/bin/env python3
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "0.0.0.0"
PORT = 9860
OWNER = "AbeyyN"
ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
RUNNER_ROOT = Path("/home/abeyy/actions-runner")
HERMES_HOME = Path("/home/abeyy/.hermes")
HERMES_WORKSPACE = Path("/home/abeyy/hermes-workspaces/986-ci-doctor")
POLL_SECONDS = 15
WAIT_SUSPECT_SECONDS = 120
START_SUSPECT_SECONDS = 90

PREFERRED_REPO_ORDER = [
    "XHomeNetwork",
    "SK-Gong-Kapas",
    "XMA7",
    "XSAHub",
    "X7-Core-Academy",
    "OpenWRT---AbeyyWRT",
    "AppCompiler-PWA",
]

GH_ENV = os.environ.copy()
GH_ENV.pop("GH_TOKEN", None)
GH_ENV.pop("GITHUB_TOKEN", None)

cache_lock = threading.Lock()
github_cache = {"updated": 0, "projects": [], "error": None}
prev_cpu = None
prev_net = None
prev_net_time = None


def run(cmd, timeout=10, cwd=None):
    try:
        return subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=GH_ENV,
            cwd=cwd,
        )
    except Exception:
        return None


def gh_json(path):
    for delay in (0, 1, 2):
        if delay:
            time.sleep(delay)
        p = run(["gh", "api", path], timeout=15)
        if p and p.returncode == 0 and p.stdout.strip():
            try:
                return json.loads(p.stdout)
            except Exception:
                pass
    return {}


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def seconds_since(value):
    ts = parse_ts(value)
    return max(0, int(time.time() - ts)) if ts else 0


def local_listener_alive(runner_dir):
    path = f"{runner_dir}/bin/Runner.Listener run"
    p = run(["pgrep", "-f", path], timeout=2)
    return bool(p and p.returncode == 0 and p.stdout.strip())


def parse_runner_config(path):
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception:
        return None
    url = str(data.get("gitHubUrl") or "").rstrip("/")
    marker = f"github.com/{OWNER}/"
    if marker not in url:
        return None
    repo = url.split(marker, 1)[1].split("/", 1)[0]
    name = str(data.get("agentName") or path.parent.name)
    return {
        "repo": repo,
        "name": name,
        "dir": str(path.parent),
        "work_folder": str(data.get("workFolder") or ""),
        "local_listener": local_listener_alive(str(path.parent)),
    }


def discover_local_runners():
    grouped = {}
    if not RUNNER_ROOT.exists():
        return grouped
    for config in sorted(RUNNER_ROOT.glob("*/.runner")):
        item = parse_runner_config(config)
        if not item:
            continue
        grouped.setdefault(item["repo"], []).append(item)
    return grouped


def ordered_repos(grouped):
    known = [r for r in PREFERRED_REPO_ORDER if r in grouped]
    extra = sorted(r for r in grouped if r not in PREFERRED_REPO_ORDER)
    return known + extra


def slim_run(r):
    if not r:
        return None
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "number": r.get("run_number"),
        "status": r.get("status"),
        "conclusion": r.get("conclusion"),
        "branch": r.get("head_branch"),
        "event": r.get("event"),
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
        "url": r.get("html_url"),
    }


def slim_job(j):
    if not j:
        return None
    steps = []
    for s in j.get("steps") or []:
        steps.append({
            "name": s.get("name"),
            "status": s.get("status"),
            "conclusion": s.get("conclusion"),
            "number": s.get("number"),
            "started_at": s.get("started_at"),
            "completed_at": s.get("completed_at"),
        })
    return {
        "id": j.get("id"),
        "name": j.get("name"),
        "status": j.get("status"),
        "conclusion": j.get("conclusion"),
        "runner_name": j.get("runner_name"),
        "runner_id": j.get("runner_id"),
        "labels": j.get("labels") or [],
        "started_at": j.get("started_at"),
        "completed_at": j.get("completed_at"),
        "url": j.get("html_url"),
        "steps": steps,
    }


def active_job_for_run(repo, run_id):
    data = gh_json(f"repos/{OWNER}/{repo}/actions/runs/{run_id}/jobs?per_page=100")
    jobs = data.get("jobs") or []
    active = next(
        (j for j in jobs if j.get("status") in ("in_progress", "queued", "pending", "waiting")),
        None,
    )
    if not active and jobs:
        active = jobs[-1]
    return slim_job(active)


def runner_for_job(runners, job):
    if job and job.get("runner_name"):
        return next((r for r in runners if r.get("name") == job.get("runner_name")), None)
    return None


def classify_action(run_data, job, runners):
    now_age = seconds_since(run_data.get("created_at"))
    assigned = runner_for_job(runners, job)
    online_healthy = [r for r in runners if r.get("online") and r.get("local_listener")]
    idle_healthy = [r for r in online_healthy if not r.get("busy")]
    any_busy = any(r.get("busy") for r in online_healthy)

    state = "QUEUED"
    anomaly = False
    reason = None
    current_step = None

    if run_data.get("status") == "queued":
        if job and job.get("runner_name"):
            state = "ASSIGNED"
        elif idle_healthy:
            state = "WAITING_FOR_RUNNER"
            if now_age >= WAIT_SUSPECT_SECONDS:
                state = "WAITING_RUNNER_SUSPECT"
                anomaly = True
                reason = f"healthy runner available but job waiting {now_age}s"
        elif any_busy:
            state = "WAITING_RUNNER_BUSY"
        else:
            state = "WAITING_FOR_RUNNER"
    elif run_data.get("status") == "in_progress":
        if not job:
            state = "STARTING"
        elif job.get("status") == "queued" and not job.get("runner_name"):
            state = "WAITING_FOR_RUNNER"
        elif job.get("status") == "queued":
            state = "ASSIGNED"
        else:
            steps = job.get("steps") or []
            active_step = next((s for s in steps if s.get("status") == "in_progress"), None)
            if not active_step:
                completed = [s for s in steps if s.get("status") == "completed"]
                active_step = completed[-1] if completed else None
            current_step = active_step.get("name") if active_step else None
            if steps:
                state = "RUNNING"
            else:
                start_age = seconds_since(
                    job.get("started_at") or run_data.get("updated_at") or run_data.get("created_at")
                )
                state = "STARTING"
                if job.get("runner_name") and start_age >= START_SUSPECT_SECONDS:
                    state = "STUCK_STARTING"
                    anomaly = True
                    reason = f"assigned to {job.get('runner_name')} but no first step for {start_age}s"

    if assigned:
        if not assigned.get("online"):
            anomaly = True
            reason = reason or f"assigned runner {assigned.get('name')} is offline"
        elif not assigned.get("local_listener"):
            anomaly = True
            reason = reason or f"local listener missing for {assigned.get('name')}"
    elif not runners:
        anomaly = True
        reason = reason or "no local runner registered for repository"
    elif not online_healthy and state not in ("RUNNING",):
        anomaly = True
        reason = reason or "no online local runner listener available"

    return {
        "state": state,
        "anomaly": anomaly,
        "reason": reason,
        "age_seconds": now_age,
        "current_step": current_step,
    }


def project_snapshot(repo, local_entries):
    runners_data = gh_json(f"repos/{OWNER}/{repo}/actions/runners?per_page=100")
    runs = gh_json(f"repos/{OWNER}/{repo}/actions/runs?per_page=20")
    api_by_name = {r.get("name"): r for r in runners_data.get("runners", [])}

    runners = []
    for local in local_entries:
        raw = api_by_name.get(local.get("name")) or {}
        runners.append({
            "name": local.get("name"),
            "dir": local.get("dir"),
            "work_folder": local.get("work_folder"),
            "online": raw.get("status") == "online",
            "busy": bool(raw.get("busy")),
            "status": raw.get("status") or "offline",
            "labels": [x.get("name") for x in raw.get("labels", [])],
            "local_listener": bool(local.get("local_listener")),
        })

    workflow_runs = runs.get("workflow_runs", [])
    active_raw = [r for r in workflow_runs if r.get("status") in ("queued", "in_progress")][:6]
    active = []
    for r in active_raw:
        sr = slim_run(r)
        job = active_job_for_run(repo, r.get("id"))
        sr["job"] = job
        sr["action"] = classify_action(sr, job, runners)
        active.append(sr)

    running = [r for r in active if r.get("status") == "in_progress"]
    queued = [r for r in active if r.get("status") == "queued"]
    failed = next(
        (slim_run(r) for r in workflow_runs if r.get("status") == "completed" and r.get("conclusion") == "failure"),
        None,
    )
    latest = slim_run(workflow_runs[0]) if workflow_runs else None
    anomalies = [r for r in active if (r.get("action") or {}).get("anomaly")]

    return {
        "repo": repo,
        "runners": runners,
        "running": running,
        "queued": queued,
        "active": active,
        "anomalies": anomalies,
        "failed": failed,
        "latest": latest,
    }


def github_worker():
    while True:
        err = None
        projects = []
        try:
            grouped = discover_local_runners()
            repos = ordered_repos(grouped)
            with ThreadPoolExecutor(max_workers=max(1, len(repos))) as ex:
                futs = {ex.submit(project_snapshot, repo, grouped[repo]): repo for repo in repos}
                results = {}
                for fut in as_completed(futs):
                    repo = futs[fut]
                    try:
                        results[repo] = fut.result()
                    except Exception as e:
                        results[repo] = {
                            "repo": repo,
                            "runners": [{
                                **local,
                                "online": False,
                                "busy": False,
                                "status": "error",
                                "labels": [],
                            } for local in grouped.get(repo, [])],
                            "running": [],
                            "queued": [],
                            "active": [],
                            "anomalies": [],
                            "failed": None,
                            "latest": None,
                        }
                        err = str(e)
                projects = [results[r] for r in repos if r in results]
        except Exception as e:
            err = str(e)
        with cache_lock:
            github_cache["updated"] = time.time()
            github_cache["projects"] = projects
            github_cache["error"] = err
        time.sleep(POLL_SECONDS)


def cpu_percent():
    global prev_cpu
    try:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        vals = list(map(int, fields))
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        now = (idle, total)
        if prev_cpu is None:
            prev_cpu = now
            return 0.0
        di = idle - prev_cpu[0]
        dt = total - prev_cpu[1]
        prev_cpu = now
        return round(max(0.0, min(100.0, (1.0 - di / dt) * 100.0 if dt else 0.0)), 1)
    except Exception:
        return 0.0


def mem_stats():
    vals = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            vals[k] = int(v.strip().split()[0]) * 1024
    except Exception:
        pass
    total = vals.get("MemTotal", 1)
    avail = vals.get("MemAvailable", 0)
    used = max(0, total - avail)
    return total, used, avail


def net_stats():
    global prev_net, prev_net_time
    rows = []
    try:
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            if ":" not in line:
                continue
            iface, rest = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            parts = rest.split()
            rows.append((iface, int(parts[0]), int(parts[8])))
    except Exception:
        rows = []
    if not rows:
        return "none", 0, 0, 0.0, 0.0
    iface, rx, tx = max(rows, key=lambda x: x[1] + x[2])
    now = time.time()
    rx_rate = tx_rate = 0.0
    if prev_net and prev_net_time and prev_net[0] == iface:
        dt = max(0.001, now - prev_net_time)
        rx_rate = max(0.0, (rx - prev_net[1]) / dt)
        tx_rate = max(0.0, (tx - prev_net[2]) / dt)
    prev_net = (iface, rx, tx)
    prev_net_time = now
    return iface, rx, tx, rx_rate, tx_rate


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def github_latency():
    start = time.perf_counter()
    try:
        s = socket.create_connection(("github.com", 443), timeout=2.5)
        s.close()
        return True, round((time.perf_counter() - start) * 1000, 1)
    except Exception:
        return False, None


def tmux_sessions():
    p = run(["tmux", "list-sessions", "-F", "#S"], timeout=3)
    if not p or p.returncode != 0:
        return []
    return [x.strip() for x in p.stdout.splitlines() if x.strip()]


def command_text(cmd, timeout=5):
    p = run(cmd, timeout=timeout)
    if not p or p.returncode != 0:
        return ""
    return p.stdout.strip()


def hermes_snapshot():
    version_text = command_text(["hermes", "--version"], timeout=5)
    version_line = version_text.splitlines()[0] if version_text else ""
    provider = command_text(["hermes", "config", "get", "model.provider"], timeout=5)
    model = command_text(["hermes", "config", "get", "model.default"], timeout=5)
    commit = command_text(
        ["git", "-C", str(HERMES_HOME / "hermes-agent"), "rev-parse", "--short", "HEAD"],
        timeout=3,
    )
    installed = bool(version_line)
    workspace = HERMES_WORKSPACE.exists()
    wrapper = Path("/home/abeyy/bin/hermes986").exists()
    configured = bool(provider and model)
    ready = installed and workspace and wrapper and configured
    return {
        "installed": installed,
        "ready": ready,
        "version": version_line or "not installed",
        "provider": provider or "not configured",
        "model": model or "not configured",
        "commit": commit or "-",
        "workspace": str(HERMES_WORKSPACE),
        "workspace_ready": workspace,
        "wrapper_ready": wrapper,
    }


def system_snapshot():
    cpu = cpu_percent()
    mem_total, mem_used, mem_avail = mem_stats()
    disk = shutil.disk_usage("/")
    try:
        load1 = float(os.getloadavg()[0])
    except Exception:
        load1 = 0.0
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        uptime = 0
    iface, rx, tx, rx_rate, tx_rate = net_stats()
    online, latency = github_latency()
    return {
        "hostname": socket.gethostname(),
        "cpu_percent": cpu,
        "cpu_threads": os.cpu_count() or 0,
        "load1": round(load1, 2),
        "memory": {
            "total": mem_total,
            "used": mem_used,
            "available": mem_avail,
            "percent": round(mem_used / mem_total * 100, 1) if mem_total else 0,
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": round(disk.used / disk.total * 100, 1) if disk.total else 0,
        },
        "uptime_seconds": int(uptime),
        "network": {
            "interface": iface,
            "local_ip": local_ip(),
            "rx_bytes": rx,
            "tx_bytes": tx,
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
            "internet": online,
            "github_latency_ms": latency,
        },
        "tmux": tmux_sessions(),
    }


def full_snapshot():
    sys = system_snapshot()
    hermes = hermes_snapshot()
    with cache_lock:
        projects = json.loads(json.dumps(github_cache.get("projects", [])))
        gh_updated = github_cache.get("updated", 0)
        gh_error = github_cache.get("error")

    runners = [r for p in projects for r in p.get("runners", [])]
    online_runners = sum(1 for r in runners if r.get("online"))
    local_listeners = sum(1 for r in runners if r.get("local_listener"))
    busy_runners = sum(1 for r in runners if r.get("busy"))
    running = sum(len(p.get("running", [])) for p in projects)
    queued = sum(len(p.get("queued", [])) for p in projects)
    anomalies = sum(len(p.get("anomalies", [])) for p in projects)
    waiting = sum(
        1
        for p in projects
        for r in p.get("active", [])
        if (r.get("action") or {}).get("state", "").startswith("WAITING")
    )
    starting = sum(
        1
        for p in projects
        for r in p.get("active", [])
        if (r.get("action") or {}).get("state") in ("ASSIGNED", "STARTING", "STUCK_STARTING")
    )
    failures = sum(1 for p in projects if p.get("failed"))

    state = "OK"
    reasons = []
    if runners and online_runners < len(runners):
        state = "CRITICAL"
        reasons.append(f"{len(runners) - online_runners} runner offline")
    if runners and local_listeners < len(runners):
        state = "CRITICAL"
        reasons.append(f"{len(runners) - local_listeners} local listener missing")
    if not sys["network"]["internet"]:
        state = "CRITICAL"
        reasons.append("GitHub network down")
    if sys["disk"]["percent"] >= 92 or sys["memory"]["available"] < 750 * 1024 * 1024:
        state = "CRITICAL"
        reasons.append("resource critical")
    if state == "OK" and anomalies:
        state = "WARNING"
        reasons.append(f"{anomalies} runner/action anomaly")
    if state == "OK" and (
        sys["disk"]["percent"] >= 85
        or sys["memory"]["available"] < 1500 * 1024 * 1024
        or sys["load1"] > 4.5
    ):
        state = "WARNING"
        reasons.append("resource warning")
    if state == "OK" and not hermes["ready"]:
        state = "WARNING"
        reasons.append("Hermes not fully configured")

    return {
        "timestamp": time.time(),
        "system": sys,
        "hermes": hermes,
        "github": {
            "updated": gh_updated,
            "age_seconds": round(max(0, time.time() - gh_updated), 1) if gh_updated else None,
            "error": gh_error,
            "poll_seconds": POLL_SECONDS,
        },
        "summary": {
            "repo_total": len(projects),
            "runner_total": len(runners),
            "runner_online": online_runners,
            "local_listeners": local_listeners,
            "runner_busy": busy_runners,
            "running": running,
            "queued": queued,
            "waiting_runner": waiting,
            "starting": starting,
            "anomalies": anomalies,
            "recent_failures": failures,
        },
        "health": {"state": state, "reasons": reasons},
        "projects": projects,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_bytes(self, body, ctype="text/plain; charset=utf-8", status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if not INDEX.exists():
                self.send_bytes(b"dashboard index missing", status=500)
                return
            self.send_bytes(INDEX.read_bytes(), "text/html; charset=utf-8")
            return
        if self.path.startswith("/api/status"):
            body = json.dumps(full_snapshot(), separators=(",", ":")).encode()
            self.send_bytes(body, "application/json; charset=utf-8")
            return
        if self.path == "/healthz":
            self.send_bytes(b"ok\n")
            return
        self.send_bytes(b"not found\n", status=404)


if __name__ == "__main__":
    threading.Thread(target=github_worker, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Fujitsu dashboard v3 listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
