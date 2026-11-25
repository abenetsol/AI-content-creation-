# ARCHITECTURE.md

## The shape: one contract, five stages

```
                         schema/storyboard.schema.json
                                     │ validates (every stage, every run)
                                     ▼
                              storyboard.json  ◄── the only file a human edits
        ┌───────────────┬───────────┴──────┬──────────────────┬──────────────┐
        ▼               ▼                  ▼                  ▼              │
   narration        keyframes            shots              music           │
   Gemini TTS       FLUX.2 klein 4B      Wan 2.2 TI2V-5B    ACE-Step 1.5     │
   (Google API)     (Cloudflare API)     (Kaggle T4)        (Kaggle T4)      │
   outputs/         outputs/refs/        outputs/clips/     outputs/music/   │
   narration/*.wav  outputs/keyframes/   *_9x16.mp4         candidate_*.wav  │
        │               │                  │                  │              │
        └───────────────┴─────────┬────────┴──────────────────┘              │
                                  ▼                                          │
                              assemble  (FFmpeg, local, deterministic) ◄─────┘
                    timeline → picture → sound → subtitles → end card
                    outputs/film/film_9x16.mp4, film_16x9.mp4, radio_cut_16x9.mp4

   every model call ──► runs.jsonl   (stage, shot, model, params, seed, latency, GPU s, cached, status)
```

Each stage is one module in `pipeline/`, reads only `storyboard.json` plus the outputs of the stages before it, and writes only under its own `outputs/` folder. `pipeline/film.py` runs them in order; each can also be run alone (`python -m pipeline.<stage>`).

## The contract: `storyboard.json`

| Section | What it holds | Read by |
|---|---|---|
| `meta` | title, logline, language (`am`), subtitle language, audience, intent | docs, assemble |
| `style` | the style block appended to every prompt, two palettes (present = cool, memory = warm), the golden style reference prompt and seed, keyframe model and size | keyframes |
| `characters` | one description per character (re-used *verbatim* in every prompt), reference sheet prompt and seed, `real_person` and `consent_ref` | keyframes (consent gate) |
| `narration` | TTS model, fallback models, voice, `lead_s`/`tail_s` timing | narration, assemble |
| `shots[]` | id, beat, **reason**, shot size, time (palette), characters, image prompt, motion prompt, render mode (`wan` / `kenburns` / `still`), Ken Burns direction, **seed**, minimum duration, transition, narration (Amharic text + English subtitle) | all stages |
| `music` | caption, bpm, key, duration, candidate seeds, chosen candidate and reason, ducking parameters, spotting cues | music, assemble |
| `end_card` | lines and duration | assemble |

Validation is two layers: JSON Schema (types, enums, required fields, 8–12 shots, `additionalProperties: false` so typos fail loudly) and code checks in `common.load_storyboard()` (unique shot ids, characters exist, music cues point at real shots). An invalid storyboard stops the run before any model is called.

Prompts and seeds live in the storyboard, not in code, so any frame can be explained and reproduced from the file alone.

## Caching: only what changed is regenerated

`common.Cache` is content-addressed. For each item a stage hashes **everything that affects its output** and stores the artifact at `outputs/.cache/<stage>/<hash>.<ext>`, then copies it to the readable path.

| Stage | Hash inputs |
|---|---|
| narration | model, voice, style instruction, text |
| keyframes | model, full prompt (shot + characters + palette + style), seed, size, **hashes of the reference image files** |
| shots | model settings, motion prompt, seed, **hash of the keyframe file** |
| music | model, caption, bpm, key, duration, seeds |
| assemble segments | source file hash, segment length, size, Ken Burns params |

Because the keyframe hash includes the reference files' hashes and the clip hash includes the keyframe's hash, a change ripples **downstream only as far as it really reaches**: editing one shot's image prompt regenerates that keyframe, then (if it is a Wan shot) that clip, then re-assembles. Nothing else calls a model. Measured: editing s02 and s07 → `2 generated, 9 cached` (EXPERIMENTS E8).

The cache key always includes the model id, so a fallback model's output is never confused with the primary's.

## Failure handling

| Failure | Handling | Where |
|---|---|---|
| Transient HTTP errors (429/5xx, timeouts) | `common.retry()` exponential back-off | all API stages |
| **Daily quota exhausted** (Gemini free tier, 10/day/model) | detect `PerDay` in the 429, mark the model exhausted, fall through `fallback_models`; if all are out, stop with a clear message and keep cached lines | narration |
| Safety filter false positive | logged with status `error`, stage continues; fix by changing the seed in the storyboard | keyframes |
| Missing consent | shot **skipped** and logged `skipped_no_consent`; never generated | keyframes (`check_consent` reads `ETHICS.md`) |
| Kaggle: no GPU / no internet / OOM / timeout | preflight in the job logs GPU + internet in the first seconds; each clip/candidate saved as soon as it is done, so partial results survive; job `report.json` carries the traceback | `pipeline/kaggle/*.py` |
| Our network drops while waiting on Kaggle | status poll errors are treated as "unknown" and polling continues; `--resume` re-attaches without losing the queue slot; downloads retry | `kaggle_job.py` |
| Wan clip missing for a shot | assembly falls back to Ken Burns on the keyframe | `assemble.segment()` |
| No chosen score | assembly uses a synthesized placeholder drone | `assemble.music_source()` |
| Edit integrity | assembly verifies the picture track length equals the timeline, else fails | `assemble.picture()` |
| Secrets | only in `.env` (gitignored); `.env.example` lists names | `common.load_env()` |

## Assembly in detail (FFmpeg, deterministic)

1. **Timeline:** shot length = `max(min_duration_s, lead_s + narration + tail_s)`. The voice sets the cut. Written to `outputs/assemble/timeline.json`.
2. **Picture:** per shot, a Wan clip (first 0.25 s trimmed, slowed up to 1.5x to fill, then held) or a Ken Burns move (cover-crop the square keyframe to the target aspect, 2x upscale to avoid zoompan jitter, slow zoom/pan from the storyboard). Joined with `xfade` (dissolve, fade-through-black, or a one-frame "cut").
3. **Sound:** narration lines placed at their timeline positions; score faded in/out, gain −6 dB, **ducked** with `sidechaincompress` (threshold 0.02, ratio 6, attack 300 ms, release 1500 ms) keyed by the narration; **two-pass `loudnorm`** to −14 LUFS, then `alimiter` at −1.5 dBFS.
4. **Subtitles:** SRT generated from the storyboard, ≤2 lines; burned in with libass. Vertical uses a higher `MarginV` to stay inside the safe area above platform UI.
5. **End card:** proverb in Ge'ez script (Ebrima / Noto Sans Ethiopic), English translation, dedication, "Made with AI" with every model listed.
6. **Export:** H.264 + AAC, 24 fps constant, `+faststart`; 1080×1920 and 1920×1080.

## Why this shape

- **One JSON contract** makes the walkthrough demo trivial: change one field, re-run, and the logs show one new model call.
- **Square keyframe masters** (subject centred) crop cleanly to both 9:16 and 16:9 from the same image.
- **GPU work isolated in Kaggle jobs** with the spec embedded in the pushed script: the exact job is reproducible from the repo, and the rest of the pipeline runs on a laptop.
- **FFmpeg for everything after generation**: same inputs, same output, so the edit is code, not a project file.
