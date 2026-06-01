#!/usr/bin/env python3
"""
ALFRED development runner.

Starts both servers concurrently in one terminal:
  - FastAPI/uvicorn on http://localhost:8000
  - Vite dev server  on http://localhost:5173

Usage:
    python scripts/dev.py

Press Ctrl-C once to stop both.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
BOLD = "\033[1m"


def banner() -> None:
    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════╗")
    print(f"║       ALFRED — dev runner            ║")
    print(f"╚══════════════════════════════════════╝{RESET}\n")
    print(f"  {GREEN}Backend{RESET}  → http://localhost:8000")
    print(f"  {GREEN}Frontend{RESET} → http://localhost:5173\n")
    print(f"  Press {BOLD}Ctrl-C{RESET} to stop both servers.\n")


def check_prereqs() -> None:
    """Warn loudly if required tools are missing."""
    issues: list[str] = []

    # Python deps: just try importing fastapi as a proxy.
    try:
        import fastapi  # noqa: F401
    except ImportError:
        issues.append(
            "FastAPI not found. Run:  pip install -e '.[dev]'  from the repo root."
        )

    # pnpm
    if subprocess.run(["pnpm", "--version"], capture_output=True).returncode != 0:
        issues.append("pnpm not found. Install from https://pnpm.io/installation")

    if issues:
        print(f"{RED}Prerequisite check failed:{RESET}")
        for issue in issues:
            print(f"  ✗ {issue}")
        sys.exit(1)


def main() -> None:
    banner()
    check_prereqs()

    processes: list[subprocess.Popen] = []

    try:
        # --- Backend --------------------------------------------------------
        backend_env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR)}
        backend_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "alfred.main:app",
                "--reload",
                "--port",
                "8000",
                "--host",
                "0.0.0.0",
            ],
            cwd=BACKEND_DIR,
            env=backend_env,
        )
        processes.append(backend_proc)
        print(f"{GREEN}✓ Backend starting (pid {backend_proc.pid})…{RESET}")

        # Brief pause so uvicorn starts before Vite proxies hit it.
        time.sleep(1.5)

        # --- Frontend -------------------------------------------------------
        # Check if node_modules exist; if not, run pnpm install first.
        if not (FRONTEND_DIR / "node_modules").exists():
            print("  node_modules not found — running pnpm install…")
            subprocess.run(["pnpm", "install"], cwd=FRONTEND_DIR, check=True)

        frontend_proc = subprocess.Popen(
            ["pnpm", "dev"],
            cwd=FRONTEND_DIR,
        )
        processes.append(frontend_proc)
        print(f"{GREEN}✓ Frontend starting (pid {frontend_proc.pid})…{RESET}\n")

        # Wait until one of them exits (e.g. crash) or Ctrl-C is pressed.
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)

        # If one crashed, report it.
        for proc in processes:
            if proc.poll() is not None and proc.returncode != 0:
                print(f"\n{RED}Process {proc.pid} exited with code {proc.returncode}.{RESET}")

    except KeyboardInterrupt:
        print(f"\n{CYAN}Shutting down…{RESET}")

    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print(f"{GREEN}All servers stopped.{RESET}\n")


if __name__ == "__main__":
    main()