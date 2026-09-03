#!/usr/bin/env python3
import json, os, shutil, socket, subprocess, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOME = Path('/home/abeyy')
ROOT = HOME / 'actions-runner'
SCHED = HOME / '.local/share/redmi-scheduler'
BIND = os.getenv('REDMI_TELEMETRY_BIND', '0.0.0.0')
PORT = int(os.getenv('REDMI_TELEMETRY_PORT', '9862'))
VERSION = 'redmi-telemetry-3.0'
POLL = 8
LOCK = threading.RLock()
DATA = {
    'version': VERSION,
    'source': 'redmi-build',
    'dashboard_owner': 'rapsi-nas',
    'system': {}, 'summary': {}, 'projects': [], 'runners': [],
    'runs': [], 'queue': [], 'history': [], 'network': {}, 'hermes': {},
    'github': {'poll_seconds': POLL, 'age_seconds': None},
    'health': {'state': 'STARTING', 'reasons': ['telemetry warming up']},
    'error': None,
}
PREV_CPU = None
PREV_NET = None
PREV_NET_TIME = None
GITHUB_AT = 0.0


def cmd(args, timeout=12):
    try:
        p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 124, '', str(e)


def gh(args, timeout=18):
    rc, out, err = cmd(['env', '-u', 'GH_TOKEN', '-u', 'GITHUB_TOKEN', 'gh'] + args, timeout)
    if rc:
        raise RuntimeError((err or out or f'gh rc={rc}').strip()[:240])
    return json.loads(out or 'null')


def dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None


def age(s):
    x = dt(s)
    if not x:
        return 0
    return max(0, int((datetime.now(timezone.utc) - x.astimezone(timezone.utc)).total_seconds()))


def repo_from(url):
    try:
        p = urlparse(url or '')
        parts = [x for x in p.path.split('/') if x]
        if p.hostname == 'github.com' and len(parts) > 1:
            return f'{parts[0]}/{parts[1].removesuffix(".git")}'
    except Exception:
        pass
    return None


def ps_rows():
    rc, out, _ = cmd(['ps', '-eo', 'pid=,pcpu=,pmem=,etime=,args='], 4)
    rows = []
    if rc:
        return rows
    for line in out.splitlines():
        p = line.strip().split(None, 4)
        if len(p) != 5:
            continue
        try:
            rows.append((int(p[0]), float(p[1]), float(p[2]), p[3], p[4]))
        except Exception:
            pass
    return rows


def cpu_percent():
    global PREV_CPU
    try:
        vals = [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
        idle = vals[3] + vals[4]
        total = sum(vals)
        if PREV_CPU is None:
            PREV_CPU = (idle, total)
            return 0.0
        di, dtot = idle - PREV_CPU[0], total - PREV_CPU[1]
        PREV_CPU = (idle, total)
        return round(max(0.0, min(100.0, 100.0 * (1.0 - di / dtot))), 1) if dtot else 0.0
    except Exception:
        return 0.0


def system_snapshot():
    mem = {}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                mem[k] = int(v.strip().split()[0]) * 1024
    except Exception:
        pass
    mt, ma = mem.get('MemTotal', 0), mem.get('MemAvailable', 0)
    du = shutil.disk_usage('/')
    try:
        l1, l5, l15 = os.getloadavg()
    except Exception:
        l1 = l5 = l15 = 0.0
    try:
        uptime = int(float(Path('/proc/uptime').read_text().split()[0]))
    except Exception:
        uptime = 0
    temp = None
    for f in Path('/sys/class/thermal').glob('thermal_zone*/temp'):
        try:
            x = float(f.read_text().strip())
            x = x / 1000 if x > 1000 else x
            if 0 < x < 150:
                temp = max(temp or 0.0, x)
        except Exception:
            pass
    rc, tmux_out, _ = cmd(['tmux', 'list-sessions', '-F', '#S'], 3)
    tmux = tmux_out.splitlines() if rc == 0 else []
    return {
        'hostname': socket.gethostname(),
        'cpu_percent': cpu_percent(), 'cpu_threads': os.cpu_count() or 0,
        'load1': round(l1, 2), 'load5': round(l5, 2), 'load15': round(l15, 2),
        'memory': {
            'percent': round((mt - ma) / mt * 100, 1) if mt else 0.0,
            'available': ma, 'total': mt, 'used': max(0, mt - ma),
        },
        'disk': {
            'percent': round(du.used / du.total * 100, 1) if du.total else 0.0,
            'free': du.free, 'total': du.total, 'used': du.used,
        },
        'temperature_c': round(temp, 1) if temp is not None else None,
        'uptime_seconds': uptime,
        'processes': len(ps_rows()),
        'tmux': tmux,
    }


def local_runners(rows):
    out = []
    if not ROOT.exists():
        return out
    for d in sorted(ROOT.iterdir(), key=lambda x: x.name.lower()):
        f = d / '.runner'
        if not f.exists():
            continue
        try:
            cfg = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception:
            cfg = {}
        marker = f'{d}/bin/Runner.Listener run'
        proc = next((x for x in rows if marker in x[4]), None)
        repo = repo_from(cfg.get('gitHubUrl'))
        out.append({
            'directory': d.name,
            'name': cfg.get('agentName', '?'),
            'repo': repo.split('/', 1)[-1] if repo else d.name,
            'repo_full': repo,
            'work': cfg.get('workFolder', '?'),
            'local_listener': bool(proc), 'pid': proc[0] if proc else None,
            'online': bool(proc), 'busy': False, 'status': 'online' if proc else 'offline',
        })
    return out


def apply_github_runner_status(rr):
    by_repo = {}
    for r in rr:
        if r.get('repo_full'):
            by_repo.setdefault(r['repo_full'], []).append(r)
    errors = []
    for repo, items in by_repo.items():
        try:
            payload = gh(['api', f'repos/{repo}/actions/runners?per_page=100']) or {}
            remote = {x.get('name'): x for x in payload.get('runners', [])}
            for r in items:
                x = remote.get(r.get('name'))
                if x:
                    r['status'] = x.get('status') or 'offline'
                    r['online'] = x.get('status') == 'online'
                    r['busy'] = bool(x.get('busy'))
                else:
                    r['status'] = 'missing'
                    r['online'] = False
        except Exception as e:
            errors.append(f'{repo}: {e}')
    return errors


def weight(name):
    n = (name or '').lower()
    if any(x in n for x in ('build', 'compile', 'gradle', 'assemble', 'bundle', 'apk', 'aab', 'openwrt')):
        return 6.0, 300
    if 'test' in n:
        return 2.0, 90
    if 'analy' in n or 'lint' in n:
        return 2.0, 60
    if any(x in n for x in ('pub get', 'resolve', 'depend', 'prepare')):
        return 2.0, 45
    if any(x in n for x in ('deploy', 'publish', 'release')):
        return 2.0, 75
    if any(x in n for x in ('upload', 'package', 'stage', 'sign', 'verify')):
        return 1.5, 45
    return 1.0, 25


def job_progress(job):
    steps = job.get('steps') or []
    if job.get('status') == 'completed':
        return 100, 'Completed', 0
    if not steps:
        return 2, 'Starting', None
    total = done = 0.0
    eta = 0
    current = 'Starting'
    for s in steps:
        if s.get('conclusion') == 'skipped':
            continue
        w, sec = weight(s.get('name'))
        total += w
        st = s.get('status')
        if st == 'completed':
            done += w
        elif st == 'in_progress':
            current = s.get('name') or 'Running'
            elapsed = age(s.get('started_at'))
            frac = min(0.9, elapsed / max(1, sec))
            done += w * frac
            eta += max(0, sec - elapsed)
        else:
            eta += sec
    return min(99, max(1, round(done / max(1.0, total) * 100))), current, int(eta)


def github_snapshot(repos):
    active, queue, history, errors = [], [], [], []
    for repo in sorted(set(x for x in repos if x)):
        try:
            runs = gh(['run', 'list', '-R', repo, '--limit', '18', '--json',
                       'databaseId,name,workflowName,status,conclusion,event,headBranch,createdAt,startedAt,updatedAt,url']) or []
        except Exception as e:
            errors.append(f'{repo}: {e}')
            continue
        for r in runs:
            wf = r.get('workflowName') or r.get('name') or '?'
            status = r.get('status') or ''
            base = {
                'repo': repo.split('/', 1)[-1], 'repo_full': repo,
                'workflow': wf, 'workflowName': wf, 'name': wf,
                'branch': r.get('headBranch'), 'headBranch': r.get('headBranch'),
                'databaseId': r.get('databaseId'), 'id': r.get('databaseId'),
                'status': status, 'conclusion': r.get('conclusion') or '',
                'createdAt': r.get('createdAt'), 'updatedAt': r.get('updatedAt'),
                'url': r.get('url'), 'event': r.get('event'),
            }
            if status in ('queued', 'waiting', 'requested', 'pending'):
                queue.append(base)
                continue
            if status == 'completed':
                a, b = dt(r.get('startedAt')), dt(r.get('updatedAt'))
                base['duration'] = int((b - a).total_seconds()) if a and b else None
                base['result'] = r.get('conclusion')
                history.append(base)
                continue
            if status != 'in_progress':
                continue
            rid = r.get('databaseId')
            jobs = []
            try:
                jobs = (gh(['api', f'repos/{repo}/actions/runs/{rid}/jobs?per_page=100']) or {}).get('jobs', [])
            except Exception as e:
                errors.append(f'{repo}/{rid}: {e}')
            jrows = []
            for j in jobs:
                if j.get('conclusion') == 'skipped':
                    continue
                pct, step, eta = job_progress(j)
                jrows.append({
                    'name': j.get('name'), 'status': j.get('status'),
                    'runner_name': j.get('runner_name'), 'runner': j.get('runner_name'),
                    'progress': pct, 'step': step, 'eta': eta,
                    'elapsed': age(j.get('started_at')),
                })
            running = [x for x in jrows if x['status'] == 'in_progress']
            src = running or jrows
            pct = round(sum(x['progress'] for x in src) / len(src)) if src else 2
            current = running[0] if running else (src[0] if src else None)
            runner_name = current.get('runner_name') if current else None
            state = 'RUNNING' if running else ('ASSIGNED' if runner_name else 'WAITING_FOR_RUNNER')
            created_age = age(r.get('startedAt') or r.get('createdAt'))
            anomaly = (state == 'WAITING_FOR_RUNNER' and created_age > 180) or created_age > 5400
            if anomaly and state == 'WAITING_FOR_RUNNER':
                state = 'WAITING_RUNNER_SUSPECT'
            active.append({
                **base, 'number': rid, 'progress': pct,
                'job': {'name': current.get('name') if current else None, 'runner_name': runner_name},
                'action': {
                    'state': state, 'anomaly': anomaly, 'age_seconds': created_age,
                    'current_step': current.get('step') if current else None,
                    'reason': 'waiting for matching runner' if state.startswith('WAITING') else None,
                },
                'jobs': jrows,
            })
    history.sort(key=lambda x: x.get('updatedAt') or '', reverse=True)
    queue.sort(key=lambda x: x.get('createdAt') or '')
    return active, queue[:50], history[:80], errors


def network_snapshot():
    global PREV_NET, PREV_NET_TIME
    rows = []
    try:
        for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
            if ':' not in line:
                continue
            iface, rest = line.split(':', 1)
            iface = iface.strip()
            if iface == 'lo':
                continue
            p = rest.split()
            rows.append((iface, int(p[0]), int(p[8])))
    except Exception:
        pass
    if rows:
        iface, rx, tx = max(rows, key=lambda x: x[1] + x[2])
        now = time.time()
        rx_rate = tx_rate = 0.0
        if PREV_NET and PREV_NET_TIME and PREV_NET[0] == iface:
            delta = max(0.001, now - PREV_NET_TIME)
            rx_rate = max(0.0, (rx - PREV_NET[1]) / delta)
            tx_rate = max(0.0, (tx - PREV_NET[2]) / delta)
        PREV_NET = (iface, rx, tx)
        PREV_NET_TIME = now
    else:
        iface, rx, tx, rx_rate, tx_rate = 'none', 0, 0, 0.0, 0.0
    ip = 'unknown'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(('8.8.8.8', 53))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    online = False
    latency = None
    start = time.perf_counter()
    try:
        s = socket.create_connection(('github.com', 443), timeout=2.0)
        s.close()
        online = True
        latency = round((time.perf_counter() - start) * 1000, 1)
    except Exception:
        pass
    return {
        'interface': iface, 'rx_bytes': rx, 'tx_bytes': tx,
        'rx_rate': rx_rate, 'tx_rate': tx_rate,
        'local_ip': ip, 'internet': online, 'github_latency_ms': latency,
    }


def hermes_snapshot():
    def text(args, timeout=5):
        rc, out, _ = cmd(args, timeout)
        return out.strip() if rc == 0 else ''
    version = text([str(HOME / '.hermes/hermes-agent/venv/bin/hermes'), '--version']) if (HOME / '.hermes/hermes-agent/venv/bin/hermes').exists() else text(['hermes', '--version'])
    provider = text(['hermes', 'config', 'get', 'model.provider']) if shutil.which('hermes') else ''
    model = text(['hermes', 'config', 'get', 'model.default']) if shutil.which('hermes') else ''
    commit = text(['git', '-C', str(HOME / '.hermes/hermes-agent'), 'rev-parse', '--short', 'HEAD'], 3)
    workspace = HOME / 'hermes-workspaces/986-ci-doctor'
    wrapper = HOME / 'bin/hermes986'
    installed = bool(version)
    ready = installed and workspace.exists() and wrapper.exists() and bool(provider and model)
    return {
        'installed': installed, 'ready': ready,
        'version': version.splitlines()[0] if version else 'not installed',
        'provider': provider or 'not configured', 'model': model or 'not configured',
        'commit': commit or '-', 'workspace_ready': workspace.exists(),
    }


def scheduler_snapshot(kind):
    out = []
    d = SCHED / kind
    if not d.exists():
        return out
    for f in d.iterdir():
        try:
            p = f.read_text(errors='replace').rstrip().split('\t', 8)
            if len(p) >= 8:
                out.append({'priority': p[0], 'epoch': int(p[1]), 'class': p[3], 'repo': p[4], 'workflow': p[5], 'branch': p[7], 'elapsed': max(0, int(time.time()) - int(p[1]))})
        except Exception:
            pass
    return out


def build_projects(rr, active, history):
    groups = {}
    for r in rr:
        key = r.get('repo') or r.get('directory')
        groups.setdefault(key, []).append(r)
    projects = []
    for repo, runners in sorted(groups.items()):
        a = [x for x in active if x.get('repo') == repo]
        h = next((x for x in history if x.get('repo') == repo), None)
        latest = None
        if h:
            latest = {
                'name': h.get('workflow'), 'number': h.get('id'),
                'conclusion': h.get('conclusion'), 'status': h.get('status'),
                'branch': h.get('branch'), 'url': h.get('url'),
            }
        projects.append({'repo': repo, 'runners': runners, 'active': a, 'latest': latest})
    return projects


def summarize(rr, active, queue, projects):
    online = sum(bool(x.get('online')) for x in rr)
    listeners = sum(bool(x.get('local_listener')) for x in rr)
    busy = sum(bool(x.get('busy')) for x in rr)
    waiting = sum(1 for x in active if str((x.get('action') or {}).get('state', '')).startswith('WAITING'))
    starting = sum(1 for x in active if (x.get('action') or {}).get('state') in ('ASSIGNED', 'STARTING'))
    anomalies = sum(1 for x in active if (x.get('action') or {}).get('anomaly')) + sum(1 for x in rr if not x.get('online') or not x.get('local_listener'))
    return {
        'runner_online': online, 'runner_total': len(rr), 'local_listeners': listeners,
        'runner_busy': busy, 'repo_total': len(projects),
        'running': len(active), 'queued': len(queue),
        'waiting_runner': waiting, 'starting': starting, 'anomalies': anomalies,
        'runner_online_pct': round(online / max(1, len(rr)) * 100, 1),
        'runner_busy_pct': round(busy / max(1, len(rr)) * 100, 1),
    }


def refresh_local():
    while True:
        rows = ps_rows()
        rr = local_runners(rows)
        remote_errors = apply_github_runner_status(rr)
        sys = system_snapshot()
        net = network_snapshot()
        hermes = hermes_snapshot()
        with LOCK:
            active = list(DATA.get('runs') or [])
            queue = list(DATA.get('queue') or [])
            history = list(DATA.get('history') or [])
            projects = build_projects(rr, active, history)
            summary = summarize(rr, active, queue, projects)
            reasons = []
            if summary['runner_online'] != summary['runner_total']:
                reasons.append(f"GitHub online {summary['runner_online']}/{summary['runner_total']}")
            if summary['local_listeners'] != summary['runner_total']:
                reasons.append(f"listeners {summary['local_listeners']}/{summary['runner_total']}")
            if summary['anomalies']:
                reasons.append(f"{summary['anomalies']} anomaly")
            if remote_errors:
                reasons.append('runner API partial error')
            state = 'CRITICAL' if summary['runner_online'] < max(1, summary['runner_total'] - 2) else ('WARNING' if reasons else 'HEALTHY')
            DATA.update({
                'updated': datetime.now().astimezone().isoformat(timespec='seconds'),
                'system': sys, 'host': {
                    'hostname': sys['hostname'], 'threads': sys['cpu_threads'], 'load': sys['load1'],
                    'cpu_pct': sys['cpu_percent'], 'mem_pct': sys['memory']['percent'],
                    'mem_available_mb': round(sys['memory']['available'] / 1048576),
                    'disk_pct': sys['disk']['percent'], 'disk_free_gb': round(sys['disk']['free'] / 1073741824, 1),
                    'processes': sys['processes'],
                },
                'runners': rr, 'projects': projects, 'summary': summary,
                'network': net, 'hermes': hermes,
                'scheduler': {'active': scheduler_snapshot('active'), 'queued': scheduler_snapshot('requests')},
                'health': {'state': state, 'reasons': reasons},
            })
        time.sleep(1)


def refresh_github():
    global GITHUB_AT
    while True:
        with LOCK:
            repos = [x.get('repo_full') for x in DATA.get('runners', []) if x.get('repo_full')]
        if repos:
            active, queue, history, errors = github_snapshot(repos)
            GITHUB_AT = time.time()
            with LOCK:
                rr = list(DATA.get('runners') or [])
                projects = build_projects(rr, active, history)
                summary = summarize(rr, active, queue, projects)
                DATA.update({
                    'runs': active, 'queue': queue, 'history': history,
                    'projects': projects, 'summary': summary,
                    'github': {'poll_seconds': POLL, 'age_seconds': 0},
                    'github_updated': datetime.now().astimezone().isoformat(timespec='seconds'),
                    'error': '; '.join(errors[:8]) if errors else None,
                })
        for _ in range(POLL):
            with LOCK:
                if GITHUB_AT:
                    DATA['github']['age_seconds'] = max(0, int(time.time() - GITHUB_AT))
            time.sleep(1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_json(self, obj, code=200):
        b = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, max-age=0')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ('/health', '/healthz'):
            with LOCK:
                s = DATA.get('summary') or {}
                h = DATA.get('health') or {}
            return self.send_json({
                'ok': h.get('state') != 'CRITICAL', 'service': 'redmi-telemetry',
                'version': VERSION, 'host': 'redmi-build',
                'runners': s.get('runner_total', 0), 'online': s.get('runner_online', 0),
                'dashboard_owner': 'rapsi-nas',
            })
        if self.path in ('/state', '/api/state', '/api/status'):
            with LOCK:
                snap = json.loads(json.dumps(DATA))
            return self.send_json(snap)
        return self.send_json({'ok': False, 'error': 'telemetry only; dashboard is hosted on rapsi-nas'}, 404)


if __name__ == '__main__':
    threading.Thread(target=refresh_local, daemon=True).start()
    threading.Thread(target=refresh_github, daemon=True).start()
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
