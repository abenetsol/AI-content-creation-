"""Summarise runs.jsonl into the measured tables in COSTS.md.

    python -m pipeline.costs            # prints a markdown table
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict

from .common import RUNS

# List-price equivalents (USD) for what we ran on free tiers. Sources and dates in COSTS.md.
LIST_PRICE = {
    "gemini_tts_per_call": 0.002,       # ~5 s of audio per line
    "flux_klein_per_image": 0.0035,     # Workers AI paid tier, 1024x1024, 4 steps
    "t4_per_gpu_hour": 0.35,            # on-demand T4 (cloud list price)
}


def load() -> list[dict]:
    if not RUNS.exists():
        return []
    return [json.loads(l) for l in RUNS.read_text(encoding="utf-8").splitlines() if l.strip()]


def summarise(rows: list[dict]) -> str:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["stage"], r["model"])].append(r)
    out = ["| Stage | Model | Calls (new) | Cached | Errors | Median latency | Total model time | GPU min | List-price equiv. |",
           "|---|---|---|---|---|---|---|---|---|"]
    total_usd = 0.0
    for (stage, model), rs in sorted(groups.items()):
        new = [r for r in rs if r.get("status") == "ok" and not r.get("cached")]
        cached = sum(1 for r in rs if r.get("cached"))
        errs = sum(1 for r in rs if r.get("status") not in ("ok",))
        lats = [r["latency_ms"] for r in new if r.get("latency_ms")]
        med = f"{statistics.median(lats)/1000:.1f} s" if lats else "–"
        if model == "job-overhead":  # wall time here is mostly queue; report it separately
            med = tot = "–"
        else:
            tot = f"{sum(lats)/1000/60:.1f} min" if lats else "–"
        gpu = sum(r.get("gpu_s") or 0 for r in rs if r.get("provider") in ("kaggle", "colab")) / 60
        usd = 0.0
        if "tts" in model:
            usd = len(new) * LIST_PRICE["gemini_tts_per_call"]
        elif "flux" in model:
            usd = len(new) * LIST_PRICE["flux_klein_per_image"]
        if gpu:
            usd = gpu / 60 * LIST_PRICE["t4_per_gpu_hour"]
        total_usd += usd
        out.append(f"| {stage} | `{model}` | {len(new)} | {cached} | {errs} | {med} | {tot} | "
                   f"{gpu:.1f} | ${usd:.3f} |")
    queue_h = sum(r.get("queue_wait_s") or 0 for r in rows) / 3600
    gpu_m = sum(r.get("gpu_s") or 0 for r in rows if r.get("provider") in ("kaggle", "colab")) / 60
    out.append(f"\n**GPU used: {gpu_m:.1f} min. Logged queue wait: {queue_h:.1f} h** (jobs waiting for a free T4).")
    out.append(f"\n**Total list-price equivalent: ${total_usd:.2f}** (actual spend: $0, all free tiers). "
               f"{len(rows)} logged calls.")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(summarise(load()))
