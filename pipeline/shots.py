"""Stage: moving shots. Wan 2.2 TI2V-5B image-to-video on a Kaggle T4.

Only shots with render == "wan" are animated; the rest get a Ken Burns move in
assembly (the brief's documented fallback). Clips are generated vertically
(9:16, the main deliverable); the 16:9 export uses Ken Burns on the keyframe.

Cache key per shot: model + motion prompt + seed + keyframe hash + size/frames/steps.
Only shots whose key changed are sent to Kaggle, so editing one motion prompt
re-renders exactly one clip.

    python -m pipeline.shots               # every wan shot not yet cached
    python -m pipeline.shots --only s05
    python -m pipeline.shots --dry-run     # show what would be rendered
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess

from .common import (OUTPUTS, ROOT, Cache, ffmpeg_bin, file_hash, input_hash, load_storyboard, log_call,
                     select_shots)
from .kaggle_job import run_job

STAGE = "shots"
OUT = OUTPUTS / "clips"
SETTINGS = {
    "model_id": "Wan-AI/Wan2.2-TI2V-5B-Diffusers",
    "width": 480, "height": 832,         # 480p draft, 9:16
    "num_frames": 81, "fps": 24,         # ~3.4 s; assembly slows/holds to fit the shot
    "steps": 30, "guidance": 5.0,
    "negative": "blurry, distorted face, deformed hands, extra fingers, flicker, text, watermark, fast motion",
}
STYLE_SUFFIX = "painterly illustration, gentle subtle natural motion, stable composition"


def crop_jpeg(png, W, H) -> bytes:
    """Centre-crop the square keyframe to the clip aspect and encode as JPEG."""
    r = subprocess.run([ffmpeg_bin(), "-loglevel", "error", "-i", str(png), "-vf",
                        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
                        "-q:v", "3", "-f", "image2", "-vcodec", "mjpeg", "pipe:1"], capture_output=True, check=True)
    return r.stdout


def run(only: list[str] | None = None, dry_run: bool = False, resume: bool = False) -> None:
    sb = load_storyboard()
    cache = Cache(STAGE)
    todo, hashes = [], {}
    for shot in select_shots(sb, only):
        if shot["render"] != "wan":
            continue
        key = OUTPUTS / "keyframes" / f"{shot['id']}.png"
        if not key.exists():
            print(f"  {shot['id']}: no keyframe yet, skipped (run keyframes first)")
            continue
        prompt = f"{shot['motion_prompt']}. {STYLE_SUFFIX}"
        h = input_hash({**SETTINGS, "prompt": prompt, "seed": shot["seed"], "key": file_hash(key)})
        hashes[shot["id"]] = h
        dest = OUT / f"{shot['id']}_9x16.mp4"
        hit = cache.get(h, "mp4")
        if hit:
            cache.publish(hit, dest)
            log_call(STAGE, "kaggle", "wan2.2-ti2v-5b", {"hash": h}, seed=shot["seed"], shot=shot["id"],
                     cached=True, latency_ms=0, output=str(dest.relative_to(ROOT)))
            print(f"  {shot['id']}: cached")
            continue
        todo.append({"id": shot["id"], "seed": shot["seed"], "prompt": prompt,
                     "image_b64": base64.b64encode(crop_jpeg(key, SETTINGS["width"], SETTINGS["height"])).decode()})

    if not todo:
        print("shots: nothing to render")
        return
    print(f"shots: {len(todo)} to render on Kaggle: {[t['id'] for t in todo]}")
    if dry_run:
        return

    spec = {**SETTINGS, "shots": todo}
    jobdir = cache.dir / ("job_" + input_hash({"ids": [t["id"] for t in todo], "h": [hashes[t["id"]] for t in todo]}))
    res = run_job("wan_job.py", "heritage-wan", spec, "heritage wan", jobdir, timeout_s=4 * 3600, resume=resume)
    rep = {}
    for name in ("report.json", "report_gpu0.json"):
        p = jobdir / name
        if p.exists():
            rep[name] = json.loads(p.read_text())
    clips = rep.get("report_gpu0.json", {}).get("clips", [])
    err = rep.get("report.json", {}).get("error") or rep.get("report_gpu0.json", {}).get("error")

    OUT.mkdir(parents=True, exist_ok=True)
    for c in clips:
        src = jobdir / c["file"]
        if not src.exists():
            continue
        h = hashes[c["id"]]
        cached = cache.path(h, "mp4")
        cached.write_bytes(src.read_bytes())
        cache.publish(cached, OUT / f"{c['id']}_9x16.mp4")
        log_call(STAGE, "kaggle", "wan2.2-ti2v-5b",
                 {k: SETTINGS[k] for k in ("width", "height", "num_frames", "steps", "guidance")} | {"hash": h},
                 seed=c["seed"], shot=c["id"], latency_ms=c["gen_s"] * 1000, gpu_s=c["gen_s"],
                 output=f"outputs/clips/{c['id']}_9x16.mp4", device=rep.get("report_gpu0.json", {}).get("device"))
    log_call(STAGE, "kaggle", "job-overhead", {"timings": rep.get("report.json", {}).get("timings"),
                                                "load_s": rep.get("report_gpu0.json", {}).get("load_s")},
             # queue wait is not GPU time: a job that never ran logs 0, a finished one its own load time
             gpu_s=0 if res["status"] in ("timeout", "queued") else round(rep.get("report_gpu0.json", {}).get("load_s") or 0),
             latency_ms=res["wall_s"] * 1000, queue_and_run_s=round(res["wall_s"]),
             status="ok" if not err else "partial" if clips else "error", error=err)
    done = {c["id"] for c in clips}
    missing = [t["id"] for t in todo if t["id"] not in done]
    print(f"shots: {len(done)} rendered in {res['wall_s']/60:.1f} min wall"
          + (f"; missing {missing} -> Ken Burns fallback. error: {err}" if missing else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true", help="re-attach to the running Kaggle job instead of pushing")
    a = ap.parse_args()
    run(a.only, a.dry_run, a.resume)
