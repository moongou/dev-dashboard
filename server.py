#!/usr/bin/env python3
"""
Dev Dashboard — Local Development Service Manager
Install: pip install fastapi uvicorn
Run: python server.py
Visit: http://localhost:9999
"""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
import signal
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
import wave
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI()
HOME = Path.home()
VOICE_SERVICES_HOME = HOME / "voice-services"
LOG_DIR = Path("/tmp/dev-dashboard")
LOG_DIR.mkdir(exist_ok=True)
SETTINGS_DIR = HOME / ".config/dev-dashboard"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
MODAL_AUTH_PATHS = (HOME / ".modal.toml", HOME / ".config/modal.toml")
ALLOWED_SETTINGS = (
  "TAVILY_API_KEY",
  "NVIDIA_API_KEY",
  "MODAL_TOKEN_ID",
  "MODAL_TOKEN_SECRET",
)
DEFAULT_EXEC_PATHS = (
  "/opt/homebrew/bin",
  "/usr/local/bin",
  "/usr/bin",
  "/bin",
  "/usr/sbin",
  "/sbin",
)
VIBEVOICE_ASR_PROJECT_ID = "vibevoice-asr-m3"
VIBEVOICE_ASR_HOTKEY_FILE = SETTINGS_DIR / "vibevoice_asr_hotkey.json"

# Running processes {id: Popen}
_procs: dict = {}
class ExternalProc:
  """Lightweight wrapper for a process not started by a live Popen

  Used when the start command detaches/execs into a different PID. The
  wrapper provides a minimal Popen-like interface used by stop logic.
  """
  def __init__(self, pid: int):
    self.pid = pid

  def poll(self):
    try:
      os.kill(self.pid, 0)
      return None
    except OSError:
      return 1

  def wait(self, timeout: float | None = None):
    deadline = time.monotonic() + (timeout or 0)
    while True:
      try:
        os.kill(self.pid, 0)
      except OSError:
        return 0
      if timeout is not None and time.monotonic() > deadline:
        raise TimeoutError()
      time.sleep(0.1)

  def kill(self):
    try:
      os.kill(self.pid, signal.SIGKILL)
    except Exception:
      pass
_projects_cache: dict[str, object] = {"data": None, "expires_at": 0.0}
_projects_cache_lock = asyncio.Lock()
_projects_executor = ThreadPoolExecutor(max_workers=8)

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
#   required_settings - Optional: list of settings keys required before start

PROJECTS = [
  {
    "id": "customs-params-frontend",
    "name": "海关参数库 Frontend",
    "desc": "Customs Parameters 参数管理界面 (port 5420)",
    "intro": (
      "React 18 + Vite + Tailwind 前端，提供 89 张海关业务参数表的浏览/检索/增删改，"
      "以及『数据溯源与更新』页（来源登记、采集批次、更新时间、多源冲突）。"
      "通过 /api 代理到 8420 后端。"
    ),
    "port": 5420,
    "group": "AI",
    "dir": str(HOME / "VS-CODE-PROJECT/Customs_Parameters/frontend"),
    "cmd": ["npm", "run", "dev", "--", "--port", "5420"],
    "url_path": "/",
    "has_git": False,
    "tags": ["海关", "参数库", "前端", "溯源"],
  },
  {
    "id": "customs-params-backend",
    "name": "海关参数库 Backend",
    "desc": "Customs Parameters FastAPI backend (port 8420)",
    "intro": (
      "FastAPI + SQLAlchemy + PostgreSQL(5433) 后端，提供通用参数表 CRUD、总览统计，"
      "以及 /api/provenance 溯源接口（来源、批次、冲突、按表更新汇总、手动更新）。"
    ),
    "port": 8420,
    "group": "AI",
    "dir": str(HOME / "VS-CODE-PROJECT/Customs_Parameters/backend"),
    "cmd": [".venv/bin/uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8420"],
    "url_path": "/docs",
    "has_git": False,
    "tags": ["海关", "参数库", "后端", "接口服务"],
  },
  {
    "id": "daydayup-frontend",
    "name": "DayDayUp Frontend",
    "desc": "天天向上 Vue 3 智能办公工作台 (port 5174)",
    "intro": (
      "Vue 3 + Tailwind CSS frontend for the DayDayUp intelligent office assistant. "
      "It proxies /api requests to the FastAPI backend on port 8000."
    ),
    "port": 5174,
    "group": "AI",
    "dir": str(HOME / "VS-CODE-PROJECT/DayDayUp/frontend"),
    "cmd": ["npm", "run", "dev", "--", "--port", "5174"],
    "url_path": "/",
    "has_git": False,
    "tags": ["智能办公", "前端", "网页界面"],
  },
  {
    "id": "daydayup-backend",
    "name": "DayDayUp Backend",
    "desc": "天天向上 FastAPI backend (port 8000)",
    "intro": (
      "FastAPI service for DayDayUp. Provides streaming chat, model routing, "
      "document parsing, semantic mapping, search integration, and task APIs."
    ),
    "port": 8000,
    "group": "AI",
    "dir": str(HOME / "VS-CODE-PROJECT/DayDayUp/backend"),
    "cmd": [".venv/bin/uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
    "url_path": "/docs",
    "has_git": False,
    "tags": ["智能办公", "后端", "接口服务"],
  },
  {
    "id": "deepanalyze-docker",
    "name": "DeepAnalyze Docker",
    "desc": "DeepAnalyze demo/chat CPU container (frontend 18400)",
    "intro": (
      "Local Docker Desktop packaging for the real DeepAnalyze demo/chat stack. "
      "Starts the backend API, workspace file server, and Next.js frontend together "
      "from the locally built deepanalyze-local image. "
      "Host ports: frontend 18400, backend 18200, file service 18100."
    ),
    "port": 18400,
    "group": "Docker",
    "dir": str(HOME / "IdeaProjects/DeepAnalyze"),
    "cmd": [
      "bash", "-lc",
      "docker rm -f deepanalyze-local >/dev/null 2>&1 || true; exec docker run --name deepanalyze-local -p 18400:4000 -p 18200:8200 -p 18100:8100 deepanalyze-local:2.0.11"
    ],
    "stop_cmd": ["docker", "rm", "-f", "deepanalyze-local"],
    "update_cmd": [
      "bash", "-lc",
      "cd /Users/m4max/IdeaProjects/DeepAnalyze && docker build --pull=false --build-arg BASE_IMAGE=mcr.microsoft.com/devcontainers/python:3.12-bookworm -f docker/Dockerfile.local -t deepanalyze-local:latest -t deepanalyze-local:2.0.11 ."
    ],
    "url_path": "/",
    "has_git": True,
  },
    # ── WiFi-DensePose ────────────────────────────────────────────────────────
    {
        "id": "wifi-densepose",
        "name": "WiFi-DensePose API",
        "desc": "WiFi-based human pose estimation API (port 8080)",
        "intro": (
            "FastAPI server for WiFi-DensePose — real-time human pose estimation "
            "using WiFi CSI (Channel State Information) and DensePose neural networks. "
            "Provides RESTful endpoints and WebSocket streaming for pose data."
        ),
        "port": 8090,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/RuView"),
        "cmd": [
            "env",
            "PYTHONPATH=" + str(HOME / "IdeaProjects/RuView/v1"),
            "/usr/local/bin/python3.13",
            "-m", "uvicorn",
            "v1.src.api.main:app",
            "--host", "0.0.0.0",
            "--port", "8090",
        ],
        "url_path": "/docs",
        "has_git": True,
        "update_cmd": ["bash", "-c", "cd " + str(HOME / "IdeaProjects/RuView") + " && git pull"],
    },
    {
        "id": "wifi-densepose-viz",
        "name": "WiFi-DensePose Visualizer",
        "desc": "OpenCV real-time pose skeleton visualizer (port 8091)",
        "intro": (
            "Flask + OpenCV MJPEG stream visualizer for WiFi-DensePose. "
            "Renders real-time human skeletons from the pose API as a video feed. "
            "Visit the root URL to see the live pose stream in the browser."
        ),
        "port": 8091,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/RuView"),
        "cmd": [
            "/usr/local/bin/python3.13",
            str(HOME / "IdeaProjects/RuView/wifi-densepose-viz.py"),
        ],
        "url_path": "/",
        "has_git": True,
        "update_cmd": ["bash", "-c", "cd " + str(HOME / "IdeaProjects/RuView") + " && git pull"],
    },
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
            "-c '/Users/m4max/VS-CODE-PROJECT/deer-flow/docker/nginx/nginx.local.conf' "
            "-p '/Users/m4max/VS-CODE-PROJECT/deer-flow' "
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
            ".venv/bin/langgraph",
            "dev", "--no-browser", "--port", "2024", "--n-jobs-per-worker", "1",
        ],
        "url_path": "/",
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && .venv/bin/pip install -r requirements.txt"],
    },
      {
        "id": "deepagents-async-subagent",
        "name": "DeepAgents Async Subagent",
        "desc": "DeepAgents async subagent FastAPI server (port 2025)",
        "intro": (
          "Self-hosted Agent Protocol example from the Deep Agents monorepo. "
          "Exposes a FastAPI service for async subagent orchestration, with "
          "thread/run endpoints and a simple /ok health check. "
          "Dependencies are managed with uv inside examples/async-subagent-server."
        ),
        "port": 2025,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/deepagents/deepagents/examples/async-subagent-server"),
        "cmd": [
          ".venv/bin/python",
          "-m",
          "uvicorn",
          "server:app",
          "--host",
          "127.0.0.1",
          "--port",
          "2025",
        ],
        "env": {
          "ANTHROPIC_AUTH_TOKEN": "8ed0ffdcba4e4727bf4e11224efddae7.zSngM8znQ9AZPK3McohwG1yF",
          "ANTHROPIC_BASE_URL": "https://ollama.com",
          "ANTHROPIC_DEFAULT_HAIKU_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_DEFAULT_OPUS_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_DEFAULT_SONNET_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_REASONING_MODEL": "kimi-k2.6:cloud",
        },
        "no_ui": True,
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && uv sync"],
      },
      {
        "id": "deepagents-deep-research",
        "name": "DeepAgents Deep Research",
        "desc": "Deep research LangGraph dev server (port 2027)",
        "intro": (
          "Deep research example from the Deep Agents monorepo. "
          "Runs a local LangGraph dev server with API docs on /docs and "
          "requires TAVILY_API_KEY for graph loading before the service can start."
        ),
        "port": 2027,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/deepagents/deepagents/examples/deep_research"),
        "cmd": [
          ".venv/bin/langgraph",
          "dev",
          "--host",
          "127.0.0.1",
          "--port",
          "2027",
          "--no-browser",
        ],
        "env": {
          "ANTHROPIC_AUTH_TOKEN": "8ed0ffdcba4e4727bf4e11224efddae7.zSngM8znQ9AZPK3McohwG1yF",
          "ANTHROPIC_BASE_URL": "https://ollama.com",
          "ANTHROPIC_DEFAULT_HAIKU_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_DEFAULT_OPUS_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_DEFAULT_SONNET_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_REASONING_MODEL": "kimi-k2.6:cloud",
          "TAVILY_API_KEY": "${TAVILY_API_KEY}",
        },
        "required_settings": ["TAVILY_API_KEY"],
        "url_path": "/docs",
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && uv sync"],
      },
      {
        "id": "deepagents-nvidia-deep-agent",
        "name": "DeepAgents NVIDIA Deep Agent",
        "desc": "Nemotron + Modal LangGraph dev server (port 2028)",
        "intro": (
          "NVIDIA Nemotron deep agent example from the Deep Agents monorepo. "
          "Runs a LangGraph dev server with a Modal-backed sandbox and requires "
          "TAVILY_API_KEY, NVIDIA_API_KEY, and Modal token credentials before startup."
        ),
        "port": 2028,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/deepagents/deepagents/examples/nvidia_deep_agent"),
        "cmd": [
          ".venv/bin/langgraph",
          "dev",
          "--host",
          "127.0.0.1",
          "--port",
          "2028",
          "--no-browser",
          "--allow-blocking",
        ],
        "env": {
          "ANTHROPIC_AUTH_TOKEN": "8ed0ffdcba4e4727bf4e11224efddae7.zSngM8znQ9AZPK3McohwG1yF",
          "ANTHROPIC_BASE_URL": "https://ollama.com",
          "ANTHROPIC_DEFAULT_HAIKU_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_DEFAULT_OPUS_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_DEFAULT_SONNET_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_MODEL": "kimi-k2.6:cloud",
          "ANTHROPIC_REASONING_MODEL": "kimi-k2.6:cloud",
          "TAVILY_API_KEY": "${TAVILY_API_KEY}",
          "NVIDIA_API_KEY": "${NVIDIA_API_KEY}",
          "MODAL_TOKEN_ID": "${MODAL_TOKEN_ID}",
          "MODAL_TOKEN_SECRET": "${MODAL_TOKEN_SECRET}",
        },
        "required_settings": [
          "TAVILY_API_KEY",
          "NVIDIA_API_KEY",
        ],
        "requires_modal_auth": True,
        "url_path": "/docs",
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && uv sync"],
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
            "/Users/m4max/miniconda3/envs/cosyvoice/bin/python "
            "runtime/python/fastapi/server.py --port 50000 "
            "--model_dir /Users/m4max/IdeaProjects/CosyVoice/pretrained_models/CosyVoice2-0.5B",
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
        "cmd": [".venv/bin/python", "app/server.py"],
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
      {
        "id": "qwen3-tts",
        "name": "Qwen3-TTS Fast",
        "desc": "Qwen3-TTS 0.6B CustomVoice fast local demo (port 7802)",
        "intro": (
          "Qwen3-TTS fast local Gradio demo configured for Apple Silicon Macs. "
          "Uses a Python 3.12 virtualenv, downloads weights from ModelScope for Mainland China, "
          "and starts the 0.6B CustomVoice model with MPS-friendly defaults."
        ),
        "port": 7802,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/QWEN3-TTS/Qwen3-TTS"),
        "cmd": [
          "bash",
          "-lc",
          "PORT=7802 HOST=127.0.0.1 bash scripts/run_local_m3max_demo.sh",
        ],
        "url_path": "/",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-lc",
          "git pull && source .venv/bin/activate && python -m pip install -U torch torchaudio modelscope -i https://pypi.tuna.tsinghua.edu.cn/simple && python -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple",
        ],
      },
      {
        "id": "qwen3-tts-hq",
        "name": "Qwen3-TTS HQ",
        "desc": "Qwen3-TTS 1.7B CustomVoice high-quality local demo (port 7803)",
        "intro": (
          "Qwen3-TTS high-quality local Gradio demo configured for Apple Silicon Macs. "
          "Uses the 1.7B CustomVoice model with ModelScope downloads, a Python 3.12 virtualenv, "
          "and lower queue concurrency to fit local MPS inference more comfortably."
        ),
        "port": 7803,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/QWEN3-TTS/Qwen3-TTS"),
        "cmd": [
          "bash",
          "-lc",
          "PORT=7803 HOST=127.0.0.1 bash scripts/run_local_m3max_hq.sh",
        ],
        "url_path": "/",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-lc",
          "git pull && source .venv/bin/activate && python -m pip install -U torch torchaudio modelscope -i https://pypi.tuna.tsinghua.edu.cn/simple && python -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple",
        ],
      },
      {
        "id": "qwen3-tts-clone",
        "name": "Qwen3-TTS Clone",
        "desc": "Qwen3-TTS 0.6B Base voice clone local demo (port 7804)",
        "intro": (
          "Qwen3-TTS local voice clone demo configured for Apple Silicon Macs. "
          "Uses the lighter 0.6B Base model with ModelScope downloads and a Python 3.12 virtualenv, "
          "so you can upload a reference clip and run local cloning through the built-in Gradio UI."
        ),
        "port": 7804,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/QWEN3-TTS/Qwen3-TTS"),
        "cmd": [
          "bash",
          "-lc",
          "PORT=7804 HOST=127.0.0.1 bash scripts/run_local_m3max_clone.sh",
        ],
        "url_path": "/",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-lc",
          "git pull && source .venv/bin/activate && python -m pip install -U torch torchaudio modelscope -i https://pypi.tuna.tsinghua.edu.cn/simple && python -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple",
        ],
      },
      {
        "id": "lumen-ai-m3max",
        "name": "Lumen AI (M3 Max)",
          "desc": "Lumen AI local explorer with local Ollama (port 7810)",
        "intro": (
          "Lumen AI local deployment optimized for Apple Silicon M3 Max. "
            "Uses local Ollama service as provider by default on http://127.0.0.1:11434/v1, "
            "and is isolated from cloud mode via .lumen_provider.local.env."
        ),
        "port": 7810,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/lumen AI/lumen"),
        "cmd": [
          "bash",
          "-lc",
            "LUMEN_PROVIDER_CONFIG=.lumen_provider.local.env PORT=7810 HOST=127.0.0.1 bash scripts/run_local_m3max.sh",
        ],
        "url_path": "/lumen_ai",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-lc",
          "git pull && bash scripts/setup_local_m3max.sh",
        ],
      },
        {
          "id": "lumen-ai-ollama-cloud",
          "name": "Lumen AI (Ollama Cloud)",
          "desc": "Lumen AI with Ollama cloud provider (port 7811)",
          "intro": (
            "Lumen AI cloud deployment using Ollama OpenAI-compatible endpoint. "
            "Runs on a dedicated local port and keeps start/stop/open controls in the dev dashboard. "
              "Reads provider credentials from the repo-local .lumen_provider.cloud.env."
          ),
          "port": 7811,
          "group": "AI",
          "dir": str(HOME / "VS-CODE-PROJECT/lumen AI/lumen"),
          "cmd": [
            "bash",
            "-lc",
              "LUMEN_PROVIDER_CONFIG=.lumen_provider.cloud.env PORT=7811 HOST=127.0.0.1 bash scripts/run_local_m3max.sh",
          ],
          "url_path": "/lumen_ai",
          "has_git": True,
          "update_cmd": [
            "bash",
            "-lc",
            "git pull && bash scripts/setup_local_m3max.sh",
          ],
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
            str(HOME / "IdeaProjects/FunASR/.venv/bin/python"),
            "runtime/python/http/server.py",
            "--port", "10096", "--host", "127.0.0.1",
        ],
        "no_ui": True,
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
    {
        "id": "funasr-wss",
        "name": "FunASR WebSocket",
        "desc": "FunASR WebSocket ASR server (port 10095, LAN accessible)",
        "intro": (
            "FunASR WebSocket server — provides real-time and offline speech recognition "
            "via WebSocket protocol on port 10095. Supports 2pass, online streaming, and "
            "offline modes. Binds to 0.0.0.0 so it is accessible from other devices on LAN. "
            "Used by the FunASR Test Page for browser-based ASR testing."
        ),
        "port": 10095,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/FunASR"),
        "cmd": [
            str(HOME / "IdeaProjects/FunASR/.venv/bin/python"),
            "runtime/python/websocket/funasr_wss_server.py",
            "--port", "10095", "--host", "0.0.0.0",
            "--certfile", "",
        ],
        "no_ui": True,
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
    {
        "id": "funasr-test",
        "name": "FunASR Test Page",
        "desc": "FunASR ASR browser test page (port 8079, LAN accessible)",
        "intro": (
            "A modern browser-based test page for FunASR speech recognition. "
            "Supports microphone recording and file upload, with 2pass/online/offline "
            "mode switching, ITN toggle, and hotword configuration. "
            "Auto-detects the ASR WebSocket server address from the current host IP. "
            "Binds to 0.0.0.0:8079 so it is accessible from other devices on LAN."
        ),
        "port": 8079,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/FunASR/web-pages/asr-test"),
        "cmd": ["python3", "server.py", "--port", "8079"],
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
      {
        "id": "capswriter-asr",
        "name": "CapsWriter Offline",
        "desc": "CapsWriter-Offline 原生语音输入服务（SenseVoice，端口 6016）",
        "intro": (
          "CapsWriter-Offline 原生服务端。基于 sherpa-onnx SenseVoice 模型，支持中/英/日/韩/粤语音识别。 "
          "WebSocket 协议，C/S 架构。服务端独立子进程运行 ASR 模型，Client 负责全局快捷键、录音、流式识别、 "
          "热词 RAG 替换、LLM 润色/问答、角色系统、文件转录等。完全离线，无需联网。"
        ),
        "port": 6016,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/CapsWriter-Offline"),
        "cmd": [".venv/bin/python", "core_server.py"],
        "no_ui": True,
        "has_git": True,
        "update_cmd": ["git", "pull"],
      },
      {
        "id": "capswriter-http-asr",
        "name": "CapsWriter HTTP ASR",
        "desc": "CapsWriter SenseVoice HTTP wrapper service (port 6701)",
        "intro": (
          "CapsWriter HTTP wrapper service for the local voice-services workspace. "
          "Exposes a simple /health + /transcribe API on port 6701 while keeping "
          "the existing SenseVoice model path and wrapper behavior unchanged."
        ),
        "port": 6701,
        "group": "AI",
        "dir": str(VOICE_SERVICES_HOME / "services/capswriter"),
        "cmd": ["bash", "start.sh"],
        "stop_cmd": ["bash", "stop.sh"],
        "no_ui": True,
      },
      {
        "id": "vosk-asr",
        "name": "Vosk ASR",
        "desc": "Vosk lightweight offline ASR service (port 6702)",
        "intro": (
          "Vosk speech recognition service. "
          "Lightweight offline ASR for low-resource, on-device transcription."
        ),
        "port": 6702,
        "group": "AI",
        "dir": str(VOICE_SERVICES_HOME / "services/vosk"),
        "cmd": ["bash", "start.sh"],
        "stop_cmd": ["bash", "stop.sh"],
        "no_ui": True,
      },
      {
        "id": "vibing-local-bridge",
        "name": "Vibing Local Bridge",
        "desc": "Vibing pipeline bridge with local ASR and configurable postprocess (port 8765)",
        "intro": (
          "Local replacement for Vibing's remote pipeline endpoint. "
          "It accepts Vibing WebSocket/HTTP pipeline traffic on port 8765, bridges ASR to local Vosk, "
          "and provides a settings page for semantic postprocessing providers, API keys, and model names."
        ),
        "port": 8765,
        "group": "AI",
        "dir": str(VOICE_SERVICES_HOME / "services/vibingbridge"),
        "cmd": ["bash", "start.sh"],
        "stop_cmd": ["bash", "stop.sh"],
        "url_path": "/settings",
      },
      {
        "id": "cheetah-asr",
        "name": "Picovoice Cheetah ASR",
        "desc": "Picovoice Cheetah streaming ASR service (port 6703)",
        "intro": (
          "Picovoice Cheetah edge streaming speech recognition service. "
          "Designed for real-time transcription with low latency on-device."
        ),
        "port": 6703,
        "group": "AI",
        "dir": str(VOICE_SERVICES_HOME / "services/cheetah"),
        "cmd": ["bash", "start.sh"],
        "stop_cmd": ["bash", "stop.sh"],
        "no_ui": True,
      },
      {
        "id": "vibevoice-asr-m3",
        "name": "VibeVoice ASR (M3)",
        "desc": "VibeVoice ASR + Right Option hold-to-talk (port 6708)",
        "intro": (
          "VibeVoice ASR local service for Apple Silicon Macs. "
          "Runs on MPS with a FastAPI endpoint and optional Right Option hold-to-talk. "
          "Press and hold Right Option to record, release to transcribe, and copy the text to clipboard."
        ),
        "port": 6708,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/VibeVoice/VibeVoice"),
        "cmd": [
          "bash",
          "-lc",
          "HOST=127.0.0.1 PORT=6708 HF_ENDPOINT=${HF_ENDPOINT:-https://hf-mirror.com} "
          "PYTORCH_ENABLE_MPS_FALLBACK=1 bash demo/run_vibevoice_asr_m3.sh",
        ],
        "url_path": "/docs",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-lc",
          "git pull && bash demo/install_vibevoice_asr_m3.sh",
        ],
      },
      {
        "id": "vibing-desktop",
        "name": "Vibing Desktop (语音识别)",
        "desc": "Vibing Electron 桌面语音识别应用 — 按键录音 → 本地 Whisper 识别 → AI 重排/整理 → 自动粘贴",
        "intro": (
          "Vibing 是一款基于 Electron 的本地优先语音输入工具。 "
          "按住热键录音，松开后通过本地 Whisper 完成语音识别，"
          "可选通过重排模型（硅基流动 / 阿里百炼 / Ollama 等）对转写文本进行语义整理，"
          "最终自动粘贴到当前光标位置并同步复制到剪贴板。\n\n"
          "支持多种语音识别后端（Whisper.cpp / faster-whisper）、"
          "多供应商 AI 整理模型以及灵活的热键配置。"
        ),
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/Vibing-ymg/Vibing"),
        "cmd": ["npm", "start"],
        "no_ui": True,
        "has_git": True,
        "update_cmd": ["bash", "-lc", "git pull && npm install"],
        "tags": ["语音识别", "桌面应用", "Electron", "Whisper", "本地服务"],
      },
      {
        "id": "stepaudio2-tts",
        "name": "Step-Audio 2 TTS",
        "desc": "Step-Audio 2 multimodal speech service (port 6705)",
        "intro": (
          "Step-Audio 2 speech service. "
          "End-to-end multimodal speech model for local voice generation pipelines."
        ),
        "port": 6705,
        "group": "AI",
        "dir": str(VOICE_SERVICES_HOME / "services/stepaudio2"),
        "cmd": ["bash", "start.sh"],
        "stop_cmd": ["bash", "stop.sh"],
        "no_ui": True,
        "has_git": True,
        "update_cmd": ["git", "-C", str(VOICE_SERVICES_HOME / "repos/Step-Audio2"), "pull"],
      },
      {
        "id": "fireredtts-1s",
        "name": "FireRedTTS-1S",
        "desc": "FireRedTTS-1S streaming TTS service (port 6706)",
        "intro": (
          "FireRedTTS-1S text-to-speech service. "
          "Supports low-latency streaming synthesis and voice cloning."
        ),
        "port": 6706,
        "group": "AI",
        "dir": str(VOICE_SERVICES_HOME / "services/fireredtts"),
        "cmd": ["bash", "start.sh"],
        "stop_cmd": ["bash", "stop.sh"],
        "no_ui": True,
        "has_git": True,
        "update_cmd": ["git", "-C", str(VOICE_SERVICES_HOME / "repos/FireRedTTS"), "pull"],
      },
      {
        "id": "openvoice-tts",
        "name": "OpenVoice TTS",
        "desc": "OpenVoice real-time voice cloning service (port 6707)",
        "intro": (
          "OpenVoice speech synthesis service. "
          "Real-time voice cloning for local TTS and style transfer scenarios."
        ),
        "port": 6707,
        "group": "AI",
        "dir": str(VOICE_SERVICES_HOME / "services/openvoice"),
        "cmd": ["bash", "start.sh"],
        "stop_cmd": ["bash", "stop.sh"],
        "no_ui": True,
        "has_git": True,
        "update_cmd": ["git", "-C", str(VOICE_SERVICES_HOME / "repos/OpenVoice"), "pull"],
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
            "/Users/m4max/docker-data/mineru-venv/bin/mineru-gradio "
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
            "/Users/m4max/docker-data/mineru-venv/bin/python "
            "-m mineru.cli.fast_api --host 127.0.0.1 --port 49680",
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
        "cmd": ["python3", "app.py"],
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
        "cmd": ["python3", "-m", "app.main"],
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
      {
        "id": "guanyu-workbench",
        "name": "观雨",
        "desc": "观雨智能分析工作台 (port 4000)",
        "intro": (
          "观雨海关风险分析智能体工作台。"
          "开发面板会以 CPU-only 非交互模式拉起本地前端工作台 http://localhost:4000，"
          "并同时启动后端 API http://localhost:8200 与文件服务 http://localhost:8100，"
          "方便在本机直接完成界面调试、流程验证与日常使用。"
        ),
        "port": 4000,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/DeepAnalyze"),
        "cmd": [
          "bash",
          "-lc",
          "./start.sh --backend cpu && for _ in $(seq 1 30); do lsof -tiTCP:4000 -sTCP:LISTEN >/dev/null 2>&1 && break; sleep 1; done && while lsof -tiTCP:4000 -sTCP:LISTEN >/dev/null 2>&1; do sleep 5; done",
        ],
        "stop_cmd": ["bash", "stop.sh"],
        "url_path": "/",
        "has_git": True,
        "update_cmd": ["git", "pull", "--ff-only"],
      },
      {
        "id": "osint-ai-framework-backend",
        "name": "OSINT AI Framework Backend",
        "desc": "Django API for the OSINT AI Framework (port 8011)",
        "intro": (
          "Django development server for the OSINT AI Framework. "
          "Runs the local API, admin site, and health endpoint on port 8011. "
          "Uses the repository-local virtual environment in backend/.venv."
        ),
        "port": 8011,
        "group": "Python",
        "dir": str(HOME / "VS-CODE-PROJECT/osint-AI-framework/osint-AI-framework/backend"),
        "cmd": [
          ".venv/bin/python",
          "manage.py",
          "runserver",
          "0.0.0.0:8011",
        ],
        "url_path": "/healthz/",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-c",
          "git pull && .venv/bin/pip install -e '.[dev]' && .venv/bin/python manage.py migrate",
        ],
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
        "cmd": ["python3", "server.py"],
        "has_git": True,
        "update_cmd": ["git", "pull"],
    },
    # ── ChatTTS ───────────────────────────────────────────────────────────────
    {
        "id": "chattts",
        "name": "ChatTTS",
        "desc": "ChatTTS generative speech model — Gradio WebUI (port 9998)",
        "intro": (
            "ChatTTS — a generative text-to-speech model designed for natural dialogue. "
            "Supports fine-grained prosody control, speaker embedding, streaming synthesis, "
            "and multi-speaker generation. "
            "Runs a Gradio WebUI on port 9998. "
            "Models loaded from local asset_hf/ directory (1.1 GB). "
            "Powered by Apple MPS fallback on M-series Macs."
        ),
        "port": 9998,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/ChatTTs/ChatTTS"),
        "cmd": [
            "bash", "-c",
            "PYTORCH_ENABLE_MPS_FALLBACK=1 "
            "venv/bin/python examples/web/webui.py "
            "--server_name 127.0.0.1 --server_port 9998 "
            "--custom_path ./asset_hf",
        ],
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && venv/bin/pip install -r requirements.txt"],
    },
    # ── Quantization ────────────────────────────────────────────────────────────
    {
        "id": "quantization",
        "name": "A股量化交易系统",
        "desc": "多因子模型A股量化交易系统 (port 8050)",
        "intro": (
            "基于多因子模型的A股量化交易系统，支持因子计算、选股策略、回测和可视化分析。 "
            "使用 Flask 提供 Web 服务，端口 8050。"
        ),
        "port": 8050,
        "group": "Python",
        "dir": str(HOME / "IdeaProjects/Quantization"),
        "cmd": [
            "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
            "web/app.py",
        ],
        "has_git": True,
        "update_cmd": [
            "bash", "-c",
            "git pull && /Library/Frameworks/Python.framework/Versions/3.13/bin/pip install -r requirements.txt",
        ],
    },
    # ── Agent Deck ──────────────────────────────────────────────────────────────
    {
        "id": "agent-deck",
        "name": "Agent Deck",
        "desc": "Agent-deck tmux session manager with web UI (port 8000)",
        "intro": (
            "Agent-deck — a tmux-based session manager for AI coding workflows. "
            "Provides a TUI for managing multiple tmux sessions and a web UI for remote access. "
            "Built with Go and Bubble Tea."
        ),
        "port": 8000,
        "group": "Tools",
        "dir": str(HOME / "IdeaProjects/agent-deck"),
        "cmd": [
            str(HOME / "IdeaProjects/agent-deck/build/agent-deck"),
            "web",
            "--listen", "0.0.0.0:8000",
        ],
        "has_git": True,
        "update_cmd": ["git", "pull"],
        "url_path": "/",
    },
      # ── Agent Browser ───────────────────────────────────────────────────────
      {
        "id": "agent-browser-dashboard",
        "name": "agent-browser Dashboard",
        "desc": "agent-browser observability dashboard (port 4849)",
        "intro": (
          "Embedded observability dashboard served directly by the compiled agent-browser "
          "binary. Shows live browser viewports, activity feeds, console output, network "
          "events, storage, and AI chat for local sessions. Runs on port 4849 here so it "
          "does not collide with the default dashboard port 4848."
        ),
        "port": 4849,
        "group": "Tools",
        "dir": str(HOME / "VS-CODE-PROJECT/AGENT BROWSER/agent-browser"),
        "cmd": [
          "env",
          "AGENT_BROWSER_DASHBOARD=1",
          "AGENT_BROWSER_DASHBOARD_PORT=4849",
          str(HOME / "VS-CODE-PROJECT/AGENT BROWSER/agent-browser/bin/agent-browser-darwin-arm64"),
        ],
        "url_path": "/",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-lc",
          "git pull && pnpm install && cargo build --release --manifest-path cli/Cargo.toml && node scripts/copy-native.js",
        ],
      },
    # ── RAG-Anything ────────────────────────────────────────────────────────────
    {
        "id": "raganything-ui",
        "name": "RAG-Anything UI",
        "desc": "RAG-Anything Task Console (port 9997)",
        "intro": (
            "Web-based task manager for RAG-Anything multimodal RAG system. "
            "Provides a GUI to run all example scripts, configure environment variables, "
            "and view live logs without touching the terminal."
        ),
        "port": 9997,
        "group": "AI",
        "dir": str(HOME / "IdeaProjects/RAG-Anything"),
        "cmd": [
            str(HOME / "IdeaProjects/RAG-Anything/.venv/bin/python"),
            "ui/app.py",
            "9997",
        ],
        "url_path": "/",
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && uv sync --all-extras"],
    },
    # ── TrendRadar MCP ────────────────────────────────────────────────────────
    {
        "id": "trendradar-mcp",
        "name": "TrendRadar MCP",
        "desc": "TrendRadar 热点新闻聚合 MCP HTTP 服务 (port 3335)",
        "intro": (
            "TrendRadar — 热点新闻聚合与分析工具的 MCP Server。 "
            "提供新闻热点查询、聚合推送等功能。通过 HTTP 传输协议在 port 3335 上运行。"
        ),
        "port": 3335,
        "group": "Python",
        "dir": str(HOME / "IdeaProjects/TrendRadar"),
        "cmd": [
            str(HOME / ".local/bin/uv"),
            "--directory", str(HOME / "IdeaProjects/TrendRadar"),
            "run", "python", "-m", "mcp_server.server",
            "--transport", "http",
            "--host", "0.0.0.0",
            "--port", "3335",
        ],
        "no_ui": True,
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && uv sync"],
    },
      # ── LangExtract ───────────────────────────────────────────────────────────
      {
        "id": "langextract-api",
        "name": "LangExtract API",
        "desc": "LangExtract FastAPI service for structured extraction (port 8010)",
        "intro": (
          "Local FastAPI wrapper for the LangExtract Python library. "
          "Provides /health, /docs, and /extract endpoints so LangExtract can "
          "be used as a managed local HTTP service."
        ),
        "port": 8010,
        "group": "Python",
        "dir": str(HOME / "VS-CODE-PROJECT/langextract/langextract"),
        "cmd": [
          str(HOME / "VS-CODE-PROJECT/langextract/langextract/.venv/bin/python"),
          "scripts/langextract_service.py",
          "--host",
          "127.0.0.1",
          "--port",
          "8010",
        ],
        "url_path": "/docs",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-c",
          "git pull && .venv/bin/python -m pip install -e '.[test,notebook,openai,service]'",
        ],
      },
    # ── Crawl4AI ─────────────────────────────────────────────────────────────
    {
        "id": "crawl4ai",
        "name": "Crawl4AI",
      "desc": "Crawl4AI API + monitoring dashboard (port 11235)",
        "intro": (
        "Local Crawl4AI service for the current repository. Starts the deploy/docker "
        "FastAPI server directly from the checked-out source tree, exposing the "
        "Crawl4AI API, monitoring dashboard, and playground on port 11235. "
        "Open /dashboard for live metrics or /playground for interactive API testing."
        ),
        "port": 11235,
      "group": "Python",
      "dir": str(HOME / "VS-CODE-PROJECT/crawl4ai/crawl4ai"),
      "cmd": [
        "bash",
        "-c",
        "cd deploy/docker && ../../.venv/bin/python3.13 -m uvicorn server:app --host 127.0.0.1 --port 11235",
      ],
        "url_path": "/dashboard",
        "has_git": True,
      "update_cmd": [
        "bash",
        "-c",
        "git pull && .venv/bin/pip install -e . && .venv/bin/pip install -r deploy/docker/requirements.txt",
      ],
    },
    # ── WorldMonitor ────────────────────────────────────────────────────────────
    {
        "id": "worldmonitor",
        "name": "WorldMonitor",
        "desc": "实时全球情报监控平台 (port 3002)",
        "intro": (
            "WorldMonitor 是一个实时全球情报监控平台，提供多维度数据可视化、供应链韧性分析、"
            "地缘政治追踪、自然灾害监测等功能。基于 Vite + React + TypeScript 构建。"
        ),
        "port": 3002,
        "group": "Node",
        "dir": str(HOME / "IdeaProjects/worldmonitor"),
        "cmd": ["npx", "vite", "--port", "3002"],
        "has_git": True,
        "update_cmd": ["bash", "-c", "git pull && npm ci"],
    },
      {
        "id": "chartdb",
        "name": "ChartDB",
        "desc": "ChartDB 本地开发服务 (port 3010)",
        "intro": (
          "ChartDB 开源数据库图编辑器本地开发实例。"
          "针对 Apple Silicon M3 Max 优化了 Node 内存和线程池参数，"
          "在复杂导入与构建场景下更稳定。"
          "该项目本身不需要下载模型权重；若使用 AI 功能，请配置 OPENAI 兼容接口。"
        ),
        "port": 3010,
        "group": "Node",
        "dir": str(HOME / "VS-CODE-PROJECT/ChartDB/chartdb"),
        "cmd": [
          "bash",
          "-lc",
          "NODE_OPTIONS='--max-old-space-size=16384' UV_THREADPOOL_SIZE=32 CHOKIDAR_USEPOLLING=0 npm run dev -- --host 127.0.0.1 --port 3010",
        ],
        "url_path": "/",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-lc",
          "git pull && npm install --prefer-offline --no-audit --fund=false",
        ],
      },
      {
        "id": "osint-ai-framework-frontend",
        "name": "OSINT AI Framework Frontend",
        "desc": "Vite frontend for the OSINT AI Framework (port 3000)",
        "intro": (
          "React + TypeScript frontend for the OSINT AI Framework. "
          "Configured to call the local Django backend on port 8011 via "
          "VITE_API_BASE_URL."
        ),
        "port": 3000,
        "group": "Node",
        "dir": str(HOME / "VS-CODE-PROJECT/osint-AI-framework/osint-AI-framework/frontend"),
        "cmd": ["npx", "vite", "--host", "0.0.0.0", "--port", "3000"],
        "env": {
          "VITE_API_BASE_URL": "http://localhost:8011",
        },
        "has_git": True,
        "update_cmd": [
          "bash",
          "-c",
          "git pull && npm install && npx playwright install",
        ],
      },
      {
        "id": "n8n",
        "name": "N8N",
        "desc": "N8N workflow automation — pnpm dev server (port 5678)",
        "intro": (
          "Fair-code workflow automation platform with native AI capabilities. "
          "This is the development build running via pnpm in the n8n repository."
        ),
        "port": 5678,
        "group": "Node",
        "dir": str(HOME / "VS-CODE-PROJECT/N8N/n8n"),
        "cmd": [
          "bash",
          "-lc",
          "pnpm exec dotenvx run -f .env.local -- pnpm start",
        ],
        "url_path": "/",
        "has_git": True,
        "pinned": True,
        "update_restart": True,
        "update_cmd": [
          "bash",
          "-lc",
          "git pull && NPM_CONFIG_REGISTRY=https://registry.npmmirror.com pnpm install --ignore-scripts && NODE_OPTIONS=--max-old-space-size=32768 TURBO_CONCURRENCY=12 pnpm build > build.log 2>&1",
        ],
      },
      {
        "id": "dify",
        "name": "Dify",
        "desc": "LLM app development platform (Docker Compose, ports 80/443)",
        "intro": (
          "Dify is an open-source LLM application development platform. "
          "Runs via Docker Compose with nginx on ports 80 and 443. "
          "Provides AI workflow orchestration, RAG, and agent capabilities."
        ),
        "port": 80,
        "group": "Docker",
        "dir": str(HOME / "Hermes Application/dify/dify/docker"),
        "cmd": [
          "bash", "-c",
          "docker compose up -d",
        ],
        "stop_cmd": [
          "bash", "-c",
          "docker compose down",
        ],
        "url_path": "/",
        "has_git": True,
        "update_cmd": [
          "bash", "-c",
          "git pull && docker compose pull && docker compose up -d",
        ],
      },
      {
        "id": "huaxia-adventure",
        "name": "山河之旅",
        "desc": "中国地理文化探险游戏 MVP 原型",
        "intro": (
          "基于真实中国经纬度的旅行探险游戏。玩家在中国地图上探索城市、收集地标、"
          "回答地理文化问题获取金币。支持多个角色皮肤、AI 出题、题库管理、赛季排行等功能。"
          "使用 Vite + React + TypeScript 构建。"
        ),
        "port": 5173,
        "group": "Node",
        "dir": str(HOME / "4033-Travel in China"),
        "cmd": [
          "bash",
          "-lc",
          "/opt/homebrew/bin/npm run dev -- --host 0.0.0.0",
        ],
        "stop_cmd": [
          "pkill",
          "-f",
          "vite.*--host",
        ],
        "url_path": "/",
        "has_git": True,
      },
      {
        "id": "auditmind",
        "name": "AuditMind",
        "desc": "智能审计工作台（FastAPI + React）",
        "intro": (
          "AuditMind 审计智能体原型，覆盖规定学习、违规清单、特征推理、"
          "数据验证与报告输出。开发面板启动时会同时拉起后端 8030 和前端 5266。"
        ),
        "port": 5266,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/AuditMind"),
        "cmd": [
          "bash",
          "-lc",
          "AUDITMIND_BACKEND_PORT=8030 AUDITMIND_FRONTEND_PORT=5266 PORT=5266 VITE_API_BASE_URL=http://127.0.0.1:8030/api scripts/dev-dashboard-start.sh",
        ],
        "stop_cmd": [
          "bash",
          "-lc",
          "scripts/dev-dashboard-stop.sh",
        ],
        "url_path": "/",
        "has_git": True,
      },
      {
        "id": "customs-admin-cases",
        "name": "Customs Admin Cases",
        "desc": "海关行政处罚案件平台（FastAPI + /ui）",
        "intro": (
          "海关行政处罚案件采集与检索平台。"
          "后端提供案件与报告 API，并在同端口挂载 /ui 下载态势仪表板。"
          "面板启动时会使用 backend/.venv312 在 8012 端口拉起服务。"
        ),
        "port": 8012,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/Customs-Admin-Cases/backend"),
        "cmd": [
          "bash",
          "-lc",
          "/Users/m4max/VS-CODE-PROJECT/Customs-Admin-Cases/backend/.venv312/bin/uvicorn app.main:app --host 127.0.0.1 --port 8012",
        ],
        "url_path": "/ui/",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-lc",
          "cd /Users/m4max/VS-CODE-PROJECT/Customs-Admin-Cases && git pull",
        ],
      },
      {
        "id": "openhuman-dev",
        "name": "OpenHuman Dev",
        "desc": "OpenHuman 本地开发实例（前端 18000，core 18001）",
        "intro": (
          "OpenHuman 本地源码开发服务。"
          "面板启动时会先拉起 openhuman-core，再启动 Vite 前端，"
          "并把前端固定到 18000、core 固定到 18001，便于统一从 dashboard 启停。"
        ),
        "port": 18000,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/Openhuman"),
        "cmd": [
          "bash",
          "-lc",
          "bash ./scripts/dev-dashboard-start.sh",
        ],
        "env": {
          "GGML_NATIVE": "OFF",
          "OPENHUMAN_DEV_PORT": "18000",
          "OPENHUMAN_CORE_PORT": "18001",
          "OPENHUMAN_CORE_RPC_URL": "http://127.0.0.1:18001/rpc",
          "VITE_OPENHUMAN_CORE_RPC_URL": "http://127.0.0.1:18001/rpc",
        },
        "url_path": "/",
        "has_git": True,
        "update_cmd": [
          "bash",
          "-lc",
          "git pull --ff-only && git submodule update --init --recursive && pnpm install",
        ],
      },
    # ── CodeGraph ─────────────────────────────────────────────────────────────
    {
        "id": "codegraph",
        "name": "CodeGraph",
        "desc": "语义代码智能引擎，为 Claude Code 提供代码知识图谱 (port 7474)",
        "intro": (
            "本地优先的代码智能系统，基于 tree-sitter 构建语义知识图谱，支持 19+ 种语言。"
            "94% 更少工具调用 · 77% 更快探索 · 100% 本地运行。"
            "提供 MCP 服务器接口供 Claude Code 使用，同时运行状态仪表板于 port 7474。"
        ),
        "port": 7474,
        "group": "工具链",
        "dir": str(HOME / "VS-CODE-PROJECT/Codegraph"),
        "cmd": [
          "node",
          str(HOME / "VS-CODE-PROJECT/Codegraph/devserver.js"),
        ],
        "env": {"PORT": "7474"},
        "url_path": "/",
        "has_git": True,
        "update_cmd": [
            "bash",
            "-c",
            "cd " + str(HOME / "VS-CODE-PROJECT/Codegraph") + " && git -c http.proxy='' -c https.proxy='' pull && npm install && npm run build",
        ],
    },
    # ── GatherInfo ───────────────────────────────────────────────────────────
    {
        "id": "gatherinfo",
        "name": "GatherInfo",
        "desc": "全球跨境贸易情报平台（FastAPI + Vite）",
        "intro": (
          "汇聚海关与贸易数据，提供风险评分、情报查询与仿真工具。"
          "面板启动时会在后端 8108 与前端 5178 上运行。"
        ),
        "port": 5178,
        "group": "AI",
        "dir": str(HOME / "VS-CODE-PROJECT/GatherInfo"),
        "cmd": ["bash", "-lc", "./scripts/dev.sh"],
        "url_path": "/",
        "has_git": False,
    },
]

BY_ID = {p["id"]: p for p in PROJECTS}

# ─── Utility Functions ────────────────────────────────────────────────────────


def port_pids(port: int, *, listeners_only: bool = False) -> list[int]:
  cmd = ["lsof", "-ti", f"TCP:{port}"]
  if listeners_only:
    cmd.extend(["-sTCP:LISTEN"])

  r = subprocess.run(
    cmd,
    capture_output=True,
    text=True,
  )
  return [int(x) for x in r.stdout.split() if x.isdigit()]


def clear_stale_port_processes(port: int, *, listeners_only: bool = False) -> list[int]:
  stale_pids = port_pids(port, listeners_only=listeners_only)
  if not stale_pids:
    return []

  for process_id in stale_pids:
    try:
      os.kill(process_id, signal.SIGTERM)
    except OSError:
      pass

  deadline = time.monotonic() + 2.0
  while time.monotonic() < deadline:
    remaining = port_pids(port, listeners_only=listeners_only)
    if not remaining:
      return []
    time.sleep(0.1)

  for process_id in remaining:
    try:
      os.kill(process_id, signal.SIGKILL)
    except OSError:
      pass

  time.sleep(0.2)
  return port_pids(port)


def empty_settings_state() -> dict[str, object]:
  return {"secrets": {}, "tags": [], "project_tags": {}}


def normalize_tag_label(value: object) -> str:
  if not isinstance(value, str):
    return ""
  return " ".join(value.split()).strip()


def unique_strings(values: list[str]) -> list[str]:
  seen: set[str] = set()
  result: list[str] = []
  for value in values:
    normalized = normalize_tag_label(value)
    if not normalized:
      continue
    key = normalized.casefold()
    if key in seen:
      continue
    seen.add(key)
    result.append(normalized)
  return result


def normalize_settings_state(payload: object) -> dict[str, object]:
  state = empty_settings_state()
  if not isinstance(payload, dict):
    return state

  raw_secrets = payload.get("secrets")
  if not isinstance(raw_secrets, dict):
    raw_secrets = payload

  secrets = {
    key: value.strip()
    for key, value in raw_secrets.items()
    if key in ALLOWED_SETTINGS and isinstance(value, str) and value.strip()
  }

  raw_project_tags = payload.get("project_tags", {})
  candidate_tags: list[str] = []

  raw_tags = payload.get("tags", [])
  if isinstance(raw_tags, list):
    for tag in raw_tags:
      normalized = normalize_tag_label(tag)
      if normalized:
        candidate_tags.append(normalized)

  if isinstance(raw_project_tags, dict):
    for tag_list in raw_project_tags.values():
      if not isinstance(tag_list, list):
        continue
      for tag in tag_list:
        normalized = normalize_tag_label(tag)
        if normalized:
          candidate_tags.append(normalized)

  tags = unique_strings(candidate_tags)
  allowed_tags = {tag.casefold(): tag for tag in tags}
  project_tags: dict[str, list[str]] = {}

  if isinstance(raw_project_tags, dict):
    for project_id, tag_list in raw_project_tags.items():
      if project_id not in BY_ID or not isinstance(tag_list, list):
        continue

      normalized_tags: list[str] = []
      for tag in tag_list:
        normalized = normalize_tag_label(tag)
        if not normalized:
          continue
        canonical = allowed_tags.get(normalized.casefold())
        if canonical:
          normalized_tags.append(canonical)

      unique_tags = unique_strings(normalized_tags)
      if unique_tags:
        project_tags[project_id] = unique_tags

  state["secrets"] = secrets
  state["tags"] = tags
  state["project_tags"] = project_tags
  return state


def load_settings_state() -> dict[str, object]:
  if not SETTINGS_FILE.exists():
    return empty_settings_state()

  try:
    raw = json.loads(SETTINGS_FILE.read_text())
  except (OSError, json.JSONDecodeError):
    return empty_settings_state()

  return normalize_settings_state(raw)


def public_settings() -> dict[str, object]:
  state = load_settings_state()
  secrets = state.get("secrets", {})
  if not isinstance(secrets, dict):
    secrets = {}

  tags = state.get("tags", [])
  if not isinstance(tags, list):
    tags = []

  project_tags = state.get("project_tags", {})
  if not isinstance(project_tags, dict):
    project_tags = {}

  return {
    "secrets": {key: secrets.get(key, "") for key in ALLOWED_SETTINGS},
    "tags": tags,
    "project_tags": project_tags,
  }


def save_settings(payload: dict[str, object]) -> dict[str, object]:
  SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
  normalized = normalize_settings_state(payload)
  SETTINGS_FILE.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")
  return normalized


def _normalize_bool(value: object, default: bool) -> bool:
  if isinstance(value, bool):
    return value
  if isinstance(value, (int, float)):
    return bool(value)
  if isinstance(value, str):
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on", "enabled"}:
      return True
    if lowered in {"0", "false", "no", "off", "disabled"}:
      return False
  return default


def _normalize_hotkey_mode(value: object, default: str = "toggle") -> str:
  if isinstance(value, str):
    mode = value.strip().lower()
    if mode in {"toggle", "hold"}:
      return mode
  return default


def default_vibevoice_hotkey_state() -> dict[str, object]:
  return {
    "hotkey_enabled": True,
    "hotkey_mode": "toggle",
    "paste_at_cursor": True,
    "hotkey_context_info": "",
  }


def normalize_vibevoice_hotkey_state(payload: object) -> dict[str, object]:
  state = default_vibevoice_hotkey_state()
  if not isinstance(payload, dict):
    return state

  state["hotkey_enabled"] = _normalize_bool(
    payload.get("hotkey_enabled"),
    bool(state["hotkey_enabled"]),
  )
  state["hotkey_mode"] = _normalize_hotkey_mode(
    payload.get("hotkey_mode"),
    str(state["hotkey_mode"]),
  )
  state["paste_at_cursor"] = _normalize_bool(
    payload.get("paste_at_cursor"),
    bool(state["paste_at_cursor"]),
  )

  context_value = payload.get("hotkey_context_info")
  if isinstance(context_value, str):
    # Keep multi-line context if users want to provide hotwords or hints.
    state["hotkey_context_info"] = context_value.strip()[:2000]

  return state


def load_vibevoice_hotkey_state() -> dict[str, object]:
  if not VIBEVOICE_ASR_HOTKEY_FILE.exists():
    return default_vibevoice_hotkey_state()

  try:
    payload = json.loads(VIBEVOICE_ASR_HOTKEY_FILE.read_text())
  except Exception:
    return default_vibevoice_hotkey_state()

  return normalize_vibevoice_hotkey_state(payload)


def save_vibevoice_hotkey_state(payload: object) -> dict[str, object]:
  SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
  normalized = normalize_vibevoice_hotkey_state(payload)
  VIBEVOICE_ASR_HOTKEY_FILE.write_text(
    json.dumps(normalized, indent=2, sort_keys=True) + "\n"
  )
  return normalized


def resolve_project_env(
  proj: dict,
  settings_state: dict[str, object] | None = None,
) -> dict[str, str]:
  resolved = dict(os.environ)
  existing_path = resolved.get("PATH", "")
  path_parts = [item for item in DEFAULT_EXEC_PATHS if item]
  path_parts.extend(item for item in existing_path.split(os.pathsep) if item)
  resolved["PATH"] = os.pathsep.join(dict.fromkeys(path_parts))
  settings = settings_state or load_settings_state()
  secrets = settings.get("secrets", {})
  if not isinstance(secrets, dict):
    secrets = {}

  for key, value in proj.get("env", {}).items():
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
      setting_key = value[2:-1]
      resolved_value = secrets.get(setting_key) or os.environ.get(setting_key)
      if resolved_value:
        resolved[key] = resolved_value
      continue

    resolved[key] = value

  if proj.get("id") == VIBEVOICE_ASR_PROJECT_ID:
    hotkey_state = load_vibevoice_hotkey_state()
    hotkey_enabled = bool(hotkey_state.get("hotkey_enabled", True))
    resolved["ENABLE_RIGHT_OPTION"] = "1" if hotkey_enabled else "0"
    resolved["HOTKEY_MODE"] = _normalize_hotkey_mode(
      hotkey_state.get("hotkey_mode"),
      "toggle",
    )
    paste_at_cursor = bool(hotkey_state.get("paste_at_cursor", True))
    resolved["HOTKEY_PASTE_AT_CURSOR"] = "1" if paste_at_cursor else "0"

    context_info = str(hotkey_state.get("hotkey_context_info") or "").strip()
    if context_info:
      resolved["HOTKEY_CONTEXT_INFO"] = context_info
    else:
      resolved.pop("HOTKEY_CONTEXT_INFO", None)

  return resolved


def has_modal_auth(env: dict[str, str]) -> bool:
  if env.get("MODAL_TOKEN_ID") and env.get("MODAL_TOKEN_SECRET"):
    return True
  return any(path.exists() for path in MODAL_AUTH_PATHS)


def missing_project_requirements(proj: dict, env: dict[str, str]) -> list[str]:
  missing = [name for name in proj.get("required_settings", []) if not env.get(name)]
  if proj.get("requires_modal_auth") and not has_modal_auth(env):
    missing.append("Modal auth (MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or modal setup)")
  return missing


def is_running(port: int) -> bool:
  for host in ("127.0.0.1", "::1"):
    try:
      with socket.create_connection((host, port), timeout=0.2):
        return True
    except OSError:
      continue
  return False


def http_ready(port: int) -> bool:
    """Check if a service is ready by probing both IPv4 and IPv6."""
    candidates = [("127.0.0.1", port), ("::1", port)]
    for host, p in candidates:
        try:
            s = socket.create_connection((host, p), timeout=2)
            s.close()
            return True
        except Exception:
            continue
    return False


@lru_cache(maxsize=None)
def is_git_repo(directory: str) -> bool:
    r = subprocess.run(
        ["git", "-C", directory, "rev-parse", "--git-dir"],
        capture_output=True,
    )
    return r.returncode == 0


def project_status(
  pid: str,
  settings_state: dict[str, object] | None = None,
) -> dict:
    proj = BY_ID[pid]
    port = proj.get("port")
    running = (pid in _procs and _procs[pid].poll() is None) if port is None else is_running(port)
    managed = pid in _procs and _procs[pid].poll() is None
    pids = [_procs[pid].pid] if managed else []
    has_git = "update_cmd" in proj or is_git_repo(proj["dir"])
    env = resolve_project_env(proj, settings_state)
    missing_requirements = missing_project_requirements(proj, env)

    tags: list[str] = []
    if settings_state:
      project_tags = settings_state.get("project_tags", {})
      if isinstance(project_tags, dict):
        tags = list(project_tags.get(pid, []))

    return {
      "running": running,
      "managed": managed,
      "pids": pids,
      "has_git": has_git,
      "tags": tags,
      "missing_requirements": missing_requirements,
    }


def invalidate_projects_cache() -> None:
  _projects_cache["data"] = None
  _projects_cache["expires_at"] = 0.0


def _project_with_status(project: dict, settings_state: dict[str, object]) -> dict:
  return {**project, **project_status(project["id"], settings_state)}


def _build_projects_snapshot() -> list[dict]:
  settings_state = load_settings_state()
  return [_project_with_status(project, settings_state) for project in PROJECTS]


ASR_PLAYGROUND_SERVICES = {
  "capswriter-asr": {
    "name": "CapsWriter",
    "transport": "websocket",
    "url": "ws://127.0.0.1:6016",
  },
  "vosk-asr": {
    "name": "Vosk",
    "transport": "http",
    "url": "http://127.0.0.1:6702/transcribe",
  },
}


def _build_multipart_body(
  *,
  fields: dict[str, str],
  files: dict[str, tuple[str, bytes, str]],
) -> tuple[bytes, str]:
  boundary = f"----DevDashboardBoundary{int(time.time() * 1000)}"
  body = bytearray()

  for name, value in fields.items():
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
    body.extend(value.encode())
    body.extend(b"\r\n")

  for name, (filename, content, content_type) in files.items():
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
      f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
    body.extend(content)
    body.extend(b"\r\n")

  body.extend(f"--{boundary}--\r\n".encode())
  return bytes(body), boundary


async def _transcribe_with_vosk(audio_bytes: bytes, filename: str) -> dict[str, object]:
  payload, boundary = _build_multipart_body(
    fields={},
    files={"audio": (filename or "sample.wav", audio_bytes, "audio/wav")},
  )
  request = urllib.request.Request(
    ASR_PLAYGROUND_SERVICES["vosk-asr"]["url"],
    data=payload,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="POST",
  )

  try:
    with urllib.request.urlopen(request, timeout=120) as response:
      body = response.read().decode("utf-8")
      return json.loads(body)
  except urllib.error.HTTPError as exc:
    detail = exc.read().decode("utf-8", errors="ignore")
    raise HTTPException(exc.code, detail or "Vosk request failed") from exc
  except urllib.error.URLError as exc:
    raise HTTPException(502, f"Vosk unavailable: {exc.reason}") from exc


async def _transcribe_with_capswriter(audio_bytes: bytes) -> dict[str, object]:
  try:
    import io
    import websockets
  except ImportError as exc:
    raise HTTPException(500, "websockets package is required for CapsWriter proxying") from exc

  try:
    with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
      sample_rate = wav_file.getframerate()
      channels = wav_file.getnchannels()
      sample_width = wav_file.getsampwidth()
      frames = wav_file.readframes(wav_file.getnframes())
  except wave.Error as exc:
    raise HTTPException(400, f"Invalid WAV file: {exc}") from exc

  if sample_rate != 16000 or channels != 1 or sample_width != 2:
    raise HTTPException(400, "CapsWriter requires 16kHz mono 16-bit PCM WAV")

  samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
  float_bytes = struct.pack(
    "<" + "f" * len(samples),
    *[sample / 32768.0 for sample in samples],
  )
  task_id = f"dev-dashboard-{int(time.time() * 1000)}"
  payload = {
    "source": "file",
    "is_final": True,
    "task_id": task_id,
    "context": "dev-dashboard-ui",
    "seg_duration": 15,
    "seg_overlap": 0.2,
    "time_start": time.time(),
    "data": base64.b64encode(float_bytes).decode("ascii"),
  }

  try:
    async with websockets.connect(
      ASR_PLAYGROUND_SERVICES["capswriter-asr"]["url"],
      subprotocols=["binary"],
      max_size=None,
      proxy=None,
    ) as websocket:
      await websocket.send(json.dumps(payload))
      while True:
        message = await asyncio.wait_for(websocket.recv(), timeout=120)
        result = json.loads(message)
        if result.get("task_id") == task_id and result.get("is_final"):
          return result
  except TimeoutError as exc:
    raise HTTPException(504, "CapsWriter timed out while transcribing audio") from exc
  except Exception as exc:
    raise HTTPException(502, f"CapsWriter unavailable: {exc}") from exc


# ─── API ──────────────────────────────────────────────────────────────────────


@app.get("/")
async def index():
    return HTMLResponse(HTML)


@app.get("/tools/asr-playground")
async def asr_playground():
  return HTMLResponse(ASR_PLAYGROUND_HTML)


@app.get("/tools/vibevoice-asr-control")
async def vibevoice_asr_control():
  return HTMLResponse(VIBEVOICE_ASR_CONTROL_HTML)


@app.get("/api/vibevoice-asr/config")
async def get_vibevoice_asr_config():
  return {"ok": True, "config": load_vibevoice_hotkey_state()}


@app.post("/api/vibevoice-asr/config")
async def update_vibevoice_asr_config(payload: dict[str, object]):
  config = save_vibevoice_hotkey_state(payload)
  return {"ok": True, "config": config}


@app.get("/api/vibevoice-asr/health")
async def get_vibevoice_asr_health():
  project = BY_ID.get(VIBEVOICE_ASR_PROJECT_ID)
  if not project:
    return {"ok": False, "running": False, "msg": "project not found", "health": None}

  if not is_running(project["port"]):
    return {"ok": True, "running": False, "health": None}

  target = f"http://127.0.0.1:{project['port']}/health"
  try:
    with urllib.request.urlopen(target, timeout=2.0) as response:
      health = json.loads(response.read().decode("utf-8"))
    return {"ok": True, "running": True, "health": health}
  except Exception as exc:
    return {"ok": False, "running": True, "msg": str(exc), "health": None}


@app.get("/api/settings")
async def get_settings():
  return public_settings()


@app.post("/api/settings")
async def update_settings(payload: dict[str, object]):
  settings = save_settings(payload)
  invalidate_projects_cache()
  secrets = settings.get("secrets", {})
  if not isinstance(secrets, dict):
    secrets = {}
  return {
    "ok": True,
    "settings": {
      "secrets": {key: secrets.get(key, "") for key in ALLOWED_SETTINGS},
      "tags": settings.get("tags", []),
      "project_tags": settings.get("project_tags", {}),
    },
  }


@app.post("/api/asr/transcribe")
async def asr_transcribe(payload: dict[str, object]):
  service_id = str(payload.get("service_id") or "").strip()
  if service_id not in ASR_PLAYGROUND_SERVICES:
    raise HTTPException(400, "Unsupported ASR service")

  audio_base64 = str(payload.get("audio_base64") or "")
  if not audio_base64:
    raise HTTPException(400, "audio_base64 is required")

  try:
    audio_bytes = base64.b64decode(audio_base64)
  except Exception as exc:
    raise HTTPException(400, "Invalid base64 audio payload") from exc

  filename = str(payload.get("filename") or "sample.wav")
  if service_id == "vosk-asr":
    result = await _transcribe_with_vosk(audio_bytes, filename)
  else:
    result = await _transcribe_with_capswriter(audio_bytes)

  return {"ok": True, "service_id": service_id, "result": result}


@app.get("/api/projects/{pid}/ping")
async def ping_project(pid: str):
    proj = BY_ID.get(pid)
    if not proj:
        raise HTTPException(404, "Project not found")
    port = proj.get("port")
    if port is None:
        managed = pid in _procs and _procs[pid].poll() is None
        return {"ready": managed}
    ready = http_ready(port)
    return {"ready": ready}


@app.get("/api/projects")
async def list_projects():
  now = time.monotonic()
  cached = _projects_cache["data"]
  if cached is not None and now < _projects_cache["expires_at"]:
    return JSONResponse(cached)

  if _projects_cache_lock.locked() and cached is not None:
    return JSONResponse(cached)

  async with _projects_cache_lock:
    now = time.monotonic()
    cached = _projects_cache["data"]
    if cached is not None and now < _projects_cache["expires_at"]:
      return JSONResponse(cached)

    loop = asyncio.get_running_loop()
    snapshot = await loop.run_in_executor(
      _projects_executor, _build_projects_snapshot
    )
    _projects_cache["data"] = snapshot
    _projects_cache["expires_at"] = time.monotonic() + 5.0
    return JSONResponse(snapshot)


@app.post("/api/projects/{pid}/start")
async def start_project(pid: str):
  proj = BY_ID.get(pid)
  if not proj:
    raise HTTPException(404, "Project not found")

  port = proj.get("port")
  if port is not None:
    if is_running(port):
      return {"ok": False, "msg": f"Port {port} is already in use"}
    stale_pids = port_pids(port)
    if stale_pids:
      remaining = clear_stale_port_processes(port)
      if remaining:
        pids = ", ".join(str(process_id) for process_id in remaining)
        return {"ok": False, "msg": f"Port {port} is held by stale process(es): {pids}"}

  work_dir = Path(proj["dir"])
  if not work_dir.exists():
    return {"ok": False, "msg": f"Directory not found: {proj['dir']}"}

  log_path = LOG_DIR / f"{pid}.log"
  settings_state = load_settings_state()
  env = resolve_project_env(proj, settings_state)
  missing_settings = missing_project_requirements(proj, env)
  if missing_settings:
    names = ", ".join(missing_settings)
    return {"ok": False, "msg": f"Missing required settings: {names}. Open Settings and configure them first."}

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
        stdin=subprocess.DEVNULL,
        stdout=lf,
        stderr=lf,
        start_new_session=True,
      )

    _procs[pid] = proc
    time.sleep(0.5)
    if proc.poll() is not None:
      # The start command exited quickly. It's common for start scripts to
      # exec/detach into another PID (docker, system wrappers, etc). If the
      # project advertises a port, wait briefly for the port to become ready
      # and, if found, register the external PID so stop logic can manage it.
      if port is not None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
          if http_ready(port):
            pids = port_pids(port)
            if pids:
              ext = ExternalProc(pids[0])
              _procs[pid] = ext
              invalidate_projects_cache()
              return {"ok": True, "pid": pids[0]}
            invalidate_projects_cache()
            return {"ok": True, "pid": proc.pid}
          time.sleep(0.25)

      # No port or port never appeared — treat as failure and surface logs.
      _procs.pop(pid, None)
      lines = log_path.read_text(errors="ignore").splitlines()[-20:]
      msg = "\n".join(lines).strip() or f"Process exited immediately with code {proc.returncode}"
      invalidate_projects_cache()
      return {"ok": False, "msg": msg}

    invalidate_projects_cache()
    return {"ok": True, "pid": proc.pid}
  except Exception as e:
    return {"ok": False, "msg": str(e)}


@app.post("/api/projects/{pid}/stop")
async def stop_project(pid: str):
  proj = BY_ID.get(pid)
  if not proj:
    raise HTTPException(404, "Project not found")

  port = proj.get("port")

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

  # Fallback: ensure the advertised listener port is actually released.
  if port is not None:
    remaining = clear_stale_port_processes(port, listeners_only=True)
    if remaining:
      invalidate_projects_cache()
      pids = ", ".join(str(process_id) for process_id in remaining)
      return {"ok": False, "msg": f"Port {port} is still held by process(es): {pids}"}

  invalidate_projects_cache()
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
        invalidate_projects_cache()
        return {"ok": result.returncode == 0, "output": output.strip()}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


@app.get("/api/projects/{pid}/logs")
async def get_logs(pid: str):
    """Return the latest log lines for a project."""
    proj = BY_ID.get(pid)
    if not proj:
        raise HTTPException(404)

    log_path = LOG_DIR / f"{pid}.log"
    log_path.touch()
    lines = log_path.read_text(errors="ignore").splitlines()[-80:]
    return JSONResponse({"lines": lines})


# ─── Frontend HTML ────────────────────────────────────────────────────────────
ASR_PLAYGROUND_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ASR 测试台</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #08111d;
    --panel: rgba(11, 19, 33, 0.9);
    --line: rgba(110, 124, 149, 0.2);
    --text: #e6eef8;
    --muted: #91a4bb;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    font-family: 'IBM Plex Sans', sans-serif;
    color: var(--text);
    background:
      radial-gradient(circle at top left, rgba(54, 199, 255, 0.16), transparent 24%),
      radial-gradient(circle at top right, rgba(244, 114, 182, 0.12), transparent 22%),
      linear-gradient(180deg, #050913 0%, #09111d 100%);
  }
  .shell {
    width: min(980px, calc(100vw - 32px));
    margin: 0 auto;
    padding: 28px 0 44px;
  }
  .hero, .panel {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 24px;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.28);
  }
  .hero {
    padding: 24px;
    margin-bottom: 18px;
  }
  .eyebrow {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: #74d8ff;
  }
  h1 {
    margin: 8px 0 10px;
    font-size: clamp(28px, 4vw, 42px);
    letter-spacing: -0.04em;
  }
  .hero p {
    margin: 0;
    color: var(--muted);
    line-height: 1.7;
  }
  .panel {
    padding: 20px;
  }
  .grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 18px;
  }
  .field-label {
    display: block;
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 8px;
  }
  select, input[type="file"], textarea {
    width: 100%;
    border-radius: 14px;
    border: 1px solid rgba(148, 163, 184, 0.2);
    background: rgba(7, 12, 22, 0.88);
    color: var(--text);
    padding: 12px 14px;
    font: inherit;
  }
  textarea {
    min-height: 124px;
    resize: vertical;
  }
  .card {
    padding: 16px;
    border-radius: 18px;
    background: rgba(8, 13, 24, 0.72);
    border: 1px solid rgba(148, 163, 184, 0.12);
  }
  .card + .card { margin-top: 14px; }
  .btn-row {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 14px;
  }
  button {
    min-height: 42px;
    padding: 0 16px;
    border-radius: 14px;
    border: 1px solid transparent;
    background: rgba(30, 41, 59, 0.82);
    color: var(--text);
    cursor: pointer;
    font: inherit;
  }
  button.primary {
    background: rgba(34, 197, 94, 0.16);
    border-color: rgba(34, 197, 94, 0.28);
    color: #92f0b5;
  }
  .hint {
    margin-top: 10px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.6;
  }
  .status {
    margin-top: 14px;
    padding: 12px 14px;
    border-radius: 14px;
    border: 1px solid rgba(148, 163, 184, 0.16);
    background: rgba(7, 12, 22, 0.72);
    color: var(--muted);
    min-height: 48px;
  }
  .status.ok { color: #92f0b5; border-color: rgba(34, 197, 94, 0.28); }
  .status.warn { color: #fdba74; border-color: rgba(245, 158, 11, 0.28); }
  .mono {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
  }
  audio {
    width: 100%;
    margin-top: 10px;
  }
  @media (max-width: 860px) {
    .grid { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="eyebrow">Speech Integration Playground</div>
      <h1>ASR 测试台</h1>
      <p>在同一个页面里直接测试 CapsWriter 和 Vosk。支持上传 16kHz 单声道 WAV，或在浏览器里直接录音后发给 dashboard 代理，再由 dashboard 转发到对应服务。</p>
    </section>

    <section class="panel">
      <div class="grid">
        <div>
          <div class="card">
            <label class="field-label" for="service-select">识别服务</label>
            <select id="service-select">
              <option value="capswriter-asr">CapsWriter WebSocket</option>
              <option value="vosk-asr">Vosk HTTP</option>
            </select>
            <div class="hint">CapsWriter 更接近实时输入链路；Vosk 更适合简单 HTTP 集成。</div>
          </div>

          <div class="card">
            <label class="field-label" for="file-input">上传 WAV 音频</label>
            <input id="file-input" type="file" accept="audio/wav">
            <div class="hint">建议使用 16kHz、单声道、16-bit PCM WAV。上传后可直接发送到所选服务。</div>
            <audio id="audio-preview" controls hidden></audio>
          </div>

          <div class="card">
            <div class="field-label">浏览器录音</div>
            <div class="btn-row">
              <button id="record-btn" type="button">开始录音</button>
              <button id="stop-btn" type="button" disabled>停止录音</button>
              <button id="send-btn" class="primary" type="button" disabled>发送识别</button>
            </div>
            <div id="status-box" class="status">等待音频输入…</div>
          </div>
        </div>

        <div>
          <div class="card">
            <label class="field-label" for="result-text">识别结果</label>
            <textarea id="result-text" readonly placeholder="识别结果会出现在这里"></textarea>
            <div class="hint">CapsWriter 会返回 text_accu 等字段；Vosk 会返回 text 和 words。</div>
          </div>

          <div class="card">
            <div class="field-label">原始响应</div>
            <textarea id="raw-json" class="mono" readonly placeholder="原始 JSON 响应"></textarea>
          </div>
        </div>
      </div>
    </section>
  </main>

<script>
const serviceSelect = document.getElementById('service-select');
const fileInput = document.getElementById('file-input');
const audioPreview = document.getElementById('audio-preview');
const recordBtn = document.getElementById('record-btn');
const stopBtn = document.getElementById('stop-btn');
const sendBtn = document.getElementById('send-btn');
const statusBox = document.getElementById('status-box');
const resultText = document.getElementById('result-text');
const rawJson = document.getElementById('raw-json');

let mediaStream = null;
let audioContext = null;
let processor = null;
let sourceNode = null;
let recordedChunks = [];
let wavBlob = null;
let wavFileName = 'browser-recording.wav';

const query = new URLSearchParams(window.location.search);
const presetService = query.get('service');
if (presetService === 'capswriter-asr' || presetService === 'vosk-asr') {
  serviceSelect.value = presetService;
}

function setStatus(message, tone = '') {
  statusBox.textContent = message;
  statusBox.className = `status ${tone}`.trim();
}

function floatTo16BitPCM(float32Array) {
  const buffer = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buffer);
  let offset = 0;
  for (const sample of float32Array) {
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7FFF, true);
    offset += 2;
  }
  return view;
}

function encodeWav(samples, sampleRate) {
  const pcm = floatTo16BitPCM(samples);
  const wavBuffer = new ArrayBuffer(44 + pcm.byteLength);
  const view = new DataView(wavBuffer);

  const writeString = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + pcm.byteLength, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, pcm.byteLength, true);

  for (let index = 0; index < pcm.byteLength; index += 1) {
    view.setUint8(44 + index, pcm.getUint8(index));
  }

  return new Blob([view], { type: 'audio/wav' });
}

async function blobToBase64(blob) {
  const arrayBuffer = await blob.arrayBuffer();
  let binary = '';
  const bytes = new Uint8Array(arrayBuffer);
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

function setPreview(blob, fileName) {
  wavBlob = blob;
  wavFileName = fileName;
  audioPreview.src = URL.createObjectURL(blob);
  audioPreview.hidden = false;
  sendBtn.disabled = false;
}

fileInput.addEventListener('change', () => {
  const [file] = fileInput.files || [];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.wav')) {
    setStatus('请上传 WAV 文件。', 'warn');
    return;
  }
  setPreview(file, file.name);
  setStatus(`已加载 ${file.name}，可以发送识别。`, 'ok');
});

recordBtn.addEventListener('click', async () => {
  mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  audioContext = new AudioContext({ sampleRate: 16000 });
  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  processor = audioContext.createScriptProcessor(4096, 1, 1);
  recordedChunks = [];

  processor.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    recordedChunks.push(new Float32Array(input));
  };

  sourceNode.connect(processor);
  processor.connect(audioContext.destination);
  recordBtn.disabled = true;
  stopBtn.disabled = false;
  sendBtn.disabled = true;
  setStatus('录音中…点击“停止录音”后可发送识别。');
});

stopBtn.addEventListener('click', async () => {
  if (!audioContext || !processor) return;
  processor.disconnect();
  sourceNode.disconnect();
  mediaStream.getTracks().forEach(track => track.stop());
  await audioContext.close();

  const totalLength = recordedChunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(totalLength);
  let offset = 0;
  for (const chunk of recordedChunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }

  const blob = encodeWav(merged, 16000);
  setPreview(blob, 'browser-recording.wav');
  recordBtn.disabled = false;
  stopBtn.disabled = true;
  setStatus('录音已完成，可以发送识别。', 'ok');
});

sendBtn.addEventListener('click', async () => {
  if (!wavBlob) {
    setStatus('请先上传或录制音频。', 'warn');
    return;
  }

  sendBtn.disabled = true;
  setStatus(`正在发送到 ${serviceSelect.selectedOptions[0].text}…`);

  try {
    const audioBase64 = await blobToBase64(wavBlob);
    const response = await fetch('/api/asr/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        service_id: serviceSelect.value,
        filename: wavFileName,
        audio_base64: audioBase64,
      }),
    });

    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.detail || payload.msg || '识别失败');
    }

    const result = payload.result || {};
    resultText.value = result.text_accu || result.text || JSON.stringify(result);
    rawJson.value = JSON.stringify(result, null, 2);
    setStatus(`识别完成：${serviceSelect.selectedOptions[0].text}`, 'ok');
  } catch (error) {
    resultText.value = '';
    rawJson.value = '';
    setStatus(`识别失败：${error.message}`, 'warn');
  } finally {
    sendBtn.disabled = false;
  }
});
</script>
</body>
</html>"""

VIBEVOICE_ASR_CONTROL_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VibeVoice ASR 控制台</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #080f1a;
    --panel: rgba(12, 20, 34, 0.9);
    --line: rgba(110, 131, 165, 0.24);
    --text: #e7eef8;
    --muted: #97abc4;
    --ok: #22c55e;
    --warn: #f59e0b;
    --accent: #38bdf8;
    --danger: #fb7185;
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    min-height: 100vh;
    color: var(--text);
    font-family: 'IBM Plex Sans', sans-serif;
    background:
      radial-gradient(circle at 0% 0%, rgba(56, 189, 248, 0.18), transparent 24%),
      radial-gradient(circle at 100% 0%, rgba(249, 115, 22, 0.14), transparent 20%),
      linear-gradient(180deg, #050912 0%, #08111d 54%, #09111d 100%);
  }

  .shell {
    width: min(980px, calc(100vw - 32px));
    margin: 0 auto;
    padding: 26px 0 44px;
  }

  .hero,
  .panel {
    border: 1px solid var(--line);
    border-radius: 22px;
    background: var(--panel);
    box-shadow: 0 22px 56px rgba(0, 0, 0, 0.32);
  }

  .hero {
    padding: 22px;
    margin-bottom: 16px;
  }

  .kicker {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #7dd3fc;
  }

  h1 {
    margin: 8px 0 8px;
    font-size: clamp(28px, 4.6vw, 40px);
    letter-spacing: -0.04em;
  }

  .hero p {
    margin: 0;
    color: var(--muted);
    line-height: 1.7;
  }

  .hero-actions {
    margin-top: 14px;
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }

  .panel {
    padding: 18px;
  }

  .grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 16px;
  }

  .card {
    border-radius: 18px;
    border: 1px solid rgba(148, 163, 184, 0.16);
    background: rgba(7, 13, 24, 0.78);
    padding: 16px;
  }

  .card h2 {
    margin: 0 0 12px;
    font-size: 18px;
  }

  .status-chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-height: 34px;
    padding: 0 12px;
    border-radius: 999px;
    border: 1px solid rgba(148, 163, 184, 0.18);
    color: var(--muted);
    font-size: 13px;
  }

  .status-chip.ok {
    color: #9befbc;
    border-color: rgba(34, 197, 94, 0.38);
    background: rgba(34, 197, 94, 0.12);
  }

  .status-chip.warn {
    color: #fdba74;
    border-color: rgba(245, 158, 11, 0.36);
    background: rgba(245, 158, 11, 0.12);
  }

  .status-chip.danger {
    color: #fda4af;
    border-color: rgba(251, 113, 133, 0.34);
    background: rgba(251, 113, 133, 0.12);
  }

  .switch-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin: 14px 0;
  }

  .switch-label {
    font-weight: 600;
  }

  input[type="checkbox"].switch {
    width: 54px;
    height: 30px;
    appearance: none;
    border-radius: 999px;
    background: rgba(71, 85, 105, 0.72);
    border: 1px solid rgba(148, 163, 184, 0.2);
    position: relative;
    cursor: pointer;
    transition: background 0.18s ease;
  }

  input[type="checkbox"].switch::after {
    content: '';
    width: 22px;
    height: 22px;
    border-radius: 999px;
    background: #fff;
    position: absolute;
    top: 3px;
    left: 3px;
    transition: transform 0.18s ease;
  }

  input[type="checkbox"].switch:checked {
    background: rgba(14, 165, 233, 0.78);
  }

  input[type="checkbox"].switch:checked::after {
    transform: translateX(24px);
  }

  select,
  textarea {
    width: 100%;
    border-radius: 14px;
    border: 1px solid rgba(148, 163, 184, 0.22);
    background: rgba(4, 10, 18, 0.92);
    color: var(--text);
    font: inherit;
    padding: 12px;
  }

  textarea {
    min-height: 108px;
    resize: vertical;
  }

  .label {
    display: block;
    margin-bottom: 8px;
    font-size: 13px;
    color: var(--muted);
  }

  .hint {
    margin-top: 8px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.6;
  }

  .btn-row {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 14px;
  }

  button, .link-btn {
    min-height: 40px;
    padding: 0 14px;
    border-radius: 12px;
    border: 1px solid transparent;
    background: rgba(30, 41, 59, 0.8);
    color: var(--text);
    cursor: pointer;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  button.primary {
    background: rgba(14, 165, 233, 0.18);
    border-color: rgba(14, 165, 233, 0.4);
    color: #7dd3fc;
  }

  button.warn {
    background: rgba(251, 113, 133, 0.16);
    border-color: rgba(251, 113, 133, 0.35);
    color: #fda4af;
  }

  button:disabled {
    opacity: 0.58;
    cursor: not-allowed;
  }

  .health-grid {
    margin-top: 12px;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }

  .kv {
    border-radius: 12px;
    border: 1px solid rgba(148, 163, 184, 0.14);
    background: rgba(5, 10, 18, 0.85);
    padding: 10px;
  }

  .kv .k {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 4px;
  }

  .kv .v {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    line-height: 1.5;
    word-break: break-all;
  }

  .message {
    margin-top: 14px;
    padding: 11px 12px;
    border-radius: 12px;
    border: 1px solid rgba(148, 163, 184, 0.16);
    background: rgba(7, 12, 22, 0.78);
    color: var(--muted);
    min-height: 44px;
  }

  .message.ok {
    color: #9befbc;
    border-color: rgba(34, 197, 94, 0.34);
  }

  .message.warn {
    color: #fdba74;
    border-color: rgba(245, 158, 11, 0.34);
  }

  .message.error {
    color: #fda4af;
    border-color: rgba(251, 113, 133, 0.34);
  }

  @media (max-width: 860px) {
    .grid {
      grid-template-columns: 1fr;
    }
  }
</style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="kicker">Voice Input Control</div>
      <h1>VibeVoice ASR 热键控制台</h1>
      <p>这里可以直接开关 VibeVoice ASR 服务，并设置 Right Option 热键识别功能。保存热键配置后，可一键重启服务立即生效。</p>
      <div class="hero-actions">
        <a class="link-btn" href="/">返回主面板</a>
        <button id="open-docs-btn" type="button">打开 ASR API 文档</button>
      </div>
    </section>

    <section class="panel">
      <div class="grid">
        <article class="card">
          <h2>服务开关</h2>
          <div id="service-chip" class="status-chip">状态加载中...</div>

          <div class="switch-row">
            <div class="switch-label">VibeVoice ASR 运行开关</div>
            <input id="service-toggle" class="switch" type="checkbox" aria-label="服务开关">
          </div>

          <div class="btn-row">
            <button id="refresh-btn" type="button">刷新状态</button>
            <button id="restart-btn" class="warn" type="button">重启服务</button>
          </div>

          <div class="health-grid">
            <div class="kv">
              <div class="k">项目状态</div>
              <div id="project-state" class="v">-</div>
            </div>
            <div class="kv">
              <div class="k">引擎状态</div>
              <div id="engine-state" class="v">-</div>
            </div>
            <div class="kv">
              <div class="k">模型</div>
              <div id="model-path" class="v">-</div>
            </div>
            <div class="kv">
              <div class="k">设备 / 热键</div>
              <div id="device-hotkey" class="v">-</div>
            </div>
          </div>
        </article>

        <article class="card">
          <h2>热键设置</h2>
          <div class="switch-row">
            <div class="switch-label">启用 Right Option 识别</div>
            <input id="hotkey-toggle" class="switch" type="checkbox" aria-label="热键开关">
          </div>

          <label class="label" for="hotkey-mode">触发方式</label>
          <select id="hotkey-mode" aria-label="热键触发方式">
            <option value="toggle">单击开 / 单击关（推荐）</option>
            <option value="hold">按住录音 / 松开识别</option>
          </select>
          <p class="hint">你现在想要的交互是“单击开 / 单击关”，即第一下开始录音，第二下结束并识别。</p>

          <div class="switch-row" style="margin-top: 16px;">
            <div class="switch-label">识别后自动粘贴到当前光标</div>
            <input id="paste-at-cursor" class="switch" type="checkbox" aria-label="自动粘贴到当前光标">
          </div>

          <label class="label" for="context-info">热键上下文（可选）</label>
          <textarea id="context-info" placeholder="可填写业务术语、热词或提示信息，提升热键识别场景表现。"></textarea>

          <div class="btn-row">
            <button id="save-btn" class="primary" type="button">保存热键设置</button>
            <button id="save-restart-btn" type="button">保存并重启服务</button>
          </div>
        </article>
      </div>

      <div id="message" class="message">就绪。你可以先打开服务开关，然后按一下 Right Option 开始，再按一下结束并自动输入到当前光标。</div>
    </section>
  </main>

<script>
const PROJECT_ID = 'vibevoice-asr-m3';
const ASR_HEALTH_URL = '/api/vibevoice-asr/health';

const serviceToggle = document.getElementById('service-toggle');
const hotkeyToggle = document.getElementById('hotkey-toggle');
const hotkeyModeSelect = document.getElementById('hotkey-mode');
const pasteAtCursorToggle = document.getElementById('paste-at-cursor');
const contextInfoInput = document.getElementById('context-info');
const serviceChip = document.getElementById('service-chip');
const projectState = document.getElementById('project-state');
const engineState = document.getElementById('engine-state');
const modelPath = document.getElementById('model-path');
const deviceHotkey = document.getElementById('device-hotkey');
const messageBox = document.getElementById('message');

let project = null;
let health = null;
let syncingServiceToggle = false;
let busy = false;

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function setBusy(value) {
  busy = value;
  const buttons = document.querySelectorAll('button, input.switch, textarea, select');
  buttons.forEach(el => {
    if (el.id === 'service-toggle' && syncingServiceToggle) return;
    el.disabled = value;
  });
}

function setMessage(text, type = '') {
  messageBox.textContent = text;
  messageBox.className = `message ${type}`.trim();
}

function setServiceToggleChecked(value) {
  syncingServiceToggle = true;
  serviceToggle.checked = Boolean(value);
  syncingServiceToggle = false;
}

async function fetchProject() {
  const response = await fetch('/api/projects');
  const payload = await response.json();
  project = (payload || []).find(item => item.id === PROJECT_ID) || null;
}

async function fetchHealth() {
  if (!project || !project.running) {
    health = null;
    return;
  }

  try {
    const response = await fetch(ASR_HEALTH_URL, { cache: 'no-store' });
    if (!response.ok) {
      health = null;
      return;
    }
    const payload = await response.json();
    health = payload.health || null;
  } catch (_) {
    health = null;
  }
}

async function fetchConfig() {
  const response = await fetch('/api/vibevoice-asr/config');
  const payload = await response.json();
  if (!payload.ok) {
    throw new Error(payload.msg || '读取热键配置失败');
  }
  const config = payload.config || {};
  hotkeyToggle.checked = Boolean(config.hotkey_enabled);
  hotkeyModeSelect.value = config.hotkey_mode === 'hold' ? 'hold' : 'toggle';
  pasteAtCursorToggle.checked = config.paste_at_cursor !== false;
  contextInfoInput.value = String(config.hotkey_context_info || '');
}

function renderRuntime() {
  if (!project) {
    serviceChip.textContent = '未找到项目配置';
    serviceChip.className = 'status-chip danger';
    projectState.textContent = 'missing';
    engineState.textContent = '-';
    modelPath.textContent = '-';
    deviceHotkey.textContent = '-';
    setServiceToggleChecked(false);
    return;
  }

  const running = Boolean(project.running);
  setServiceToggleChecked(running);

  if (running) {
    serviceChip.textContent = '服务运行中';
    serviceChip.className = 'status-chip ok';
  } else {
    serviceChip.textContent = '服务未运行';
    serviceChip.className = 'status-chip warn';
  }

  projectState.textContent = running ? 'running' : 'stopped';
  if (!health) {
    engineState.textContent = running ? 'probing...' : '-';
    modelPath.textContent = '-';
    deviceHotkey.textContent = '-';
    return;
  }

  engineState.textContent = health.status || '-';
  modelPath.textContent = health.model || '-';
  const hotkeyOn = health.hotkey_enabled ? 'on' : 'off';
  const mode = health.hotkey_mode || hotkeyModeSelect.value || 'toggle';
  const paste = health.paste_at_cursor === false ? 'off' : 'on';
  deviceHotkey.textContent = `${health.device || '-'} / hotkey ${hotkeyOn} / mode ${mode} / paste ${paste}`;
}

async function refreshAll() {
  await Promise.all([fetchProject(), fetchConfig()]);
  await fetchHealth();
  renderRuntime();
}

async function callProjectAction(action) {
  const response = await fetch(`/api/projects/${PROJECT_ID}/${action}`, { method: 'POST' });
  const payload = await response.json();
  if (!payload.ok) {
    throw new Error(payload.msg || `执行 ${action} 失败`);
  }
}

async function saveConfig() {
  const payload = {
    hotkey_enabled: hotkeyToggle.checked,
    hotkey_mode: hotkeyModeSelect.value,
    paste_at_cursor: pasteAtCursorToggle.checked,
    hotkey_context_info: contextInfoInput.value,
  };

  const response = await fetch('/api/vibevoice-asr/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!data.ok) {
    throw new Error(data.msg || '保存热键配置失败');
  }
}

async function restartService() {
  if (project && project.running) {
    await callProjectAction('stop');
    await sleep(900);
  }
  await callProjectAction('start');
}

document.getElementById('open-docs-btn').addEventListener('click', () => {
  window.open('http://127.0.0.1:6708/docs', '_blank');
});

document.getElementById('refresh-btn').addEventListener('click', async () => {
  setBusy(true);
  try {
    await refreshAll();
    setMessage('状态已刷新。', 'ok');
  } catch (error) {
    setMessage(`刷新失败：${String(error)}`, 'error');
  } finally {
    setBusy(false);
  }
});

document.getElementById('restart-btn').addEventListener('click', async () => {
  setBusy(true);
  try {
    await restartService();
    await sleep(1200);
    await refreshAll();
    setMessage('服务已重启。模型首次加载期间会显示 loading。', 'ok');
  } catch (error) {
    setMessage(`重启失败：${String(error)}`, 'error');
  } finally {
    setBusy(false);
  }
});

serviceToggle.addEventListener('change', async () => {
  if (syncingServiceToggle || busy) return;
  setBusy(true);
  try {
    if (serviceToggle.checked) {
      await callProjectAction('start');
      setMessage('服务启动中。首次模型加载可能需要较长时间。', 'warn');
    } else {
      await callProjectAction('stop');
      setMessage('服务已停止。', 'ok');
    }

    await sleep(900);
    await refreshAll();
  } catch (error) {
    setMessage(`操作失败：${String(error)}`, 'error');
    await refreshAll();
  } finally {
    setBusy(false);
  }
});

document.getElementById('save-btn').addEventListener('click', async () => {
  setBusy(true);
  try {
    await saveConfig();
    setMessage('热键配置已保存。下次启动服务时会生效。', 'ok');
    await refreshAll();
  } catch (error) {
    setMessage(`保存失败：${String(error)}`, 'error');
  } finally {
    setBusy(false);
  }
});

document.getElementById('save-restart-btn').addEventListener('click', async () => {
  setBusy(true);
  try {
    await saveConfig();
    await restartService();
    await sleep(1200);
    await refreshAll();
    setMessage('配置已保存并重启服务。', 'ok');
  } catch (error) {
    setMessage(`保存并重启失败：${String(error)}`, 'error');
  } finally {
    setBusy(false);
  }
});

refreshAll().then(() => {
  setMessage('控制台已连接。', 'ok');
}).catch(error => {
  setMessage(`初始化失败：${String(error)}`, 'error');
});

setInterval(() => {
  if (!busy) {
    refreshAll().catch(() => {});
  }
}, 6000);
</script>
</body>
</html>"""

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dev Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<style>
  :root {
    --bg: #09101d;
    --panel: rgba(12, 19, 33, 0.88);
    --panel-strong: rgba(9, 15, 26, 0.96);
    --panel-soft: rgba(17, 26, 43, 0.78);
    --line: rgba(84, 104, 133, 0.28);
    --text: #e7eef8;
    --muted: #8fa1b8;
    --accent: #36c7ff;
    --accent-soft: rgba(54, 199, 255, 0.14);
    --ok: #22c55e;
    --warn: #f59e0b;
    --danger: #fb7185;
    --shadow: 0 22px 60px rgba(0, 0, 0, 0.34);
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    min-height: 100vh;
    color: var(--text);
    font-family: 'IBM Plex Sans', sans-serif;
    background:
      radial-gradient(circle at 0% 0%, rgba(54, 199, 255, 0.18), transparent 24%),
      radial-gradient(circle at 100% 0%, rgba(244, 114, 182, 0.16), transparent 22%),
      linear-gradient(180deg, #04070f 0%, #0a1220 48%, #09111d 100%);
  }

  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(rgba(255, 255, 255, 0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255, 255, 255, 0.03) 1px, transparent 1px);
    background-size: 22px 22px;
    opacity: 0.16;
    pointer-events: none;
  }

  a, button, input, textarea { font: inherit; }

  .shell {
    width: min(1480px, calc(100vw - 32px));
    margin: 0 auto;
  }

  .topbar {
    position: sticky;
    top: 0;
    z-index: 20;
    padding: 18px 0 14px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.12);
    background: rgba(4, 8, 16, 0.78);
    backdrop-filter: blur(16px);
  }

  .topbar-inner {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    align-items: center;
    gap: 12px;
  }

  .topbar-copy {
    min-width: 0;
    max-width: 580px;
  }

  .eyebrow {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    letter-spacing: 0.24em;
    text-transform: uppercase;
    color: #6dcff6;
  }

  .title-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 3px;
  }

  .brand-mark {
    width: 34px;
    height: 34px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 10px;
    background: linear-gradient(135deg, rgba(54, 199, 255, 0.25), rgba(244, 114, 182, 0.16));
    border: 1px solid rgba(114, 225, 255, 0.28);
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.05);
  }

  .topbar h1 {
    margin: 0;
    font-size: clamp(30px, 4.2vw, 40px);
    line-height: 1;
    letter-spacing: -0.04em;
  }

  .topbar-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-direction: row;
    flex-wrap: wrap;
    justify-content: center;
    justify-self: end;
    padding: 4px;
    border-radius: 16px;
    border: 1px solid rgba(148, 163, 184, 0.1);
    background: rgba(8, 14, 25, 0.42);
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.02);
  }

  .topbar-status {
    display: flex;
    align-items: center;
    gap: 8px;
    justify-self: end;
    min-width: 0;
    padding: 4px;
    border-radius: 16px;
    border: 1px solid rgba(148, 163, 184, 0.1);
    background: rgba(8, 14, 25, 0.42);
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.02);
  }

  .topbar-status-grid {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: nowrap;
    justify-content: flex-end;
  }

  .topbar-status .btn-compact,
  .topbar-status-grid .rail-option,
  .topbar-actions .btn {
    min-height: 34px;
    padding: 0 11px;
    border-radius: 10px;
    gap: 6px;
    font-size: 12px;
    white-space: nowrap;
  }

  .dashboard-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 18px;
    align-items: start;
    padding: 18px 0 30px;
  }

  .rail,
  .stage,
  #modal,
  #settings-modal,
  #project-tag-modal {
    background: linear-gradient(180deg, var(--panel) 0%, var(--panel-strong) 100%);
    border: 1px solid var(--line);
    box-shadow: var(--shadow);
  }

  .rail {
    position: relative;
    border-radius: 22px;
    padding: 16px;
    overflow: hidden;
  }

  .filter-toolbar {
    position: relative;
    z-index: 1;
    display: grid;
    grid-template-columns: minmax(0, 3fr) minmax(240px, 1fr);
    gap: 12px;
    align-items: stretch;
  }

  .filter-toolbar .rail-block {
    height: 100%;
    margin-top: 0;
  }

  .rail-search-block {
    grid-column: 1 / -1;
    padding: 10px 12px;
  }

  .search-inline-row {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: 10px;
  }

  .search-controls {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 12px;
    align-items: end;
  }

  .search-sort-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .rail-search-block .field-label {
    margin-bottom: 0;
    font-size: 12px;
    white-space: nowrap;
  }

  .rail-search-block .field-input {
    min-height: 34px;
    padding: 7px 10px;
  }

  .status-toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .status-toolbar .topbar-status-grid {
    flex: 1 1 320px;
    justify-content: flex-start;
  }

  .rail-inline-filters {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .rail-tags-block {
    position: relative;
    z-index: 1;
    min-width: 0;
  }

  .rail-tags-block .tag-cloud-inline {
    min-height: 98px;
    align-content: flex-start;
    overflow: hidden;
  }

  .rail-group-block {
    min-width: 0;
  }

  .rail-group-block .rail-inline-filters {
    align-content: flex-start;
  }

  .rail-tags-block .tag-pill {
    min-height: 24px;
    padding: 3px 8px;
    font-size: 10px;
    gap: 5px;
  }

  .rail-tags-block .pill-count {
    min-width: 14px;
    padding: 0 4px;
    font-size: 8px;
  }

  .rail::before,
  .stage::before,
  #settings-modal::before {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(140deg, rgba(54, 199, 255, 0.08), transparent 34%, transparent 62%, rgba(244, 114, 182, 0.05));
    pointer-events: none;
  }

  .stage {
    position: relative;
    border-radius: 24px;
    padding: 16px;
    min-height: 720px;
    overflow: hidden;
    background: linear-gradient(180deg, rgba(18, 37, 60, 0.82), rgba(9, 16, 28, 0.96));
  }

  .rail-block {
    position: relative;
    padding: 14px;
    border-radius: 18px;
    background: var(--panel-soft);
    border: 1px solid rgba(110, 124, 149, 0.16);
  }

  .rail-block + .rail-block {
    margin-top: 10px;
  }

  .rail-block.primary {
    background: linear-gradient(180deg, rgba(15, 24, 40, 0.84), rgba(11, 17, 30, 0.96));
    border-color: rgba(54, 199, 255, 0.18);
  }

  .rail-accent {
    background: linear-gradient(180deg, rgba(15, 43, 63, 0.76), rgba(12, 19, 33, 0.94));
    border-color: rgba(54, 199, 255, 0.22);
  }

  .section-kicker {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: #67d2ff;
    margin-bottom: 10px;
  }

  .section-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
  }

  .section-row span:first-child {
    font-weight: 600;
    letter-spacing: -0.01em;
  }

  .section-meta {
    font-size: 12px;
    color: #7f91a7;
  }

  .mini-link,
  .mini-danger {
    border: 0;
    background: transparent;
    color: #90cdf4;
    cursor: pointer;
    font-size: 12px;
    padding: 0;
  }

  .mini-danger { color: #fda4af; }

  .mini-link:hover,
  .mini-danger:hover {
    color: #ffffff;
  }

  .field-label {
    display: block;
    margin-bottom: 8px;
    font-size: 12px;
    font-weight: 600;
    color: #cfe0f4;
  }

  .field-label.inline {
    margin-bottom: 0;
    white-space: nowrap;
  }

  .field-help {
    margin: 6px 0 0;
    font-size: 11px;
    line-height: 1.5;
    color: #7f91a7;
  }

  .field-help.compact { margin-top: 4px; }

  .field-input {
    width: 100%;
    min-height: 40px;
    padding: 9px 11px;
    border-radius: 12px;
    border: 1px solid rgba(114, 132, 159, 0.22);
    background: rgba(7, 12, 22, 0.88);
    color: var(--text);
    outline: none;
    transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
  }

  .field-input:focus {
    border-color: rgba(54, 199, 255, 0.62);
    box-shadow: 0 0 0 3px rgba(54, 199, 255, 0.14);
    background: rgba(8, 15, 26, 0.96);
  }

  .field-input.narrow {
    max-width: 240px;
  }

  .search-sort-row .field-input {
    min-width: 168px;
  }

  .rail-stack,
  .tag-cloud,
  .card-tag-row,
  .settings-tag-cloud {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .status-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .status-grid .rail-option {
    width: auto;
    min-height: 42px;
    padding: 9px 10px;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .status-grid .rail-option-label {
    gap: 6px;
    white-space: nowrap;
  }

  .status-grid .rail-option-meta {
    margin-left: 0;
  }

  .topbar-status-grid .rail-option {
    width: auto;
    min-width: 0;
    min-height: 34px;
    padding: 0 11px;
    gap: 6px;
    border-radius: 10px;
    background: rgba(13, 22, 36, 0.82);
    border-color: rgba(90, 108, 136, 0.2);
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.02);
    font-size: 12px;
  }

  .topbar-status-grid .rail-option-label {
    gap: 6px;
    flex: 0 1 auto;
    white-space: nowrap;
  }

  .topbar-status-grid .rail-option-icon {
    width: 14px;
    font-size: 11px;
  }

  .topbar-status-grid .rail-option-meta {
    min-width: 1.8em;
    font-size: 10px;
  }

  .rail-inline-filters .rail-option {
    width: auto;
    min-height: 34px;
    padding: 6px 10px;
    gap: 8px;
    border-radius: 14px;
    font-size: 12px;
  }

  .rail-inline-filters .rail-option-label {
    gap: 6px;
    white-space: nowrap;
  }

  .rail-inline-filters .rail-option-icon {
    width: 14px;
    font-size: 11px;
  }

  .rail-inline-filters .rail-option-meta {
    min-width: 1.8em;
    font-size: 10px;
  }

  .topbar-status-grid .rail-option-meta,
  .rail-option-meta {
    flex-shrink: 0;
    min-width: 2.1em;
    text-align: right;
  }

  .rail-option {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 12px;
    border-radius: 12px;
    border: 1px solid rgba(114, 132, 159, 0.18);
    background: rgba(8, 14, 24, 0.7);
    color: #d8e4f5;
    cursor: pointer;
    transition: transform 0.16s ease, border-color 0.16s ease, background 0.16s ease;
  }

  .rail-option:hover {
    transform: translateY(-1px);
    border-color: rgba(144, 205, 244, 0.34);
    background: rgba(12, 20, 35, 0.92);
  }

  .rail-option.active {
    border-color: rgba(54, 199, 255, 0.56);
    background: rgba(10, 36, 56, 0.82);
    box-shadow: inset 0 0 0 1px rgba(127, 224, 255, 0.22);
  }

  .rail-option-label {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    text-align: left;
  }

  .rail-option-icon {
    width: 18px;
    display: inline-flex;
    justify-content: center;
    font-size: 13px;
    color: #86d9ff;
  }

  .rail-option-meta {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: #93abc5;
  }

  .tag-pill {
    --tag-h: 196;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    min-height: 30px;
    padding: 6px 10px;
    border-radius: 999px;
    border: 1px solid hsla(var(--tag-h), 72%, 62%, 0.24);
    background: hsla(var(--tag-h), 64%, 14%, 0.56);
    color: hsl(var(--tag-h), 90%, 85%);
    cursor: pointer;
    transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
  }

  .tag-pill:hover {
    transform: translateY(-1px);
    border-color: hsla(var(--tag-h), 78%, 72%, 0.44);
  }

  .tag-pill.active {
    background: hsla(var(--tag-h), 78%, 18%, 0.88);
    border-color: hsla(var(--tag-h), 90%, 74%, 0.7);
    box-shadow: inset 0 0 0 1px hsla(var(--tag-h), 92%, 78%, 0.32);
  }

  .tag-pill.compact {
    min-height: 26px;
    padding: 4px 9px;
    font-size: 11px;
  }

  .tag-pill.muted {
    opacity: 0.48;
  }

  .tag-pill.static {
    cursor: default;
  }

  .tag-dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
    background: hsl(var(--tag-h), 94%, 76%);
    box-shadow: 0 0 0 3px hsla(var(--tag-h), 78%, 62%, 0.18);
  }

  .pill-count {
    min-width: 18px;
    padding: 1px 6px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(255, 255, 255, 0.06);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    text-align: center;
  }

  .rail-details {
    margin-top: 10px;
    border-radius: 18px;
    border: 1px solid rgba(110, 124, 149, 0.16);
    background: rgba(10, 16, 27, 0.62);
    overflow: hidden;
  }

  .rail-details summary {
    list-style: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 12px 14px;
    font-weight: 600;
    color: #dce8f7;
  }

  .rail-details summary::-webkit-details-marker { display: none; }

  .rail-details[open] summary {
    border-bottom: 1px solid rgba(148, 163, 184, 0.1);
  }

  .secondary-stack {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 12px;
  }

  .rail-compact {
    padding: 12px;
  }

  .summary-label {
    display: block;
    margin-top: 4px;
    font-size: 11px;
    color: var(--muted);
  }

  .active-filters {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: 12px;
    flex-wrap: wrap;
    min-height: 38px;
    padding: 8px 12px;
    margin-bottom: 14px;
    border-radius: 14px;
    border: 1px solid rgba(148, 163, 184, 0.12);
    background: rgba(8, 14, 25, 0.6);
  }

  .active-filters.empty {
    display: none;
  }

  .active-filter-list {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .active-filter-chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 5px 9px;
    border-radius: 999px;
    border: 1px solid rgba(110, 124, 149, 0.24);
    background: rgba(11, 20, 34, 0.88);
    color: #dce8f7;
    font-size: 11px;
  }

  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 40px;
    padding: 0 14px;
    border-radius: 12px;
    border: 1px solid transparent;
    color: var(--text);
    cursor: pointer;
    transition: transform 0.16s ease, border-color 0.16s ease, background 0.16s ease, opacity 0.16s ease;
  }

  .btn-compact {
    min-height: 36px;
    padding: 0 12px;
    border-radius: 14px;
    font-size: 12px;
    white-space: nowrap;
  }

  .btn:hover {
    transform: translateY(-1px);
  }

  .btn:disabled,
  .mini-link:disabled,
  .mini-danger:disabled {
    opacity: 0.45;
    cursor: not-allowed;
    transform: none;
  }

  .btn-start {
    background: rgba(34, 197, 94, 0.14);
    border-color: rgba(34, 197, 94, 0.28);
    color: #86efac;
  }

  .btn-start:hover { background: rgba(34, 197, 94, 0.22); }

  .btn-stop {
    background: rgba(251, 113, 133, 0.14);
    border-color: rgba(251, 113, 133, 0.28);
    color: #fda4af;
  }

  .btn-stop:hover { background: rgba(251, 113, 133, 0.22); }

  .btn-log {
    background: rgba(30, 41, 59, 0.72);
    border-color: rgba(96, 165, 250, 0.18);
    color: #bfd6ef;
  }

  .btn-log:hover { background: rgba(38, 52, 74, 0.94); }

  #compact-toggle-btn.active {
    background: rgba(54, 199, 255, 0.2);
    border-color: rgba(54, 199, 255, 0.42);
    color: #9be9ff;
  }

  .btn-open {
    background: rgba(30, 58, 95, 0.76);
    border-color: rgba(96, 165, 250, 0.24);
    color: #8fcbff;
  }

  .btn-open:hover { background: rgba(35, 73, 118, 0.96); }

  .btn-settings {
    background: rgba(15, 23, 42, 0.84);
    border-color: rgba(148, 163, 184, 0.18);
    color: #dbe7f5;
  }

  .btn-settings:hover { background: rgba(23, 36, 61, 0.96); }

  .btn-update {
    min-height: 28px;
    padding: 0 10px;
    border-radius: 999px;
    background: rgba(103, 80, 164, 0.22);
    border: 1px solid rgba(167, 139, 250, 0.28);
    color: #d8b4fe;
    cursor: pointer;
    font-size: 11px;
    transition: transform 0.16s ease, background 0.16s ease;
  }

  .btn-update:hover { transform: translateY(-1px); background: rgba(109, 40, 217, 0.28); }

  .stage-grid {
    display: grid;
    gap: 14px;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  }

  .card {
    position: relative;
    padding: 14px;
    border-radius: 20px;
    background: linear-gradient(180deg, rgba(14, 23, 38, 0.96), rgba(8, 13, 24, 0.98));
    border: 1px solid rgba(110, 124, 149, 0.22);
    overflow: hidden;
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.03);
    transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
  }

  .card:hover {
    transform: translateY(-2px);
    border-color: rgba(148, 163, 184, 0.34);
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.26), inset 0 0 0 1px rgba(255, 255, 255, 0.04);
  }

  .card::before {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(135deg, rgba(54, 199, 255, 0.08), transparent 44%, transparent 66%, rgba(244, 114, 182, 0.06));
    opacity: 0;
    transition: opacity 0.16s ease;
    pointer-events: none;
  }

  .card:hover::before,
  .card.running::before,
  .card.warning::before {
    opacity: 1;
  }

  .card.running {
    border-color: rgba(34, 197, 94, 0.34);
  }

  .card.warning {
    border-color: rgba(245, 158, 11, 0.36);
  }

  .card-header,
  .card-meta {
    position: relative;
    z-index: 1;
  }

  .card-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    padding-bottom: 6px;
  }

  .status-cluster {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  .dot {
    width: 10px;
    height: 10px;
    border-radius: 999px;
    flex-shrink: 0;
    transition: background 0.2s ease, box-shadow 0.2s ease;
  }

  .dot-on  { background: var(--ok); box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.14); }
  .dot-off { background: rgba(107, 114, 128, 0.72); }
  .dot-busy { background: var(--warn); animation: blink 1s infinite; box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.14); }

  @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.34; } }

  .card-title {
    font-size: 17px;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 0;
  }

  .card-inline-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .group-badge {
    display: inline-flex;
    align-items: center;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    border: 1px solid transparent;
  }

  .badge-Python { background: rgba(30, 58, 95, 0.22); color: #93c5fd; border-color: rgba(59, 130, 246, 0.24); }
  .badge-Node   { background: rgba(20, 83, 45, 0.22); color: #86efac; border-color: rgba(34, 197, 94, 0.24); }
  .badge-Java   { background: rgba(67, 26, 0, 0.26); color: #fdba74; border-color: rgba(251, 146, 60, 0.24); }
  .badge-Docker { background: rgba(12, 26, 53, 0.22); color: #7dd3fc; border-color: rgba(14, 165, 233, 0.24); }
  .badge-Education { background: rgba(76, 29, 149, 0.22); color: #d8b4fe; border-color: rgba(147, 51, 234, 0.24); }
  .badge-AI     { background: rgba(76, 29, 149, 0.22); color: #f0abfc; border-color: rgba(217, 70, 239, 0.24); }
  .badge-Tools  { background: rgba(26, 31, 46, 0.9); color: #cbd5e1; border-color: rgba(148, 163, 184, 0.18); }
  .badge-default { background: rgba(15, 23, 42, 0.9); color: #dbe7f5; border-color: rgba(148, 163, 184, 0.18); }

  .card-brief {
    position: relative;
    z-index: 1;
    margin: 10px 0 0;
    color: var(--muted);
    line-height: 1.52;
    font-size: 13px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .card-main {
    position: relative;
    z-index: 1;
    margin-top: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }

  .card-main-btn {
    min-width: 108px;
    white-space: nowrap;
  }

  .card-icon-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-left: auto;
  }

  .icon-btn {
    width: 32px;
    height: 32px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 10px;
    border: 1px solid rgba(114, 132, 159, 0.24);
    background: rgba(10, 17, 30, 0.82);
    color: #b9cde4;
    cursor: pointer;
    transition: transform 0.16s ease, border-color 0.16s ease, background 0.16s ease, color 0.16s ease;
  }

  .icon-btn:hover {
    transform: translateY(-1px);
    border-color: rgba(96, 165, 250, 0.42);
    background: rgba(16, 29, 49, 0.96);
    color: #e6f0fa;
  }

  .icon-btn.active {
    border-color: rgba(54, 199, 255, 0.56);
    background: rgba(9, 40, 62, 0.9);
    color: #8ee4ff;
  }

  .icon-btn.success {
    border-color: rgba(34, 197, 94, 0.46);
    color: #86efac;
  }

  .icon-btn.danger {
    border-color: rgba(251, 113, 133, 0.46);
    color: #fda4af;
  }

  .icon-btn:disabled {
    opacity: 0.45;
    cursor: not-allowed;
    transform: none;
  }

  .card-detail {
    position: relative;
    z-index: 1;
    max-height: 0;
    opacity: 0;
    overflow: hidden;
    margin-top: 0;
    padding-top: 0;
    border-top: 1px solid transparent;
    transition: max-height 0.26s ease, opacity 0.2s ease, margin-top 0.2s ease, padding-top 0.2s ease, border-color 0.2s ease;
  }

  .card.expanded .card-detail {
    max-height: 520px;
    opacity: 1;
    margin-top: 12px;
    padding-top: 12px;
    border-top-color: rgba(148, 163, 184, 0.12);
  }

  .card-detail-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .service-state-chip {
    display: inline-flex;
    align-items: center;
    min-height: 24px;
    padding: 0 9px;
    border-radius: 999px;
    font-size: 11px;
    border: 1px solid transparent;
  }

  .service-state-chip.running {
    color: #86efac;
    border-color: rgba(34, 197, 94, 0.32);
    background: rgba(20, 83, 45, 0.3);
  }

  .service-state-chip.stopped {
    color: #c8d5e6;
    border-color: rgba(110, 124, 149, 0.28);
    background: rgba(15, 23, 42, 0.74);
  }

  .card-tag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 10px;
  }

  .intro-text {
    margin: 9px 0 0;
    padding-left: 8px;
    border-left: 2px solid rgba(148, 163, 184, 0.2);
    color: #93a7be;
    font-size: 12px;
    line-height: 1.6;
  }

  .btn-copy-intro {
    margin-top: 10px;
    min-height: 28px;
    padding: 0 10px;
    border-radius: 999px;
    border: 1px solid rgba(148, 163, 184, 0.18);
    background: rgba(15, 23, 42, 0.8);
    color: #9eb4ca;
    cursor: pointer;
    transition: border-color 0.16s ease, color 0.16s ease;
  }

  .btn-copy-intro:hover { color: #dbe7f5; border-color: rgba(165, 180, 252, 0.34); }
  .btn-copy-intro.copied { color: #86efac; border-color: rgba(34, 197, 94, 0.34); }

  .url-row {
    position: relative;
    z-index: 1;
    margin-top: 10px;
    display: inline-flex;
    align-items: center;
    padding: 6px 10px;
    border-radius: 12px;
    border: 1px solid rgba(96, 165, 250, 0.14);
    background: rgba(11, 20, 34, 0.68);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: #7eb8ff;
    word-break: break-all;
    text-decoration: none;
  }

  .url-row.muted { color: #5d7188; }

  .warning-note {
    position: relative;
    z-index: 1;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-top: 10px;
    padding: 7px 10px;
    border-radius: 14px;
    border: 1px solid rgba(245, 158, 11, 0.24);
    background: rgba(120, 53, 15, 0.16);
    color: #fdba74;
    font-size: 11px;
    line-height: 1.5;
  }

  .hint-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    border-radius: 999px;
    border: 1px dashed rgba(148, 163, 184, 0.22);
    color: #7f91a7;
    background: rgba(8, 14, 24, 0.34);
    font-size: 11px;
  }

  .hint-chip.subtle {
    border-style: solid;
    color: #9db2c8;
    background: rgba(12, 20, 34, 0.7);
  }

  .card .btn {
    min-height: 34px;
    padding: 0 12px;
    border-radius: 10px;
    font-size: 12px;
  }

  .compact-core-action {
    display: none;
    width: 30px;
    height: 30px;
    font-size: 13px;
  }

  body.compact-mode .stage-grid {
    gap: 10px;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  }

  body.compact-mode .card {
    padding: 10px 12px;
    cursor: pointer;
  }

  body.compact-mode .card-header {
    align-items: center;
    padding-bottom: 0;
  }

  body.compact-mode .status-cluster {
    flex: 1;
    min-width: 0;
  }

  body.compact-mode .card-title {
    font-size: 15px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  body.compact-mode .group-badge,
  body.compact-mode .card-brief,
  body.compact-mode .card-main-btn {
    display: none;
  }

  body.compact-mode .compact-core-action {
    display: inline-flex;
  }

  body.compact-mode .card-main {
    margin-top: 0;
    min-height: 0;
  }

  body.compact-mode .card-icon-row {
    width: 100%;
    margin-left: 0;
    opacity: 0;
    max-height: 0;
    overflow: hidden;
    pointer-events: none;
    transition: opacity 0.18s ease, max-height 0.18s ease, margin-top 0.18s ease;
  }

  body.compact-mode .card.expanded .card-icon-row {
    opacity: 1;
    max-height: 56px;
    margin-top: 8px;
    pointer-events: auto;
  }

  body.compact-mode .card.expanded .card-detail {
    margin-top: 8px;
    padding-top: 8px;
  }

  body.compact-mode .card button,
  body.compact-mode .card a {
    cursor: pointer;
  }

  .empty-state {
    display: grid;
    place-items: center;
    min-height: 260px;
    border-radius: 18px;
    border: 1px dashed rgba(148, 163, 184, 0.16);
    background: rgba(7, 12, 22, 0.44);
    color: #8aa0b8;
    text-align: center;
    padding: 24px;
  }

  .empty-state strong {
    display: block;
    color: #dbe7f5;
    font-size: 18px;
    margin-bottom: 8px;
  }

  .empty-mini {
    color: #7f91a7;
    font-size: 12px;
  }

  #modal-overlay,
  #settings-overlay,
  #project-tag-overlay {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 60;
    align-items: center;
    justify-content: center;
    padding: 24px;
    background: rgba(0, 0, 0, 0.78);
    backdrop-filter: blur(10px);
  }

  #modal-overlay.open,
  #settings-overlay.open,
  #project-tag-overlay.open { display: flex; }

  #modal,
  #settings-modal,
  #project-tag-modal {
    position: relative;
    width: min(920px, 100%);
    max-height: min(88vh, 920px);
    border-radius: 26px;
    overflow: hidden;
  }

  #settings-modal {
    width: min(1180px, 100%);
    display: flex;
    flex-direction: column;
  }

  #project-tag-modal {
    width: min(760px, 100%);
    display: flex;
    flex-direction: column;
  }

  .modal-header,
  .modal-footer {
    position: relative;
    z-index: 1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    padding: 18px 22px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.12);
    flex-shrink: 0;
  }

  .modal-footer {
    border-top: 1px solid rgba(148, 163, 184, 0.12);
    border-bottom: 0;
    background: linear-gradient(180deg, rgba(10, 16, 28, 0.94), rgba(7, 12, 22, 0.98));
  }

  .modal-title-wrap {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .modal-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: #7dd3fc;
  }

  .modal-title {
    font-weight: 700;
    letter-spacing: -0.02em;
  }

  #log-box {
    position: relative;
    z-index: 1;
    flex: 1;
    min-height: 280px;
    overflow-y: auto;
    padding: 18px 22px 24px;
    background: rgba(3, 7, 14, 0.92);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    line-height: 1.68;
  }

  .log-err  { color: #fda4af; }
  .log-warn { color: #fdba74; }
  .log-info { color: #86efac; }
  .log-dim  { color: #516173; }
  .log-norm { color: #d7e4f2; }

  .settings-layout {
    position: relative;
    z-index: 1;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
    padding: 16px 18px 18px;
    overflow-y: auto;
    overflow-x: hidden;
    flex: 1 1 auto;
    min-height: 0;
  }

  .settings-card {
    padding: 16px;
    border-radius: 20px;
    border: 1px solid rgba(110, 124, 149, 0.16);
    background: rgba(9, 15, 26, 0.6);
  }

  .settings-card-wide {
    grid-column: 1 / -1;
  }

  .settings-card .field-row + .field-row {
    margin-top: 14px;
  }

  .tag-create-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
  }

  .settings-tag-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-top: 14px;
  }

  .suggestion-group {
    padding: 12px;
    border-radius: 16px;
    border: 1px solid rgba(148, 163, 184, 0.12);
    background: rgba(4, 8, 16, 0.32);
  }

  .suggestion-group + .suggestion-group {
    margin-top: 10px;
  }

  .suggestion-group-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
  }

  .suggestion-group-title {
    font-size: 13px;
    font-weight: 600;
    color: #dce8f7;
  }

  .settings-tag-item,
  .settings-project-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
    padding: 12px;
    border-radius: 16px;
    border: 1px solid rgba(148, 163, 184, 0.12);
    background: rgba(4, 8, 16, 0.36);
  }

  .settings-project-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin-top: 14px;
    padding-right: 4px;
  }

  .settings-project-meta {
    min-width: 210px;
  }

  .settings-project-name {
    font-weight: 600;
    letter-spacing: -0.01em;
  }

  .settings-project-sub {
    margin-top: 4px;
    font-size: 12px;
    color: #7f91a7;
  }

  .settings-project-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    flex: 1;
    align-items: flex-start;
  }

  .settings-status-note {
    font-size: 12px;
    color: #8fb5d6;
  }

  .settings-save-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
  }

  .modal-actions {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .card-tag-actions {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 10px;
  }

  .card-tag-edit-btn {
    min-height: 28px;
    padding: 0 10px;
    border-radius: 999px;
    font-size: 11px;
  }

  .project-tag-editor-body {
    position: relative;
    z-index: 1;
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    padding: 18px 22px 22px;
  }

  .project-tag-editor-summary {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
    margin-bottom: 16px;
  }

  .project-tag-editor-tags {
    min-height: 56px;
    align-content: flex-start;
  }

  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(71, 85, 105, 0.58); border-radius: 999px; }

  @media (max-width: 1320px) {
    .filter-toolbar {
      grid-template-columns: minmax(0, 2.4fr) minmax(220px, 1fr);
    }
  }

  @media (max-width: 900px) {
    .card-header,
    .modal-header,
    .modal-footer,
    .settings-tag-item,
    .settings-project-row {
      flex-direction: column;
      align-items: flex-start;
    }

    .search-controls {
      grid-template-columns: 1fr;
    }

    .search-sort-row,
    .project-tag-editor-summary {
      flex-direction: column;
      align-items: flex-start;
    }

    .search-sort-row .field-input {
      width: 100%;
      max-width: none;
    }

    .topbar-inner {
      grid-template-columns: 1fr;
      align-items: flex-start;
    }

    .topbar-status,
    .topbar-actions {
      width: 100%;
      justify-self: stretch;
    }

    .topbar-actions {
      align-items: flex-end;
      justify-content: flex-end;
    }

    .filter-toolbar {
      grid-template-columns: 1fr;
    }

    .status-toolbar {
      align-items: stretch;
    }

    .topbar-status-grid,
    .rail-inline-filters {
      width: 100%;
      flex-wrap: wrap;
      justify-content: flex-start;
    }

    .topbar-status-grid .rail-option,
    .topbar-status .btn-compact,
    .topbar-actions .btn,
    .rail-inline-filters .rail-option,
    .status-toolbar .btn-compact {
      flex: 1 1 150px;
    }

    .rail-tags-block .tag-cloud-inline {
      min-height: 0;
    }

    .status-grid {
      grid-template-columns: 1fr;
    }

    .card-main {
      flex-direction: column;
      align-items: stretch;
    }

    .card-main-btn {
      width: 100%;
    }

    .card-icon-row {
      margin-left: 0;
      justify-content: flex-start;
      width: 100%;
    }

    body.compact-mode .card-main {
      margin-top: 0;
    }

    body.compact-mode .card-icon-row {
      opacity: 0;
      max-height: 0;
      margin-top: 0;
      pointer-events: none;
    }

    body.compact-mode .card.expanded .card-icon-row {
      opacity: 1;
      max-height: 96px;
      margin-top: 8px;
      pointer-events: auto;
    }

    .settings-layout {
      grid-template-columns: 1fr;
    }

    .settings-card-wide {
      grid-column: auto;
    }

    .field-input.narrow {
      max-width: none;
    }
  }
</style>
</head>
<body>
<header class="topbar">
  <div class="shell topbar-inner">
    <div class="topbar-copy">
      <div class="eyebrow">Tag-Driven Local Ops Board</div>
      <div class="title-row">
        <span class="brand-mark">&#9863;</span>
        <div>
          <h1>Dev Dashboard</h1>
        </div>
      </div>
    </div>
    <div class="topbar-status">
      <div id="status-filters" class="topbar-status-grid"></div>
      <button class="btn btn-log btn-compact" type="button" onclick="clearAllFilters()" title="清空当前搜索、状态、分组与标签筛选">&#10227; 重置视图</button>
    </div>
    <div class="topbar-actions">
      <button class="btn btn-settings" type="button" onclick="openSettings()">&#9881; 设置</button>
      <button class="btn btn-log" type="button" onclick="refreshAll()">&#8635; 刷新</button>
      <button id="compact-toggle-btn" class="btn btn-log" type="button" onclick="toggleCompactMode()" title="开启超紧凑模式（每卡一行核心信息，点击卡片展开详情）" aria-pressed="false">&#128269; 紧凑</button>
    </div>
  </div>
</header>

<main class="shell">
  <div class="dashboard-grid">
    <section class="rail">
      <div class="filter-toolbar">
        <section class="rail-block primary rail-search-block">
          <div class="search-controls">
            <div class="search-inline-row">
              <label class="field-label" for="search-input">搜索服务</label>
              <input id="search-input" class="field-input" type="text" placeholder="名称 / 描述 / 标签 / 分组" oninput="setSearchQuery(this.value)">
            </div>
            <div class="search-sort-row">
              <label class="field-label inline" for="sort-select">排序</label>
              <select id="sort-select" class="field-input narrow" onchange="setSortMode(this.value)">
                <option value="default">默认分组</option>
                <option value="name">名称 A-Z</option>
              </select>
            </div>
          </div>
        </section>

        <section class="rail-block rail-tags-block">
          <div id="tag-filters" class="tag-cloud tag-cloud-inline"></div>
        </section>

        <section class="rail-block rail-group-block">
          <div id="group-filters" class="rail-inline-filters"></div>
        </section>
      </div>
    </section>

    <section class="stage">
      <div id="active-filters" class="active-filters empty"></div>
      <div id="groups"></div>
    </section>
  </div>
</main>

<div id="modal-overlay" onclick="closeModal(event)">
  <div id="modal">
    <div class="modal-header">
      <div class="modal-title-wrap">
        <span class="modal-label">logs</span>
        <span id="modal-title" class="modal-title"></span>
        <span id="modal-port" class="section-meta"></span>
      </div>
      <div class="modal-actions">
        <button class="btn btn-log" type="button" onclick="clearLogs()">清空</button>
        <button class="btn btn-stop" type="button" onclick="closeModal()">&#10005; 关闭</button>
      </div>
    </div>
    <div id="log-box"></div>
  </div>
</div>

<div id="settings-overlay" onclick="closeSettings(event)">
  <div id="settings-modal">
    <div class="modal-header">
      <div class="modal-title-wrap">
        <span class="modal-label">settings</span>
        <span class="modal-title">标签与启动配置</span>
      </div>
      <button class="btn btn-stop" type="button" onclick="closeSettings()">&#10005; 关闭</button>
    </div>

    <div class="settings-layout">
      <section class="settings-card">
        <div class="section-kicker">Secrets</div>
        <div class="field-row">
          <label for="setting-tavily" class="field-label">TAVILY_API_KEY</label>
          <input id="setting-tavily" class="field-input" type="password" placeholder="tvly-..." autocomplete="off">
          <p class="field-help">Deep Research 在图加载阶段需要这个密钥。</p>
        </div>
        <div class="field-row">
          <label for="setting-nvidia" class="field-label">NVIDIA_API_KEY</label>
          <input id="setting-nvidia" class="field-input" type="password" placeholder="nvapi-..." autocomplete="off">
          <p class="field-help">NVIDIA Nemotron 示例依赖这个模型访问密钥。</p>
        </div>
        <div class="field-row">
          <label for="setting-modal-id" class="field-label">MODAL_TOKEN_ID</label>
          <input id="setting-modal-id" class="field-input" type="password" placeholder="ak-..." autocomplete="off">
          <p class="field-help">某些 Graph 在导入前就会触发 Modal 鉴权。</p>
        </div>
        <div class="field-row">
          <label for="setting-modal-secret" class="field-label">MODAL_TOKEN_SECRET</label>
          <input id="setting-modal-secret" class="field-input" type="password" placeholder="as-..." autocomplete="off">
          <p class="field-help">与上面的 Modal Token ID 成对使用。</p>
        </div>
      </section>

      <section class="settings-card">
        <div class="section-kicker">Tag Registry</div>
        <div class="tag-create-row">
          <input id="new-tag-input" class="field-input" type="text" placeholder="例如：语音、语音识别、语音合成" onkeydown="handleNewTagKeydown(event)">
          <button class="btn btn-settings" type="button" onclick="addTagFromInput()">添加标签</button>
        </div>
        <p class="field-help compact">新增标签后会先加入当前草稿，再在底部点击“确认保存”写入。内置一组常用中文标签，按主题整组加入后，再在项目映射里逐个挂载。</p>
        <div id="quick-tag-suggestions" class="settings-tag-cloud" style="margin-top: 14px;"></div>
        <div id="settings-tags" class="settings-tag-list"></div>
      </section>

      <section class="settings-card settings-card-wide">
        <div class="section-row">
          <div>
            <div class="section-kicker">Project Mapping</div>
            <p class="field-help compact">为项目打上多个标签。点击标签即可切换绑定。</p>
          </div>
          <input id="settings-project-search" class="field-input narrow" type="text" placeholder="筛选项目" oninput="renderSettingsProjectAssignments()">
        </div>
        <div id="settings-projects" class="settings-project-list"></div>
      </section>
    </div>

    <div class="modal-footer">
      <div class="settings-save-bar">
        <span class="field-help">所有标签关系和密钥都会保存在 ~/.config/dev-dashboard/settings.json</span>
        <div class="modal-actions">
          <span class="settings-status-note">修改服务标签后，点击确认保存生效</span>
          <button class="btn btn-log" type="button" onclick="closeSettings()">取消</button>
          <button id="save-settings-btn" class="btn btn-start" type="button" onclick="saveSettings()">确认保存</button>
        </div>
      </div>
    </div>
  </div>
</div>

<div id="project-tag-overlay" onclick="closeProjectTagEditor(event)">
  <div id="project-tag-modal">
    <div class="modal-header">
      <div class="modal-title-wrap">
        <span class="modal-label">tags</span>
        <span id="project-tag-title" class="modal-title"></span>
      </div>
      <button class="btn btn-stop" type="button" onclick="closeProjectTagEditor()">&#10005; 关闭</button>
    </div>

    <div class="project-tag-editor-body">
      <div class="project-tag-editor-summary">
        <div>
          <div id="project-tag-subtitle" class="section-meta"></div>
          <p class="field-help compact">点击标签即可多选；已选标签会高亮。也可以直接新增标签并立即绑定到当前项目。</p>
        </div>
        <span id="project-tag-count" class="hint-chip subtle">0 个标签</span>
      </div>

      <div class="field-row">
        <label for="project-tag-new-input" class="field-label">新增标签</label>
        <div class="tag-create-row">
          <input id="project-tag-new-input" class="field-input" type="text" placeholder="例如：本地工具、实验性" onkeydown="handleProjectTagNewKeydown(event)">
          <button class="btn btn-settings" type="button" onclick="addProjectTagFromInput()">添加并选中</button>
        </div>
      </div>

      <div id="project-tag-editor-tags" class="settings-tag-cloud project-tag-editor-tags"></div>
    </div>

    <div class="modal-footer">
      <div class="settings-save-bar">
        <span class="field-help">保存后会立即写入 ~/.config/dev-dashboard/settings.json</span>
        <div class="modal-actions">
          <button class="btn btn-log" type="button" onclick="closeProjectTagEditor()">取消</button>
          <button id="save-project-tag-btn" class="btn btn-start" type="button" onclick="saveProjectTagEditor()">保存标签</button>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
let projects = [];
let autoScroll = true;
let logPollTimer = null;
let loadProjectsPromise = null;
let loadSettingsPromise = null;
let searchQuery = '';
let selectedStatus = 'all';
let selectedGroup = 'All';
let activeTags = [];
let expandedCards = new Set();
const COMPACT_MODE_STORAGE_KEY = 'dev-dashboard-compact-mode';
const SORT_MODE_STORAGE_KEY = 'dev-dashboard-sort-mode';
let compactMode = true;
let sortMode = 'default';
let projectTagEditor = null;
try {
  const storedCompactMode = localStorage.getItem(COMPACT_MODE_STORAGE_KEY);
  compactMode = storedCompactMode === null ? true : storedCompactMode === '1';
} catch (_) {
  compactMode = true;
}
try {
  const storedSortMode = localStorage.getItem(SORT_MODE_STORAGE_KEY);
  sortMode = storedSortMode === 'name' ? 'name' : 'default';
} catch (_) {
  sortMode = 'default';
}

const DEFAULT_SETTINGS_STATE = {
  secrets: {
    TAVILY_API_KEY: '',
    NVIDIA_API_KEY: '',
    MODAL_TOKEN_ID: '',
    MODAL_TOKEN_SECRET: '',
  },
  tags: [],
  project_tags: {},
};

let settingsState = cloneSettingsState(DEFAULT_SETTINGS_STATE);
let settingsDraft = null;

const GROUP_ORDER = ['Python', 'Node', 'AI', 'Docker', 'Java', 'Education', 'Tools'];
const GROUP_ICON = {
  Python: '&#128013;',
  Node: '&#128994;',
  AI: '&#129302;',
  Docker: '&#128051;',
  Java: '&#9749;',
  Education: '&#128218;',
  Tools: '&#128295;',
};
const STATUS_OPTIONS = [
  { id: 'all', label: '全部', icon: '&#8862;' },
  { id: 'running', label: '运行中', icon: '&#9679;' },
  { id: 'stopped', label: '已停止', icon: '&#9711;' },
];
const STATUS_LABELS = {
  all: '全部',
  running: '运行中',
  stopped: '已停止',
};
const SUGGESTED_TAG_GROUPS = [
  {
    title: '语音能力',
    tags: ['语音', '语音识别', '语音合成', '语音克隆', '流式语音', '离线语音'],
  },
  {
    title: '模型与智能体',
    tags: ['大模型', '智能体', '工作流', '多模态', '检索增强', '推理服务'],
  },
  {
    title: '文档与视觉',
    tags: ['文档解析', '光学识别', '视觉', '图像生成', '知识库', '数据处理'],
  },
  {
    title: '工程与接入',
    tags: ['前端', '后端', '接口服务', '网页界面', '本地服务', '工具链'],
  },
  {
    title: '运维与状态',
    tags: ['监控', '调试', '研究', '实验性', '自动化', '集成测试'],
  },
];

function normalizeTagLabel(value) {
  return String(value ?? '').trim().replace(/\s+/g, ' ');
}

function uniqueStrings(values) {
  const seen = new Set();
  const result = [];
  for (const rawValue of values || []) {
    const value = normalizeTagLabel(rawValue);
    if (!value) continue;
    const key = value.toLocaleLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(value);
  }
  return result;
}

function collectMappedTags(projectTags) {
  const collected = [];
  if (!projectTags || typeof projectTags !== 'object') return collected;
  for (const tags of Object.values(projectTags)) {
    if (Array.isArray(tags)) collected.push(...tags);
  }
  return uniqueStrings(collected);
}

function cloneSettingsState(source) {
  const base = source && typeof source === 'object' ? source : DEFAULT_SETTINGS_STATE;
  const secrets = { ...DEFAULT_SETTINGS_STATE.secrets };
  const secretSource = base.secrets && typeof base.secrets === 'object' ? base.secrets : {};
  for (const key of Object.keys(secrets)) {
    secrets[key] = typeof secretSource[key] === 'string' ? secretSource[key] : '';
  }

  const projectTags = {};
  const mappingSource = base.project_tags && typeof base.project_tags === 'object' ? base.project_tags : {};
  for (const [projectId, tags] of Object.entries(mappingSource)) {
    const normalized = uniqueStrings(tags);
    if (normalized.length) projectTags[projectId] = normalized;
  }

  const tags = uniqueStrings([
    ...(Array.isArray(base.tags) ? base.tags : []),
    ...collectMappedTags(projectTags),
  ]);

  return { secrets, tags, project_tags: projectTags };
}

function coerceSettingsState(data) {
  if (!data || typeof data !== 'object') return cloneSettingsState(DEFAULT_SETTINGS_STATE);
  const secretSource = data.secrets && typeof data.secrets === 'object' ? data.secrets : data;
  const normalized = {
    secrets: { ...DEFAULT_SETTINGS_STATE.secrets },
    tags: uniqueStrings(Array.isArray(data.tags) ? data.tags : []),
    project_tags: {},
  };

  for (const key of Object.keys(DEFAULT_SETTINGS_STATE.secrets)) {
    normalized.secrets[key] = typeof secretSource[key] === 'string' ? secretSource[key] : '';
  }

  const projectTags = data.project_tags && typeof data.project_tags === 'object' ? data.project_tags : {};
  for (const [projectId, tags] of Object.entries(projectTags)) {
    const uniqueTags = uniqueStrings(Array.isArray(tags) ? tags : []);
    if (uniqueTags.length) normalized.project_tags[projectId] = uniqueTags;
  }

  normalized.tags = uniqueStrings([...normalized.tags, ...collectMappedTags(normalized.project_tags)]);
  return normalized;
}

function compactProjectTags(mapping) {
  const result = {};
  for (const [projectId, tags] of Object.entries(mapping || {})) {
    const uniqueTags = uniqueStrings(Array.isArray(tags) ? tags : []);
    if (uniqueTags.length) result[projectId] = uniqueTags;
  }
  return result;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeJsString(value) {
  return String(value ?? '')
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/\n/g, '\\n');
}

function tagHue(tag) {
  let hash = 0;
  for (const character of String(tag)) {
    hash = ((hash << 5) - hash) + character.charCodeAt(0);
    hash |= 0;
  }
  return Math.abs(hash) % 360;
}

function tagStyle(tag) {
  return `--tag-h:${tagHue(tag)}`;
}

function compareLabels(left, right) {
  return String(left ?? '').localeCompare(String(right ?? ''), 'zh-Hans-CN', {
    numeric: true,
    sensitivity: 'base',
  });
}

function renderTagPill(tag, options = {}) {
  const classes = ['tag-pill'];
  if (options.active) classes.push('active');
  if (options.compact) classes.push('compact');
  if (options.muted) classes.push('muted');
  if (!options.onclick) classes.push('static');
  const countMarkup = Number.isFinite(options.count)
    ? `<span class="pill-count">${options.count}</span>`
    : '';
  const content = `<span class="tag-dot"></span><span>${escapeHtml(tag)}</span>${countMarkup}`;
  if (!options.onclick) {
    return `<span class="${classes.join(' ')}" style="${tagStyle(tag)}">${content}</span>`;
  }
  return `<button type="button" class="${classes.join(' ')}" style="${tagStyle(tag)}" onclick="${options.onclick}">${content}</button>`;
}

function renderRailOption(label, count, active, onclick, icon) {
  return `
    <button type="button" class="rail-option ${active ? 'active' : ''}" onclick="${onclick}">
      <span class="rail-option-label">
        <span class="rail-option-icon">${icon || ''}</span>
        <span>${escapeHtml(label)}</span>
      </span>
      <span class="rail-option-meta">${count}</span>
    </button>
  `;
}

function renderActiveChip(label) {
  return `<span class="active-filter-chip">${label}</span>`;
}

function groupBadgeClass(group) {
  return GROUP_ORDER.includes(group) ? `badge-${group}` : 'badge-default';
}

function hasActiveTag(tag) {
  const key = normalizeTagLabel(tag).toLocaleLowerCase();
  return activeTags.some(activeTag => normalizeTagLabel(activeTag).toLocaleLowerCase() === key);
}

function isTagIncluded(tags, tag) {
  const key = normalizeTagLabel(tag).toLocaleLowerCase();
  return (tags || []).some(item => normalizeTagLabel(item).toLocaleLowerCase() === key);
}

function getKnownTags(source = settingsState) {
  const collected = [
    ...(Array.isArray(source?.tags) ? source.tags : []),
    ...collectMappedTags(source?.project_tags),
  ];
  if (source === settingsState) {
    for (const project of projects) {
      if (Array.isArray(project.tags)) collected.push(...project.tags);
    }
  }
  return uniqueStrings(collected);
}

function getVisibleGroups() {
  const discovered = uniqueStrings(projects.map(project => project.group).filter(Boolean));
  const ordered = GROUP_ORDER.filter(group => discovered.includes(group));
  const extras = discovered.filter(group => !GROUP_ORDER.includes(group)).sort(compareLabels);
  return ['All', ...ordered, ...extras];
}

function groupIndex(group) {
  const index = GROUP_ORDER.indexOf(group);
  return index === -1 ? GROUP_ORDER.length : index;
}

function orderedProjects(list, mode = sortMode) {
  return list.slice().sort((left, right) => {
    if (mode === 'name') {
      const nameDelta = compareLabels(left.name, right.name);
      if (nameDelta !== 0) return nameDelta;
      const groupDelta = compareLabels(left.group, right.group);
      if (groupDelta !== 0) return groupDelta;
      return (Number(left.port) || 0) - (Number(right.port) || 0);
    }

    // Pinned items always come first in the default grouped view.
    if (left.pinned && !right.pinned) return -1;
    if (!left.pinned && right.pinned) return 1;
    const groupDelta = groupIndex(left.group) - groupIndex(right.group);
    if (groupDelta !== 0) return groupDelta;
    const nameDelta = compareLabels(left.name, right.name);
    if (nameDelta !== 0) return nameDelta;
    return (Number(left.port) || 0) - (Number(right.port) || 0);
  });
}

function matchesSearch(project) {
  if (!searchQuery.trim()) return true;
  const haystack = [
    project.name,
    project.desc,
    project.group,
    ...(Array.isArray(project.tags) ? project.tags : []),
  ].join(' ').toLocaleLowerCase();
  return haystack.includes(searchQuery.trim().toLocaleLowerCase());
}

function matchesStatus(project, status = selectedStatus) {
  if (status === 'running') return !!project.running;
  if (status === 'stopped') return !project.running;
  return true;
}

function matchesGroup(project, group = selectedGroup) {
  return group === 'All' || project.group === group;
}

function matchesTags(project, tags = activeTags) {
  if (!tags.length) return true;
  return tags.every(tag => isTagIncluded(project.tags, tag));
}

function getFilteredProjects(options = {}) {
  return projects.filter(project => {
    if (!options.ignoreSearch && !matchesSearch(project)) return false;
    if (!options.ignoreStatus && !matchesStatus(project)) return false;
    if (!options.ignoreGroup && !matchesGroup(project)) return false;
    if (!options.ignoreTags && !matchesTags(project)) return false;
    return true;
  });
}

function getStatusCount(status) {
  const base = getFilteredProjects({ ignoreStatus: true });
  if (status === 'all') return base.length;
  return base.filter(project => matchesStatus(project, status)).length;
}

function getGroupCount(group) {
  const base = getFilteredProjects({ ignoreGroup: true });
  if (group === 'All') return base.length;
  return base.filter(project => project.group === group).length;
}

function getTagCount(tag) {
  const base = getFilteredProjects({ ignoreTags: true });
  return base.filter(project => isTagIncluded(project.tags, tag)).length;
}

function countTaggedProjects() {
  return projects.filter(project => Array.isArray(project.tags) && project.tags.length).length;
}

function countProjectsWithTag(mapping, tag) {
  return Object.values(mapping || {}).filter(tags => isTagIncluded(tags, tag)).length;
}

function syncSearchInput() {
  const input = document.getElementById('search-input');
  if (input && input.value !== searchQuery) input.value = searchQuery;
}

function syncSortSelect() {
  const select = document.getElementById('sort-select');
  if (select && select.value !== sortMode) select.value = sortMode;
}

function syncSettingsInputs() {
  if (!settingsDraft) return;
  const fields = {
    'setting-tavily': 'TAVILY_API_KEY',
    'setting-nvidia': 'NVIDIA_API_KEY',
    'setting-modal-id': 'MODAL_TOKEN_ID',
    'setting-modal-secret': 'MODAL_TOKEN_SECRET',
  };

  for (const [elementId, secretName] of Object.entries(fields)) {
    const input = document.getElementById(elementId);
    if (input) input.value = settingsDraft.secrets[secretName] || '';
  }
}

function renderSidebar() {
  syncSearchInput();
  syncSortSelect();

  const statusHost = document.getElementById('status-filters');
  if (statusHost) {
    statusHost.innerHTML = STATUS_OPTIONS.map(option => (
      renderRailOption(
        option.label,
        getStatusCount(option.id),
        selectedStatus === option.id,
        `setStatusFilter('${option.id}')`,
        option.icon,
      )
    )).join('');
  }

  const groupHost = document.getElementById('group-filters');
  if (groupHost) {
    const groups = getVisibleGroups();
    if (!groups.includes(selectedGroup)) selectedGroup = 'All';
    groupHost.innerHTML = groups.map(group => (
      renderRailOption(
        group,
        getGroupCount(group),
        selectedGroup === group,
        `setGroupFilter('${escapeJsString(group)}')`,
        group === 'All' ? '&#8862;' : (GROUP_ICON[group] || '&#9679;'),
      )
    )).join('');
  }

  const tagHost = document.getElementById('tag-filters');
  if (tagHost) {
    const tags = getKnownTags(settingsState);
    tagHost.innerHTML = tags.length
      ? tags.map(tag => renderTagPill(tag, {
          count: getTagCount(tag),
          active: hasActiveTag(tag),
          muted: getTagCount(tag) === 0,
          onclick: `toggleTagFilter('${escapeJsString(tag)}')`,
        })).join('')
      : '<span class="empty-mini">还没有标签。到设置里创建后，这里会自动出现。</span>';
  }
}

function renderActiveFilters() {
  const host = document.getElementById('active-filters');
  if (!host) return;

  const items = [];
  if (searchQuery.trim()) items.push(renderActiveChip(`检索：${escapeHtml(searchQuery.trim())}`));
  if (selectedStatus !== 'all') items.push(renderActiveChip(`状态：${STATUS_LABELS[selectedStatus]}`));
  if (selectedGroup !== 'All') items.push(renderActiveChip(`分组：${escapeHtml(selectedGroup)}`));
  for (const tag of activeTags) {
    items.push(renderTagPill(tag, {
      active: true,
      compact: true,
      onclick: `toggleTagFilter('${escapeJsString(tag)}')`,
    }));
  }

  if (!items.length) {
    host.classList.add('empty');
    host.innerHTML = '';
    return;
  }

  host.classList.remove('empty');
  host.innerHTML = `<div class="active-filter-list">${items.join('')}</div>`;
}

function cardHTML(project) {
  const isExpanded = expandedCards.has(project.id);
  const dotClass = project.running ? 'dot-on' : 'dot-off';
  const cardClasses = [
    'card',
    project.running ? 'running' : '',
    project.missing_requirements && project.missing_requirements.length ? 'warning' : '',
    isExpanded ? 'expanded' : '',
  ].filter(Boolean).join(' ');
  const proto = project.ssl ? 'https' : 'http';
  const url = `${proto}://localhost:${project.port}${project.url_path || ''}`;
  const safeName = escapeHtml(project.name);
  const safeDesc = escapeHtml(project.desc || '暂无服务描述');
  const group = escapeHtml(project.group || 'Other');
  const safeId = escapeJsString(project.id);
  const safeUrl = escapeJsString(url);
  const isAsrPlayable = project.id === 'capswriter-asr' || project.id === 'vosk-asr';
  const isVibeVoiceAsr = project.id === 'vibevoice-asr-m3';
  const allTags = Array.isArray(project.tags) ? project.tags : [];
  const visibleTags = isExpanded ? allTags : allTags.slice(0, 4);
  const hiddenTagCount = isExpanded ? 0 : Math.max(0, allTags.length - visibleTags.length);
  const tagMarkup = visibleTags.length
    ? visibleTags.map(tag => renderTagPill(tag, {
        active: hasActiveTag(tag),
        compact: true,
        onclick: `toggleTagFilter('${escapeJsString(tag)}')`,
      })).join('') + (hiddenTagCount ? `<span class="hint-chip subtle">+${hiddenTagCount}</span>` : '')
    : '<span class="hint-chip subtle">未标记</span>';

  const tagActionMarkup = `
    <div class="card-tag-actions">
      <span class="section-meta">${allTags.length ? `已绑定 ${allTags.length} 个标签` : '当前还没有标签'}</span>
      <button class="btn btn-log card-tag-edit-btn" type="button" onclick="openProjectTagEditor('${safeId}')">编辑标签</button>
    </div>
  `;

  const warningMarkup = project.missing_requirements && project.missing_requirements.length
    ? `<div class="warning-note">&#9888; 需要配置：${escapeHtml(project.missing_requirements.join(', '))}</div>`
    : '';

  const urlMarkup = project.running && !project.no_ui
    ? `<a class="url-row" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">访问 ${escapeHtml(url)}</a>`
    : '';

  const primaryAction = project.running
    ? `<button class="btn btn-stop card-main-btn" type="button" onclick="stop('${safeId}')">&#9632; 停止服务</button>`
    : (project.missing_requirements && project.missing_requirements.length)
      ? `<button class="btn btn-settings card-main-btn" type="button" onclick="openSettings()">&#9881; 去配置</button>`
      : `<button class="btn btn-start card-main-btn" type="button" onclick="startAndOpen('${safeId}', ${project.port})">&#9654; 启动服务</button>`;

  const compactQuickAction = project.running
    ? `<button class="icon-btn compact-core-action danger" type="button" title="停止服务" aria-label="停止服务" onclick="stop('${safeId}')">&#9632;</button>`
    : (project.missing_requirements && project.missing_requirements.length)
      ? `<button class="icon-btn compact-core-action" type="button" title="去配置" aria-label="去配置" onclick="openSettings()">&#9881;</button>`
      : `<button class="icon-btn compact-core-action" type="button" title="启动服务" aria-label="启动服务" onclick="startAndOpen('${safeId}', ${project.port})">&#9654;</button>`;

  const iconButtons = [
    project.running && !project.no_ui
      ? `<button class="icon-btn" type="button" title="打开服务页面" aria-label="打开服务页面" onclick="window.open('${safeUrl}', '_blank')">&#8599;</button>`
      : '',
    project.has_git
      ? `<button class="icon-btn" id="upd-${safeId}" type="button" title="拉取最新更新" aria-label="更新服务" onclick="pullUpdate('${safeId}','${escapeJsString(project.name)}')">&#8635;</button>`
      : '',
    project.update_restart
      ? `<button class="icon-btn" id="upd-restart-${safeId}" type="button" title="更新并重启" aria-label="更新并重启" onclick="updateAndRestart('${safeId}','${escapeJsString(project.name)}')">&#8635;&#9654;</button>`
      : '',
    isAsrPlayable
      ? `<button class="icon-btn" type="button" title="打开 ASR 测试台" aria-label="打开 ASR 测试台" onclick="window.open('${escapeJsString(`/tools/asr-playground?service=${project.id}`)}', '_blank')">&#127908;</button>`
      : '',
    isVibeVoiceAsr
      ? `<button class="icon-btn" type="button" title="打开热键控制台" aria-label="打开热键控制台" onclick="window.open('/tools/vibevoice-asr-control', '_blank')">&#8997;</button>`
      : '',
    `<button class="icon-btn" type="button" title="查看服务日志" aria-label="查看服务日志" onclick="openLogs('${safeId}', '${escapeJsString(project.name)}', ${project.port})">&#128203;</button>`,
    `<button class="icon-btn ${isExpanded ? 'active' : ''}" type="button" title="${isExpanded ? '收起详情' : '展开详情'}" aria-label="${isExpanded ? '收起详情' : '展开详情'}" onclick="toggleCardDetails('${safeId}')">&#9432;</button>`,
  ].filter(Boolean).join('');

  const introMarkup = project.intro
    ? `
      <p class="intro-text">${escapeHtml(project.intro)}</p>
      <button class="btn-copy-intro" type="button" onclick="copyIntro(this, \`${project.intro.replace(/`/g, '\\`')}\`)">复制说明</button>
    `
    : '';

  return `
    <article class="${cardClasses}" id="card-${safeId}" onclick="handleCardClick(event, '${safeId}')">
      <div class="card-header">
        <div class="status-cluster">
          <div class="dot ${dotClass}"></div>
          <div>
            <h2 class="card-title">${safeName}</h2>
          </div>
        </div>
        <div class="card-inline-meta">
          <span class="group-badge ${groupBadgeClass(project.group)}">${group}</span>
          <span class="hint-chip subtle">:${project.port}</span>
          ${compactQuickAction}
        </div>
      </div>

      <p class="card-brief">${safeDesc}</p>

      <div class="card-main">
        ${primaryAction}
        <div class="card-icon-row">
          ${iconButtons}
        </div>
      </div>

      <div class="card-detail">
        <div class="card-detail-row">
          <span class="service-state-chip ${project.running ? 'running' : 'stopped'}">${project.running ? '在线' : '离线'}</span>
          ${project.no_ui ? '<span class="hint-chip">无 Web UI</span>' : ''}
        </div>
        ${warningMarkup}
        <div class="card-tag-row">${tagMarkup}</div>
        ${tagActionMarkup}
        ${introMarkup}
        ${urlMarkup}
      </div>
    </article>
  `;
}

function render() {
  renderSidebar();
  renderActiveFilters();

  const validIds = new Set(projects.map(project => project.id));
  expandedCards = new Set([...expandedCards].filter(id => validIds.has(id)));

  const filteredProjects = orderedProjects(getFilteredProjects());

  document.getElementById('groups').innerHTML = filteredProjects.length
    ? `<div class="stage-grid">${filteredProjects.map(cardHTML).join('')}</div>`
    : `
      <div class="empty-state">
        <div>
          <strong>没有命中当前筛选条件</strong>
          <span>可以清空顶部筛选，或者去设置里补充标签关系。</span>
        </div>
      </div>
    `;
}

async function loadProjects() {
  if (loadProjectsPromise) return loadProjectsPromise;

  try {
    loadProjectsPromise = fetch('/api/projects')
      .then(response => response.json())
      .then(data => {
        projects = Array.isArray(data) ? data : [];
        render();
        if (projectTagEditor) renderProjectTagEditor();
        return projects;
      })
      .finally(() => {
        loadProjectsPromise = null;
      });
    await loadProjectsPromise;
  } catch (error) {
    loadProjectsPromise = null;
    console.error(error);
  }
}

async function loadSettings() {
  if (loadSettingsPromise) return loadSettingsPromise;

  try {
    loadSettingsPromise = fetch('/api/settings')
      .then(response => response.json())
      .then(data => {
        settingsState = coerceSettingsState(data);
        render();
        if (projectTagEditor) renderProjectTagEditor();
        return settingsState;
      })
      .finally(() => {
        loadSettingsPromise = null;
      });
    await loadSettingsPromise;
  } catch (error) {
    loadSettingsPromise = null;
    console.error(error);
  }
}

function setSearchQuery(value) {
  searchQuery = String(value || '');
  render();
}

function setSortMode(mode) {
  const nextMode = mode === 'name' ? 'name' : 'default';
  if (sortMode === nextMode) return;
  sortMode = nextMode;
  try {
    localStorage.setItem(SORT_MODE_STORAGE_KEY, sortMode);
  } catch (_) {}
  render();
}

function setStatusFilter(status) {
  if (selectedStatus === status) return;
  selectedStatus = status;
  render();
}

function setGroupFilter(group) {
  if (selectedGroup === group) return;
  selectedGroup = group;
  render();
}

function toggleTagFilter(tag) {
  const normalized = normalizeTagLabel(tag);
  if (!normalized) return;
  if (hasActiveTag(normalized)) {
    activeTags = activeTags.filter(activeTag => normalizeTagLabel(activeTag).toLocaleLowerCase() !== normalized.toLocaleLowerCase());
  } else {
    activeTags = [...activeTags, normalized];
  }
  render();
}

function clearAllFilters() {
  searchQuery = '';
  selectedStatus = 'all';
  selectedGroup = 'All';
  activeTags = [];
  expandedCards.clear();
  render();
}

function shouldIgnoreCardToggle(target) {
  return !!target?.closest('button, a, input, select, textarea, label');
}

function handleCardClick(event, id) {
  if (!compactMode || !id || shouldIgnoreCardToggle(event.target)) return;
  toggleCardDetails(id);
}

function toggleCardDetails(id) {
  if (!id) return;
  if (expandedCards.has(id)) expandedCards.delete(id);
  else expandedCards.add(id);
  render();
}

function applyCompactMode() {
  document.body.classList.toggle('compact-mode', compactMode);
  const button = document.getElementById('compact-toggle-btn');
  if (!button) return;
  button.classList.toggle('active', compactMode);
  button.setAttribute('aria-pressed', compactMode ? 'true' : 'false');
  button.innerHTML = compactMode ? '&#128374; 标准' : '&#128269; 紧凑';
  button.title = compactMode
    ? '已开启超紧凑模式，点击恢复标准布局'
    : '开启超紧凑模式（每卡一行核心信息，点击卡片展开详情）';
}

function toggleCompactMode() {
  compactMode = !compactMode;
  try {
    localStorage.setItem(COMPACT_MODE_STORAGE_KEY, compactMode ? '1' : '0');
  } catch (_) {}
  applyCompactMode();
  render();
}

async function openSettings() {
  await Promise.all([loadSettings(), loadProjects()]);
  settingsDraft = cloneSettingsState(settingsState);
  const projectSearch = document.getElementById('settings-project-search');
  const newTagInput = document.getElementById('new-tag-input');
  if (projectSearch) projectSearch.value = '';
  if (newTagInput) newTagInput.value = '';
  syncSettingsInputs();
  renderSettingsModal();
  document.getElementById('settings-overlay').classList.add('open');
}

function closeSettings(event) {
  const overlay = document.getElementById('settings-overlay');
  if (event && event.target !== overlay) return;
  overlay.classList.remove('open');
  settingsDraft = null;
}

function renderSettingsModal() {
  if (!settingsDraft) return;
  syncSettingsInputs();
  renderQuickTagSuggestions();
  renderSettingsTags();
  renderSettingsProjectAssignments();
}

function renderQuickTagSuggestions() {
  const host = document.getElementById('quick-tag-suggestions');
  if (!host || !settingsDraft) return;

  const known = getKnownTags(settingsDraft).map(tag => tag.toLocaleLowerCase());
  const groups = SUGGESTED_TAG_GROUPS
    .map((group, index) => {
      const availableTags = group.tags.filter(tag => !known.includes(tag.toLocaleLowerCase()));
      return { ...group, index, availableTags };
    })
    .filter(group => group.availableTags.length);

  host.innerHTML = groups.length
    ? groups.map(group => `
        <div class="suggestion-group">
          <div class="suggestion-group-header">
            <span class="suggestion-group-title">${escapeHtml(group.title)}</span>
            <button class="mini-link" type="button" onclick="addTagGroup(${group.index})">整组加入</button>
          </div>
          <div class="settings-tag-cloud">
            ${group.availableTags.map(tag => renderTagPill(tag, {
              compact: true,
              onclick: `addTag('${escapeJsString(tag)}')`,
            })).join('')}
          </div>
        </div>
      `).join('')
    : '<span class="empty-mini">常用建议标签都已经加入了。</span>';
}

function renderSettingsTags() {
  const host = document.getElementById('settings-tags');
  if (!host || !settingsDraft) return;

  const tags = getKnownTags(settingsDraft);
  if (!tags.length) {
    host.innerHTML = '<div class="empty-mini">先创建标签，再为项目绑定。</div>';
    return;
  }

  host.innerHTML = tags.map(tag => `
    <div class="settings-tag-item">
      <div>${renderTagPill(tag, { count: countProjectsWithTag(settingsDraft.project_tags, tag) })}</div>
      <button class="mini-danger" type="button" onclick="removeTag('${escapeJsString(tag)}')">删除</button>
    </div>
  `).join('');
}

function renderSettingsProjectAssignments() {
  const host = document.getElementById('settings-projects');
  if (!host || !settingsDraft) return;

  const projectSearch = document.getElementById('settings-project-search');
  const query = (projectSearch?.value || '').trim().toLocaleLowerCase();
  const tags = getKnownTags(settingsDraft);
  const filteredProjects = orderedProjects(projects.filter(project => {
    if (!query) return true;
    return [project.name, project.desc, project.group].join(' ').toLocaleLowerCase().includes(query);
  }));

  if (!filteredProjects.length) {
    host.innerHTML = '<div class="empty-mini">没有匹配的项目。</div>';
    return;
  }

  host.innerHTML = filteredProjects.map(project => {
    const mappedTags = settingsDraft.project_tags[project.id] || [];
    const tagOptions = tags.length
      ? tags.map(tag => renderTagPill(tag, {
          compact: true,
          active: isTagIncluded(mappedTags, tag),
          onclick: `toggleProjectTag('${escapeJsString(project.id)}', '${escapeJsString(tag)}')`,
        })).join('')
      : '<span class="empty-mini">先创建标签。</span>';
    return `
      <div class="settings-project-row">
        <div class="settings-project-meta">
          <div class="settings-project-name">${escapeHtml(project.name)}</div>
          <div class="settings-project-sub">${escapeHtml(project.group || 'Other')} · :${project.port}</div>
        </div>
        <div class="settings-project-actions">${tagOptions}</div>
      </div>
    `;
  }).join('');
}

function addTag(tag) {
  if (!settingsDraft) return;
  const normalized = addTagToState(settingsDraft, tag);
  if (!normalized) return;
  renderSettingsModal();
}

function addTagGroup(groupIndex) {
  if (!settingsDraft) return;
  const group = SUGGESTED_TAG_GROUPS[groupIndex];
  if (!group) return;
  settingsDraft.tags = uniqueStrings([...(settingsDraft.tags || []), ...group.tags]);
  renderSettingsModal();
}

function addTagFromInput() {
  const input = document.getElementById('new-tag-input');
  if (!input) return;
  addTag(input.value);
  input.value = '';
  input.focus();
}

function handleNewTagKeydown(event) {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  addTagFromInput();
}

function removeTag(tag) {
  if (!settingsDraft) return;
  settingsDraft.tags = (settingsDraft.tags || []).filter(item => normalizeTagLabel(item).toLocaleLowerCase() !== normalizeTagLabel(tag).toLocaleLowerCase());
  const nextMapping = {};
  for (const [projectId, tags] of Object.entries(settingsDraft.project_tags || {})) {
    const remaining = (tags || []).filter(item => normalizeTagLabel(item).toLocaleLowerCase() !== normalizeTagLabel(tag).toLocaleLowerCase());
    if (remaining.length) nextMapping[projectId] = remaining;
  }
  settingsDraft.project_tags = nextMapping;
  renderSettingsModal();
}

function addTagToState(targetState, tag) {
  if (!targetState) return '';
  const normalized = normalizeTagLabel(tag);
  if (!normalized) return '';
  targetState.tags = uniqueStrings([...(targetState.tags || []), normalized]);
  return normalized;
}

function toggleProjectTagAssignment(targetState, projectId, tag) {
  if (!targetState || !projectId) return;
  if (!targetState.project_tags || typeof targetState.project_tags !== 'object') {
    targetState.project_tags = {};
  }

  const currentTags = targetState.project_tags[projectId] || [];
  if (isTagIncluded(currentTags, tag)) {
    const remaining = currentTags.filter(item => normalizeTagLabel(item).toLocaleLowerCase() !== normalizeTagLabel(tag).toLocaleLowerCase());
    if (remaining.length) targetState.project_tags[projectId] = remaining;
    else delete targetState.project_tags[projectId];
    return;
  }

  targetState.project_tags[projectId] = uniqueStrings([...currentTags, tag]);
}

function ensureProjectTagAssignment(targetState, projectId, tag) {
  if (!targetState || !projectId) return;
  if (!targetState.project_tags || typeof targetState.project_tags !== 'object') {
    targetState.project_tags = {};
  }
  const currentTags = targetState.project_tags[projectId] || [];
  if (isTagIncluded(currentTags, tag)) return;
  targetState.project_tags[projectId] = uniqueStrings([...currentTags, tag]);
}

function toggleProjectTag(projectId, tag) {
  if (!settingsDraft) return;
  toggleProjectTagAssignment(settingsDraft, projectId, tag);
  renderSettingsModal();
}

function getProjectById(projectId) {
  return projects.find(project => project.id === projectId) || null;
}

async function openProjectTagEditor(projectId) {
  await Promise.all([loadSettings(), loadProjects()]);
  const project = getProjectById(projectId);
  if (!project) return;
  projectTagEditor = {
    projectId,
    draft: cloneSettingsState(settingsState),
  };
  renderProjectTagEditor();
  document.getElementById('project-tag-overlay').classList.add('open');
  const input = document.getElementById('project-tag-new-input');
  if (input) {
    input.value = '';
    input.focus();
  }
}

function closeProjectTagEditor(event) {
  const overlay = document.getElementById('project-tag-overlay');
  if (!overlay) return;
  if (event && event.target !== overlay) return;
  overlay.classList.remove('open');
  projectTagEditor = null;
}

function renderProjectTagEditor() {
  if (!projectTagEditor) return;
  const project = getProjectById(projectTagEditor.projectId);
  if (!project) {
    closeProjectTagEditor();
    return;
  }

  const title = document.getElementById('project-tag-title');
  const subtitle = document.getElementById('project-tag-subtitle');
  const count = document.getElementById('project-tag-count');
  const host = document.getElementById('project-tag-editor-tags');
  if (!title || !subtitle || !count || !host) return;

  const mappedTags = projectTagEditor.draft.project_tags[project.id] || [];
  const tags = getKnownTags(projectTagEditor.draft).slice().sort((left, right) => {
    const selectedDelta = Number(isTagIncluded(mappedTags, right)) - Number(isTagIncluded(mappedTags, left));
    if (selectedDelta !== 0) return selectedDelta;
    return compareLabels(left, right);
  });

  title.textContent = project.name;
  subtitle.textContent = `${project.group || 'Other'} · :${project.port}`;
  count.textContent = `${mappedTags.length} 个标签`;
  host.innerHTML = tags.length
    ? tags.map(tag => renderTagPill(tag, {
        compact: true,
        active: isTagIncluded(mappedTags, tag),
        onclick: `toggleProjectTagEditorTag('${escapeJsString(tag)}')`,
      })).join('')
    : '<span class="empty-mini">当前还没有可选标签，请先新增一个。</span>';
}

function toggleProjectTagEditorTag(tag) {
  if (!projectTagEditor) return;
  toggleProjectTagAssignment(projectTagEditor.draft, projectTagEditor.projectId, tag);
  renderProjectTagEditor();
}

function addProjectTagFromInput() {
  if (!projectTagEditor) return;
  const input = document.getElementById('project-tag-new-input');
  if (!input) return;
  const normalized = addTagToState(projectTagEditor.draft, input.value);
  if (!normalized) return;
  ensureProjectTagAssignment(projectTagEditor.draft, projectTagEditor.projectId, normalized);
  input.value = '';
  input.focus();
  renderProjectTagEditor();
}

function handleProjectTagNewKeydown(event) {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  addProjectTagFromInput();
}

async function saveProjectTagEditor() {
  if (!projectTagEditor) return;
  const button = document.getElementById('save-project-tag-btn');
  if (!button) return;

  const original = button.textContent;
  button.disabled = true;
  button.textContent = '保存中...';

  const payload = {
    secrets: projectTagEditor.draft.secrets,
    tags: uniqueStrings(projectTagEditor.draft.tags || []),
    project_tags: compactProjectTags(projectTagEditor.draft.project_tags),
  };

  try {
    const response = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!data.ok) {
      alert('保存失败：' + (data.msg || '未知错误'));
      button.textContent = original;
      button.disabled = false;
      return;
    }

    settingsState = coerceSettingsState(data.settings || payload);
    if (settingsDraft) settingsDraft = cloneSettingsState(settingsState);
    button.textContent = '已保存';
    await loadProjects();
    render();
    setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
      closeProjectTagEditor();
      if (settingsDraft) renderSettingsModal();
    }, 400);
  } catch (error) {
    alert('保存失败：' + String(error));
    button.textContent = original;
    button.disabled = false;
  }
}

async function saveSettings() {
  if (!settingsDraft) return;
  const button = document.getElementById('save-settings-btn');
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '保存中...';

  settingsDraft.secrets = {
    TAVILY_API_KEY: document.getElementById('setting-tavily').value || '',
    NVIDIA_API_KEY: document.getElementById('setting-nvidia').value || '',
    MODAL_TOKEN_ID: document.getElementById('setting-modal-id').value || '',
    MODAL_TOKEN_SECRET: document.getElementById('setting-modal-secret').value || '',
  };

  const payload = {
    secrets: settingsDraft.secrets,
    tags: uniqueStrings(settingsDraft.tags || []),
    project_tags: compactProjectTags(settingsDraft.project_tags),
  };

  try {
    const response = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!data.ok) {
      alert('保存失败：' + (data.msg || '未知错误'));
      button.textContent = original;
      button.disabled = false;
      return;
    }

    settingsState = coerceSettingsState(data.settings || payload);
    button.textContent = '已保存';
    await loadProjects();
    render();
    setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
      closeSettings();
    }, 500);
  } catch (error) {
    alert('保存失败：' + String(error));
    button.textContent = original;
    button.disabled = false;
  }
}

async function startAndOpen(id, port) {
  setCardBusy(id, true);
  const response = await fetch(`/api/projects/${id}/start`, { method: 'POST' });
  const data = await response.json();
  if (!data.ok) {
    alert('启动失败：' + (data.msg || '未知错误'));
    setCardBusy(id, false);
    return;
  }

  let attempts = 0;
  const timer = setInterval(async () => {
    attempts += 1;
    await loadProjects();
    const project = projects.find(item => item.id === id);
    if (project && !project.running && !project.managed && attempts >= 2) {
      clearInterval(timer);
      setCardBusy(id, false);
      const logs = await fetch(`/api/projects/${id}/logs`).then(res => res.json()).catch(() => ({ lines: [] }));
      const excerpt = (logs.lines || []).slice(-8).join('\n');
      alert('服务启动后立即退出。\n\n' + (excerpt || '请打开日志查看具体原因。'));
      return;
    }

    if (project && project.running) {
      try {
        const pingResponse = await fetch(`/api/projects/${id}/ping`);
        const pingData = await pingResponse.json();
        if (pingData.ready) {
          clearInterval(timer);
          const openProto = project.ssl ? 'https' : 'http';
          if (!project.no_ui) {
            window.open(`${openProto}://localhost:${port}${project.url_path || ''}`, '_blank');
          }
          setCardBusy(id, false);
          return;
        }
      } catch (_) {}
    }

    if (attempts >= 30) {
      clearInterval(timer);
      setCardBusy(id, false);
      alert('服务启动超时，请查看日志。');
    }
  }, 2000);
}

async function start(id) {
  setCardBusy(id, true);
  const response = await fetch(`/api/projects/${id}/start`, { method: 'POST' });
  const data = await response.json();
  if (!data.ok) alert('启动失败：' + (data.msg || '未知错误'));
  await sleep(1200);
  await loadProjects();
  setCardBusy(id, false);
}

async function stop(id) {
  setCardBusy(id, true);
  await fetch(`/api/projects/${id}/stop`, { method: 'POST' });
  await sleep(800);
  await loadProjects();
  setCardBusy(id, false);
}

function setCardBusy(id, busy) {
  const card = document.getElementById(`card-${id}`);
  if (!card) return;
  const dot = card.querySelector('.dot');
  if (dot) dot.className = `dot ${busy ? 'dot-busy' : ''}`;
  card.querySelectorAll('button').forEach(button => {
    if (button.id === 'save-settings-btn') return;
    button.disabled = busy;
  });
}

function openLogs(id, name, port) {
  document.getElementById('modal-title').textContent = name;
  document.getElementById('modal-port').textContent = ':' + port;
  document.getElementById('log-box').innerHTML = '';
  document.getElementById('modal-overlay').classList.add('open');

  if (logPollTimer) {
    clearInterval(logPollTimer);
    logPollTimer = null;
  }

  const box = document.getElementById('log-box');
  if (box && !box.dataset.bound) {
    box.addEventListener('scroll', () => {
      autoScroll = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
    });
    box.dataset.bound = 'true';
  }

  const loadLogs = async () => {
    const response = await fetch(`/api/projects/${id}/logs`);
    const data = await response.json();
    const target = document.getElementById('log-box');
    if (!target) return;
    target.innerHTML = '';
    for (const line of data.lines || []) {
      appendLog(line);
    }
  };

  loadLogs();
  logPollTimer = setInterval(loadLogs, 1500);
}

function appendLog(line) {
  const logBox = document.getElementById('log-box');
  if (!logBox) return;

  const row = document.createElement('div');
  row.style.cssText = 'white-space:pre-wrap;word-break:break-all;padding:1px 0;';

  const lower = line.toLowerCase();
  if (!line.trim()) {
    row.style.height = '6px';
  } else if (/error|exception|traceback|fatal|critical/.test(lower)) {
    row.className = 'log-err';
  } else if (/warn|warning/.test(lower)) {
    row.className = 'log-warn';
  } else if (/✓|success|started|listening|ready|compiled|running/.test(lower)) {
    row.className = 'log-info';
  } else if (/^[─=\s]*$/.test(line)) {
    row.className = 'log-dim';
  } else {
    row.className = 'log-norm';
  }

  row.textContent = line;
  logBox.appendChild(row);
  if (autoScroll) logBox.scrollTop = logBox.scrollHeight;
}

function clearLogs() {
  document.getElementById('log-box').innerHTML = '';
}

function copyIntro(button, text) {
  navigator.clipboard.writeText(text).then(() => {
    button.textContent = '已复制';
    button.classList.add('copied');
    setTimeout(() => {
      button.textContent = '复制';
      button.classList.remove('copied');
    }, 1800);
  });
}

function closeModal(event) {
  const overlay = document.getElementById('modal-overlay');
  if (event && event.target !== overlay) return;
  overlay.classList.remove('open');
  if (logPollTimer) {
    clearInterval(logPollTimer);
    logPollTimer = null;
  }
}

async function refreshAll() {
  await Promise.all([loadSettings(), loadProjects()]);
}

async function pullUpdate(id, name) {
  const button = document.getElementById(`upd-${id}`);
  if (!button) return;
  const original = button.innerHTML;
  const originalTitle = button.title;
  button.classList.remove('success');
  button.innerHTML = '&#8987;';
  button.title = '更新中...';
  button.disabled = true;
  try {
    const response = await fetch(`/api/projects/${id}/update`, { method: 'POST' });
    const data = await response.json();
    if (data.ok) {
      button.innerHTML = '&#10003;';
      button.title = '更新成功';
      button.classList.add('success');
      setTimeout(() => {
        button.innerHTML = original;
        button.title = originalTitle;
        button.classList.remove('success');
        button.disabled = false;
      }, 2200);
    } else {
      showUpdateResult(name, false, data.msg || data.output || '未知错误');
      button.innerHTML = original;
      button.title = originalTitle;
      button.disabled = false;
    }
  } catch (error) {
    showUpdateResult(name, false, String(error));
    button.innerHTML = original;
    button.title = originalTitle;
    button.disabled = false;
  }
}

function showUpdateResult(name, ok, output) {
  const existing = document.getElementById('update-result-modal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'update-result-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.78);backdrop-filter:blur(8px);z-index:100;display:flex;align-items:center;justify-content:center;padding:24px;';
  modal.innerHTML = `
    <div style="width:min(760px,100%);max-height:80vh;overflow:hidden;border-radius:24px;border:1px solid rgba(148,163,184,0.16);background:linear-gradient(180deg, rgba(12,19,33,0.92), rgba(6,10,18,0.98));box-shadow:0 22px 60px rgba(0,0,0,0.38);">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:18px 22px;border-bottom:1px solid rgba(148,163,184,0.12);">
        <span style="font-weight:700;color:${ok ? '#86efac' : '#fda4af'};">${ok ? '✓' : '✗'} ${escapeHtml(name)} ${ok ? '更新成功' : '更新失败'}</span>
        <button onclick="document.getElementById('update-result-modal').remove()" style="min-height:36px;padding:0 12px;border-radius:12px;border:1px solid rgba(148,163,184,0.18);background:rgba(15,23,42,0.82);color:#dbe7f5;cursor:pointer;">关闭</button>
      </div>
      <pre style="margin:0;max-height:60vh;overflow:auto;padding:18px 22px;background:rgba(3,7,14,0.92);color:#d7e4f2;font:12px/1.68 'IBM Plex Mono', monospace;white-space:pre-wrap;word-break:break-all;">${escapeHtml(output)}</pre>
    </div>`;
  modal.addEventListener('click', event => {
    if (event.target === modal) modal.remove();
  });
  document.body.appendChild(modal);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function updateAndRestart(id, name) {
  const button = document.getElementById(`upd-restart-${id}`);
  if (!button) return;
  const original = button.innerHTML;
  button.innerHTML = '&#8987;';
  button.title = '停止中...';
  button.disabled = true;
  setCardBusy(id, true);
  try {
    // Step 1: stop if running
    await fetch(`/api/projects/${id}/stop`, { method: 'POST' });
    await sleep(1200);
    // Step 2: run update_cmd (git pull + build)
    button.innerHTML = '&#8635;';
    button.title = '构建中...';
    const updateResponse = await fetch(`/api/projects/${id}/update`, { method: 'POST' });
    const updateData = await updateResponse.json();
    if (!updateData.ok) {
      showUpdateResult(name, false, updateData.msg || updateData.output || '更新失败');
      button.innerHTML = original;
      button.title = '更新并重启';
      button.disabled = false;
      setCardBusy(id, false);
      await loadProjects();
      return;
    }
    // Step 3: restart
    button.innerHTML = '&#9654;';
    button.title = '重启中...';
    const startResponse = await fetch(`/api/projects/${id}/start`, { method: 'POST' });
    const startData = await startResponse.json();
    await loadProjects();
    if (startData.ok) {
      button.innerHTML = '&#10003;';
      button.title = '完成';
      button.classList.add('success');
      setTimeout(() => {
        button.innerHTML = original;
        button.title = '更新并重启';
        button.classList.remove('success');
        button.disabled = false;
        setCardBusy(id, false);
      }, 2200);
    } else {
      showUpdateResult(name, false, '更新成功，但重启失败：' + (startData.msg || ''));
      button.innerHTML = original;
      button.title = '更新并重启';
      button.disabled = false;
      setCardBusy(id, false);
    }
  } catch (error) {
    showUpdateResult(name, false, String(error));
    button.innerHTML = original;
    button.title = '更新并重启';
    button.disabled = false;
    setCardBusy(id, false);
  }
}

applyCompactMode();

Promise.all([loadSettings(), loadProjects()]);

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) loadProjects();
});

setInterval(() => {
  if (!document.hidden) loadProjects();
}, 10000);

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  closeModal();
  closeSettings();
  closeProjectTagEditor();
});
</script>
</body>
</html>"""

if __name__ == "__main__":
    print("Dev Dashboard -> http://localhost:9999")
    uvicorn.run(app, host="127.0.0.1", port=9999, log_level="warning")
