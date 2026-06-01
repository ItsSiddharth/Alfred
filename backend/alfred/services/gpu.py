"""
services/gpu.py — Hardware detection without admin/sudo.

Detection priority:
  1. NVIDIA via pynvml (best accuracy, library-based)
  2. NVIDIA via nvidia-smi subprocess (fallback if pynvml unavailable)
  3. Apple Silicon via sysctl + psutil
  4. CPU-only fallback

Returns a HardwareInfo dataclass used by the model recommender.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum

import psutil

logger = logging.getLogger(__name__)


class GPUBackend(str, Enum):
    cuda = "cuda"
    metal = "metal"
    cpu = "cpu"


@dataclass
class HardwareInfo:
    backend: GPUBackend = GPUBackend.cpu
    gpu_name: str = "None (CPU only)"
    total_vram_mb: int = 0          # 0 when no GPU
    free_vram_mb: int = 0
    total_ram_mb: int = 0
    cpu_count: int = 1
    # Derived convenience
    vram_labels: list[str] = field(default_factory=list)  # e.g. ["8 GB VRAM", "CPU only"]

    @property
    def total_vram_gb(self) -> float:
        return round(self.total_vram_mb / 1024, 1)

    @property
    def free_vram_gb(self) -> float:
        return round(self.free_vram_mb / 1024, 1)

    @property
    def total_ram_gb(self) -> float:
        return round(self.total_ram_mb / 1024, 1)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend.value,
            "gpu_name": self.gpu_name,
            "total_vram_mb": self.total_vram_mb,
            "free_vram_mb": self.free_vram_mb,
            "total_vram_gb": self.total_vram_gb,
            "free_vram_gb": self.free_vram_gb,
            "total_ram_mb": self.total_ram_mb,
            "total_ram_gb": self.total_ram_gb,
            "cpu_count": self.cpu_count,
        }


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _detect_nvidia_pynvml() -> HardwareInfo | None:
    """Try pynvml — most accurate NVIDIA detection."""
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        if count == 0:
            return None

        # Use the first GPU (ALFRED targets single-GPU setups).
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name: str = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode()

        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        total_mb = int(mem.total / 1024 / 1024)
        free_mb = int(mem.free / 1024 / 1024)

        pynvml.nvmlShutdown()

        logger.info("pynvml detected: %s  total=%d MB  free=%d MB", name, total_mb, free_mb)
        return HardwareInfo(
            backend=GPUBackend.cuda,
            gpu_name=name,
            total_vram_mb=total_mb,
            free_vram_mb=free_mb,
        )
    except Exception as exc:
        logger.debug("pynvml detection failed: %s", exc)
        return None


def _parse_nvidia_smi_mb(label: str, line: str) -> int | None:
    """Extract MiB value from an nvidia-smi --query line."""
    # nvidia-smi --query-gpu=... --format=csv,noheader,nounits returns plain numbers.
    try:
        return int(line.strip())
    except ValueError:
        return None


def _detect_nvidia_smi() -> HardwareInfo | None:
    """Fallback: parse nvidia-smi subprocess output."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        first_line = result.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in first_line.split(",")]
        if len(parts) < 3:
            return None

        name = parts[0]
        total_mb = int(parts[1])
        free_mb = int(parts[2])

        logger.info(
            "nvidia-smi detected: %s  total=%d MB  free=%d MB", name, total_mb, free_mb
        )
        return HardwareInfo(
            backend=GPUBackend.cuda,
            gpu_name=name,
            total_vram_mb=total_mb,
            free_vram_mb=free_mb,
        )
    except Exception as exc:
        logger.debug("nvidia-smi detection failed: %s", exc)
        return None


def _detect_apple_silicon() -> HardwareInfo | None:
    """Detect Apple Silicon — uses sysctl to confirm arm64 + chip brand."""
    import platform

    if platform.system() != "Darwin":
        return None

    try:
        # Check for Apple Silicon (arm64).
        arch = subprocess.run(
            ["uname", "-m"], capture_output=True, text=True, timeout=3
        ).stdout.strip()
        if arch != "arm64":
            return None

        # Get marketing name e.g. "Apple M2 Pro"
        brand = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        if not brand:
            brand = "Apple Silicon"

        # On Apple Silicon, VRAM is shared with system RAM — report total RAM.
        total_ram_mb = int(psutil.virtual_memory().total / 1024 / 1024)
        # Heuristic: GPU can use up to ~75% of unified memory.
        usable_vram_mb = int(total_ram_mb * 0.75)

        logger.info("Apple Silicon detected: %s  unified RAM=%d MB", brand, total_ram_mb)
        return HardwareInfo(
            backend=GPUBackend.metal,
            gpu_name=brand,
            total_vram_mb=usable_vram_mb,
            free_vram_mb=usable_vram_mb,  # approximate; Metal manages this dynamically
        )
    except Exception as exc:
        logger.debug("Apple Silicon detection failed: %s", exc)
        return None


def _cpu_only_info() -> HardwareInfo:
    total_ram_mb = int(psutil.virtual_memory().total / 1024 / 1024)
    cpu_count = psutil.cpu_count(logical=True) or 1
    logger.info("No GPU detected — CPU-only mode.  RAM=%d MB", total_ram_mb)
    return HardwareInfo(
        backend=GPUBackend.cpu,
        gpu_name="None (CPU only)",
        total_vram_mb=0,
        free_vram_mb=0,
        total_ram_mb=total_ram_mb,
        cpu_count=cpu_count,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_hardware() -> HardwareInfo:
    """
    Detect GPU/VRAM without admin rights.

    Detection order: pynvml → nvidia-smi → Apple Silicon → CPU only.
    Always returns a valid HardwareInfo — never raises.
    """
    info = (
        _detect_nvidia_pynvml()
        or _detect_nvidia_smi()
        or _detect_apple_silicon()
        or _cpu_only_info()
    )

    # Always fill in system RAM and CPU count.
    if info.total_ram_mb == 0:
        info.total_ram_mb = int(psutil.virtual_memory().total / 1024 / 1024)
    if info.cpu_count == 1:
        info.cpu_count = psutil.cpu_count(logical=True) or 1

    return info


# ---------------------------------------------------------------------------
# Model VRAM estimator
# ---------------------------------------------------------------------------


def estimate_vram_mb(params_b: float, quant_bits: int) -> int:
    """
    Estimate VRAM required to load a model.

    Formula: params_b × 1e9 × (quant_bits / 8) bytes, plus ~20% KV-cache overhead.
    Returns megabytes.
    """
    raw_bytes = params_b * 1_000_000_000 * (quant_bits / 8)
    with_overhead = raw_bytes * 1.20  # KV-cache + activations overhead
    return int(with_overhead / 1024 / 1024)


def vram_fit_label(required_mb: int, available_mb: int) -> str:
    """
    Return a fit label given required vs. available VRAM.

    "fits"     — required ≤ 80% of available
    "tight"    — required ≤ 100% of available
    "too_large" — exceeds available
    """
    if available_mb == 0:
        # CPU mode — always "fits" (Ollama will use RAM).
        return "fits"
    ratio = required_mb / available_mb
    if ratio <= 0.80:
        return "fits"
    elif ratio <= 1.00:
        return "tight"
    else:
        return "too_large"


# ---------------------------------------------------------------------------
# Curated model catalog
# ---------------------------------------------------------------------------
# Each entry represents a pull-able Ollama tag.  quant_bits is the default
# quantization for that tag (Q4_K_M ≈ 4 bits, Q8 ≈ 8 bits, F16 ≈ 16 bits).

CATALOG: list[dict] = [
    # ── Qwen 2.5 family ──────────────────────────────────────────────────
    {
        "ollama_tag": "qwen2.5:0.5b",
        "family": "Qwen",
        "display_name": "Qwen 2.5 0.5B",
        "params_b": 0.5,
        "quant_bits": 4,
        "context_k": 32,
        "description": "Tiny Qwen model — fast on any hardware",
        "strengths": ["fast", "low memory"],
    },
    {
        "ollama_tag": "qwen2.5:3b",
        "family": "Qwen",
        "display_name": "Qwen 2.5 3B",
        "params_b": 3.0,
        "quant_bits": 4,
        "context_k": 32,
        "description": "Small but capable, good for CPU-only machines",
        "strengths": ["reasoning", "code"],
    },
    {
        "ollama_tag": "qwen2.5:7b",
        "family": "Qwen",
        "display_name": "Qwen 2.5 7B",
        "params_b": 7.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Best all-round small model for research tasks",
        "strengths": ["reasoning", "long context", "code"],
    },
    {
        "ollama_tag": "qwen2.5:14b",
        "family": "Qwen",
        "display_name": "Qwen 2.5 14B",
        "params_b": 14.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Strong mid-size model for complex analysis",
        "strengths": ["reasoning", "analysis", "long context"],
    },
    {
        "ollama_tag": "qwen2.5:32b",
        "family": "Qwen",
        "display_name": "Qwen 2.5 32B",
        "params_b": 32.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Large model — needs 24+ GB VRAM",
        "strengths": ["reasoning", "analysis", "code"],
    },
    # ── Qwen 2.5 Coder ───────────────────────────────────────────────────
    {
        "ollama_tag": "qwen2.5-coder:7b",
        "family": "Qwen Coder",
        "display_name": "Qwen 2.5 Coder 7B",
        "params_b": 7.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Code-specialised Qwen — excellent for experiment scripts",
        "strengths": ["code generation", "debugging", "Python"],
    },
    {
        "ollama_tag": "qwen2.5-coder:14b",
        "family": "Qwen Coder",
        "display_name": "Qwen 2.5 Coder 14B",
        "params_b": 14.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Stronger code model for complex ML implementations",
        "strengths": ["code generation", "refactoring", "ML"],
    },
    # ── Llama 3.x family ─────────────────────────────────────────────────
    {
        "ollama_tag": "llama3.2:3b",
        "family": "Llama",
        "display_name": "Llama 3.2 3B",
        "params_b": 3.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Meta's compact Llama — fast instruction following",
        "strengths": ["instruction following", "fast"],
    },
    {
        "ollama_tag": "llama3.1:8b",
        "family": "Llama",
        "display_name": "Llama 3.1 8B",
        "params_b": 8.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Well-rounded open model, strong at instruction following",
        "strengths": ["instruction following", "reasoning"],
    },
    {
        "ollama_tag": "llama3.3:70b",
        "family": "Llama",
        "display_name": "Llama 3.3 70B",
        "params_b": 70.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Full Llama — needs 40+ GB VRAM",
        "strengths": ["complex reasoning", "research", "analysis"],
    },
    # ── Mistral family ────────────────────────────────────────────────────
    {
        "ollama_tag": "mistral:7b",
        "family": "Mistral",
        "display_name": "Mistral 7B",
        "params_b": 7.0,
        "quant_bits": 4,
        "context_k": 32,
        "description": "Classic fast 7B, very efficient for inference",
        "strengths": ["speed", "instruction following"],
    },
    {
        "ollama_tag": "mistral-nemo:12b",
        "family": "Mistral",
        "display_name": "Mistral Nemo 12B",
        "params_b": 12.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Mistral's 128k context window model",
        "strengths": ["long context", "reasoning"],
    },
    # ── DeepSeek Coder ────────────────────────────────────────────────────
    {
        "ollama_tag": "deepseek-coder-v2:16b",
        "family": "DeepSeek",
        "display_name": "DeepSeek Coder V2 16B",
        "params_b": 16.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Top open code model — excellent for ML experiment code",
        "strengths": ["code generation", "Python", "ML"],
    },
    {
        "ollama_tag": "deepseek-r1:7b",
        "family": "DeepSeek",
        "display_name": "DeepSeek R1 7B",
        "params_b": 7.0,
        "quant_bits": 4,
        "context_k": 64,
        "description": "Reasoning-focused distilled model with thinking traces",
        "strengths": ["step-by-step reasoning", "math", "research"],
    },
    {
        "ollama_tag": "deepseek-r1:14b",
        "family": "DeepSeek",
        "display_name": "DeepSeek R1 14B",
        "params_b": 14.0,
        "quant_bits": 4,
        "context_k": 64,
        "description": "Larger reasoning model — strong hypothesis analysis",
        "strengths": ["deep reasoning", "research", "analysis"],
    },
    # ── Gemma 3 ───────────────────────────────────────────────────────────
    {
        "ollama_tag": "gemma3:4b",
        "family": "Gemma",
        "display_name": "Gemma 3 4B",
        "params_b": 4.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Google's efficient small model, strong at science tasks",
        "strengths": ["science", "reasoning", "efficient"],
    },
    {
        "ollama_tag": "gemma3:12b",
        "family": "Gemma",
        "display_name": "Gemma 3 12B",
        "params_b": 12.0,
        "quant_bits": 4,
        "context_k": 128,
        "description": "Google's mid-size model with strong research capabilities",
        "strengths": ["science", "reasoning", "analysis"],
    },
]


def get_recommended_models(hw: HardwareInfo) -> list[dict]:
    """
    Return catalog models annotated with VRAM fit, sorted best-fit first.

    fit values: "fits" > "tight" > "too_large"
    CPU-only machines: all models shown as "fits" (Ollama uses RAM).
    """
    annotated = []
    for model in CATALOG:
        required_mb = estimate_vram_mb(model["params_b"], model["quant_bits"])
        fit = vram_fit_label(required_mb, hw.total_vram_mb)
        annotated.append(
            {
                **model,
                "required_vram_mb": required_mb,
                "required_vram_gb": round(required_mb / 1024, 1),
                "fit": fit,
            }
        )

    fit_order = {"fits": 0, "tight": 1, "too_large": 2}
    annotated.sort(key=lambda m: (fit_order[m["fit"]], m["params_b"]))
    return annotated