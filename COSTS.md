# COSTS.md: what the film cost, measured

**Actual spend: $0.** Everything ran on free tiers. Every number below comes from `runs.jsonl` (one line per model call) via `python -m pipeline.costs`. The list-price column shows what the same calls would cost on paid tiers, using the approximate prices in `pipeline/costs.py` (Gemini TTS ~$0.002 per short line, Workers AI FLUX.2 klein ~$0.0035 per 1024² image, on-demand T4 ~$0.35/h). Check current pricing before relying on them.

## Measured, whole project (including experiments and failures)

| Stage | Model | Calls (new) | Cached | Errors | Median latency | Total model time | GPU min | List-price equiv. |
|---|---|---|---|---|---|---|---|---|
| assemble | `ffmpeg` | 7 | 0 | 0 | 105.3 s | 20.5 min | 0.0 | $0.000 |
| keyframes | `@cf/black-forest-labs/flux-2-klein-4b` | 25 | 12 | 2 | 20.5 s | 8.7 min | 0.0 | $0.088 |
| music | `ACE-Step-1.5` | 3 | 0 | 5 | 17.6 s | 0.9 min | 4.7 | $0.028 |
| music | `job-overhead` | 1 | 0 | 0 | – | – | 5.9 | $0.034 |
| narration | `gemini-3.8-flash-lite-tts` | 6 | 5 | 0 | 5.1 s | 1.0 min | 0.0 | $0.012 |
| narration | `gemini-3.8-flash-tts` | 12 | 21 | 5 | 8.7 s | 2.2 min | 0.0 | $0.024 |
| shots | `job-overhead` | 1 | 0 | 2 | – | – | 19.4 | $0.113 |
| smoke | `@cf/black-forest-labs/flux-1-schnell` | 1 | 0 | 1 | 5.5 s | 0.1 min | 0.0 | $0.004 |
| smoke | `@cf/black-forest-labs/flux-2-klein-4b` | 1 | 0 | 0 | 12.1 s | 0.2 min | 0.0 | $0.004 |
| smoke | `kernels.list` | 1 | 0 | 0 | – | – | 0.0 | $0.000 |
| smoke | `models.list` | 1 | 0 | 0 | – | – | 0.0 | $0.000 |
| smoke | `whoami-v2` | 1 | 0 | 0 | – | – | 0.0 | $0.000 |

**GPU used: 30.0 min. Logged queue wait: 38.7 h** (jobs waiting for a free T4).

**Total list-price equivalent: $0.31** (actual spend: $0, all free tiers). 113 logged calls.

*Calls (new)* are successful uncached calls; *Cached* are cache hits that cost nothing. *GPU min* counts only time a GPU was actually allocated: setup (install, model download, load) plus generation, taken from each job's own `report.json`. The queue wait is summed per job; the music and video jobs waited **in parallel**, so the wall-clock wait was about **19.5 h** (2026-11-25 10:52 → 2026-11-26 06:18 UTC).

## What one film costs to regenerate from scratch (list price)

| Stage | Work | Calls | List price |
|---|---|---|---|
| Narration | 9 lines | 9 TTS | $0.018 |
| Keyframes | 5 reference sheets + 11 shots | 16 images | $0.056 |
| Music | 3 candidates, ACE-Step 1.5 turbo, 8 steps | ~7 T4-min (6 setup + 1 generate) | $0.041 |
| Moving shots | 5 × Wan 2.2 TI2V-5B clips | not completed (see below) | (~$0.10–0.15 est.) |
| Assembly | 2 aspect ratios, FFmpeg on a laptop | local CPU, ~10 min | $0 |
| **Total** | | | **≈ $0.12** without Wan, ≈ $0.25 with it |

**Regenerating one shot** (the walkthrough demo): 1 FLUX call (~20 s, $0.0035) + re-assembly. Everything else is a cache hit.

## Where the time went

| | Time |
|---|---|
| Model calls (TTS + FLUX), total | ~12 min |
| GPU actually used (all attempts) | 30 min |
| Final assembly, both aspect ratios | 10.4 min (laptop CPU) |
| **Waiting in the Kaggle T4 queue** | **~19.5 h wall-clock** |

The expensive resource was never money. It was **queue time and daily quotas**.

## Free-tier limits that shaped the pipeline

| Service | Limit I hit | What it changed |
|---|---|---|
| Gemini TTS (free) | **10 requests / day / model** | `DailyQuotaExhausted` + a fallback model chain; cache every line; `--upgrade` re-voices fallback lines the next day (FAILURES F3) |
| Cloudflare Workers AI | daily free neuron allowance; occasional safety false positive | cached by content hash, so re-runs cost nothing; change the seed on a false positive (F5) |
| Kaggle GPU | 30 GPU-h / week, **max 2 concurrent batch sessions**, phone verification for GPU + internet; T4 queue of ~10 h on the day I needed it | single-GPU jobs, `--resume`, preflight checks, queue time logged separately from GPU time (F8, F15, F16) |
| Google Colab (free) | T4 when available, interactive only | the same ACE-Step job exported as a notebook (`--colab`) and imported back (`--import-zip`). This is how the final score was made: ~7 GPU-min, 20 min wall |

## What went to failures

GPU minutes on failed attempts: about 23 of the 30 (installs that failed, an OOM, a timeout). Each failure used only a few GPU minutes but cost hours of queue. That's why F17 recommends a local `pip install --dry-run` smoke test before pushing any GPU job.
