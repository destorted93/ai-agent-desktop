# Two‑Voice Dialogue Tutorial Template

Description
<!-- DESCRIPTION_START -->
Generate a multi‑file, two‑speaker (F and M) dialogue tutorial for any topic. Beginner‑friendly, engaging, and TTS‑ready. Provide three inputs: TOPIC, TONE, and POINTS_IN_ORDER. Backend can scrape this description for listing, and extract the prompt between PROMPT_START/END for placeholder substitution.

Placeholders: {{TOPIC}}, {{TONE}}, {{POINTS_IN_ORDER}}
Output format: index of files + per‑chapter blocks wrapped with "--- FILE: {filename} ---" and "--- END FILE ---"; filenames follow NN-kebab-slug.md.
<!-- DESCRIPTION_END -->

Prompt
<!-- PROMPT_START -->
Task
Create a beginner‑friendly, two‑voice dialogue tutorial that can be read aloud for TTS and later turned into a video. Make it engaging, clear, and substantive.

Inputs
- TOPIC: {{TOPIC}}
- TONE: {{TONE}}
- POINTS_IN_ORDER: {{POINTS_IN_ORDER}}

Dialogue Style
- Two speakers only: use exact tags “F:” and “M:”.
- F is curious, warm, and sharp; M is patient, expert, and concrete.
- Cadence: M carries the explanation with real examples; F punctuates with short nods (e.g., “Mhm.” “Right.” “Okay.”) and curiosity questions that push for depth, a new angle, or an example.
- Ratio: ~25% F lines, ~75% M lines.
- Nods: ~one brief F nod every 4–6 M lines; do not spam.
- Follow‑ups: ~one crisp F question every 1–3 M paragraphs; make each question purposeful.
- Accessibility: define terms plainly on first use; avoid jargon unless you immediately explain it.
- TTS‑friendly: mostly 6–18‑word sentences; varied rhythm; no stage directions; no “Title:” labels.

Continuity Rules
- Use POINTS_IN_ORDER as the chapter flow. If a point is broad, split it across 2–3 short chapters; if narrow, merge adjacent points. Aim for 8–14 total chapters.
- End each chapter with a subtle forward‑looking hook (usually from M, sometimes F).
- Start the next chapter with F picking up that hook in a fresh way (no paraphrase or duplication). The whole script should read as one continuous conversation when concatenated.
- No meta talk about chapters/slides inside the dialogue.

Content Expectations (generic)
- Teach the TOPIC clearly using the provided points.
- Use concrete, real‑world examples in most chapters (at least one per chapter when natural).
- Use analogies sparingly but effectively. If links/excerpts appear in POINTS_IN_ORDER, integrate them and cite inline like (Source: short_name_or_url).
- Never invent statistics; prefer qualitative phrasing when uncertain.
- Keep it practical and respectful of non‑experts.

Deliverable Format
- First, print an index of files.
- Then output each chapter as a separate block using this exact wrapper:
  --- FILE: 00-opening.md ---
  F: …
  M: …
  --- END FILE ---
- File naming: NN-kebab-slug.md where NN is 00, 01, 02… in order.
- No duplicate setup lines across files. The concatenated blocks must read as a seamless conversation.

Now produce the tutorial.
<!-- PROMPT_END -->
