# Output contract

Read this reference before processing or delivering files.

## Character workspace

All generated artifacts for one character live under `works/<character-slug>/` in the skill directory. Resolve it with `scripts/character_workspace.py --name <角色名>`. Do not add new job files to a shared `work/` folder or the skill root.

```text
works/<character-slug>/
├── character.json
├── static-sheet.png
├── static-prompt.json
├── layout.json
├── job-state.json
├── tile-plan.json
├── prompts.json
├── video-task.json
├── route.json
├── raw-video/
└── output/                      # numbered stickers + processing.json + ZIP
```

## Required generic package

```text
works/<character-slug>/output/
├── 01.webp ... NN.webp
├── 01.gif  ... NN.gif
├── 01.png  ... NN.png
├── layout.json
├── job-state.json               when static approval was required
├── prompts.json                 when generation occurred
├── route.json                   when routing occurred
├── processing.json
└── sticker-pack.zip
```

Number files in row-major order with at least two digits. `NN` must equal `detected_layout.count`, not a count copied from the initial prompt.
For independent static stickers, `layout.json` may declare a synthetic single-row numbering layout and must also record `source_type: separate-static-stickers`.

## Processing rules

- Default to 6 fps for chat-scale previews, but preserve a user-specified frame rate.
- Preserve aspect ratio inside each detected cell. Do not stretch a crop to square unless a target platform profile explicitly requires a square canvas with padding.
- Prefer a real source alpha channel. Otherwise estimate the uniform background from corners and borders, then remove only background-like regions connected to the crop edge.
- Avoid global color deletion: a face, garment, or prop similar to the key color must remain opaque when not connected to the outer background.
- Retain a clean first-frame transparent PNG for each animation.
- Also export a looping GIF per cell for platforms that do not accept Animated WebP. GIF transparency is binary (palette), not a full alpha channel; dithering and fringe are expected and should be reported rather than hidden.
- Package only delivery artifacts and reports; omit temporary raw-frame directories.
- Use `scripts/assemble_delivery.py` to collect media and audit artifacts before delivery. The ZIP must include `job-state.json`, `prompts.json`, and `route.json` whenever those stages occurred.
- Refuse to mix new output with prior numbered files by default. Reuse an output directory only with an explicit `--overwrite`, which removes known generated artifacts but preserves unrelated files.

## QC report

`processing.json` should record source size, detected grid, frame count, fps, alpha method, per-cell alpha coverage, loop-end difference, warnings, and the exact output list. Warn when:

- layout confidence is below `0.75`;
- foreground touches a crop boundary;
- alpha coverage changes sharply across frames;
- first and last frames differ enough to cause a visible jump;
- a cell is empty or nearly full-frame foreground;
- an encoder cannot preserve alpha.

Visual identity and whether a motion feels natural remain human review items. Do not report them as machine-proven.

## Prompt-only delivery

When no video or local image-processing capability is available, deliver `static-prompt.json`, `tile-plan.json`, `prompts.json`, `route.json`, and `prompt-only.json` with `generated_video: false`. Do not create placeholder media or claim that generation started.
