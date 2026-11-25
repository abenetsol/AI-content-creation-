# ETHICS.md: consent, labelling, licences, privacy

## 1. Consent

The keyframe stage reads this file. A shot that includes a character marked `real_person: true` in `storyboard.json` is **not generated** unless that character's consent line below says `granted`. (See `pipeline/keyframes.py → check_consent`.)

<a id="baba"></a>
### Baba (my late father)
- **How he appears:** never his face and never his voice. Only: his hands in a suit cuff (s02), seen from behind (s04), a faint silhouette (s07, s11), and his empty chair and suit jacket (s06). The figure is an illustrated, generic man in a suit, **not** generated from any photograph of him.
- **Words attributed to him:** only the proverb *እንቁላል ቀስ በቀስ በእግሩ ይሄዳል*, which he really said. The narrator *quotes* him; no new words are put in his mouth.
- **Voice:** no voice cloning. The narration uses a stock Gemini TTS voice and is spoken by "me", not by him.
- **Who consented:** because he has passed away, consent was asked from close family.
  - Family member: my mother (Baba's wife)
  - Date: 2026-11-25. Channel: _TODO (phone call / message)_
  - What they were shown: the storyboard description above and the radio cut
  - Her answer: "Yes, that would be nice, to do a tribute video." She agreed to the film as described: Baba shown only as hands, from behind and as a silhouette, with no face and no cloned voice.

consent[baba]: granted (mother, 2026-11-25)

<a id="narrator"></a>
### Narrator (me)
- I appear only from behind, as hands, or as a misted reflection. I consent to my likeness being depicted this way and to the narration being spoken by a synthetic voice on my behalf.

consent[narrator]: granted (self, 2026-11-25)

### Other characters
The child, the friend in the café, the neighbour, the hen and the chick are generic and fictional. No real person is depicted.

## 2. Labelling
- **In the film:** a 4-second end card states "Made with AI" and lists every model used (images FLUX.2 klein, video Wan 2.2, music ACE-Step 1.5, voice Gemini TTS).
- **On the platform:** the post is published with the platform's AI-generated-content label switched on (TikTok "AI-generated content" toggle / YouTube "altered or synthetic content" disclosure).
- **Provenance:** Gemini TTS audio carries Google's SynthID watermark; nothing in the pipeline strips watermarks or metadata.

## 3. Licences

| Component | Used for | Licence / terms | Output use |
|---|---|---|---|
| Gemini 3.8 Flash TTS (+ Flash-Lite TTS fallback) | Narration | Gemini API Terms (free tier; Google may use free-tier inputs to improve products) | Allowed; SynthID watermark kept |
| FLUX.2 klein 4B on Cloudflare Workers AI | Keyframes | Apache 2.0 (model); Cloudflare Workers AI terms | Allowed, incl. commercial |
| Wan 2.2 TI2V-5B | Moving shots | Apache 2.0 | Allowed, incl. commercial |
| ACE-Step 1.5 | Score | MIT; trained on licensed / royalty-free / synthetic data | Allowed, incl. commercial |
| FFmpeg (gyan.dev full build) | Assembly | GPL v3 (build) | Tool only; no code redistributed |
| Ebrima font (Windows) / Noto Sans Ethiopic | End card | Microsoft system font / SIL OFL | Rendered into pixels only |

Rejected: FLUX.2 klein **9B** (non-commercial licence).

## 4. Style and privacy
- **Style** is described in words (painterly, soft brushwork, film grain), never by naming a living artist.
- **Private data:** free-tier endpoints may train on inputs. Prompts contain no names, no places more specific than "Addis Ababa", and no photographs of my family. My father is only "Baba" or "a man in a dark suit".
- **Secrets:** API keys live only in `.env`, which is in `.gitignore`. `.env.example` lists the variable names without values.
