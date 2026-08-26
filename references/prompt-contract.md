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

## Static sticker sheet

Collect the image, style, and Emoji/text reactions according to [intake-and-approval.md](intake-and-approval.md), then compile them with `scripts/compile_static_prompt.py`. Preserve the user's character identity and requested symbols. Ask for one square or explicitly sized transparent sheet with wide, empty gutters. Use rounded toy-like geometry, polished materials, soft studio lighting, and subtle ambient occlusion only when the user selects the 3D toy style.

Each cell must contain one complete reaction with safe padding. Small semantic decorative accents are encouraged when they clarify the reaction or match the selected style—for example hearts, music notes, sparkles, tears, blush marks, sweat drops, stars, or motion lines—but use them selectively and keep them inside the cell. Avoid captions and avoid objects crossing gutters. A transparent result is preferred; a single clean key color is acceptable when real alpha is unavailable.

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
- real alpha when supported, otherwise the selected uniform key color.

Describe each cell in row-major order. Motions must be inferred from what is actually visible: e.g. a guitar may be strummed, a kiss may lean forward slightly, and teary eyes may blink once. Do not invent a prop merely because an emoji appeared in the original request.

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

`camera motion, zoom, pan, tilt, roll, shake, global animation, synchronized board movement, cross-cell interaction, layout change, extra character, extra limb, duplicate prop, text, caption, border, scene, floor, gradient, shadow backdrop, checkerboard transparency, white fringe, black fringe, dirty semi-transparent edge`.

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
