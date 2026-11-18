"""
Persistent storage for the NDS bot on hosts with an ephemeral disk (Render free plan).

The bot keeps writing its JSON files locally as before. This module:
  * restore():   on startup, downloads the files from a private GitHub repo and merges them in;
  * start_sync(): every SYNC_SECONDS, uploads any file whose content changed;
  * flush():      uploads immediately (called on SIGTERM before a redeploy/restart);
  * start_keepalive(): pings the service's own public URL so the free instance does not sleep.

Environment:
  GITHUB_TOKEN      token with Contents read/write on DATA_REPO (required; without it this is a no-op)
  DATA_REPO         owner/name of the private data repo (default: cajjadhabibi/nds-bot-data)
  DATA_BRANCH       branch (default: main)
  DATA_DIR          folder inside the repo (default: data)
  RENDER_EXTERNAL_URL  set automatically by Render; used for the keep-alive ping
"""
import base64
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["active_trades.json", "subscribers.json", "pending_requests.json", "exec_state.json", "voided_trades.json", "missed_signals.json"]

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
REPO = os.environ.get("DATA_REPO", "cajjadhabibi/nds-bot-data").strip()
BRANCH = os.environ.get("DATA_BRANCH", "main").strip()
DATA_DIR = os.environ.get("DATA_DIR", "data").strip().strip("/")
SYNC_SECONDS = int(os.environ.get("DATA_SYNC_SECONDS", "300"))
KEEPALIVE_SECONDS = int(os.environ.get("KEEPALIVE_SECONDS", "600"))

_lock = threading.Lock()
_remote_sha = {}      # file -> blob sha on GitHub
_last_hash = {}       # file -> sha256 of last uploaded content


def enabled() -> bool:
    return bool(TOKEN and REPO)


def _log(msg):
    print(f"[cloud_store] {msg}", flush=True)


def _api(method, path, body=None):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    if method == "GET":
        url += f"?ref={BRANCH}"
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "nds-bot-store",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def _read_local(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = f.read()
        json.loads(raw)          # skip half-written files
        return raw
    except Exception:
        return None


def _merge(name, remote, local):
    if local is None:
        return remote
    if isinstance(remote, dict) and isinstance(local, dict):
        out = dict(remote)
        out.update(local)        # entries written since startup win
        return out
    if isinstance(remote, list) and isinstance(local, list):
        out = list(remote)
        out += [x for x in local if x not in out]
        return out
    return local


def restore():
    """Download stored files and merge them into the local ones. Safe to call when disabled."""
    if not enabled():
        _log("GITHUB_TOKEN not set — persistence disabled (data will be lost on restart).")
        return False
    ok = True
    for name in FILES:
        status, data = _api("GET", f"{DATA_DIR}/{name}")
        if status == 404:
            _log(f"{name}: not in store yet")
            continue
        if status != 200:
            _log(f"{name}: restore failed HTTP {status} {data.get('message', '')}")
            ok = False
            continue
        _remote_sha[name] = data.get("sha")
        try:
            remote = json.loads(base64.b64decode(data.get("content", "")).decode("utf-8"))
        except Exception as e:
            _log(f"{name}: bad remote JSON ({e})")
            ok = False
            continue
        local_raw = _read_local(name)
        merged = _merge(name, remote, json.loads(local_raw) if local_raw else None)
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        size = len(merged) if hasattr(merged, "__len__") else "?"
        _log(f"{name}: restored ({size} entries)")
    return ok


def _upload(name, raw, reason):
    body = {
        "message": f"data: {name} ({reason})",
        "content": base64.b64encode(raw.encode("utf-8")).decode(),
        "branch": BRANCH,
    }
    if _remote_sha.get(name):
        body["sha"] = _remote_sha[name]
    status, data = _api("PUT", f"{DATA_DIR}/{name}", body)
    if status == 409 or status == 422:        # stale sha: refresh and retry once
        s2, d2 = _api("GET", f"{DATA_DIR}/{name}")
        if s2 == 200:
            body["sha"] = d2.get("sha")
            status, data = _api("PUT", f"{DATA_DIR}/{name}", body)
    if status in (200, 201):
        _remote_sha[name] = data.get("content", {}).get("sha")
        return True
    _log(f"{name}: upload failed HTTP {status} {data.get('message', '')}")
    return False


def flush(reason="sync"):
    """Upload every file whose content changed since the last upload."""
    if not enabled():
        return
    with _lock:
        for name in FILES:
            raw = _read_local(name)
            if raw is None:
                continue
            h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if _last_hash.get(name) == h:
                continue
            if _upload(name, raw, reason):
                _last_hash[name] = h


def start_sync():
    if not enabled():
        return

    def loop():
        while True:
            time.sleep(SYNC_SECONDS)
            try:
                flush("periodic")
            except Exception as e:
                _log(f"sync error: {e}")

    threading.Thread(target=loop, daemon=True, name="cloud-store-sync").start()
    _log(f"sync every {SYNC_SECONDS}s to {REPO}@{BRANCH}/{DATA_DIR}")


def start_keepalive():
    url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if not url:
        return

    def loop():
        while True:
            time.sleep(KEEPALIVE_SECONDS)
            try:
                urllib.request.urlopen(url.rstrip("/") + "/health", timeout=30).read()
            except Exception as e:
                _log(f"keep-alive ping failed: {e}")

    threading.Thread(target=loop, daemon=True, name="keepalive").start()
    _log(f"keep-alive ping every {KEEPALIVE_SECONDS}s -> {url}/health")
