"""Run a GPU job on Kaggle from the command line and bring the results home.

A job is a self-contained Python script (pipeline/kaggle/*.py) plus a JSON spec.
The spec is embedded into the script at push time, so the Kaggle kernel needs no
dataset upload and is fully reproducible from what is logged in runs.jsonl.

    push -> poll status -> download /kaggle/working outputs -> log GPU seconds
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .common import ROOT, require_env

KAGGLE_DIR = ROOT / "pipeline" / "kaggle"
SPEC_MARKER = "SPEC = {}  # replaced at push time"


def kaggle_cli() -> list[str]:
    exe = Path(sys.executable).with_name("kaggle.exe" if os.name == "nt" else "kaggle")
    return [str(exe)] if exe.exists() else [sys.executable, "-m", "kaggle"]


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([*kaggle_cli(), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"kaggle {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r


def username() -> str:
    r = _run(["config", "view"])
    for line in r.stdout.splitlines():
        if "username" in line:
            return line.split(":", 1)[1].strip()
    raise RuntimeError("could not read Kaggle username from `kaggle config view`")


def push(script: str, slug: str, spec: dict, title: str) -> str:
    require_env("KAGGLE_API_TOKEN")
    user = username()
    src = (KAGGLE_DIR / script).read_text(encoding="utf-8")
    if SPEC_MARKER not in src:
        raise RuntimeError(f"{script} has no SPEC marker")
    src = src.replace(SPEC_MARKER, "SPEC = " + json.dumps(spec, ensure_ascii=False))
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        (dpath / "job.py").write_text(src, encoding="utf-8")
        meta = {
            "id": f"{user}/{slug}",
            "title": title,
            "code_file": "job.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": True,
            "enable_internet": True,
            "machine_shape": "NvidiaTeslaT4",
            "dataset_sources": [],
            "competition_sources": [],
            "kernel_sources": [],
        }
        (dpath / "kernel-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        r = _run(["kernels", "push", "-p", str(dpath)])
        msg = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()
        print("  " + msg)
        # The CLI exits 0 even when Kaggle refuses the push (e.g. "Maximum batch GPU
        # session count of 2 reached"); without this check we'd wait on a job that doesn't exist (F15).
        if "error" in msg.lower():
            raise RuntimeError(f"Kaggle refused the push: {msg}")
    return f"{user}/{slug}"


def status(ref: str) -> str:
    try:
        r = _run(["kernels", "status", ref], check=False)
    except Exception as e:  # network blip on our side must not kill a 1-hour GPU job (FAILURES F13)
        return f"unknown ({type(e).__name__})"
    # Only trust the explicit status field. Matching the word "error" anywhere in the CLI
    # output turned a transient API error into a fake job failure (FAILURES F14).
    m = re.search(r"KernelWorkerStatus\.(\w+)", r.stdout)
    if not m:
        return f"unknown ({(r.stdout + r.stderr).strip()[-60:]})"
    return {"COMPLETE": "complete", "ERROR": "error", "CANCEL_REQUESTED": "cancel", "CANCEL_ACKNOWLEDGED": "cancel",
            "RUNNING": "running", "QUEUED": "queued"}.get(m.group(1), m.group(1).lower())


def wait(ref: str, timeout_s: int = 3 * 3600, poll_s: int = 30) -> tuple[str, float]:
    t0 = time.time()
    last = None
    while True:
        s = status(ref)
        if s != last:
            print(f"  [{time.time() - t0:5.0f}s] {ref}: {s}")
            last = s
        if s in ("complete", "error", "cancel"):
            return s, time.time() - t0
        if time.time() - t0 > timeout_s:
            return "timeout", time.time() - t0
        time.sleep(poll_s)


def download(ref: str, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    _run(["kernels", "output", ref, "-p", str(dest)])
    return dest


def run_job(script: str, slug: str, spec: dict, title: str, dest: Path, timeout_s: int = 3 * 3600,
            resume: bool = False) -> dict:
    """Push (or re-attach with resume=True), wait, download. Returns {'status', 'wall_s', 'dest', 'log'}."""
    if resume:
        require_env("KAGGLE_API_TOKEN")
        ref = f"{username()}/{slug}"
        print(f"  re-attaching to {ref}")
    else:
        ref = push(script, slug, spec, title)
        time.sleep(10)
    st, wall = wait(ref, timeout_s=timeout_s)
    for attempt in range(5):
        try:
            download(ref, dest)
            break
        except Exception as e:
            print(f"  download failed ({e}); retry {attempt + 1}/5")
            time.sleep(20)
    log = next(iter(dest.glob("*.log")), None)
    return {"status": st, "wall_s": wall, "dest": dest, "ref": ref,
            "log": log.read_text(encoding="utf-8", errors="replace")[-3000:] if log else ""}
