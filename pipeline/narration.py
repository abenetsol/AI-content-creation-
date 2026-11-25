"""Stage: narration. One Gemini TTS call per shot that has a narration line.

Output: outputs/narration/<shot>.wav (24 kHz mono) and outputs/narration/durations.json
Cache key: model + voice + style instruction + text. Change one line in
storyboard.json and only that line is re-synthesised.

    python -m pipeline.narration                 # all shots
    python -m pipeline.narration --only s02 s07  # selected shots
    python -m pipeline.narration --audition Charon Sulafat Kore   # voice tests on the first line
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import time
import wave
from pathlib import Path

import requests

from .common import (OUTPUTS, Cache, RetryableError, ffmpeg_bin, input_hash, load_storyboard, log_call,
                     require_env, retry, select_shots)

STAGE = "narration"
OUT = OUTPUTS / "narration"
SAMPLE_RATE = 24000
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MIN_GAP_S = 6.0


class DailyQuotaExhausted(Exception):
    """Free tier: 10 requests/day/model. Retrying is pointless; switch model."""


def tts(text: str, *, model: str, voice: str, style: str, key: str) -> tuple[bytes, float]:
    body = {
        # Gemini TTS treats a short "Say ...:" prefix as direction. A longer
        # instruction separated by blank lines was read aloud (see FAILURES.md F2).
        "contents": [{"parts": [{"text": f"{style.strip()} {text}" if style else text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }

    def call():
        t0 = time.perf_counter()
        try:
            r = requests.post(API.format(model=model), json=body, timeout=120,
                              headers={"x-goog-api-key": key})
        except requests.RequestException as e:
            raise RetryableError(str(e))
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code == 429 and "PerDay" in r.text:
            raise DailyQuotaExhausted(model)
        if r.status_code in (429, 500, 502, 503, 504):
            raise RetryableError(f"HTTP {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        data = next(p["inlineData"]["data"] for p in parts if "inlineData" in p)
        return base64.b64decode(data), ms

    # Free tier is rate-limited per minute (FAILURES.md F3): pace calls and back off long.
    time.sleep(MIN_GAP_S)
    return retry(call, attempts=5, base_delay=15, what=f"TTS {voice}")


def write_wav(pcm: bytes, path: Path) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return len(pcm) / 2 / SAMPLE_RATE


def speech_seconds(path: Path) -> float:
    """Duration minus silences >= 0.25 s (so pauses don't count as slow speech)."""
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path), "-af", "silencedetect=n=-40dB:d=0.25",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    sil = sum(float(x) for x in re.findall(r"silence_duration: ([\d.]+)", r.stderr))
    return max(0.1, wav_duration(path) - sil)


def pace(dest: Path, line: dict, cfg: dict) -> float:
    """Pitch-preserving time-stretch toward target_syllables_per_s (EXPERIMENTS E12).

    Ge'ez script is an abugida: one character is one syllable, so counting
    characters in U+1200-U+137F gives a fair speaking-rate measure for Amharic.
    """
    target = cfg.get("target_syllables_per_s")
    if not target or line.get("keep_pace"):
        return 1.0
    syl = len(re.findall(r"[ሀ-፿]", line["text"]))
    rate = syl / speech_seconds(dest)
    factor = max(0.85, min(1.15, target / rate))   # atempo >1 = faster
    if abs(factor - 1) < 0.03:
        return 1.0
    tmp = dest.with_suffix(".tmp.wav")
    subprocess.run([ffmpeg_bin(), "-loglevel", "error", "-y", "-i", str(dest), "-af", f"atempo={factor:.4f}",
                    str(tmp)], check=True)
    tmp.replace(dest)
    return factor


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def run(only: list[str] | None = None, upgrade: bool = False) -> dict:
    env = require_env("GEMINI_API_KEY")
    sb = load_storyboard()
    cfg = sb["narration"]
    cache = Cache(STAGE)
    durations_path = OUT / "durations.json"
    durations = json.loads(durations_path.read_text()) if durations_path.exists() else {}
    generated = reused = 0

    models = [cfg["model"], *cfg.get("fallback_models", [])]
    exhausted: set[str] = set()
    used_by = {}

    for shot in select_shots(sb, only):
        line = shot["narration"]
        dest = OUT / f"{shot['id']}.wav"
        if not line:
            durations.pop(shot["id"], None)
            dest.unlink(missing_ok=True)
            continue

        def key_for(m):
            return input_hash({"model": m, "voice": cfg["voice"], "style": cfg["style_instruction"], "text": line["text"]})

        # 1) cache: any model in the chain that already rendered this exact line.
        #    --upgrade only accepts the primary model's cache, so lines made by a fallback
        #    model are re-voiced once the primary's daily quota is back (one consistent voice).
        allowed = models[:1] if upgrade else models
        hit = next(((m, key_for(m), cache.get(key_for(m), "wav")) for m in allowed if cache.get(key_for(m), "wav")), None)
        if hit:
            m, h, path = hit
            cache.publish(path, dest)
            reused += 1
            log_call(STAGE, "gemini", m, {"voice": cfg["voice"], "hash": h}, shot=shot["id"],
                     latency_ms=0, cached=True, output=str(dest.relative_to(OUTPUTS.parent)))
        else:
            # 2) generate with the first model whose daily quota is not exhausted
            for m in models:
                if m in exhausted:
                    continue
                h = key_for(m)
                try:
                    pcm, ms = tts(line["text"], model=m, voice=cfg["voice"],
                                  style=cfg["style_instruction"], key=env["GEMINI_API_KEY"])
                except DailyQuotaExhausted:
                    exhausted.add(m)
                    log_call(STAGE, "gemini", m, {"voice": cfg["voice"], "hash": h}, shot=shot["id"],
                             status="quota_exhausted", error="free tier daily limit (10/day/model)")
                    print(f"  ! {m}: daily quota exhausted, falling back")
                    continue
                except Exception as e:
                    log_call(STAGE, "gemini", m, {"voice": cfg["voice"], "hash": h}, shot=shot["id"],
                             status="error", error=str(e))
                    raise
                write_wav(pcm, cache.path(h, "wav"))
                cache.publish(cache.path(h, "wav"), dest)
                generated += 1
                log_call(STAGE, "gemini", m, {"voice": cfg["voice"], "chars": len(line["text"]), "hash": h},
                         shot=shot["id"], latency_ms=ms, output=str(dest.relative_to(OUTPUTS.parent)))
                break
            else:
                raise SystemExit(f"narration: every TTS model in {models} is out of daily quota; "
                                 f"{shot['id']} not generated. Re-run tomorrow, cached lines are kept.")
            m = m
        used_by[shot["id"]] = m
        factor = pace(dest, line, cfg)
        durations[shot["id"]] = round(wav_duration(dest), 3)
        print(f"  {shot['id']}: {durations[shot['id']]:.2f}s {'(cached)' if hit else '(new)'} [{used_by[shot['id']]}]"
              + (f" tempo x{factor:.2f}" if factor != 1.0 else ""))

    OUT.mkdir(parents=True, exist_ok=True)
    durations_path.write_text(json.dumps(durations, indent=2))
    total = sum(durations.values())
    print(f"narration: {generated} generated, {reused} cached, {total:.1f}s of speech")
    return durations


def audition(voices: list[str]) -> None:
    env = require_env("GEMINI_API_KEY")
    sb = load_storyboard()
    cfg = sb["narration"]
    shot = next(s for s in sb["shots"] if s["narration"])
    for v in voices:
        pcm, ms = tts(shot["narration"]["text"], model=cfg["model"], voice=v,
                      style=cfg["style_instruction"], key=env["GEMINI_API_KEY"])
        dest = OUT / "audition" / f"{v}.wav"
        dur = write_wav(pcm, dest)
        log_call(STAGE, "gemini", cfg["model"], {"voice": v, "purpose": "audition"}, shot=shot["id"],
                 latency_ms=ms, output=str(dest.relative_to(OUTPUTS.parent)))
        print(f"  {v}: {dur:.2f}s, {ms:.0f} ms -> {dest}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--audition", nargs="*")
    ap.add_argument("--upgrade", action="store_true", help="re-voice lines that came from a fallback model")
    a = ap.parse_args()
    if a.audition:
        audition(a.audition)
    else:
        run(a.only, a.upgrade)
