"""Stage: music. ACE-Step 1.5 score candidates, generated on a Kaggle T4.

Like a composer's sketchbook: N candidates from fixed seeds, you listen under the
narration, then set music.chosen and music.choice_reason in storyboard.json.

Cache key: caption + bpm + key + duration + seeds + model. Unchanged music
settings never touch the GPU again.

    python -m pipeline.music
"""
from __future__ import annotations

import json
import shutil

from .common import OUTPUTS, Cache, input_hash, load_storyboard, log_call
from .kaggle_job import run_job

STAGE = "music"
OUT = OUTPUTS / "music"


def run(resume: bool = False) -> None:
    sb = load_storyboard()
    m = sb["music"]
    seeds = (m.get("seeds") or [])[: m["candidates"]]
    spec = {"caption": m["caption"], "bpm": m["bpm"], "key": m["key"], "duration_s": m["duration_s"],
            "seeds": seeds, "dit_model": "acestep-v15-turbo", "steps": 8}
    h = input_hash({"model": m["model"], **spec})
    cache = Cache(STAGE)
    cdir = cache.dir / h
    report_path = cdir / "report.json"

    if report_path.exists() and all((cdir / f"candidate_{i}.wav").exists() for i in range(len(seeds))):
        print(f"music: cached ({h})")
        for i, seed in enumerate(seeds):
            cache.publish(cdir / f"candidate_{i}.wav", OUT / f"candidate_{i}.wav")
            log_call(STAGE, "kaggle", m["model"], {"hash": h}, seed=seed, cached=True, latency_ms=0,
                     output=f"outputs/music/candidate_{i}.wav")
        return

    print(f"music: pushing ACE-Step job to Kaggle ({len(seeds)} candidates)")
    res = run_job("acestep_job.py", "heritage-acestep", spec, "heritage acestep", cdir, timeout_s=2 * 3600, resume=resume)
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    if res["status"] != "complete" or report.get("error") or not report.get("candidates"):
        err = report.get("error") or res["log"][-800:] or res["status"]
        log_call(STAGE, "kaggle", m["model"], spec, status="error", error=err, gpu_s=round(res["wall_s"]),
                 latency_ms=res["wall_s"] * 1000)
        raise SystemExit(f"music job failed: {err}")

    OUT.mkdir(parents=True, exist_ok=True)
    for c in report["candidates"]:
        cache.publish(cdir / c["file"], OUT / c["file"])
        log_call(STAGE, "kaggle", m["model"], {**spec, "hash": h, "gpu": report.get("gpu")}, seed=c["seed"],
                 latency_ms=c["gen_s"] * 1000, gpu_s=c["gen_s"], output=f"outputs/music/{c['file']}")
    log_call(STAGE, "kaggle", "job-overhead", {"timings": report.get("timings"), "gpu": report.get("gpu")},
             gpu_s=round(res["wall_s"]), latency_ms=res["wall_s"] * 1000)
    print(f"music: {len(report['candidates'])} candidates in {res['wall_s']/60:.1f} min wall; "
          f"timings {report.get('timings')}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true", help="re-attach to the running Kaggle job instead of pushing")
    run(ap.parse_args().resume)
