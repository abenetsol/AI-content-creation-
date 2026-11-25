"""Shared helpers for every pipeline stage.

- load_env():        reads .env (never committed) into os.environ
- load_storyboard(): reads storyboard.json and validates it against schema/
- log_call():        appends one JSON line per model call to runs.jsonl
- Cache:             content-addressed cache, so a re-run only regenerates
                     the shots whose inputs changed
- retry():           exponential backoff for flaky / rate-limited providers
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORYBOARD = ROOT / "storyboard.json"
SCHEMA = ROOT / "schema" / "storyboard.schema.json"
OUTPUTS = ROOT / "outputs"
RUNS = ROOT / "runs.jsonl"
CACHE_DIR = OUTPUTS / ".cache"

# Make UTF-8 (Amharic) printable on Windows consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------- env
def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def require_env(*names: str) -> dict[str, str]:
    load_env()
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit(f"Missing environment variables: {', '.join(missing)}. Copy .env.example to .env and fill them in.")
    return {n: os.environ[n] for n in names}


# ---------------------------------------------------------------------- storyboard
def load_storyboard(path: Path = STORYBOARD) -> dict:
    import jsonschema

    sb = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(sb, schema)
    except jsonschema.ValidationError as e:
        loc = "/".join(str(p) for p in e.absolute_path)
        sys.exit(f"storyboard.json is invalid at '{loc}': {e.message}")

    ids = [s["id"] for s in sb["shots"]]
    if len(ids) != len(set(ids)):
        sys.exit("storyboard.json: shot ids must be unique")
    for s in sb["shots"]:
        for c in s["characters"]:
            if c not in sb["characters"]:
                sys.exit(f"storyboard.json: shot {s['id']} uses unknown character '{c}'")
    cue_shots = {c["at_shot"] for c in sb["music"]["cues"]}
    if not cue_shots <= set(ids):
        sys.exit(f"storyboard.json: music cue refers to unknown shot(s) {cue_shots - set(ids)}")
    return sb


def select_shots(sb: dict, only: list[str] | None) -> list[dict]:
    if not only:
        return sb["shots"]
    return [s for s in sb["shots"] if s["id"] in only]


# --------------------------------------------------------------------------- log
def log_call(stage: str, provider: str, model: str, params: dict, *, seed=None,
             latency_ms: float | None = None, status: str = "ok", cost_usd: float = 0.0,
             shot: str | None = None, output: str | None = None, cached: bool = False,
             gpu_s: float | None = None, error: str | None = None, **extra) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage": stage,
        "shot": shot,
        "provider": provider,
        "model": model,
        "params": params,
        "seed": seed,
        "latency_ms": round(latency_ms) if latency_ms is not None else None,
        "gpu_s": gpu_s,
        "status": status,
        "cached": cached,
        "cost_usd": cost_usd,
        "output": output,
    }
    if error:
        rec["error"] = error[:500]
    rec.update(extra)
    with RUNS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------------- cache
def input_hash(payload: dict) -> str:
    """Stable hash of everything that affects a stage's output for one item."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class Cache:
    """Per-stage content-addressed cache.

    Each artifact is stored under outputs/.cache/<stage>/<hash>.<ext> and then
    copied to its human-readable location (e.g. outputs/keyframes/s03.png).
    If the inputs for a shot have not changed, the hash is the same, and the
    stage reuses the cached artifact without calling the model.
    """

    def __init__(self, stage: str):
        self.stage = stage
        self.dir = CACHE_DIR / stage
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, h: str, ext: str) -> Path:
        return self.dir / f"{h}.{ext}"

    def get(self, h: str, ext: str) -> Path | None:
        p = self.path(h, ext)
        return p if p.exists() and p.stat().st_size > 0 else None

    def publish(self, cached: Path, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, dest)
        return dest


# ------------------------------------------------------------------------- retry
class RetryableError(Exception):
    pass


def retry(fn, *, attempts: int = 3, base_delay: float = 4.0, what: str = "call"):
    """Run fn(); on RetryableError back off and retry. Other errors propagate."""
    last = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except RetryableError as e:
            last = e
            if i == attempts:
                break
            delay = base_delay * (2 ** (i - 1))
            print(f"  ! {what} failed ({e}); retry {i}/{attempts - 1} in {delay:.0f}s")
            time.sleep(delay)
    raise last


def ffmpeg_bin(name: str = "ffmpeg") -> str:
    found = shutil.which(name)
    if found:
        return found
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
    if winget.exists():
        return str(winget)
    sys.exit(f"{name} not found. Install FFmpeg (e.g. `winget install Gyan.FFmpeg`).")


def shot_prompt(sb: dict, shot: dict) -> str:
    """Full keyframe prompt: shot prompt + character descriptions + palette + style block.

    Re-using the exact same character and style text in every prompt is what
    keeps the look consistent across shots.
    """
    st = sb["style"]
    palette = {"present": st["present_palette"], "memory": st["memory_palette"]}.get(shot["time"], "natural soft daylight")
    chars = "; ".join(sb["characters"][c]["description"] for c in shot["characters"])
    parts = [shot["image_prompt"], chars, palette, st["block"]]
    if st.get("negative"):
        parts.append("avoid: " + st["negative"])
    return ". ".join(p for p in parts if p)
