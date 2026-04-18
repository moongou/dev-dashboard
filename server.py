#!/usr/bin/env python3
"""
Dev Dashboard — Local Development Service Manager
Install: pip install fastapi uvicorn
Run: python server.py
Visit: http://localhost:9999
"""

import asyncio
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn

app = FastAPI()
HOME = Path.home()
LOG_DIR = Path("/tmp/dev-dashboard")
LOG_DIR.mkdir(exist_ok=True)

# Running processes {id: Popen}
_procs: dict = {}

# ─── Project Configuration ───────────────────────────────────────────────────
# Add your own local services here. Each entry represents a service you want
# to manage from the dashboard. Below are examples to get you started.
#
# Fields:
#   id       - Unique identifier (used in API URLs)
#   name     - Display name
#   desc     - Short description
#   intro    - Detailed description (shown in expandable card)
#   port     - Port the service listens on
#   group    - Category for grouping (Python, Node, AI, Docker, Java, Education, Tools)
#   dir      - Working directory (absolute path)
#   cmd      - Command to start the service (list of strings)
#   env      - Optional: extra environment variables (dict)
#   ssl      - Optional: True if the service uses HTTPS
#   stop_cmd - Optional: custom stop command (e.g., docker-compose down)
#   update_cmd - Optional: custom update command (e.g., git pull && npm install)
#   url_path - Optional: path appended to the URL when opening in browser
#   no_ui    - Optional: True if the service has no web UI
#   has_git  - Optional: True to show update button

PROJECTS = [
    # ── DeerFlow ──────────────────────────────────────────────────────────────
    {
        "id": "deer-flow-frontend",
        "name": "DeerFlow Frontend",
        "desc": "DeerFlow AI Research Agent — nginx frontend",
        "intro": (
            "Nginx reverse proxy serving the DeerFlow web UI. "
            "Part of the DeerFlow AI research agent project. "
            "Start the backend first before starting this service."
        ),
        "port": 2026,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/deer-flow"),
        "cmd": [
            "bash", "-c",
            "nginx -g 'daemon off;' "
            "-c '/Users/m3max/VS-CODE-PROJECT/deer-flow/docker/nginx/nginx.local.conf' "
            "-p '/Users/m3max/VS-CODE-PROJECT/deer-flow' "
            "> logs/nginx.log 2>&1",
        ],
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
    {
        "id": "deer-flow-backend",
        "name": "DeerFlow Backend",
        "desc": "DeerFlow LangGraph dev server (port 2024)",
        "intro": (
            "LangGraph development server powering the DeerFlow AI research agent. "
            "Provides the graph execution API consumed by the frontend. "
            "Requires Python virtualenv in the backend/ directory."
        ),
        "port": 2024,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/deer-flow/backend"),
        "cmd": [
            ".venv/bin/python", "-m", "langgraph",
            "dev", "--no-browser", "--port", "2024", "--n-jobs-per-worker", "1",
        ],
        "url_path": "/",
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && .venv/bin/pip install -r requirements.txt"],
    },
    # ── AI / Speech / Vision ──────────────────────────────────────────────────
    {
        "id": "cosyvoice",
        "name": "CosyVoice",
        "desc": "CosyVoice2 TTS FastAPI server (port 50000)",
        "intro": (
            "CosyVoice2 — multilingual text-to-speech model serving a FastAPI endpoint. "
            "Model: CosyVoice2-0.5B. Supports zero-shot cloning, cross-lingual synthesis, "
            "and instruction-following voice generation."
        ),
        "port": 50000,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/CosyVoice"),
        "cmd": [
            "bash", "-c",
            "/Users/m3max/miniconda3/envs/cosyvoice/bin/python "
            "runtime/python/fastapi/server.py --port 50000 "
            "--model_dir /Users/m3max/IdeaProjects/CosyVoice/pretrained_models/CosyVoice2-0.5B",
        ],
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
    {
        "id": "voxcpm",
        "name": "VoxCPM",
        "desc": "VoxCPM2 Tokenizer-Free TTS (port 8808)",
        "intro": (
            "VoxCPM2 — a tokenizer-free text-to-speech model for multilingual speech generation, "
            "creative voice design, and true-to-life voice cloning. "
            "Runs a Gradio web UI on port 8808."
        ),
        "port": 8808,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/VoxCPM"),
        "cmd": [".venv/bin/python", "app.py", "--port", "8808"],
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && .venv/bin/pip install -r requirements.txt"],
    },
    {
        "id": "openai-edge-tts",
        "name": "OpenAI Edge TTS",
        "desc": "OpenAI-compatible TTS API via Microsoft Edge (port 5051)",
        "intro": (
            "Drop-in OpenAI TTS API replacement powered by Microsoft Edge TTS. "
            "Exposes POST /v1/audio/speech compatible with the OpenAI SDK. "
            "Lightweight, no GPU required."
        ),
        "port": 5051,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/openai-edge-tts"),
        "cmd": ["python", "app/server.py"],
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
    {
        "id": "funasr",
        "name": "FunASR",
        "desc": "FunASR HTTP ASR server (port 10096, localhost only)",
        "intro": (
            "FunASR — a fundamental end-to-end speech recognition toolkit from Alibaba DAMO. "
            "Serves a REST HTTP API on localhost:10096 for speech-to-text inference. "
            "Supports multiple ASR models including paraformer and sensevoice."
        ),
        "port": 10096,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/FunASR"),
        "cmd": [
            "python", "runtime/python/http/server.py",
            "--port", "10096", "--host", "127.0.0.1",
        ],
        "no_ui": True,
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
    {
        "id": "mflux",
        "name": "mflux",
        "desc": "mflux MLX image generation server (port 8321)",
        "intro": (
            "mflux — a MLX-based image generation library running FLUX models "
            "natively on Apple Silicon. Provides a web server for text-to-image generation. "
            "Optimised for M-series Macs using the MLX framework."
        ),
        "port": 8321,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/mflux"),
        "cmd": [".venv/bin/python", "server.py"],
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && .venv/bin/pip install -e ."],
    },
    # ── MinerU ────────────────────────────────────────────────────────────────
    {
        "id": "mineru-gradio",
        "name": "MinerU Gradio",
        "desc": "MinerU PDF/document parser — Gradio UI (port 10002)",
        "intro": (
            "MinerU — a high-quality PDF and document parsing tool. "
            "This instance exposes a Gradio web UI for interactive document conversion "
            "including PDF → Markdown with layout analysis and formula recognition."
        ),
        "port": 10002,
        "group": "AI",
        "dir": str(HOME / "docker-data/mineru-venv"),
        "cmd": [
            "bash", "-c",
            "/Users/m3max/docker-data/mineru-venv/bin/mineru-gradio "
            "--server-name 0.0.0.0 --server-port 10002",
        ],
    },
    {
        "id": "mineru-api",
        "name": "MinerU API",
        "desc": "MinerU FastAPI document parsing API (port 49680, localhost only)",
        "intro": (
            "MinerU FastAPI server — provides a programmatic REST API for document parsing. "
            "Listens on localhost:49680. Useful for integrations that need to convert "
            "PDFs and office documents to structured Markdown or JSON."
        ),
        "port": 49680,
        "group": "AI",
        "dir": str(HOME / "docker-data/mineru-venv"),
        "cmd": [
            "bash", "-c",
            "/Users/m3max/docker-data/mineru-venv/bin/python "
            "-m mineru.cli.fast_api --host 127.0.0.1",
        ],
        "no_ui": True,
    },
    # ── Python Services ───────────────────────────────────────────────────────
    {
        "id": "ai-router",
        "name": "AI Router",
        "desc": "Local AI gateway / LLM router (port 9000)",
        "intro": (
            "AI Router — a local API gateway that routes requests to different AI backends "
            "(Ark/Volcengine, OpenAI, Whisper, etc.). "
            "Runs on localhost:9000. Managed via launchd for auto-start on login."
        ),
        "port": 9000,
        "group": "Python",
        "dir": str(HOME / "IdeaProjects/ai-router"),
        "cmd": ["python", "app.py"],
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && pip install -r requirements.txt"],
    },
    {
        "id": "roundtable-backend",
        "name": "RoundTable Backend",
        "desc": "RoundTable multi-agent discussion backend (port 8001)",
        "intro": (
            "RoundTable — a multi-agent AI discussion framework backend. "
            "Exposes a FastAPI service on port 8001 that orchestrates multiple AI agents "
            "in structured discussion rounds."
        ),
        "port": 8001,
        "group": "Python",
        "dir": str(HOME / "IdeaProjects/RoundTable/backend"),
        "cmd": ["python", "-m", "app.main"],
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
    {
        "id": "system-prompts",
        "name": "System Prompts & Models",
        "desc": "AI system prompts & model config server (port 10001)",
        "intro": (
            "A local server for managing and serving AI system prompts and model configurations. "
            "Provides a simple API to retrieve prompts for different AI tools and workflows. "
            "Runs on port 10001."
        ),
        "port": 10001,
        "group": "Tools",
        "dir": str(HOME / "docker-data/system-prompts-and-models-of-ai-tools"),
        "cmd": ["python", "server.py"],
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
    # ── Node ──────────────────────────────────────────────────────────────────
    {
        "id": "dev-dashboard-node",
        "name": "Dev Dashboard (Node)",
        "desc": "Node/webpack dev-dashboard frontend (port 9999)",
        "intro": (
            "The webpack-based Node.js dev server for the IdeaProjects/dev-dashboard project. "
            "Serves the frontend with hot-module replacement on port 9999."
        ),
        "port": 9999,
        "group": "Node",
        "dir": str(HOME / "IdeaProjects/dev-dashboard"),
        "cmd": ["npm", "run", "dev"],
        "update_cmd": ["bash", "-c", "git pull && npm install"],
        "has_git": True,
    },
]

BY_ID = {p["id"]: p for p in PROJECTS}

# ─── Utility Functions ────────────────────────────────────────────────────────


def port_pids(port: int) -> list[int]:
    r = subprocess.run(
        ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
    )
    return [int(x) for x in r.stdout.split() if x.isdigit()]


def is_running(port: int) -> bool:
    return bool(port_pids(port))


def http_ready(port: int) -> bool:
    """Check if a service is ready by probing both IPv4 and IPv6."""
    import socket

    candidates = [("127.0.0.1", port), ("::1", port)]
    for host, p in candidates:
        try:
            s = socket.create_connection((host, p), timeout=2)
            s.close()
            return True
        except Exception:
            continue
    return False


def is_git_repo(directory: str) -> bool:
    r = subprocess.run(
        ["git", "-C", directory, "rev-parse", "--git-dir"],
        capture_output=True,
    )
    return r.returncode == 0


def project_status(pid: str) -> dict:
    proj = BY_ID[pid]
    running = is_running(proj["port"])
    pids = port_pids(proj["port"]) if running else []
    managed = pid in _procs and _procs[pid].poll() is None
    has_git = "update_cmd" in proj or is_git_repo(proj["dir"])
    return {"running": running, "managed": managed, "pids": pids, "has_git": has_git}


# ─── API ──────────────────────────────────────────────────────────────────────


@app.get("/")
async def index():
    return HTMLResponse(HTML)


@app.get("/api/projects/{pid}/ping")
async def ping_project(pid: str):
    proj = BY_ID.get(pid)
    if not proj:
        raise HTTPException(404, "Project not found")
    ready = http_ready(proj["port"])
    return {"ready": ready}


@app.get("/api/projects")
async def list_projects():
    result = []
    for p in PROJECTS:
        s = project_status(p["id"])
        result.append({**p, **s})
    return result


@app.post("/api/projects/{pid}/start")
async def start_project(pid: str):
    proj = BY_ID.get(pid)
    if not proj:
        raise HTTPException(404, "Project not found")

    if is_running(proj["port"]):
        return {"ok": False, "msg": f"Port {proj['port']} is already in use"}

    work_dir = Path(proj["dir"])
    if not work_dir.exists():
        return {"ok": False, "msg": f"Directory not found: {proj['dir']}"}

    log_path = LOG_DIR / f"{pid}.log"
    env = {**os.environ, **proj.get("env", {})}

    try:
        with open(log_path, "a") as lf:
            lf.write(
                f"\n{'─' * 60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting\n{'─' * 60}\n"
            )

        with open(log_path, "a") as lf:
            proc = subprocess.Popen(
                proj["cmd"],
                cwd=str(work_dir),
                env=env,
                stdout=lf,
                stderr=lf,
                start_new_session=True,
            )
        _procs[pid] = proc
        return {"ok": True, "pid": proc.pid}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


@app.post("/api/projects/{pid}/stop")
async def stop_project(pid: str):
    proj = BY_ID.get(pid)
    if not proj:
        raise HTTPException(404, "Project not found")

    # Custom stop command (e.g., docker-compose down)
    if "stop_cmd" in proj:
        subprocess.run(proj["stop_cmd"], cwd=proj["dir"], capture_output=True)

    # Terminate the process group we started
    proc = _procs.pop(pid, None)
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # Fallback: kill processes occupying the port
    for p in port_pids(proj["port"]):
        try:
            os.kill(p, signal.SIGTERM)
        except Exception:
            pass

    return {"ok": True}


@app.post("/api/projects/{pid}/update")
async def update_project(pid: str):
    proj = BY_ID.get(pid)
    if not proj:
        raise HTTPException(404, "Project not found")

    if "update_cmd" in proj:
        cmd = proj["update_cmd"]
        label = " ".join(cmd)
    elif is_git_repo(proj["dir"]):
        cmd = ["git", "pull"]
        label = "git pull"
    else:
        return {"ok": False, "msg": "Not a git repo and no update_cmd configured"}

    log_path = LOG_DIR / f"{pid}.log"
    try:
        with open(log_path, "a") as lf:
            lf.write(
                f"\n{'─' * 60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {label}\n{'─' * 60}\n"
            )
        result = subprocess.run(
            cmd,
            cwd=proj["dir"],
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
        with open(log_path, "a") as lf:
            lf.write(output + "\n")
        return {"ok": result.returncode == 0, "output": output.strip()}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


@app.get("/api/projects/{pid}/logs/stream")
async def stream_logs(pid: str):
    """SSE real-time log stream"""
    proj = BY_ID.get(pid)
    if not proj:
        raise HTTPException(404)

    log_path = LOG_DIR / f"{pid}.log"
    log_path.touch()

    async def generator():
        # Push the last 80 lines first
        r = subprocess.run(
            ["tail", "-n", "80", str(log_path)], capture_output=True, text=True
        )
        for line in r.stdout.splitlines():
            yield f"data: {json.dumps(line)}\n\n"

        # Continuous tail
        proc = await asyncio.create_subprocess_exec(
            "tail",
            "-F",
            str(log_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
                if not line:
                    break
                yield f"data: {json.dumps(line.decode().rstrip())}\n\n"
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        finally:
            proc.terminate()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Frontend HTML ────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dev Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body { background:#0f1117; }

  .dot {
    width:9px; height:9px; border-radius:50%; flex-shrink:0;
    transition: background .3s;
  }
  .dot-on  { background:#22c55e; box-shadow:0 0 7px #22c55e88; }
  .dot-off { background:#374151; }
  .dot-busy { background:#f59e0b; animation:blink 1s infinite; }

  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.3} }

  .card {
    background:#1a1d27;
    border:1px solid #2a2d3a;
    border-radius:12px;
    transition: border-color .2s, box-shadow .2s;
  }
  .card:hover { border-color:#3b3f54; box-shadow:0 4px 20px #00000060; }
  .card.running { border-color:#22c55e33; }

  .btn {
    padding:6px 14px; border-radius:8px; font-size:13px; font-weight:500;
    cursor:pointer; transition:all .15s; border:none; outline:none;
  }
  .btn-start  { background:#16a34a22; color:#4ade80; border:1px solid #16a34a44; }
  .btn-start:hover  { background:#16a34a44; }
  .btn-stop   { background:#dc262622; color:#f87171; border:1px solid #dc262644; }
  .btn-stop:hover   { background:#dc262644; }
  .btn-log    { background:#1e293b; color:#94a3b8; border:1px solid #334155; }
  .btn-log:hover    { background:#293548; }
  .btn-open   { background:#1e3a5f; color:#60a5fa; border:1px solid #1d4ed844; }
  .btn-open:hover   { background:#1e4a7a; }
  .btn-update { background:#1a1f2e; color:#a78bfa; border:1px solid #6d28d944; font-size:11px; padding:2px 7px; border-radius:6px; cursor:pointer; transition:all .15s; }
  .btn-update:hover { background:#2d1f4e; }
  .btn-disabled { opacity:.4; cursor:not-allowed; }

  .group-badge {
    font-size:11px; padding:2px 8px; border-radius:20px; font-weight:600;
  }
  .badge-Python { background:#1e3a5f22; color:#60a5fa; border:1px solid #1d4ed844; }
  .badge-Node   { background:#14532d22; color:#4ade80; border:1px solid #16a34a44; }
  .badge-Java   { background:#431a0022; color:#fb923c; border:1px solid #c2410c44; }
  .badge-Docker { background:#0c1a3522; color:#38bdf8; border:1px solid #0369a144; }
  .badge-Education { background:#4c1d9522; color:#c084fc; border:1px solid #7c3aed44; }
  .badge-AI     { background:#4c1d9522; color:#e879f9; border:1px solid #a855f744; }
  .badge-Tools  { background:#1a1f2e; color:#94a3b8; border:1px solid #33415544; }

  /* Log modal */
  #modal-overlay {
    display:none; position:fixed; inset:0;
    background:#00000090; backdrop-filter:blur(4px);
    z-index:50; align-items:center; justify-content:center;
  }
  #modal-overlay.open { display:flex; }

  #modal {
    background:#141720; border:1px solid #2a2d3a; border-radius:16px;
    width:min(800px,95vw); max-height:85vh;
    display:flex; flex-direction:column;
  }

  #log-box {
    font-family:'JetBrains Mono','Courier New',monospace;
    font-size:12px; line-height:1.6;
    overflow-y:auto; flex:1;
    padding:12px 16px;
    background:#0a0c12; border-radius:0 0 12px 12px;
  }
  .log-err  { color:#f87171; }
  .log-warn { color:#fbbf24; }
  .log-info { color:#86efac; }
  .log-dim  { color:#4b5563; }
  .log-norm { color:#cbd5e1; }

  .intro-details summary { list-style:none; }
  .intro-details summary::-webkit-details-marker { display:none; }
  .intro-details[open] summary { color:#6b7280; }
  .intro-text { border-left:2px solid #2a2d3a; padding-left:8px; color:#6b7280; }
  .btn-copy-intro { margin-top:6px; font-size:11px; padding:2px 8px; border-radius:4px; border:1px solid #2a2d3a; background:#1a1d2a; color:#6b7280; cursor:pointer; transition:color .15s,border-color .15s; }
  .btn-copy-intro:hover { color:#a5b4fc; border-color:#a5b4fc; }
  .btn-copy-intro.copied { color:#34d399; border-color:#34d399; }

  ::-webkit-scrollbar { width:5px; height:5px; }
  ::-webkit-scrollbar-track { background:transparent; }
  ::-webkit-scrollbar-thumb { background:#2a2d3a; border-radius:3px; }
</style>
</head>
<body class="text-gray-200 min-h-screen">

<!-- Header -->
<header class="border-b border-gray-800 px-6 py-4 flex items-center justify-between sticky top-0 z-10" style="background:#0f1117cc;backdrop-filter:blur(8px)">
  <div class="flex items-center gap-3">
    <span class="text-xl">&#9889;</span>
    <span class="font-bold text-lg tracking-tight">Dev Dashboard</span>
  </div>
  <div class="flex items-center gap-4">
    <span id="stats" class="text-sm text-gray-500">Loading...</span>
    <button class="btn btn-log text-xs" onclick="refreshAll()">&#8635; Refresh</button>
  </div>
</header>

<!-- Main -->
<main class="px-6 py-6 max-w-7xl mx-auto">
  <div id="groups"></div>
</main>

<!-- Log Modal -->
<div id="modal-overlay" onclick="closeModal(event)">
  <div id="modal">
    <div class="flex items-center justify-between px-5 py-4 border-b border-gray-800">
      <div class="flex items-center gap-3">
        <span class="text-sm font-mono text-gray-400">logs</span>
        <span id="modal-title" class="font-semibold"></span>
        <span id="modal-port" class="text-xs text-gray-500 font-mono"></span>
      </div>
      <div class="flex items-center gap-2">
        <button class="btn btn-log text-xs" onclick="clearLogs()">Clear</button>
        <button class="btn btn-stop text-xs" onclick="closeModal()">&#10005; Close</button>
      </div>
    </div>
    <div id="log-box"></div>
  </div>
</div>

<script>
let projects = [];
let logEs = null;
let autoScroll = true;
let refreshTimer = null;

const GROUP_ORDER = ['Python', 'Node', 'AI', 'Docker', 'Java', 'Education', 'Tools'];
const GROUP_ICON  = { Python:'&#128013;', Node:'&#128994;', AI:'&#129302;', Docker:'&#128051;', Java:'&#9749;', Education:'&#128218;', Tools:'&#128295;' };

async function loadProjects() {
  try {
    const res = await fetch('/api/projects');
    projects = await res.json();
    render();
  } catch(e) {
    console.error(e);
  }
}

function render() {
  const groups = {};
  for (const p of projects) {
    if (!groups[p.group]) groups[p.group] = [];
    groups[p.group].push(p);
  }

  const running = projects.filter(p => p.running).length;
  document.getElementById('stats').textContent = `${running} running / ${projects.length} services`;

  const html = GROUP_ORDER
    .filter(g => groups[g])
    .map(g => `
      <section class="mb-8">
        <h2 class="text-sm font-semibold text-gray-500 uppercase tracking-widest mb-3 flex items-center gap-2">
          <span>${GROUP_ICON[g]}</span> ${g}
        </h2>
        <div class="grid gap-3" style="grid-template-columns:repeat(auto-fill,minmax(260px,1fr))">
          ${groups[g].map(cardHTML).join('')}
        </div>
      </section>
    `).join('');

  document.getElementById('groups').innerHTML = html;
}

function cardHTML(p) {
  const dot    = p.running ? 'dot-on' : 'dot-off';
  const card   = p.running ? 'running' : '';
  const badge  = `<span class="group-badge badge-${p.group}">${p.group}</span>`;
  const proto  = p.ssl ? 'https' : 'http';
  const url    = `${proto}://localhost:${p.port}${p.url_path || ''}`;

  const urlRow = p.running
    ? `<a href="${url}" target="_blank" class="text-xs font-mono text-blue-400 hover:text-blue-300 hover:underline truncate">${url}</a>`
    : `<span class="text-xs font-mono text-gray-700">${url}</span>`;

  const actionBtn = p.running
    ? `<button class="btn btn-stop" onclick="stop('${p.id}')">&#9632; Stop</button>
       ${p.no_ui ? '' : `<button class="btn btn-open" onclick="window.open('${url}','_blank')">&#8599; Open</button>`}`
    : `<button class="btn btn-start" onclick="startAndOpen('${p.id}',${p.port})">&#9654; Start</button>`;

  return `
    <div class="card ${card} p-4 flex flex-col gap-3" id="card-${p.id}">
      <div class="flex items-start justify-between">
        <div class="flex items-center gap-2">
          <div class="dot ${dot}"></div>
          <span class="font-semibold">${p.name}</span>
        </div>
        <div class="flex items-center gap-2">
          ${badge}
          <span class="text-xs font-mono text-gray-500">:${p.port}</span>
        </div>
      </div>
      <p class="text-xs text-gray-500 leading-relaxed">${p.desc}</p>
      ${p.intro ? `
      <details class="intro-details">
        <summary class="text-xs text-gray-600 cursor-pointer hover:text-gray-400 select-none flex items-center gap-2">
          <span>&#128161; Details</span>
          ${p.has_git ? `<button class="btn-update" id="upd-${p.id}" onclick="event.preventDefault();event.stopPropagation();pullUpdate('${p.id}','${p.name}')">&#8593; Update</button>` : ''}
        </summary>
        <p class="text-xs text-gray-500 leading-relaxed mt-1 intro-text">${p.intro}</p>
        <button class="btn-copy-intro" onclick="copyIntro(this, \`${p.intro.replace(/`/g, '\\`')}\`)">Copy</button>
      </details>` : ''}
      ${urlRow}
      <div class="flex items-center gap-2 flex-wrap">
        ${actionBtn}
        <button class="btn btn-log" onclick="openLogs('${p.id}','${p.name}',${p.port})">&#128203; Logs</button>
      </div>
    </div>
  `;
}

async function startAndOpen(id, port) {
  setCardBusy(id, true);
  const r = await fetch(`/api/projects/${id}/start`, {method:'POST'});
  const data = await r.json();
  if (!data.ok) {
    alert('Start failed: ' + (data.msg || 'Unknown error'));
    setCardBusy(id, false);
    return;
  }

  let attempts = 0;
  const timer = setInterval(async () => {
    attempts++;
    await loadProjects();
    const proj = projects.find(p => p.id === id);
    if (proj && proj.running) {
      try {
        const pr = await fetch(`/api/projects/${id}/ping`);
        const pd = await pr.json();
        if (pd.ready) {
          clearInterval(timer);
          const proj = projects.find(p => p.id === id);
          const openProto = (proj && proj.ssl) ? 'https' : 'http';
          window.open(`${openProto}://localhost:${port}${proj.url_path || ''}`, '_blank');
          setCardBusy(id, false);
          return;
        }
      } catch (_) {}
    }
    if (attempts >= 30) {
      clearInterval(timer);
      setCardBusy(id, false);
      alert('Service startup timed out. Check logs for details.');
    }
  }, 2000);
}

async function start(id) {
  setCardBusy(id, true);
  const r = await fetch(`/api/projects/${id}/start`, {method:'POST'});
  const data = await r.json();
  if (!data.ok) alert('Start failed: ' + (data.msg || 'Unknown error'));
  await sleep(1200);
  await loadProjects();
  setCardBusy(id, false);
}

async function stop(id) {
  setCardBusy(id, true);
  await fetch(`/api/projects/${id}/stop`, {method:'POST'});
  await sleep(800);
  await loadProjects();
  setCardBusy(id, false);
}

function setCardBusy(id, busy) {
  const card = document.getElementById(`card-${id}`);
  if (!card) return;
  const dot = card.querySelector('.dot');
  if (dot) { dot.className = 'dot ' + (busy ? 'dot-busy' : ''); }
  card.querySelectorAll('button').forEach(b => b.disabled = busy);
}

function openLogs(id, name, port) {
  document.getElementById('modal-title').textContent = name;
  document.getElementById('modal-port').textContent = ':' + port;
  document.getElementById('log-box').innerHTML = '';
  document.getElementById('modal-overlay').classList.add('open');

  if (logEs) { logEs.close(); logEs = null; }

  logEs = new EventSource(`/api/projects/${id}/logs/stream`);
  logEs.onmessage = e => {
    const line = JSON.parse(e.data);
    appendLog(line);
  };

  const lb = document.getElementById('log-box');
  lb.addEventListener('scroll', () => {
    autoScroll = lb.scrollTop + lb.clientHeight >= lb.scrollHeight - 20;
  });
}

function appendLog(line) {
  const lb = document.getElementById('log-box');
  if (!lb) return;

  const div = document.createElement('div');
  div.style.cssText = 'white-space:pre-wrap;word-break:break-all;padding:1px 0;';

  const lower = line.toLowerCase();
  if (!line.trim()) {
    div.style.height = '6px';
  } else if (/error|exception|traceback|fatal|critical/.test(lower)) {
    div.className = 'log-err';
  } else if (/warn|warning/.test(lower)) {
    div.className = 'log-warn';
  } else if (/✓|success|started|listening|ready|compiled|running/.test(lower)) {
    div.className = 'log-info';
  } else if (/^[─=\s]*$/.test(line)) {
    div.className = 'log-dim';
  } else {
    div.className = 'log-norm';
  }

  div.textContent = line;
  lb.appendChild(div);

  if (autoScroll) lb.scrollTop = lb.scrollHeight;
}

function clearLogs() {
  document.getElementById('log-box').innerHTML = '';
}

function copyIntro(btn, text) {
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 2000);
  });
}

function closeModal(e) {
  if (e && e.target !== document.getElementById('modal-overlay')) return;
  document.getElementById('modal-overlay').classList.remove('open');
  if (logEs) { logEs.close(); logEs = null; }
}

function refreshAll() {
  loadProjects();
}

async function pullUpdate(id, name) {
  const btn = document.getElementById(`upd-${id}`);
  if (!btn) return;
  const orig = btn.textContent;
  btn.textContent = '⟳ Pulling...';
  btn.disabled = true;
  try {
    const r = await fetch(`/api/projects/${id}/update`, {method:'POST'});
    const data = await r.json();
    if (data.ok) {
      btn.textContent = '✓ Updated';
      btn.style.color = '#4ade80';
      setTimeout(() => { btn.textContent = orig; btn.style.color = ''; btn.disabled = false; }, 3000);
    } else {
      showUpdateResult(name, false, data.msg || data.output || 'Unknown error');
      btn.textContent = orig;
      btn.disabled = false;
    }
  } catch(e) {
    showUpdateResult(name, false, String(e));
    btn.textContent = orig;
    btn.disabled = false;
  }
}

function showUpdateResult(name, ok, output) {
  const existing = document.getElementById('update-result-modal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'update-result-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:#00000090;backdrop-filter:blur(4px);z-index:100;display:flex;align-items:center;justify-content:center;';
  modal.innerHTML = `
    <div style="background:#141720;border:1px solid #2a2d3a;border-radius:16px;width:min(700px,95vw);max-height:80vh;display:flex;flex-direction:column;">
      <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid #2a2d3a;">
        <span style="font-weight:600;color:${ok?'#4ade80':'#f87171'}">${ok?'✓':'✗'} ${name} ${ok?'Update succeeded':'Update failed'}</span>
        <button onclick="document.getElementById('update-result-modal').remove()" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;padding:4px 12px;border-radius:6px;cursor:pointer;">Close</button>
      </div>
      <pre style="font-family:'JetBrains Mono','Courier New',monospace;font-size:12px;line-height:1.6;overflow-y:auto;padding:16px;color:#cbd5e1;background:#0a0c12;border-radius:0 0 12px 12px;white-space:pre-wrap;word-break:break-all;">${output}</pre>
    </div>`;
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Auto-refresh
loadProjects();
setInterval(loadProjects, 4000);

// Close modal with Escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
});
</script>
</body>
</html>"""

if __name__ == "__main__":
    print("Dev Dashboard -> http://localhost:8888")
    uvicorn.run(app, host="127.0.0.1", port=8888, log_level="warning")
