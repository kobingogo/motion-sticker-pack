# Prompt contract

Use this reference when generating a static sheet, optimizing an image-to-video prompt, or deciding motion per cell.

## One layout truth

The requested layout and returned layout are different fields:

```json
{
  "requested_layout": {"columns": 3, "rows": 3},
  "detected_layout": {"columns": 4, "rows": 3, "count": 12, "confidence": 0.93}
}
```

Before image generation, wording such as “prefer a 3 columns × 3 rows grid” is allowed. After generation, never repeat that preference as fact. Inspect the returned image and compile the video prompt from `detected_layout`.

## Static illustrated-card sheet

Collect the optional image or text-defined character, style, and Emoji/text reactions according to [intake-and-approval.md](intake-and-approval.md), then compile them with `scripts/compile_static_prompt.py`. Preserve the user's character identity and requested symbols. With no reference image, generate the complete grid directly and do not create a standalone character image first. Ask for one square or explicitly sized transparent illustrated-card sheet with wide, empty gutters and subtle local background accents inside each cell when they support the reaction. White sticker outlines are optional: default to no white border, but honor an explicit `white` outline choice or “白边贴纸风” wording with a narrow, uniform, continuous outline that stays inside each cell and does not create alpha fringe. For a bare `3D` choice, allow the model to choose either one coherent 3D animation treatment or one coherent 3D realistic-human treatment. If the user names a 3D sub-style, treat it as a hard style constraint and do not let generic “public figure” or “sticker” priors override it. Use rounded toy-like geometry, polished materials, soft studio lighting, and subtle ambient occlusion only when the selected 3D treatment calls for them.

Treat `image_generation_request.arguments` as the provider request contract. Use a transparent-first call for reference-image and text-only generation alike, passing `background: transparent` and `output_format: png` when the runtime tool exposes them. Omission for an older schema must be recorded together with the prepared opaque fallback, but omission does not imply that prompt-driven transparency is unavailable: the first call must still request real Alpha. Inspect the returned pixels. A visible checkerboard is an opaque two-tone preview background, never proof of transparency: reject it and use the fallback call instead of attempting to matte the checkerboard.

Each cell must contain one complete reaction with safe padding. Short reaction text is an intake preference: default to avoiding it, allow the user to opt in, and never reject a sheet merely because the model added text. Existing text is part of the reviewed source and should be preserved in later animation. Small semantic decorative accents are encouraged when they clarify the reaction or match the selected style—for example hearts, music notes, sparkles, tears, blush marks, sweat drops, stars, or motion lines—but use them selectively and keep them inside the cell. Avoid adding new captions during animation and avoid objects crossing gutters. A transparent result is preferred; a single clean, uniform, high-contrast key color is acceptable only as the explicit fallback when real alpha is unavailable. For this workflow the fallback is exact `#00FF00` unless the configured subject-specific key says otherwise. The key must contrast with every character; do not use black for a dark subject or white for a light one.

Do not claim an exact returned count until the image is inspected.

After generation, stop at the static-review gate. The generated sheet must be explicitly approved before it can become a video source.

## Grid-video prompt

Compile from the actual image and a per-cell motion plan. Include:

- exact `columns × rows` and total cell count from `detected_layout`;
- a completely fixed camera and unchanged canvas ratio;
- identity, proportions, color, clothing, facial-feature, and composition locks;
- one small, independent, loopable action for every numbered cell;
- explicit prohibition of global motion, cross-cell motion, new content, borders, scenery, simulated checkerboards, and camera moves;
- return-to-start behavior or another declared loop strategy;
- real alpha when supported, otherwise the selected uniform key color that contrasts with the character.

Describe each cell in row-major order. Motions must be inferred from what is actually visible: e.g. a guitar may be strummed, a kiss may lean forward slightly, and teary eyes may blink once. Do not invent a prop merely because an emoji appeared in the original request.

For a single Grok grid video, use the motion timeline from the job's `sticker-production.json`: by default hold the start pose to 0.3 seconds, complete one small in-place action by 1.8 seconds, return by 2.6 seconds, and hold through 3 seconds. For a 6-second Grok request, keep holding that start pose for the remaining time and do not repeat the action. This encourages a clean first 3 seconds while retaining a complete 6-second source. Lock the camera, body center, and foot baseline. Keep the exact configured `#00FF00` plate unchanged in every frame. These instructions reduce risk but never replace native-frame full-frame matting, instance assignment, duration-profile sampling, short-variant endpoint auditing, and encoded-output QC.

The Grok command adapter uses a compact prompt derived from `tile_plan`, rather than sending the full verbose prompt plus repeated operational prose. Preserve one concise action per detected cell and the hard output rules, and keep the final UTF-8 instruction under 3,800 bytes (below Grok's 4,096-byte CLI limit). Validate the byte budget before making the single provider call.

## Per-cell motion plan

Prefer a small JSON plan over a long free-form paragraph:

```json
{
  "tiles": [
    {"id": "01", "motion": "lightly strum the existing guitar once", "loop": "return-to-start", "amplitude": "small"},
    {"id": "02", "motion": "blink once and lift the cheeks slightly", "loop": "return-to-start", "amplitude": "small"}
  ]
}
```

If a motion would cross a cell boundary, reduce its amplitude or replace it. Do not let all cells share one generic bounce unless the source genuinely calls for that.

## Negative constraints

The following concepts belong in the negative prompt or strict constraints:

`camera motion, zoom, pan, tilt, roll, shake, global animation, synchronized board movement, cross-cell interaction, layout change, extra character, extra limb, duplicate prop, new caption, border, scene, floor, gradient, shadow backdrop, checkerboard transparency, white fringe, black fringe, dirty semi-transparent edge`.

## Key-pose fallback prompt

Use this only when image generation is callable but video generation is not. Generate each sticker independently as 3–5 ordered poses: start, anticipation, action peak, recovery, and optionally an explicit return pose. Keep one canvas size, camera, identity, clothing, color, prop inventory, lighting, and subject scale across all poses. Use real transparency or one declared key color. Do not include text or interpolate multiple characters into one frame.

Store the resulting transparent PNGs as:

```text
keyposes/
├── 01/01-start.png 02-anticipation.png 03-peak.png 04-recovery.png
├── 02/01-start.png 02-anticipation.png 03-peak.png 04-recovery.png
└── ...
```

Then run `scripts/render_keypose_pack.py`. This gives actual pose changes but deterministic stepped timing. Optical-flow or model-based interpolation is an optional enhancement and must not be described as present when it was not run.

For the validated local preparation path, first compile per-cell prompts with
`scripts/compile_keypose_plan.py`, then run
`scripts/prepare_keyposes.py --source-cells <cells> --pose-sheets <sheets> --output-dir <keyposes>`.
The preparer uses the approved static cell as the exact START frame and rejects
ambiguous backgrounds, weak 2×2 gutters, empty cells, and an action peak that
does not materially differ from the source. See
[references/keypose-workflow.md](keypose-workflow.md).
