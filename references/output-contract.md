# Output contract

Read this reference before processing or delivering files.

## Character workspace

All generated artifacts for one character live under `works/<character-slug>/` in the skill directory. Resolve it with `scripts/character_workspace.py --name <角色名>`. Do not add new job files to a shared `work/` folder or the skill root.

```text
works/<character-slug>/
├── character.json
├── static-sheet-source.png
├── static-sheet.png
├── static-prompt.json
├── static-generation.json
├── static-alpha.json
├── layout.json
├── job-state.json
├── tile-plan.json
├── prompts.json
├── video-task.json
├── route.json
├── raw-video/
│   └── <provider>.mp4           # one accepted canonical source video
└── delivered/                   # numbered stickers + reports + one ZIP
```

## Required generic package

```text
works/<character-slug>/delivered/
├── 01.webp ... NN.webp
├── 01.gif  ... NN.gif
├── 01.png  ... NN.png
├── preview.png                  light checkerboard review composite
├── layout.json
├── job-state.json               when static approval was required
├── prompts.json                 when generation occurred
├── route.json                   when routing occurred
├── processing.json
├── 3s/                          Grok only: 24-frame derivative + reports
└── sticker-pack.zip
```

Number files in row-major order with at least two digits. `NN` must equal `detected_layout.count`, not a count copied from the initial prompt.
For independent static stickers, `layout.json` may declare a synthetic single-row numbering layout and must also record `source_type: separate-static-stickers`.

## Processing rules

- The default profiles are 3 seconds → 240×240 at 8 fps (24 frames) and 6 seconds → 240×240 at 8 fps (48 frames), both with a 192-color GIF palette ceiling.
- For a direct API provider that supports 3 seconds, request 3 seconds and preserve the matching returned duration as the only version. Do not retime it; an unexpected duration must stop and report.
- For Grok-generated grid videos, retain the complete returned 6-second duration in the root output. Also create `3s/` from the initial 24 sampled frames. Record the first/last endpoint difference for audit, but do not reject or retime the 3-second derivative when the poses differ.
- Keep one accepted provider video under `raw-video/`. An accepted attempt is promoted to the canonical provider filename instead of copied, while a rejected attempt may remain for diagnosis.
- Whole-grid registration is off by default. Enable it only for verified camera drift; frame-by-frame integer translation can create visible micro-jitter in an otherwise fixed-camera source.
- Preserve aspect ratio inside each detected cell. Do not stretch a crop to square unless a target platform profile explicitly requires a square canvas with padding.
- Prefer a real source alpha channel. Otherwise estimate the uniform background from corners and borders, then remove only background-like regions connected to the crop edge.
- A video declared as green-screen must pass strict QC before matting: every native frame's corners and border must match the declared `#00FF00` key. A gray/white checkerboard is not transparency and must be rejected, not color-inferred or globally deleted.
- For grid videos, repack each cell from its actual Alpha bounding box so every side has at least a 10% green safety corridor. Decode and inspect every native frame. Internal-seam crossings are evidence for instance assignment and safe-window selection, not automatic whole-video failure; only inseparable multi-instance merges are bad frames.
- Avoid global color deletion: a face, garment, or prop similar to the key color must remain opaque when not connected to the outer background.
- Near-black and near-white plates use a tight key automatically. Default chroma radii eat dark fur or light clothing, and GIF binary transparency turns those pixels into holes on a light chat or README background.
- Retain a clean first-frame transparent PNG for each animation.
- Also export a looping GIF per cell for platforms that do not accept Animated WebP. GIF transparency is binary (palette), not a full alpha channel; choose from bounded alpha-threshold candidates using edge erosion, residual spill, and temporal coverage, and treat Animated WebP as the quality-preferred format.
- Package only delivery artifacts and reports; omit temporary raw-frame directories. When this Skill generated the static sheet, include `static-prompt.json`, `static-generation.json`, and `static-alpha.json` so parameter fallback and alpha repair remain auditable.
- For static generation, `static-generation.json` must preserve the transparent-first call, the bounded `opaque_fallback_call`, the schema fields that were omitted, and the selected attempt. It must record that schema omission does not select the fallback and that reference-image presence does not change the background policy. Only local pixel-validation failure may select the fallback. A checkerboard/two-tone result is a failed attempt and must never be recorded as an accepted normalized source.
- Use `scripts/assemble_delivery.py --cleanup-media-dir` to collect media and audit artifacts into the canonical `delivered/` directory and remove the intermediate media directory after the ZIP succeeds. The ZIP must include `job-state.json`, `prompts.json`, and `route.json` whenever those stages occurred. Do not copy variant ZIPs into the final directory or nest one ZIP inside another.
- Refuse to mix new output with prior numbered files by default. Reuse an output directory only with an explicit `--overwrite`, which removes known generated artifacts but preserves unrelated files.
- Run the configured trial cell before full-pack encoding. The default is cell `01`, so the rule works for any non-empty layout, with a 1 MiB GIF target. For Grok, test both the full 6-second file and the 3-second derivative. A trial budget or encoded-frame failure must produce a report and stop; a pass authorizes processing the remaining cells from the same already-generated grid video, not another provider call. In the full-pack run, later GIF budget overages are warnings and do not block delivery; encoded-frame failures remain blocking.

## QC report

`processing.json` should record source size and native fps/frame count, measured duration, selected duration profile, output fps/size, production-settings path and hash, detected grid, full-frame alpha method, registration, recovered crossings, ambiguous merged frames, per-cell selected source frames, bad-frame repairs, fixed-canvas transform, adaptive GIF threshold, file-budget result, encoded-output QC, warnings, and the exact output list. Warn when:

- layout confidence is below `0.75`;
- foreground touches a crop boundary;
- an isolated bad frame had to be replaced;
- full-duration sampling cannot select a safe frame for any configured output time bin;
- in explicitly requested short-loop mode, no clean requested-duration loop remains for a cell;
- alpha coverage changes sharply across frames;
- the alpha-weighted centroid moves more than the hold-jitter threshold during the nominal hold segment;
- first and last frames differ enough to cause a visible jump;
- a cell is empty or nearly full-frame foreground;
- an encoder cannot preserve alpha.

Decode the final WebP and GIF and run QC on every encoded frame before packaging. Also composite the first-frame PNGs onto a light checkerboard preview. Dark subjects can hide interior holes on black. Visual identity and whether a motion feels natural remain human review items. Do not report them as machine-proven.

## Prompt-only delivery

When no video or local image-processing capability is available, deliver `static-prompt.json`, `tile-plan.json`, `prompts.json`, `route.json`, and `prompt-only.json` with `generated_video: false`. Do not create placeholder media or claim that generation started.
