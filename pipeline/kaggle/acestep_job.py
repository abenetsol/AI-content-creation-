# Kaggle GPU job: ACE-Step 1.5 score candidates.
# Pushed by pipeline/music.py; SPEC is injected at push time.
import json
import os
import subprocess
import sys
import time
import traceback

SPEC = {}  # replaced at push time

OUT = "/kaggle/working"
REPO = "/kaggle/tmp/ACE-Step-1.5"
report = {"spec": SPEC, "timings": {}, "candidates": [], "error": None}


def sh(cmd):
    print("$", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)


def save():
    with open(f"{OUT}/report.json", "w") as f:
        json.dump(report, f, indent=2)


try:
    t = time.time()
    os.makedirs("/kaggle/tmp", exist_ok=True)
    sh("nvidia-smi --query-gpu=name,memory.total --format=csv || true")
    sh("python -c \"import socket; socket.create_connection(('github.com', 443), 5); print('internet: ok')\"")
    sh(f"git clone --depth 1 https://github.com/ACE-Step/ACE-Step-1.5.git {REPO}")
    # nano-vllm is a bundled local package (not on PyPI) used only by the LM planner, which we
    # don't load (DiT-only). Install it without its deps (flash-attn wheels) so pip can resolve
    # ace-step; torch is pinned to +cu128 builds, hence the PyTorch index (FAILURES F17).
    sh(f"cd {REPO} && {sys.executable} -m pip install -q --no-deps --ignore-requires-python "
       f"./acestep/third_parts/nano-vllm")
    sh(f"cd {REPO} && {sys.executable} -m pip install -q --ignore-requires-python "
       f"--extra-index-url https://download.pytorch.org/whl/cu128 -e .")
    report["timings"]["install_s"] = round(time.time() - t, 1)
    save()
    sys.path.insert(0, REPO)

    import torch
    report["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"

    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler
    from acestep.inference import GenerationParams, GenerationConfig, generate_music

    t = time.time()
    dit = AceStepHandler()
    try:
        dit.initialize_service(project_root=REPO, config_path=SPEC.get("dit_model", "acestep-v15-turbo"),
                               device="cuda", offload_to_cpu=True)
    except TypeError:
        dit.initialize_service(project_root=REPO, config_path=SPEC.get("dit_model", "acestep-v15-turbo"),
                               device="cuda")
    llm = LLMHandler()  # not initialised: DiT-only mode (thinking=False), fits a 16 GB T4
    report["timings"]["load_s"] = round(time.time() - t, 1)
    save()

    params = GenerationParams(
        task_type="text2music",
        caption=SPEC["caption"],
        lyrics="[Instrumental]",
        instrumental=True,
        bpm=SPEC["bpm"],
        keyscale=SPEC["key"],
        duration=SPEC["duration_s"],
        inference_steps=SPEC.get("steps", 8),
        thinking=False,
    )
    seeds = SPEC["seeds"]
    t = time.time()
    for i, seed in enumerate(seeds):
        # one at a time keeps memory low and lets partial results survive a timeout
        cfg = GenerationConfig(batch_size=1, seeds=[seed], use_random_seed=False, audio_format="wav")
        t1 = time.time()
        res = generate_music(dit, llm, params, cfg, save_dir=f"{OUT}/raw_{i}")
        if not res.success:
            raise RuntimeError(f"seed {seed}: {res.error}")
        path = res.audios[0]["path"]
        dest = f"{OUT}/candidate_{i}.wav"
        sh(f"ffmpeg -loglevel error -y -i '{path}' -ar 48000 -ac 2 '{dest}'")
        report["candidates"].append({"index": i, "seed": seed, "file": f"candidate_{i}.wav",
                                     "gen_s": round(time.time() - t1, 1)})
        save()
    report["timings"]["generate_s"] = round(time.time() - t, 1)
except Exception as e:
    report["error"] = f"{type(e).__name__}: {e}"
    report["traceback"] = traceback.format_exc()[-4000:]
    print(report["traceback"], flush=True)
finally:
    save()
    sh(f"rm -rf {OUT}/raw_* || true")
