"""One command for the whole film: validate -> narration -> keyframes -> shots -> music -> assemble.

    python -m pipeline.film                 # full run, both aspect ratios
    python -m pipeline.film --skip-gpu      # no Kaggle: Ken Burns for every shot, placeholder or cached score
    python -m pipeline.film --radio         # narration-only radio cut

Every stage is cached by its inputs, so a re-run after editing one line of
storyboard.json only calls the model for what that line changed.
"""
from __future__ import annotations

import argparse
import time

from . import assemble, keyframes, music, narration, shots
from .common import load_storyboard, log_call


def stage(name, fn, *a, optional=False, **kw):
    t0 = time.perf_counter()
    print(f"\n=== {name} ===")
    try:
        fn(*a, **kw)
    except SystemExit as e:
        if not optional:
            raise
        print(f"  ! {name} failed ({e}); continuing with fallback")
        log_call("film", "local", name, {}, status="fallback", error=str(e))
    print(f"  ({time.perf_counter() - t0:.1f}s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-gpu", action="store_true", help="skip Kaggle stages (Wan, ACE-Step); use fallbacks")
    ap.add_argument("--radio", action="store_true")
    ap.add_argument("--draft", action="store_true")
    a = ap.parse_args()

    print("=== validate storyboard ===")
    sb = load_storyboard()
    print(f"  ok: {len(sb['shots'])} shots, language={sb['meta']['language']}")
    stage("narration", narration.run)
    if a.radio:
        stage("assemble (radio cut)", assemble.run, radio=True)
        return
    stage("reference sheets", lambda: keyframes.run_refs(sb, keyframes.require_env(
        "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")))
    stage("keyframes", keyframes.run)
    if not a.skip_gpu:
        # GPU stages are optional: if Kaggle fails, assembly falls back to Ken Burns / placeholder music.
        stage("moving shots (Wan 2.2 on Kaggle)", shots.run, optional=True)
        stage("music (ACE-Step on Kaggle)", music.run, optional=True)
    stage("assemble", assemble.run, draft=a.draft)


if __name__ == "__main__":
    main()
