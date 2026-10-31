"""Railway-style production start-up: real uvicorn process, migrations on start, health/ready, SIGTERM."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from backend.tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL, requires_services

pytestmark = requires_services
ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_api_boots_like_railway_and_stops_on_sigterm() -> None:
    port = _free_port()
    env = {
        **os.environ,
        # the start script calls `python` from PATH (the image's interpreter in production)
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
        "APP_ENV": "production",
        "PORT": str(port),
        "DATABASE_URL": TEST_DATABASE_URL.replace("localhost", "127.0.0.1"),
        "REDIS_URL": TEST_REDIS_URL.replace("localhost", "127.0.0.1"),
        "PASSWORD": "prod-key",
        "API_KEYS": "",
        "API_ADMIN_KEYS": "",
        "SERVICE_ROLE": "all",  # combined mode: API + dashboard + embedded scanner in one process
        "PUMPPORTAL_ENABLED": "false",
        "SCANNER_HEARTBEAT_INTERVAL_SECONDS": "1",
        "CORS_ALLOWED_ORIGINS": "https://dashboard.example",
        "RUN_MIGRATIONS_ON_START": "true",
        "LOG_FORMAT": "json",
        "LOG_LEVEL": "INFO",  # the test harness exports WARNING; production boots at INFO
        "AI_ENABLED": "false",
        "TELEGRAM_ENABLED": "false",
    }
    proc = subprocess.Popen([str(ROOT / "scripts" / "start.sh")], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 60
        health = None
        while time.time() < deadline:
            try:
                health = httpx.get(f"{base}/health", timeout=2)
                if health.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        assert health is not None and health.status_code == 200, (proc.stdout.read() if proc.stdout else "")
        ready = httpx.get(f"{base}/ready", timeout=5)
        assert ready.status_code == 200, ready.text
        body = ready.json()
        assert body["database"] and body["redis"] and body["schema_revision"]
        # production: OpenAPI schema disabled (the dashboard shell or a 404 is served instead)
        spec = httpx.get(f"{base}/openapi.json", timeout=5)
        assert spec.status_code == 404 or b'"openapi"' not in spec.content[:500]
        assert httpx.get(f"{base}/api/v1/dashboard", timeout=5).status_code == 401
        ok = httpx.get(f"{base}/api/v1/status/system", headers={"X-API-Key": "prod-key"}, timeout=10)
        assert ok.status_code == 200
        warnings = ok.json()["config_warnings"]
        # the only acceptable warnings here come from the test database living on localhost
        assert all(("localhost" in w) for w in warnings), warnings
        assert ok.json()["database"]["ok"] and ok.json()["api"]["environment"] == "production"
        pre = httpx.options(f"{base}/api/v1/dashboard", headers={"Origin": "https://dashboard.example", "Access-Control-Request-Method": "GET"}, timeout=5)
        assert pre.headers.get("access-control-allow-origin") == "https://dashboard.example"
        bad = httpx.options(f"{base}/api/v1/dashboard", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}, timeout=5)
        assert bad.headers.get("access-control-allow-origin") is None
        # embedded scanner comes up inside the same process and heartbeats
        scanner_status = None
        for _ in range(60):
            scanner_status = httpx.get(f"{base}/api/v1/status/system", headers={"X-API-Key": "prod-key"}, timeout=10).json()["scanner"]
            if scanner_status.get("status") == "running":
                break
            time.sleep(0.5)
        assert scanner_status and scanner_status["status"] == "running" and scanner_status["is_leader"], scanner_status
        root = httpx.get(f"{base}/", timeout=5)
        assert root.status_code == 200  # dashboard shell (if built) or service JSON
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            output, _ = proc.communicate(timeout=30)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate(timeout=10)
            code = proc.returncode
    # uvicorn's supervisor re-raises SIGTERM after a graceful shutdown, so the status is 0 or -SIGTERM;
    # the lifespan must have completed (engine disposed, redis closed) before that.
    assert code in (0, -signal.SIGTERM), f"api exited with {code}\n{output[-2000:]}"
    assert "api.stopped" in output and "scanner.stopped" in output, output[-2000:]


def test_start_script_and_dockerfile() -> None:
    start = (ROOT / "scripts" / "start.sh").read_text()
    assert "backend.api.main:app" in start and "--host 0.0.0.0" in start and '"${PORT}"' in start
    assert "exec python -m backend.scanner.main" in start  # persistent process, no schedule flags
    assert "--cron" not in start and "schedule" not in start.lower()
    text = (ROOT / "Dockerfile").read_text()
    assert "USER appuser" in text and "localhost" not in text.replace("127.0.0.1", "")
    assert "npm run build" in text and "COPY --from=web /web/dist ./web/dist" in text
    env_example = (ROOT / ".env.example").read_text()
    assert "sk-" not in env_example and "OPENAI_API_KEY=" in env_example
    subprocess.run([sys.executable, "-c", "import backend.scanner.main, backend.api.main"], check=True, cwd=ROOT)
