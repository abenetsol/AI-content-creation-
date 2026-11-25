"""Stage: assemble. Deterministic FFmpeg edit built entirely from storyboard.json.

1. Timeline   each shot lasts max(min_duration_s, lead + narration + tail); the
              voice sets the cut, as professionals cut picture to the voice track.
2. Picture    per shot: Wan clip if present, else Ken Burns over the keyframe,
              else black (radio cut). Each segment is cached by its inputs.
3. Sound      narration placed on the timeline, score ducked under it with
              sidechaincompress, two-pass loudnorm to -14 LUFS / -1 dBTP.
4. Subtitles  English SRT from the storyboard, burned in, safe-area aware.
5. End card   proverb + dedication + AI disclosure.
6. Export     9:16 (1080x1920) and 16:9 (1920x1080) H.264/AAC MP4.

    python -m pipeline.assemble --radio          # narration + placeholder music on black
    python -m pipeline.assemble                  # full film, both aspect ratios
    python -m pipeline.assemble --aspect 9x16 --draft
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import textwrap
import time
import wave
from pathlib import Path

from .common import OUTPUTS, Cache, ROOT, ffmpeg_bin, file_hash, input_hash, load_storyboard, log_call

STAGE = "assemble"
FPS = 24
WORK = OUTPUTS / "assemble"
FILM = OUTPUTS / "film"
ASPECTS = {"9x16": (1080, 1920), "16x9": (1920, 1080)}
DRAFT_SCALE = 0.5
TRANSITION_S = {"cut": 1 / FPS, "dissolve": 0.8, "fade": 0.8}
XFADE = {"cut": "fade", "dissolve": "fade", "fade": "fadeblack"}
SEG_PAD = 0.25

FONT_CANDIDATES = [
    "C:/Windows/Fonts/ebrima.ttf",                       # Windows: covers Ethiopic
    "/usr/share/fonts/truetype/noto/NotoSansEthiopic-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansEthiopic-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Kefa.ttc",       # macOS
]


def run_ff(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"ffmpeg failed:\n  {' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r


def ff_path(p: Path) -> str:
    """Escape a path for use inside an FFmpeg filter argument."""
    return str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def probe_duration(p: Path) -> float:
    r = subprocess.run([ffmpeg_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def wav_len(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / w.getframerate()


# ---------------------------------------------------------------------- timeline
def build_timeline(sb: dict) -> dict:
    cfg = sb["narration"]
    lead, tail = cfg.get("lead_s", 0.5), cfg.get("tail_s", 1.5)
    t = 0.0
    shots = []
    for s in sb["shots"]:
        wav = OUTPUTS / "narration" / f"{s['id']}.wav"
        narr = None
        dur = s["min_duration_s"]
        if s["narration"]:
            if not wav.exists():
                sys.exit(f"missing narration for {s['id']}; run `python -m pipeline.narration` first")
            n_len = wav_len(wav)
            n_start = lead + s["narration"].get("delay_s", 0)
            dur = max(dur, n_start + n_len + tail)
            narr = {"wav": str(wav.relative_to(ROOT)), "start": round(t + n_start, 3), "end": round(t + n_start + n_len, 3)}
        shots.append({"id": s["id"], "start": round(t, 3), "dur": round(dur, 3),
                      "transition_in": s.get("transition_in", "cut"), "narration": narr})
        t += dur
    end_card = {"start": round(t, 3), "dur": sb["end_card"]["duration_s"]}
    total = round(t + end_card["dur"], 3)
    tl = {"shots": shots, "end_card": end_card, "total_s": total}
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "timeline.json").write_text(json.dumps(tl, indent=2))
    return tl


# ---------------------------------------------------------------------- subtitles
def wrap(text: str, width: int) -> str:
    lines = textwrap.wrap(text, width=width)
    if len(lines) > 2:  # rebalance into two lines rather than three
        lines = textwrap.wrap(text, width=(len(text) // 2) + 6)
    return "\n".join(lines[:2] if len(lines) <= 2 else lines)


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_srt(sb: dict, tl: dict, path: Path, width: int) -> Path:
    by_id = {s["id"]: s for s in sb["shots"]}
    out, n = [], 0
    for sh in tl["shots"]:
        if not sh["narration"]:
            continue
        n += 1
        text = wrap(by_id[sh["id"]]["narration"]["subtitle"], width)
        out.append(f"{n}\n{srt_time(sh['narration']['start'])} --> {srt_time(sh['narration']['end'] + 0.4)}\n{text}\n")
    path.write_text("\n".join(out), encoding="utf-8")
    return path


# ---------------------------------------------------------------------- picture
def find_font() -> Path | None:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return Path(f)
    return None


def segment(shot: dict, sbshot: dict | None, length: float, W: int, H: int, mode: str, cache: Cache, aspect: str) -> Path:
    """Render one picture segment (no audio) and cache it by its inputs."""
    clip = OUTPUTS / "clips" / f"{shot['id']}_{aspect}.mp4"
    key = OUTPUTS / "keyframes" / f"{shot['id']}.png"
    if mode == "radio":
        src_kind, src = "black", None
    elif sbshot and sbshot["render"] == "wan" and clip.exists():
        src_kind, src = "clip", clip
    elif key.exists():
        src_kind, src = "kenburns", key
    else:
        src_kind, src = "black", None

    kb = (sbshot or {}).get("kenburns", {"direction": "in", "amount": 0.08})
    h = input_hash({"v": 5, "kind": src_kind, "src": file_hash(src) if src else None, "len": round(length, 3),
                    "W": W, "H": H, "kb": kb, "first": shot.get("first", False)})
    hit = cache.get(h, "mp4")
    if hit:
        return hit
    out = cache.path(h, "mp4")
    frames = max(1, int(round(length * FPS)))
    tail = f"fps={FPS},format=yuv420p,setsar=1,settb=AVTB"

    if src_kind == "black":
        run_ff(["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={length:.3f}", "-vf", tail,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])
    elif src_kind == "clip":
        # Trim the weak first few frames, cover-crop to the target aspect, hold the
        # last frame if the clip is shorter than the shot.
        # Trim the weak first quarter second, then stretch gently (max 1.5x slower)
        # to fill the shot; hold the last frame only for whatever remains.
        clip_len = probe_duration(src) - 0.25
        stretch = min(1.5, max(1.0, length / max(clip_len, 0.1)))
        vf = (f"trim=start=0.25,setpts=(PTS-STARTPTS)*{stretch:.4f},scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},tpad=stop_mode=clone:stop_duration={length:.3f},trim=duration={length:.3f},{tail}")
        run_ff(["-i", str(src), "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])
    else:
        # Ken Burns: crop the square keyframe to the target aspect, upscale 3x to
        # avoid zoompan jitter, then move slowly in the storyboard's direction.
        # Ease-in-out (cosine) progress instead of linear: the move starts and
        # settles gently, which reads as smoother across dissolves (DESIGN test 1).
        amt, d = kb.get("amount", 0.08), kb.get("direction", "in")
        n = frames
        e = f"(0.5-0.5*cos(PI*on/{n}))"
        z = {"in": f"1+{amt}*{e}", "out": f"1+{amt}-{amt}*{e}"}.get(d, f"1+{amt}")
        cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        pan = {"left": (f"(iw-iw/zoom)*(1-{e})", cy), "right": (f"(iw-iw/zoom)*{e}", cy),
               "up": (cx, f"(ih-ih/zoom)*(1-{e})"), "down": (cx, f"(ih-ih/zoom)*{e}")}
        x, y = pan.get(d, (cx, cy))
        # 4x supersampling before zoompan removes the sub-pixel stair-step jitter
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},scale={W*4}:{H*4},"
              f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},trim=duration={length:.3f},{tail}")
        run_ff(["-loop", "1", "-i", str(src), "-vf", vf, "-frames:v", str(n),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])
    return out


def end_card(sb: dict, length: float, W: int, H: int, cache: Cache) -> Path:
    lines = sb["end_card"]["lines"]
    font = find_font()
    h = input_hash({"v": 2, "lines": lines, "len": round(length, 3), "W": W, "H": H, "font": str(font)})
    hit = cache.get(h, "mp4")
    if hit:
        return hit
    out = cache.path(h, "mp4")
    base = int(H * (0.030 if H > W else 0.042))
    wrap_w = 30 if H > W else 52
    rows: list[tuple[str, int, str]] = []  # (text, size, color)
    for i, line in enumerate(lines):
        is_disclosure = line.lower().startswith("made with ai")
        size = int(base * (0.62 if is_disclosure else 1.15 if i == 0 else 0.9))
        color = "0xBBBBBB" if is_disclosure else "white"
        for part in textwrap.wrap(line, wrap_w if not i == 0 else 40):
            rows.append((part, size, color))
        rows.append(("", int(base * 0.6), color))
    total_h = sum(int(s * 1.35) for _, s, _ in rows)
    y = (H - total_h) // 2
    filters = []
    txt_dir = cache.dir / f"{h}_txt"
    txt_dir.mkdir(exist_ok=True)
    for j, (text, size, color) in enumerate(rows):
        if text:
            tf = txt_dir / f"{j}.txt"
            tf.write_text(text, encoding="utf-8")
            fontarg = f"fontfile='{ff_path(font)}':" if font else ""
            filters.append(f"drawtext={fontarg}textfile='{ff_path(tf)}':fontsize={size}:fontcolor={color}:"
                           f"x=(w-text_w)/2:y={y}")
        y += int(size * 1.35)
    vf = ",".join(filters + [f"fade=in:st=0:d=0.6", f"fps={FPS},format=yuv420p,setsar=1,settb=AVTB"])
    run_ff(["-f", "lavfi", "-i", f"color=c=0x0d0b09:s={W}x{H}:r={FPS}:d={length:.3f}", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])
    return out


def picture(sb: dict, tl: dict, W: int, H: int, mode: str, out: Path, aspect: str) -> Path:
    cache = Cache(STAGE)
    by_id = {s["id"]: s for s in sb["shots"]}
    items = tl["shots"]
    segs, trans = [], []
    for i, sh in enumerate(items):
        nxt = items[i + 1]["transition_in"] if i + 1 < len(items) else "dissolve"
        T = TRANSITION_S[nxt]
        # +SEG_PAD: frame rounding can leave a segment a few ms short, and xfade silently
        # truncates the whole chain when offset+duration exceeds its input (FAILURES F12).
        segs.append(segment(sh, by_id[sh["id"]], sh["dur"] + T + SEG_PAD, W, H, mode, cache, aspect))
        trans.append((nxt, T))
    segs.append(end_card(sb, tl["end_card"]["dur"], W, H, cache))

    inputs = []
    for s in segs:
        inputs += ["-i", str(s)]
    fc, last = [], "[0:v]"
    first_tr = items[0]["transition_in"]
    if first_tr in ("fade", "dissolve"):
        fc.append(f"[0:v]fade=in:st=0:d={TRANSITION_S[first_tr]:.3f}[v0]")
        last = "[v0]"
    starts = [sh["start"] for sh in items] + [tl["end_card"]["start"]]
    for k in range(1, len(segs)):
        kind, T = trans[k - 1]
        fc.append(f"{last}[{k}:v]xfade=transition={XFADE[kind]}:duration={T:.4f}:offset={starts[k]:.4f}[x{k}]")
        last = f"[x{k}]"
    run_ff([*inputs, "-filter_complex", ";".join(fc), "-map", last, "-t", f"{tl['total_s']:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out)])
    got = probe_duration(out)
    if got < tl["total_s"] - 0.2:
        sys.exit(f"picture track is {got:.2f}s but the timeline is {tl['total_s']:.2f}s; xfade chain truncated")
    return out


# ---------------------------------------------------------------------- sound
def music_source(sb: dict, total: float) -> tuple[Path, str]:
    m = sb["music"]
    if m.get("chosen") is not None:
        p = OUTPUTS / "music" / f"candidate_{m['chosen']}.wav"
        if p.exists():
            return p, f"ACE-Step candidate {m['chosen']}"
    p = WORK / "placeholder_music.wav"
    if not p.exists() or wav_len(p) < total:
        # Temp track: a soft C-minor drone (C3, Eb3, G3) with slow tremolo.
        d = f"{total + 2:.2f}"
        run_ff(["-f", "lavfi", "-i", f"sine=f=130.81:d={d}", "-f", "lavfi", "-i", f"sine=f=155.56:d={d}",
                "-f", "lavfi", "-i", f"sine=f=196.00:d={d}",
                "-filter_complex", "[0][1][2]amix=inputs=3,tremolo=f=0.2:d=0.5,lowpass=f=900,volume=0.5,"
                                   "aformat=sample_rates=48000:channel_layouts=stereo",
                "-ar", "48000", str(p)])
    return p, "placeholder drone"


def loudnorm_measure(src: Path) -> dict:
    cmd = [ffmpeg_bin(), "-hide_banner", "-i", str(src), "-af",
           "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    return json.loads(m.group(0)) if m else {}


def sound(sb: dict, tl: dict, out: Path) -> dict:
    total = tl["total_s"]
    duck = sb["music"].get("duck", {})
    music, music_label = music_source(sb, total)
    narr = [sh for sh in tl["shots"] if sh["narration"]]

    inputs = ["-i", str(music)]
    fc = []
    for i, sh in enumerate(narr, start=1):
        inputs += ["-i", str(ROOT / sh["narration"]["wav"])]
        ms = int(sh["narration"]["start"] * 1000)
        fc.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms}[n{i}]")
    fc.append("".join(f"[n{i}]" for i in range(1, len(narr) + 1)) +
              f"amix=inputs={len(narr)}:normalize=0,apad=whole_dur={total:.3f},atrim=0:{total:.3f}[voice]")
    fc.append("[voice]asplit=2[voice_mix][voice_sc]")
    gain = duck.get("music_gain_db", -6)
    fc.append(f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total:.3f},"
              f"afade=t=in:d=1.5,afade=t=out:st={max(0, total - 3.5):.3f}:d=3.5,volume={gain}dB[music]")
    fc.append(f"[music][voice_sc]sidechaincompress=threshold={duck.get('threshold', 0.02)}:"
              f"ratio={duck.get('ratio', 6)}:attack={duck.get('attack_ms', 300)}:"
              f"release={duck.get('release_ms', 1500)}[ducked]")
    fc.append("[voice_mix][ducked]amix=inputs=2:normalize=0[mix]")
    premix = WORK / "premix.wav"
    run_ff([*inputs, "-filter_complex", ";".join(fc), "-map", "[mix]", "-ar", "48000", str(premix)])

    # Two-pass loudness normalisation to -14 LUFS integrated, -1 dBTP.
    m = loudnorm_measure(premix)
    if m:
        af = (f"loudnorm=I=-14:TP=-1:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
              f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    else:
        af = "loudnorm=I=-14:TP=-1:LRA=11"
    # Limiter at -1.5 dBFS after loudnorm: loudnorm alone left the true peak at -0.87 dBTP (EXPERIMENTS E9).
    run_ff(["-i", str(premix), "-af", af + ",alimiter=limit=0.84:level=false", "-ar", "48000", str(out)])
    final = loudnorm_measure(out)
    return {"music": music_label, "premix_lufs": m.get("input_i"), "final_lufs": final.get("input_i"),
            "final_tp": final.get("input_tp")}


# ---------------------------------------------------------------------- export
def export(sb: dict, tl: dict, aspect: str, mode: str, draft: bool, audio: Path) -> Path:
    W, H = ASPECTS[aspect]
    if draft:
        W, H = int(W * DRAFT_SCALE) // 2 * 2, int(H * DRAFT_SCALE) // 2 * 2
    vertical = H > W
    pic = picture(sb, tl, W, H, mode, WORK / f"picture_{mode}_{aspect}{'_draft' if draft else ''}.mp4", aspect)
    srt = write_srt(sb, tl, WORK / f"subs_{aspect}.srt", width=30 if vertical else 44)
    # libass sizes are relative to a 288-line canvas; MarginV lifts captions
    # above the platform UI in the vertical safe area.
    style = ("FontName=Arial,FontSize=9,MarginV=58,Outline=1.2,Shadow=0.6,BorderStyle=1" if vertical
             else "FontName=Arial,FontSize=14,MarginV=18,Outline=1.2,Shadow=0.6,BorderStyle=1")
    name = "radio_cut" if mode == "radio" else "film"
    out = FILM / f"{name}_{aspect}{'_draft' if draft else ''}.mp4"
    FILM.mkdir(parents=True, exist_ok=True)
    vf = f"subtitles='{ff_path(srt)}':force_style='{style}'"
    run_ff(["-i", str(pic), "-i", str(audio), "-vf", vf, "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(out)])
    return out


def run(radio: bool = False, aspects: list[str] | None = None, draft: bool = False) -> list[Path]:
    sb = load_storyboard()
    t0 = time.perf_counter()
    tl = build_timeline(sb)
    mode = "radio" if radio else "film"
    audio = WORK / "mix.wav"
    stats = sound(sb, tl, audio)
    aspects = aspects or (["16x9"] if radio else ["9x16", "16x9"])
    outs = [export(sb, tl, a, mode, draft, audio) for a in aspects]
    ms = (time.perf_counter() - t0) * 1000
    log_call(STAGE, "local", "ffmpeg", {"mode": mode, "aspects": aspects, "draft": draft, **stats},
             latency_ms=ms, output=", ".join(str(o.relative_to(ROOT)) for o in outs), total_s=tl["total_s"])
    print(f"assemble: {tl['total_s']:.1f}s film, music={stats['music']}, "
          f"loudness {stats['final_lufs']} LUFS / {stats['final_tp']} dBTP, {ms/1000:.1f}s wall")
    for o in outs:
        print(f"  -> {o.relative_to(ROOT)}")
    return outs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", action="store_true", help="narration + placeholder music on black")
    ap.add_argument("--aspect", nargs="*", choices=list(ASPECTS))
    ap.add_argument("--draft", action="store_true", help="half resolution, for quick checks")
    a = ap.parse_args()
    run(a.radio, a.aspect, a.draft)
