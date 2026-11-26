# DESIGN.md: The Egg Walks (እንቁላል ቀስ በቀስ በእግሩ ይሄዳል)

## 1. Brief (written 2026-11-25, before any keyframe was generated)

**Story.** My father, Baba, always said *እንቁላል ቀስ በቀስ በእግሩ ይሄዳል*: "little by little, the egg walks on its own legs." He has passed away. Now I catch him in myself: in the mirror, in the way I stay slow and calm in a conflict, in the way I walk.

**Logline.** Years after losing their father, a young Ethiopian catches him in the mirror, in the way they handle a quarrel and in the way they walk, and finally understands the proverb he always repeated.

**Audience.** My family first, then young Ethiopians at home and in the diaspora who have lost a parent. They watch on a phone, vertically, in a Telegram or TikTok feed, often with the sound low. That is why the subtitles are burned in.

**Intent: what they should feel at the end.** That a parent is not fully gone: they keep walking in us, slowly, the way they taught us. My family should recognise Baba in the gestures. Strangers should think of their own parent and want to send the film to a sibling.

**Define (how might we…).** How might we make a grieving viewer feel their parent's presence in their own habits, in under 90 seconds, without showing a face?

## 2. Ideation: options considered

| Option | Idea | Why not / why |
|---|---|---|
| A. Pure fable | Only the egg → chick → hen, proverb at the end | Beautiful and easy, but impersonal. Nothing for family to recognise |
| B. Parallel lives | Chick's journey intercut with a person's slow growth | Universal, but the person was generic |
| C. The one who taught it | Father says the proverb to a child, the child grows up | Most "heritage", but needs a face, and Baba has passed |
| **D. The mirror (chosen)** | I see Baba in myself: mirror, conflict, walk; the chick is the visual metaphor | Personal *and* universal, and needs no faces. The proverb literally ends in walking |

## 3. Key design decisions

- **No faces.** Baba is shown only as hands in a suit cuff, from behind, or as a silhouette. This is a choice about respect (he cannot consent; see ETHICS.md) and about consistency (AI faces drift across shots; hands and suits don't).
- **The suit as his signature.** He wore suits. The empty suit jacket on a hook (s06) is how the film says "he is gone" without saying "died".
- **Two palettes.** *Present* is cool blue-grey morning. *Memory* is warm golden. The viewer always knows when they are, without titles.
- **Match cuts carry the meaning.** s04 (Baba calming a conflict, hand raised) ↔ s08 (me doing the same). s01 (misted mirror) ↔ s07 (Baba's silhouette in the mirror).
- **Voice sets the cut.** Every shot lasts *lead + narration + tail* (min. per-shot floor), so pictures are cut to the voice, as in a professional edit.
- **Subject centred** in every keyframe, so one square master crops cleanly to both 9:16 and 16:9.

Every shot's reason is stored in `storyboard.json → shots[].reason`.

## 4. User test 1: the radio cut

**Prototype.** `outputs/film/radio_cut_16x9.mp4`: 71.7 s, 9 Amharic narration lines (Gemini TTS, voice *Charon*) over a placeholder drone, black screen, English subtitles, AI end card.

**Questions asked**
1. In one sentence, what is this film about?
2. Which line did you feel the most? Which line lost you?
3. Was anything confusing, or too fast or too slow?
4. Does the Amharic sound natural? Any word mispronounced?
5. Did it make you think of anyone?

| # | Who | What they said | Insight |
|---|---|---|---|
| 1–3 | Three listeners from my audience: family / friends who know Amharic and colleagues on the cohort Slack, listening on their phones | First reaction: "it was interesting". Too general to act on, so I sent them three specific questions (which line did you feel most / what was confusing or too fast / which Amharic word sounded wrong). | Open questions get polite answers; specific questions get usable ones. |
| 1–3 | (answers to the specific questions) | **"*አረማመዴም የእሱ ነው* is not quite right, refine it."** | The line was a word-for-word construction ("my walk is also his"); it doesn't sound like how people speak. |
| 1–3 | | **"The speed of each sentence is not equal, even though mostly it's not obvious."** | Measured it: lines ranged from **6.6 to 9.6 syllables/s**. Lines s09–s11 came from the fallback TTS model after the daily quota ran out (FAILURES F3), so the voice also shifted slightly at the end. |
| – | Me, on the rough cut (Ken Burns stills) | "Good, but it needs a little more flow and smoothness." | Hard cuts between still images feel like a slideshow; linear zooms start and stop abruptly. |
| – | Me, native-speaker listen of the rough cut | "The word *ነበር* is said too many times and doesn't feel natural." | It appeared **5 times in the first 3 lines** (past-habitual auxiliary on every verb); written Amharic tolerates that, spoken Amharic doesn't. |

**What changed because of it** (visible in `storyboard.json` and the code):

1. **s09 narration rewritten**: *አረማመዴም የእሱ ነው።* → ***አረማመዴ እንኳን የእሱን ይመስላል።*** ("Even my walk looks like his."). Regenerated that one line only (1 TTS call, 8 cached).
2. **Even pace:** new `narration.target_syllables_per_s = 8.2`; each line is time-stretched (pitch-preserving `atempo`, max ±15%) toward it. Spread across lines went **from 3.05 to 0.96 syllables/s**. s06 ("Baba is gone now") is marked `keep_pace`, because its slowness is the point. (EXPERIMENTS E12)
3. **One voice:** once the primary model's daily quota reset (2026-11-26), `python -m pipeline.narration --upgrade` re-voiced the 5 lines that had come from fallback models (s03, s04, s09, s10, s11): 5 TTS calls, 4 lines cached. All 9 lines now share one model and voice.
4. ***ነበር* 5 → 2:** kept it only where "used to" carries meaning (his saying in s02, his habit in s04). s03 → *ልጅ እያለሁ አልገባኝም፤ ሳልራመድ መሮጥ ፈለግሁ።*; s04 → *እሱ ግን ቸኩሎ አያውቅም። ሲቆጣ እንኳ በእርጋታ ይናገር ነበር።* (subtitle: "Even when angry, he spoke calmly."). 2 TTS calls, 7 cached.
5. **Flow:** every hard cut became a 0.8 s dissolve; Ken Burns moves now ease in and out (cosine) and are 4x supersampled to remove jitter. A side effect I kept: the hatching egg (s05) now dissolves into Baba's empty chair (s06).

## 5. User test 2: the near-final cut

| # | Who | Channel | What they said | Insight |
|---|---|---|---|---|
| 1 | _TODO_ | | | |
| 2 | _TODO_ | | | |
| 3 | _TODO_ | | | |

**What changed:** _TODO_
