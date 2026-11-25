# The Egg Walks: እንቁላል ቀስ በቀስ በእግሩ ይሄዳል

A 72-second heritage short about my late father and the proverb he always repeated, *"little by little, the egg walks on its own legs"*, made entirely by a code pipeline. Amharic narration, English subtitles, original score, exported vertical (9:16) and horizontal (16:9).

One file, [`storyboard.json`](storyboard.json), is the contract between five stages: **narration → keyframes → moving shots → music → assembly**. Change one line in it, re-run, and only what that line affects is regenerated.

## Run it

**Requirements:** Python 3.11+, FFmpeg on PATH (`winget install Gyan.FFmpeg` / `apt install ffmpeg` / `brew install ffmpeg`), and free accounts for Google AI Studio, Cloudflare and Kaggle (phone-verified).

```bash
git clone <this repo> && cd <repo>
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Linux/macOS: .venv/bin/pip
cp .env.example .env        # then fill in the five keys (see below)
make film                   # or: .venv/Scripts/python -m pipeline.film
```

Outputs: `outputs/film/film_9x16.mp4` and `outputs/film/film_16x9.mp4`.

| Command | What it does |
|---|---|
| `make film` | Whole pipeline, both aspect ratios. |
| `make film-nogpu` | Same without Kaggle: every shot uses a Ken Burns move, and music falls back to the cached/placeholder score. Runs on a laptop in a few minutes. |
| `make radio` | Narration over placeholder music on black (the first user-test prototype). |
| `make validate` | Validate `storyboard.json` against `schema/`. |
| `make costs` | Summarise `runs.jsonl` into latency / GPU minutes / list-price table. |
| `python -m pipeline.keyframes --only s03 --variants 4` | Explore 4 seeds for one shot. |
| `python -m pipeline.shots --dry-run` | Show which clips would be sent to Kaggle. |

### Keys (`.env`, never committed)

| Variable | Where to get it |
|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | dash.cloudflare.com → AI → Workers AI → *Use REST API* |
| `KAGGLE_API_TOKEN` | kaggle.com/settings → API → *Create New Token* (account must be phone-verified for GPU + internet) |
| `HF_API_KEY` | huggingface.co/settings/tokens (read) |

## What it costs

$0 actual, on free tiers. Measured per stage in [COSTS.md](COSTS.md) (from `runs.jsonl`). The binding constraints were not money but **quotas and queues**: 10 Gemini TTS calls/day/model, the Cloudflare daily neuron allowance, and Kaggle T4 queue time.

## Repository map

| Path | What |
|---|---|
| `storyboard.json` | the contract: shots, reasons, prompts, seeds, narration, music cues |
| `schema/storyboard.schema.json` | JSON Schema, validated before every stage |
| `pipeline/` | one module per stage + `common.py` (validation, logging, cache, retry) + `film.py` (one command) |
| `pipeline/kaggle/` | the GPU jobs pushed to Kaggle (ACE-Step, Wan 2.2) |
| `runs.jsonl` | one line per model call: stage, shot, model, params, seed, latency, GPU s, cached, status |
| `outputs/` | every generated image, audio file and clip, kept for inspection |
| [DESIGN.md](DESIGN.md) | audience, intent, options considered, user tests and what changed |
| [ARCHITECTURE.md](ARCHITECTURE.md) | stages, contract, caching, failure handling |
| [EXPERIMENTS.md](EXPERIMENTS.md) | hypothesis → change → result |
| [FAILURES.md](FAILURES.md) | what broke, what I tried, what fixed it |
| [COSTS.md](COSTS.md) | measured latency, GPU minutes, list-price equivalent |
| [ETHICS.md](ETHICS.md) | consent record, licences, labelling, privacy |

## Regenerate one shot (the walkthrough demo)

```bash
# 1. edit shots[s06].image_prompt in storyboard.json
make keyframes      # -> "s06: new", every other shot "cached"
make assemble       # re-cuts the film with the new frame
tail -n 15 runs.jsonl   # exactly one new FLUX call, cached=false
```
