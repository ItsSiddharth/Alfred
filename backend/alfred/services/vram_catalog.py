"""
VRAM fit estimator and curated model catalog.

VRAM formula: bytes ≈ (params × bits/8) + kv_cache_overhead_mb
where kv_cache_overhead is estimated as ~10% of model size.

Fit tiers:
  "fits"   — model uses ≤ 70% of free VRAM
  "tight"  — model uses 70–95% of free VRAM
  "no_fit" — model exceeds free VRAM
  "cpu"    — no GPU detected; any model "fits" but runs on CPU

The catalog is maintained here and extended as new research models emerge.
Each entry has enough metadata for the Find Models panel to show useful info.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FitTier = Literal["fits", "tight", "no_fit", "cpu"]


@dataclass
class ModelSpec:
    """A single model variant in the catalog."""
    name: str                # Ollama pull name, e.g. "qwen2.5:7b-instruct-q4_K_M"
    display_name: str        # Human label, e.g. "Qwen 2.5 7B Q4_K_M"
    family: str              # e.g. "Qwen", "Llama", "Mistral", "DeepSeek-Coder"
    param_billions: float    # 0.5, 1.5, 3, 7, 13, 34, 70 …
    bits: int                # quantization bits (4, 5, 6, 8, 16)
    vram_estimate_mb: int    # calculated once, stored for display
    context_k: int           # default context window in K tokens
    strengths: list[str]     # short capability tags
    fit: FitTier = "cpu"     # populated by estimate_fits()


# ── VRAM formula ───────────────────────────────────────────────────────────────

def estimate_vram_mb(param_billions: float, bits: int, context_k: int = 4) -> int:
    """
    Rough VRAM estimate in MB.
    Model weights: params_B × 1e9 × (bits / 8) / (1024^2) MB
    KV-cache overhead: ~10% of weight size (conservative for research use)
    Context scaling: +2% per K context beyond 4K (very rough)
    """
    weight_mb = (param_billions * 1e9 * bits / 8) / (1024 ** 2)
    kv_overhead = weight_mb * 0.10
    ctx_overhead = weight_mb * 0.02 * max(0, context_k - 4)
    return int(weight_mb + kv_overhead + ctx_overhead)


# ── Catalog ────────────────────────────────────────────────────────────────────

def _make(
    name: str,
    display: str,
    family: str,
    params: float,
    bits: int,
    ctx_k: int,
    strengths: list[str],
) -> ModelSpec:
    return ModelSpec(
        name=name,
        display_name=display,
        family=family,
        param_billions=params,
        bits=bits,
        vram_estimate_mb=estimate_vram_mb(params, bits, ctx_k),
        context_k=ctx_k,
        strengths=strengths,
    )


# Research-friendly model catalog — ordered from smallest to largest
MODEL_CATALOG: list[ModelSpec] = [
    # ── Qwen 2.5 ──────────────────────────────────────────────────────────────
    _make("qwen2.5:1.5b", "Qwen 2.5 1.5B", "Qwen", 1.5, 4, 32,
          ["fast", "reasoning", "code"]),
    _make("qwen2.5:3b", "Qwen 2.5 3B", "Qwen", 3.0, 4, 32,
          ["fast", "reasoning", "code"]),
    _make("qwen2.5:7b", "Qwen 2.5 7B", "Qwen", 7.0, 4, 32,
          ["reasoning", "code", "research"]),
    _make("qwen2.5:14b", "Qwen 2.5 14B", "Qwen", 14.0, 4, 128,
          ["reasoning", "code", "research", "long-context"]),
    _make("qwen2.5:32b", "Qwen 2.5 32B", "Qwen", 32.0, 4, 128,
          ["reasoning", "code", "research", "long-context"]),

    # ── Qwen 2.5 Coder ────────────────────────────────────────────────────────
    _make("qwen2.5-coder:7b", "Qwen 2.5 Coder 7B", "Qwen", 7.0, 4, 32,
          ["code", "debugging", "research-code"]),
    _make("qwen2.5-coder:14b", "Qwen 2.5 Coder 14B", "Qwen", 14.0, 4, 32,
          ["code", "debugging", "research-code"]),

    # ── Llama 3.x ─────────────────────────────────────────────────────────────
    _make("llama3.2:1b", "Llama 3.2 1B", "Llama", 1.0, 4, 128,
          ["fast", "general"]),
    _make("llama3.2:3b", "Llama 3.2 3B", "Llama", 3.0, 4, 128,
          ["fast", "general"]),
    _make("llama3.1:8b", "Llama 3.1 8B", "Llama", 8.0, 4, 128,
          ["reasoning", "research", "general"]),
    _make("llama3.1:70b", "Llama 3.1 70B", "Llama", 70.0, 4, 128,
          ["reasoning", "research", "general", "large"]),
    _make("llama3.3:70b", "Llama 3.3 70B", "Llama", 70.0, 4, 128,
          ["reasoning", "research", "general", "large"]),

    # ── Mistral ───────────────────────────────────────────────────────────────
    _make("mistral:7b", "Mistral 7B", "Mistral", 7.0, 4, 32,
          ["reasoning", "general"]),
    _make("mistral-nemo:12b", "Mistral Nemo 12B", "Mistral", 12.0, 4, 128,
          ["reasoning", "research", "long-context"]),

    # ── DeepSeek Coder / R1 ───────────────────────────────────────────────────
    _make("deepseek-coder-v2:16b", "DeepSeek Coder V2 16B", "DeepSeek", 16.0, 4, 64,
          ["code", "debugging", "research-code", "MoE"]),
    _make("deepseek-r1:7b", "DeepSeek R1 7B", "DeepSeek", 7.0, 4, 64,
          ["reasoning", "research", "math"]),
    _make("deepseek-r1:14b", "DeepSeek R1 14B", "DeepSeek", 14.0, 4, 64,
          ["reasoning", "research", "math"]),
    _make("deepseek-r1:32b", "DeepSeek R1 32B", "DeepSeek", 32.0, 4, 64,
          ["reasoning", "research", "math", "large"]),

    # ── Phi-3 / Phi-4 (lightweight research) ──────────────────────────────────
    _make("phi3.5:3.8b", "Phi 3.5 3.8B", "Phi", 3.8, 4, 128,
          ["fast", "reasoning", "math"]),
    _make("phi4:14b", "Phi 4 14B", "Phi", 14.0, 4, 16,
          ["reasoning", "math", "research"]),

    # ── Gemma 3 ───────────────────────────────────────────────────────────────
    _make("gemma3:4b", "Gemma 3 4B", "Gemma", 4.0, 4, 128,
          ["fast", "general", "multimodal"]),
    _make("gemma3:12b", "Gemma 3 12B", "Gemma", 12.0, 4, 128,
          ["reasoning", "general", "multimodal"]),
    _make("gemma3:27b", "Gemma 3 27B", "Gemma", 27.0, 4, 128,
          ["reasoning", "research", "multimodal"]),
]


# ── Fit classification ─────────────────────────────────────────────────────────

def classify_fit(vram_estimate_mb: int, free_vram_mb: int, backend: str) -> FitTier:
    """
    Classify how well a model fits the detected hardware.
    backend == "cpu" → always return "cpu" tier (runs, but slowly).
    """
    if backend == "cpu":
        return "cpu"
    if free_vram_mb == 0:
        return "cpu"
    ratio = vram_estimate_mb / free_vram_mb
    if ratio <= 0.70:
        return "fits"
    elif ratio <= 0.95:
        return "tight"
    else:
        return "no_fit"


def estimate_fits(free_vram_mb: int, backend: str) -> list[ModelSpec]:
    """
    Return catalog with `fit` field populated for the given hardware.
    Sorted: fits → tight → no_fit → cpu, then by param count ascending.
    """
    FIT_ORDER = {"fits": 0, "tight": 1, "cpu": 2, "no_fit": 3}

    result: list[ModelSpec] = []
    for spec in MODEL_CATALOG:
        import dataclasses
        updated = dataclasses.replace(
            spec,
            fit=classify_fit(spec.vram_estimate_mb, free_vram_mb, backend),
        )
        result.append(updated)

    result.sort(key=lambda s: (FIT_ORDER[s.fit], s.param_billions))
    return result


def spec_to_dict(spec: ModelSpec) -> dict:
    """Serialise a ModelSpec for the REST API."""
    return {
        "name": spec.name,
        "display_name": spec.display_name,
        "family": spec.family,
        "param_billions": spec.param_billions,
        "bits": spec.bits,
        "vram_estimate_mb": spec.vram_estimate_mb,
        "context_k": spec.context_k,
        "strengths": spec.strengths,
        "fit": spec.fit,
    }