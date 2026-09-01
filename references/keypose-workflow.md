# Keypose fallback workflow

`keypose-local` is the high-fidelity local fallback when image generation is
available but a video provider is not. It uses real per-sticker pose changes;
`keyframe_fallback.py` remains a lower-cost affine-motion fallback and must be
reported as such.

## 1. Compile a plan

Start with approved, numbered static cells (`01.png` … `NN.png`). The count is
inferred from the directory and may be any value from 1 to 48:

```bash
python3 scripts/compile_keypose_plan.py \
  --input-dir works/小黑猫/cells \
  --reactions '开心,惊讶,哭泣' \
  --output-dir works/小黑猫/keypose-plan \
  --manifest works/小黑猫/artifact-manifest.json \
  --workspace works/小黑猫
```

The command writes `keypose-plan.json` and one prompt per cell under
`prompts/`. The generated suggestions are semantic hints, not visual truth;
review them against the source cell before sending them to an image tool. A
reviewed JSON list can be supplied with `--motions-file`.

## 2. Validate generated pose sheets

Ask the image tool for one opaque, uniform `#00FF00` 2×2 PNG per sticker. Put
them in a numbered directory and run:

```bash
python3 scripts/prepare_keyposes.py \
  --source-cells works/小黑猫/cells \
  --pose-sheets works/小黑猫/keypose-sheets \
  --output-dir works/小黑猫/keyposes \
  --size 240 \
  --manifest works/小黑猫/artifact-manifest.json \
  --workspace works/小黑猫
```

The preparer rejects checkerboards/ambiguous backgrounds, empty cells, weak
2×2 gutters, and sheets whose action peak is effectively identical to the
approved source. The generated START quadrant is audited but discarded as the
loop anchor: `01-start.png` is always the normalized approved static cell.
`keypose-preparation.json` records input hashes, normalization, gutter
confidence, and per-pose differences.

## 3. Render and deliver

Use the resulting `keyposes/NN/01-start.png … 04-recovery.png` folders with
`render_keypose_pack.py --fps 8 --size 240 --manifest works/小黑猫/artifact-manifest.json`.
It emits transparent PNG, lossless Animated WebP, GIF, and a processing report;
the manifest records the approved source, every pose, outputs, report, and ZIP.
Finish with `assemble_delivery.py` so audit artifacts and media share the normal
V0.3 output contract.

Both new commands use the repository output transaction and reject an output
directory that contains or is contained by its input. Re-run with
`--overwrite` only when replacing a complete, disposable output directory.
