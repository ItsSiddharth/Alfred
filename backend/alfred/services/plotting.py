"""
services/plotting.py — Standard preamble + ASCII rendering (Stage 7).

STANDARD_PREAMBLE is injected verbatim at the top of every generated
experiment script.  It defines the ALFRED protocol helpers that the runner
parses:

  log_metric(name, value, step)   → ALFRED_METRIC: name=value step=N
  plt.savefig(path, ...)          → emits ALFRED_PLOT: <abs_path> after save
  print("ALFRED_PHASE: train")    → used in generated code (no helper needed)
  logging                         → already configured to stdout
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Standard preamble — injected before every generated experiment script
# ---------------------------------------------------------------------------

STANDARD_PREAMBLE = '''\
import sys
import os
import logging

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ── ALFRED protocol helpers ─────────────────────────────────────────────────

def log_metric(name: str, value: float, step: int = 0) -> None:
    """Emit a named scalar metric; parsed by the RunnerAgent."""
    print(f"ALFRED_METRIC: {name}={float(value):.6f} step={step}", flush=True)

# Monkey-patch plt.savefig: every save emits an ALFRED_PLOT line so the
# runner can pick up the file path and emit a plot WS event.
_alfred_savefig_orig = plt.savefig

def _alfred_savefig(fname, *args, **kwargs):  # noqa: ANN001
    _alfred_savefig_orig(fname, *args, **kwargs)
    print(f"ALFRED_PLOT: {os.path.abspath(str(fname))}", flush=True)

plt.savefig = _alfred_savefig

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)

# ── Phase markers (emit at the START of each phase in generated scripts) ────
# print("ALFRED_PHASE: preprocess", flush=True)
# print("ALFRED_PHASE: train", flush=True)
# print("ALFRED_PHASE: eval", flush=True)

'''


def get_preamble() -> str:
    """Return the standard experiment preamble string."""
    return STANDARD_PREAMBLE


# ---------------------------------------------------------------------------
# ASCII rendering
# ---------------------------------------------------------------------------

def png_to_ascii(png_path: Path, width: int = 72, height: int = 18) -> str:
    """
    Convert a PNG to a fixed-size ASCII art string for display in the
    thinking tab.  Falls back to a placeholder if Pillow is not installed
    or rendering fails.
    """
    try:
        from PIL import Image  # type: ignore  # noqa: PLC0415
    except ImportError:
        return f"[ASCII plot: {png_path.name} — install Pillow for rendering]"

    try:
        img = Image.open(png_path).convert("L")
        img = img.resize((width, height), Image.LANCZOS)
        # Character ramp — light pixels → light chars, dark pixels → heavy chars
        ramp = " .,:;i1tfLCG08@#"
        pixels = list(img.getdata())
        lines = [
            "".join(ramp[int(px / 256 * len(ramp))] for px in pixels[r * width : (r + 1) * width])
            for r in range(height)
        ]
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("png_to_ascii failed for %s: %s", png_path, exc)
        return f"[ASCII plot: {png_path.name} — render failed]"


# ---------------------------------------------------------------------------
# Plot WS event
# ---------------------------------------------------------------------------

async def emit_plot_event(
    ws: object,
    project_id_str: str,
    png_path: Path,
    experiment_id: int,
) -> str:
    """
    Read PNG, base64-encode, render ASCII art, emit a "plot" WS event.
    Returns the ASCII art string (fed to the interpreter).
    """
    try:
        raw = png_path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
    except OSError as exc:
        logger.warning("emit_plot_event: cannot read %s: %s", png_path, exc)
        return f"[plot missing: {png_path.name}]"

    ascii_art = png_to_ascii(png_path)

    await ws.send(  # type: ignore[attr-defined]
        project_id_str,
        "plot",
        {
            "filename": png_path.name,
            "base64_png": b64,
            "ascii_art": ascii_art,
            "experiment_id": experiment_id,
        },
    )
    logger.debug("Plot event emitted: %s", png_path.name)
    return ascii_art
