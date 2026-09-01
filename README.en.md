# Motion Sticker Pack

[![ci](https://github.com/kobingogo/motion-sticker-pack/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kobingogo/motion-sticker-pack/actions/workflows/ci.yml)
[中文](README.md) · [MIT](LICENSE) · [Release notes](RELEASE_NOTES.md)

Turn one character image—or a text-defined character—into a reviewed, auditable, looping animated sticker pack.

`motion-sticker-pack` is an Agent Skill for Codex. The default delivery contains transparent PNG, lossless Animated WebP, compatibility GIF, processing reports, and one ZIP. Normal use is conversational; users do not need to run Python commands.

## Start in 30 seconds

Install the Skill, attach a character image, and send:

```text
$motion-sticker-pack
Create a 3×3 animated sticker pack from the attached character.
Use soft-plush. Reactions: happy, love, upset, surprised, kiss, thanks, cheer, sleepy, like.
Show me the static sheet first. Continue only after I approve it.
Keep every motion small, independent, and loopable.
```

A text description works too. The workflow generates the complete sheet directly instead of creating an unnecessary intermediate character image.

The interaction is:

1. Provide an image or character description.
2. Choose a style and reactions.
3. Review the detected static grid.
4. Explicitly approve it.
5. Use the best available motion route.
6. Download WebP/GIF/PNG and ZIP.

## Three motion tiers

| Tier | Route | Result and cost |
|---|---|---|
| AI video | `native-video`, Grok, xAI, Kling, Seedance, Wan, FAL | Articulated face/body motion; may incur provider cost |
| Real key poses | `keypose-local` | Image generation creates anticipation/peak/recovery poses; local deterministic assembly |
| Light motion | `light-motion-local` | Zero generation cost; small affine loops only, with no articulated-motion claim |

`transform-local` and `keyframe-local` remain accepted as deprecated configuration aliases. New routes emit `light-motion-local`.

When no video or local image processing is callable, `prompt-only` delivers prompts and audit files without pretending media was generated.

## 13 evidence-backed styles

The selector exposes only styles backed by a real nine-cell processed run. Every style includes a 240×240 static PNG, a real animated GIF, layout, source route, and processing report.

| ID | Style |
|---|---|
| `3d` | Coherent 3D, with explicit animation/realistic sub-style support |
| `realistic` | Cinematic realistic |
| `hand-drawn` | Warm hand-drawn |
| `chibi` | Iridescent chibi |
| `manga` | Manga/cel |
| `pixel-art` | Refined pixel art |
| `cute` | Soft plush |
| `caricature-3d` | Exaggerated 3D portrait |
| `fashion-realistic` | Fashion realistic |
| `mascot-toy` | Product mascot toy |
| `clay-cute` | Soft clay animal |
| `fantasy-plush` | Fantasy plush |
| `kawaii-anime` | Kawaii anime |

<p>
  <img src="gallery/styles/plush-toy/motion.gif" width="120" alt="Verified 3D plush motion">
  <img src="gallery/styles/cinematic-realistic/motion.gif" width="120" alt="Verified cinematic realistic motion">
  <img src="gallery/styles/iridescent-chibi/motion.gif" width="120" alt="Verified iridescent chibi motion">
  <img src="gallery/styles/pixel-art/motion.gif" width="120" alt="Verified pixel art motion">
  <img src="gallery/styles/manga-cel/motion.gif" width="120" alt="Verified manga motion">
</p>

```bash
python3 scripts/style_selector.py --format markdown
python3 scripts/style_selector.py --style clay-cute
python3 scripts/style_selector.py --verify-only
```

See [gallery/](gallery/README.md) for compact evidence. The complete legacy packs moved to a [GitHub Release asset](https://github.com/kobingogo/motion-sticker-pack/releases/download/v0.2.0/motion-sticker-pack-legacy-gallery-2026-09.zip).

## Static sheets and transparency

Prefer an image tool such as GPT-image-2 that can return real Alpha. Every static request tries a transparent RGBA PNG first. The single-color fallback is eligible only after local pixel validation rejects the transparent attempt.

- A checkerboard is a visible background, not transparency.
- The requested grid is not trusted as returned truth; the image is detected again.
- Layout confidence below 0.75 requires human confirmation.
- Changing one byte of the approved image or layout invalidates the approval hash.

## Automatic screen selection

Grok retains a strict `#00FF00` contract.

Non-Grok routes score green, blue, magenta, and cyan against foreground pixels and choose the least conflicting screen unless `--key-color` is explicit. Candidate scores, provider-specific colors, and deterministically composited inputs are recorded in `video-task.json` and `artifact-manifest.json`.

## Unified output

All new local routes default to:

- 240×240;
- 8fps;
- lossless Animated WebP as the preferred format;
- GIF for compatibility;
- PNG as the clean first frame;
- row-major numbering from the detected layout;
- one canonical `delivered/` directory and one `sticker-pack.zip`.

```text
works/<character-slug>/delivered/
├── 01.webp … NN.webp
├── 01.gif  … NN.gif
├── 01.png  … NN.png
├── preview.png
├── layout.json
├── processing.json
├── job-state.json
├── prompts.json
├── route.json
├── attempt-ledger.json
├── artifact-manifest.json
└── sticker-pack.zip
```

The normative package and QC rules are in the [output contract](references/output-contract.md).

## Safety and audit

- Static approval is SHA-256-bound.
- A billable attempt can be submitted once; interrupted state is never silently replayed.
- xAI request ids can resume the same remote job.
- Output directories use recoverable transactions and reject input/output overlap.
- `artifact-manifest.json` records lineage across sheets, prompts, poses, routes, video, and delivery.
- Every native video frame is checked for screen, Alpha, cross-cell instances, and encoded-output integrity.

Read [Routing and audit](docs/advanced/routing-and-audit.md) for details.

## Install

```bash
git clone https://github.com/kobingogo/motion-sticker-pack.git
cd motion-sticker-pack
python3 -m pip install -r requirements.txt
npm ci --ignore-scripts
```

Install the directory as a Codex Skill or reference it using your Codex environment's Skill installation flow. The agent-facing contract is [SKILL.md](SKILL.md).

## Advanced documentation

- [Providers, credentials, and ZDR](docs/advanced/providers-and-zdr.md)
- [Routing, ledger, and artifact manifest](docs/advanced/routing-and-audit.md)
- [Complete CLI reference](docs/advanced/cli-reference.md)
- [Media policy, release gate, and history-slimming evaluation](docs/advanced/repository-maintenance.md)
- [Prompt contract](references/prompt-contract.md)
- [Keypose workflow](references/keypose-workflow.md)
- [Adversarial audit](docs/adversarial-audit.md)

## Development

```bash
python3 -m unittest discover -s tests -v
npm test
python3 scripts/style_selector.py --verify-only
python3 scripts/check_repository_policy.py
```

CI runs on Python 3.10/3.12 and Node 22. Official versions are created only by the [release workflow](.github/workflows/release.yml), which tags verified `main` after the complete test and policy suite passes.

## License

MIT. Users remain responsible for rights to character likenesses, trademarks, and generated media.
