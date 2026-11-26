# EXPERIMENTS.md: hypothesis → change → result

One change per experiment, fixed seed where possible. Raw numbers are in `runs.jsonl`.

### E1: Which keyframe model? (FLUX.1 schnell vs FLUX.2 klein 4B)
- **Hypothesis:** schnell is faster and good enough.
- **Change:** same prompt, both models on Workers AI.
- **Result:** schnell 5.5 s, but no seed support (F1). klein 4B 12.1 s with seed 42, 768×1344, strong painterly result.
- **Decision:** klein 4B. Reproducibility matters more than 6 s per image; licence is Apache 2.0 (the 9B is non-commercial, rejected).

### E2: How do I steer Gemini TTS pacing?
- **Hypothesis:** a style instruction ("slowly and warmly…") makes the Amharic narration more tender.
- **Change:** same line (s02), same voice (Charon), three prompt formats.
- **Result:** instruction + blank line: **20.7 s** (instruction read aloud). `Say …:` prefix: **12.8 s** (still partly read). `systemInstruction`: **HTTP 400**. Plain text: **4.9 s**, correct.
- **Decision:** plain text. Pacing is done in the edit (`lead_s` 0.5 s, `tail_s` 1.8 s). **The suggested practice ("a short style instruction improves a voice") did not hold for Amharic on this model.**

### E3: Voice audition (Charon, Algieba, Sulafat, Kore)
- **Hypothesis:** voices differ enough to matter for a 31-year-old male narrator.
- **Result:** inconclusive: all four takes were contaminated by E2's spoken instruction (18.7–21.6 s), and the clean re-audition hit the daily quota (F3).
- **Decision:** kept Charon (male, low, calm), confirmed by the narrator himself on the radio cut.

### E4: Does FLUX.2 klein actually use a reference image?
- **Hypothesis:** `input_image_0` conditions the output.
- **Change:** village image as reference, prompt "the same village at night".
- **Result:** identical layout, huts and rocks; only light changed (11.6 s). Conditioning is **very strong**, closer to editing than to "inspiration".
- **Consequence:** references can lock consistency, but can also copy composition (see E5, E8).

### E5: Style reference vs character identity
- **Hypothesis:** adding the style reference to character sheets keeps the whole film in one look.
- **Change:** child sheet, same prompt and seed 13, with vs without the style image.
- **Result:** with: light-skinned child with straight hair, courtyard copied from the reference. Without: dark skin, curly hair, clean three-view sheet.
- **Decision:** sheets without style reference; keyframes use style ref + sheets. Pipeline changed (`run_refs`). The regenerated child sheet was a **cache hit** on the experiment's own image (same inputs → same hash).

### E6: Baba's sheet: seed vs safety filter
- **Change:** seed 11 (flagged, F5) → seed 21 (same prompt) vs rephrased prompt (seed 11).
- **Result:** both passed. Seed 21 gives grey hair (reads as fifties); the rephrased version looked ~40.
- **Decision:** seed 21: age contrast with the 31-year-old narrator is what makes s11 (two men walking) readable.

### E7: One reference or two in a two-person shot? (s07)
- **Hypothesis:** giving both sheets makes both characters appear correctly.
- **Result:** both refs → the model put **Baba** at the mirror (F6). Narrator-only ref + Baba described as a silhouette in text → correct. Seeds 1707 vs 1708: 1708 chosen (clean back view, both reflections faceless).
- **Rule now:** the reference goes to whoever must look *exactly* right; ghosts and silhouettes are described in text.

### E8: Does the cache make re-runs cheap?
- **Change:** edited the prompts of s02 and s07, re-ran the whole keyframe stage.
- **Result:** `2 generated, 9 cached`. Only the two edited shots called the model. Same for narration: after the quota failure, the re-run reused 6 cached lines and generated 3.

### E9: Loudness target
- **Change:** two-pass `loudnorm` (I = −14, TP = −1) on the narration + score mix.
- **Result:** −14.27 LUFS integrated, true peak −0.87 dBTP: 0.13 dB above the ceiling.
- **Change 2:** `alimiter` at −1.5 dBFS after loudnorm.
- **Result 2:** −14.35 LUFS / **−1.41 dBTP**. Both inside target; loudness moved only 0.08 LU.

### E10: Score candidates (ACE-Step 1.5)
- **Hypothesis:** with the same caption (tizita-inspired krar, cello, washint; 68 bpm, C minor, instrumental), different seeds give scores different enough that the choice matters, and the choice should be made *under the voice*, not by listening to the score alone.
- **Change:** 3 seeds (301, 302, 303), 8 turbo steps, DiT only (no LM planner) on a Colab T4, after the Kaggle queue stalled (FAILURES F16, F17). New `assemble --audition-music` mixes each candidate under the narration with the real ducking and loudness chain and lays it on the draft picture.
- **Result:** 17–20 s of GPU per 85 s candidate. Measured differences: candidate 0 had the widest dynamics (LRA 11.2 LU), candidate 1 was 8.2 LU, candidate 2 was 7.8 LU (most compact). Under the narration, candidate 0 came out 0.6 LU quieter than the others, so I flagged it to avoid a "louder sounds better" bias.
- **Decision:** **candidate 2 (seed 303)**, chosen by the narrator: *"it feels good and relates to the story."* Its even, compact level sits under the voice as a steady bed and doesn't fight it. Recorded in `storyboard.json → music.chosen / choice_reason`.

### E11: Wan 2.2 clip vs Ken Burns: _pending Kaggle run_
- Same shot (s10, the chick's first steps) as Wan i2v vs Ken Burns on the keyframe; measure GPU minutes vs perceived quality.

### E12: Even out the narration pace (from user test 1)
- **Hypothesis:** listeners hear uneven speed because TTS speaking rate varies per call; Ge'ez characters ≈ syllables, so I can measure and correct it.
- **Measure:** syllables ÷ speech time (silences ≥ 0.25 s removed). Before: s02 8.98, s03 9.22, s04 **9.62**, s07 7.97, s08 8.35, s09 8.72, s10 **6.57**, s11 8.51 (s06 4.95, intentional).
- **Change:** pitch-preserving `atempo` toward 8.2 syl/s, clamped to ±15%.
- **Result:** 7.40 to 8.35 syl/s, **spread 3.05 → 0.96**. s10 hit the 15% clamp (still 7.44); a bigger stretch starts to sound processed, so I accept it and will re-voice s09–s11 with the primary model instead.

### E13: Smoother picture flow (from my rough-cut review)
- **Change:** hard cuts → 0.8 s dissolves; linear Ken Burns → cosine ease-in-out; zoompan supersampling 2x → 4x.
- **Result:** film 71.7 → 73.7 s (longer overlaps); render time 105 → 130 s for the draft. The cuts no longer feel like a slideshow. To confirm in user test 2.
