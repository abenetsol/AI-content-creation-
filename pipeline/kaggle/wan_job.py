# Kaggle GPU job: Wan 2.2 TI2V-5B image-to-video, one clip per shot.
# Pushed by pipeline/shots.py; SPEC (incl. base64 keyframes) is injected at push time.
import base64
import json
import os
import subprocess
import sys
import time
import traceback

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

SPEC = {}  # replaced at push time

OUT = "/kaggle/working"
IMG = "/kaggle/tmp/keyframes"


def sh(cmd):
    print("$", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)


def worker(gpu: int, ids: list):
    import torch
    from diffusers import AutoencoderKLWan, WanImageToVideoPipeline
    from diffusers.utils import export_to_video, load_image

    rep = {"gpu": gpu, "device": torch.cuda.get_device_name(0), "clips": [], "error": None}
    path = f"{OUT}/report_gpu{gpu}.json"

    def save():
        with open(path, "w") as f:
            json.dump(rep, f, indent=2)

    try:
        t = time.time()
        mid = SPEC["model_id"]
        # T4 has no native bf16: transformer in fp16, VAE in fp32 (fp16 VAE gives NaNs / black frames)
        vae = AutoencoderKLWan.from_pretrained(mid, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanImageToVideoPipeline.from_pretrained(mid, vae=vae, torch_dtype=torch.float16)
        # The VAE encodes the start image *before* the transformer runs, so model offload never
        # moves the ~11 GB text encoder off the GPU and the VAE OOMs (FAILURES F17).
        # Encode every prompt up front, then drop the text encoder before offloading the rest.
        import gc
        pipe.text_encoder.to("cuda")
        embeds = {}
        with torch.no_grad():
            for s in SPEC["shots"]:
                pe, ne = pipe.encode_prompt(prompt=s["prompt"], negative_prompt=SPEC["negative"],
                                            do_classifier_free_guidance=True, num_videos_per_prompt=1, max_sequence_length=512,
                                            device=torch.device("cuda"))
                embeds[s["id"]] = (pe.cpu(), ne.cpu())
        pipe.text_encoder.to("cpu")
        pipe.text_encoder = None
        gc.collect()
        torch.cuda.empty_cache()
        if hasattr(pipe.vae, "enable_tiling"):
            pipe.vae.enable_tiling()
        pipe.enable_model_cpu_offload()
        rep["load_s"] = round(time.time() - t, 1)
        save()
        shots = {s["id"]: s for s in SPEC["shots"]}
        for sid in ids:
            s = shots[sid]
            t1 = time.time()
            image = load_image(f"{IMG}/{sid}.jpg")
            g = torch.Generator("cuda").manual_seed(s["seed"])
            pe, ne = embeds[sid]
            frames = pipe(image=image, prompt_embeds=pe.to("cuda"), negative_prompt_embeds=ne.to("cuda"),
                          height=SPEC["height"], width=SPEC["width"], num_frames=SPEC["num_frames"],
                          num_inference_steps=SPEC["steps"], guidance_scale=SPEC["guidance"],
                          generator=g).frames[0]
            export_to_video(frames, f"{OUT}/{sid}.mp4", fps=SPEC["fps"])
            rep["clips"].append({"id": sid, "seed": s["seed"], "file": f"{sid}.mp4",
                                 "gen_s": round(time.time() - t1, 1)})
            save()
            torch.cuda.empty_cache()
    except Exception as e:
        rep["error"] = f"{type(e).__name__}: {e}"
        rep["traceback"] = traceback.format_exc()[-4000:]
        print(rep["traceback"], flush=True)
    save()


def main():
    report = {"timings": {}, "error": None}
    t = time.time()
    try:
        sh("nvidia-smi --query-gpu=name,memory.total --format=csv || true")
        sh("python -c \"import socket; socket.create_connection(('huggingface.co', 443), 5); print('internet: ok')\"")
        sh(f"{sys.executable} -m pip install -q -U diffusers transformers accelerate ftfy imageio imageio-ffmpeg")
        report["timings"]["install_s"] = round(time.time() - t, 1)
        os.makedirs(IMG, exist_ok=True)
        for s in SPEC["shots"]:
            with open(f"{IMG}/{s['id']}.jpg", "wb") as f:
                f.write(base64.b64decode(s["image_b64"]))
        # pre-download weights once so the two workers don't race
        t = time.time()
        sh(f"{sys.executable} -c \"from huggingface_hub import snapshot_download; snapshot_download('{SPEC['model_id']}')\"")
        report["timings"]["download_s"] = round(time.time() - t, 1)

        # Single GPU, sequential. Two parallel workers would each hold ~22 GB of
        # offloaded weights in host RAM; Kaggle has ~29 GB (see FAILURES.md).
        t = time.time()
        worker(0, [s["id"] for s in SPEC["shots"]])
        report["timings"]["generate_s"] = round(time.time() - t, 1)
    except Exception as e:
        report["error"] = f"{type(e).__name__}: {e}"
        report["traceback"] = traceback.format_exc()[-4000:]
    with open(f"{OUT}/report.json", "w") as f:
        json.dump(report, f, indent=2)


main()
