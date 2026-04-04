# Dev Dashboard

A lightweight local development service manager with a dark-themed web UI. Start, stop, monitor, and manage all your local dev services from a single dashboard.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

- **Service Management** — Start/stop local services with one click
- **Real-time Logs** — SSE-based live log streaming with color-coded output
- **Health Checks** — Automatic port detection and HTTP readiness probes (IPv4 + IPv6)
- **Git Integration** — Pull updates for git-backed services directly from the UI
- **Group Organization** — Services organized by category (Python, Node, AI, Docker, etc.)
- **Dark UI** — Clean, responsive dark theme built with Tailwind CSS
- **Zero Config Frontend** — Single-file HTML embedded in the Python backend

## Quick Start

```bash
# Install dependencies
pip install fastapi uvicorn

# Run
python server.py

# Open http://localhost:9999
```

## Adding Your Services

Edit the `PROJECTS` list in `server.py`:

```python
{
    "id": "my-service",        # Unique ID
    "name": "My Service",      # Display name
    "desc": "Description",     # Short description
    "intro": "Detailed info",  # Expandable details (optional)
    "port": 8000,              # Port number
    "group": "Python",         # Category: Python, Node, AI, Docker, Java, Education, Tools
    "dir": "/path/to/project", # Working directory
    "cmd": ["python", "app.py"],  # Start command
    # Optional fields:
    # "env": {"KEY": "value"},       # Extra environment variables
    # "ssl": True,                   # HTTPS service
    # "stop_cmd": ["docker", "stop", "name"],  # Custom stop command
    # "update_cmd": ["git", "pull"],  # Custom update command
    # "url_path": "/admin",          # URL path suffix
    # "no_ui": True,                 # No web UI (hides "Open" button)
}
```

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/projects` | List all projects with status |
| GET | `/api/projects/{id}/ping` | Health check a service |
| POST | `/api/projects/{id}/start` | Start a service |
| POST | `/api/projects/{id}/stop` | Stop a service |
| POST | `/api/projects/{id}/update` | Pull updates (git pull) |
| GET | `/api/projects/{id}/logs/stream` | SSE log stream |

## License

MIT
