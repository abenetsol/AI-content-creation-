"""Stage: keyframes. FLUX.2 klein 4B on Cloudflare Workers AI.

Order matters (look development before production):
  1. refs       the golden style reference + one reference sheet per character
  2. keyframes  one square master per shot, conditioned on the style reference
                and the sheets of the characters in that shot

Consistency comes from what we give the model every time: the same style block,
the same character description text, the same reference images and a fixed seed.

Consent gate: a shot whose characters include a real person is only generated
when ETHICS.md contains `consent[<character>]: granted`.

    python -m pipeline.keyframes --refs                 # reference sheets only
    python -m pipeline.keyframes                        # every shot
    python -m pipeline.keyframes --only s03 --variants 4   # explore seeds for one shot
"""
from __future__ import annotations

import argparse
import base64
import re
import time
from pathlib import Path

import requests

from .common import (OUTPUTS, ROOT, Cache, RetryableError, file_hash, input_hash, load_storyboard, log_call,
                     require_env, retry, select_shots, shot_prompt)

STAGE = "keyframes"
OUT = OUTPUTS / "keyframes"
REFS = OUTPUTS / "refs"
API = "https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"


# ------------------------------------------------------------------- consent
def consent_status() -> dict[str, str]:
    text = (ROOT / "ETHICS.md").read_text(encoding="utf-8")
    return {m.group(1): m.group(2).lower() for m in re.finditer(r"^consent\[(\w+)\]:\s*(\w+)", text, re.M)}


def check_consent(sb: dict, chars: list[str]) -> list[str]:
    """Return the real-person characters in `chars` that lack granted consent."""
    status = consent_status()
    return [c for c in chars if sb["characters"][c].get("real_person") and status.get(c) != "granted"]


# ------------------------------------------------------------------- model call
def flux(prompt: str, *, seed: int, size: int, refs: list[Path], model: str, env: dict) -> tuple[bytes, float]:
    url = API.format(acct=env["CLOUDFLARE_ACCOUNT_ID"], model=model)
    fields = {"prompt": (None, prompt), "seed": (None, str(seed)),
              "width": (None, str(size)), "height": (None, str(size))}
    for i, r in enumerate(refs[:4]):
        fields[f"input_image_{i}"] = (r.name, r.read_bytes(), "image/png")

    def call():
        t0 = time.perf_counter()
        try:
            resp = requests.post(url, files=fields, timeout=180,
                                 headers={"Authorization": f"Bearer {env['CLOUDFLARE_API_TOKEN']}"})
        except requests.RequestException as e:
            raise RetryableError(str(e))
        ms = (time.perf_counter() - t0) * 1000
        if resp.status_code in (429, 500, 502, 503, 504):
            raise RetryableError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        img = (data.get("result") or {}).get("image")
        if not img:
            raise RuntimeError(f"no image returned: {str(data)[:300]}")
        return base64.b64decode(img), ms

    return retry(call, attempts=3, base_delay=5, what=f"FLUX seed {seed}")


def to_png(jpeg: bytes, dest: Path) -> None:
    """Workers AI returns JPEG; store PNG so downstream stages get a lossless master."""
    import subprocess
    from .common import ffmpeg_bin
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".jpg")
    tmp.write_bytes(jpeg)
    subprocess.run([ffmpeg_bin(), "-loglevel", "error", "-y", "-i", str(tmp), str(dest)], check=True)
    tmp.unlink()


def generate(name: str, prompt: str, seed: int, size: int, refs: list[Path], sb: dict, env: dict,
             cache: Cache, dest: Path, shot: str | None) -> bool:
    model = sb["style"]["keyframe_model"]
    h = input_hash({"model": model, "prompt": prompt, "seed": seed, "size": size,
                    "refs": [file_hash(r) for r in refs]})
    hit = cache.get(h, "png")
    params = {"seed": seed, "size": size, "refs": [str(r.relative_to(ROOT)) for r in refs], "hash": h,
              "prompt": prompt[:300]}
    if hit:
        cache.publish(hit, dest)
        log_call(STAGE, "cloudflare", model, params, seed=seed, shot=shot, latency_ms=0, cached=True,
                 output=str(dest.relative_to(ROOT)))
        print(f"  {name}: cached")
        return False
    try:
        jpeg, ms = flux(prompt, seed=seed, size=size, refs=refs, model=model, env=env)
    except Exception as e:
        log_call(STAGE, "cloudflare", model, params, seed=seed, shot=shot, status="error", error=str(e))
        print(f"  ! {name}: {e}")
        return False
    to_png(jpeg, cache.path(h, "png"))
    cache.publish(cache.path(h, "png"), dest)
    log_call(STAGE, "cloudflare", model, params, seed=seed, shot=shot, latency_ms=ms,
             output=str(dest.relative_to(ROOT)))
    print(f"  {name}: new ({ms/1000:.1f}s)")
    return True


# ------------------------------------------------------------------- stages
def run_refs(sb: dict, env: dict, only: list[str] | None = None) -> None:
    cache = Cache(STAGE)
    st, size = sb["style"], sb["style"]["keyframe_size"]
    if not only or "style" in only:
        generate("style", f"{st['reference_prompt']}. {st['memory_palette']}. {st['block']}", st["reference_seed"],
                 size, [], sb, env, cache, ROOT / st["reference_image"], None)
    for name, c in sb["characters"].items():
        if "sheet_prompt" not in c or (only and name not in only):
            continue
        if check_consent(sb, [name]):
            print(f"  {name}: SKIPPED, consent not granted in ETHICS.md")
            log_call(STAGE, "cloudflare", st["keyframe_model"], {"sheet": name}, status="skipped_no_consent")
            continue
        prompt = f"{c['sheet_prompt']}. {c['description']}. {st['block']}"
        # No style reference here: conditioning on the style image overrode the
        # character's identity in the text (skin tone, hair). See EXPERIMENTS.md E5.
        generate(name, prompt, c["sheet_seed"], size, [], sb, env, cache, ROOT / c["reference_image"], None)


def shot_refs(sb: dict, shot: dict) -> list[Path]:
    refs: list[Path] = []
    if shot.get("use_style_reference", True):
        refs.append(ROOT / sb["style"]["reference_image"])
    for c in shot["characters"]:
        ref = sb["characters"][c].get("reference_image")
        if ref:
            refs.append(ROOT / ref)
    seen, out = set(), []
    for r in refs:
        if r.exists() and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def run(only: list[str] | None = None, variants: int = 1) -> None:
    env = require_env("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
    sb = load_storyboard()
    cache = Cache(STAGE)
    size = sb["style"]["keyframe_size"]
    new = skipped = 0
    for shot in select_shots(sb, only):
        missing = check_consent(sb, shot["characters"])
        if missing:
            print(f"  {shot['id']}: SKIPPED, no consent recorded for {missing}")
            log_call(STAGE, "cloudflare", sb["style"]["keyframe_model"], {"missing_consent": missing},
                     shot=shot["id"], status="skipped_no_consent")
            skipped += 1
            continue
        prompt = shot_prompt(sb, shot)
        refs = shot_refs(sb, shot)
        if refs:
            prompt = ("Use the reference images only for the painting style and for how the characters look; "
                      "make a new composition. " + prompt)
        if variants > 1:
            for k in range(variants):
                seed = shot["seed"] + k
                new += generate(f"{shot['id']} seed {seed}", prompt, seed, size, refs, sb, env, cache,
                                OUT / "variants" / f"{shot['id']}_seed{seed}.png", shot["id"])
        else:
            new += generate(shot["id"], prompt, shot["seed"], size, refs, sb, env, cache,
                            OUT / f"{shot['id']}.png", shot["id"])
    print(f"keyframes: {new} generated, {skipped} skipped for consent")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refs", action="store_true", help="generate the style reference and character sheets")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--variants", type=int, default=1, help="generate N seeds per shot into keyframes/variants/")
    a = ap.parse_args()
    if a.refs:
        e = require_env("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
        run_refs(load_storyboard(), e, a.only)
    else:
        run(a.only, a.variants)
