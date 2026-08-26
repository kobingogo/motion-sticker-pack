# Motion Sticker Pack

[中文](README.md) · [MIT License](LICENSE)

> Upload a character image, pick a style and reactions, approve the static sheet, then get a sendable, packable set of looping transparent stickers.

`motion-sticker-pack` is an [Agent Skill](https://agentskills.io). After install, use it as a conversation: upload an image → choose a style → choose Emoji or a short description → **approve the static sheet** → generate video → split, matte, export WebP/GIF/PNG, and zip.

You do not need to run the Python scripts by hand, or understand FFmpeg or provider routing. The agent should follow [`SKILL.md`](SKILL.md) end to end.

```text
$motion-sticker-pack
```

## One-line install

```bash
npx skills add kobingogo/motion-sticker-pack -g -y
```

This installs the skill into the **user-level** directories of agents detected on this machine (Grok Build, Codex, Claude Code, Cursor, and others). Grok only:

```bash
npx skills add kobingogo/motion-sticker-pack -g -y -a grok
```

Update:

```bash
npx skills update motion-sticker-pack -g -y
```

## Status

The media pipeline is usable: grid detection, hash-bound static approval, sheet splitting, matting, Animated WebP, looping GIF, first-frame PNG, and ZIP are implemented and tested. This repository has verified three video routes:

```text
Local Grok Build (image_to_video)
        ↓ unavailable or failed
Direct xAI Videos API
        ↓ unavailable or failed
Local transform-local fallback motion
```

Stable reproduction on another agent depends on the work-directory and approval contracts, not on one successful manual run. The important rules:

- Static generation must use a **host tool that actually accepts the reference image**. Do not assume `image_edit` or `image_gen` exists.
- A generated sheet must be approved by the user. A user-supplied sheet uses `--source-type user-supplied` and must not be approved a second time.
- Every animation path (host native video, external provider, key poses, local transform) must run `manage_job_state.py verify` first.
- `probe` → `route` → `execute` must share the same `work/video-providers.json` and `work/video-task.json`.
- Independent stickers use `scripts/process_independent_stickers.py`. Do not invent a fake `1×1 layout.json` per file.
- `native-video` is the work mode; the provider driver name is `native-tool`. They are not two different routes.

Threat model, fixed issues, and remaining boundaries: [`docs/adversarial-audit.md`](docs/adversarial-audit.md).

## 30-second start

### 1. Install the Skill

One-line global install (auto-detects agents on this machine):

```bash
npx skills add kobingogo/motion-sticker-pack -g -y
```

Install to specific hosts only:

```bash
npx skills add kobingogo/motion-sticker-pack -g -y -a grok
npx skills add kobingogo/motion-sticker-pack -g -y -a grok -a codex -a claude-code
```

That copies the full skill, including `scripts/`, into paths such as `~/.grok/skills/motion-sticker-pack`, `~/.codex/skills/motion-sticker-pack`, and `~/.claude/skills/motion-sticker-pack`. Source: [github.com/kobingogo/motion-sticker-pack](https://github.com/kobingogo/motion-sticker-pack).

For local development you can still symlink a clone:

```bash
git clone https://github.com/kobingogo/motion-sticker-pack.git
ln -s "$PWD/motion-sticker-pack" ~/.grok/skills/motion-sticker-pack
ln -s "$PWD/motion-sticker-pack" ~/.codex/skills/motion-sticker-pack
ln -s "$PWD/motion-sticker-pack" ~/.claude/skills/motion-sticker-pack
```

Codex can use `$motion-sticker-pack`. Other hosts can say “use the motion-sticker-pack skill”.

### 2. Install local media dependencies

A full pack needs Python 3.10+, Pillow, NumPy, FFmpeg, and FFprobe:

```bash
python3 -m pip install -r requirements.txt
```

macOS: `brew install ffmpeg`. Ubuntu/Debian: `sudo apt update && sudo apt install ffmpeg`.

```bash
python3 -c "import PIL, numpy; print(PIL.__version__, numpy.__version__)"
ffmpeg -version && ffprobe -version
```

Verified with Python 3.10.12, Pillow 12.3.0, NumPy 2.2.6, and FFmpeg 8.1.2.

To use the bundled xAI / Kling / Seedance / Wan / FAL executors, also run `npm ci` at the skill root (Node 20+). Skip Node if you only probe local agent tools or use fully local animation.

### 3. Start the conversation

```text
$motion-sticker-pack
```

Upload a character reference, pick a style, and type Emoji or a short reaction list. If the host already has a callable image-to-video tool, you do not need an extra model.

On **Grok Build**, read [Privacy Opt in and ZDR](#grok-build-privacy-opt-in-and-zdr) first. Without Opt in, local `image_to_video` often fails with a ZDR/privacy error. That is not a prompt bug.

## Conversation flow

Use structured controls when the host has them; otherwise use a numbered list. The steps are the same:

```text
1. Invoke the skill
        ↓
2. Upload one character reference
        ↓
3. Choose a style (eight presets below, or custom)
        ↓
4. Choose Emoji, or type short reaction labels
        ↓
5. Compile the prompt and generate a static grid with a host tool that accepts that reference image
        ↓
6. Show the sheet and the detected grid
        ↓
   ┌─────────────┴─────────────┐
   ▼                           ▼
Approve, continue to video    Regenerate with requested changes
   │                           │
   │                           └── Discard the old approval and downstream artifacts, return to step 5
   ▼
7. verify approval hashes → generate the sheet video (or an agreed fallback)
        ↓
8. Split, matte, export WebP/GIF/PNG, assemble reports, zip
```

Style presets (aligned with the CLI and `references/style-presets.json`; **there is no `meme`**):

1. `3d` — 3D cartoon (default)
2. `hand-drawn`
3. `chibi`
4. `manga`
5. `pixel-art`
6. `realistic`
7. `cute`
8. `retro`
9. `custom` — a short style description

Typical conversation:

```text
User: $motion-sticker-pack

Agent: Please upload a character reference.

User: [image]

Agent: Choose a style: 1. 3D cartoon  2. Hand-drawn  3. Chibi  4. Manga
      5. Pixel art  6. Realistic  7. Cute  8. Retro  9. Custom

User: 3D

Agent: Type the Emoji or short reactions to include.

User: 🎸😍🥹😘🥰

Agent: [shows the static sheet]
Detected layout: 3 columns × 3 rows, 9 cells, confidence 0.99.
Choose:
- Approve, continue to video
- Regenerate, and tell me what to change

User: Approve, continue to video

Agent: [verify → pick a video capability → generate → split/matte → ZIP]
```

If the first message already has the image, style, and reactions, skip the intake questions and generate the static sheet. **A skill-generated sheet still needs explicit approval.** A user-uploaded finished sheet does not.

## What you can do with it

| You provide | The agent does |
|---|---|
| One character reference | Generate a static sheet → detect the grid → wait for approval → animate and pack |
| One finished static sheet | Detect the grid, `--source-type user-supplied`, no second approval |
| Several independent transparent stickers | `process_independent_stickers.py`, no fake contact sheet |
| One grid video | Extract a representative frame if needed, then split, matte, pack |
| Several independent videos | Skip grid splitting, post-process each clip |

This skill does not invent a character identity from scratch and is not a general NLE. The input should already contain a recognizable character.

Useful extras: reactions or Emoji, style, whether paid external APIs are allowed, local-only, layout preference, duration, fps. Unspecified values stay conservative: small motion, locked camera, loopable, 6 fps, transparent or a clean key color.

On Grok, `image_to_video` only accepts **6 or 10 seconds**. The workflow default is 6. Do not put 3 seconds into a task that will be sent to Grok.

## Grok Build privacy: Opt in and ZDR

Grok Build video tools are gated by account privacy. If you see `video tools are unavailable under ZDR`, check privacy settings first. Do not rewrite the prompt, and do not hand-edit `~/.grok` to fake a policy.

These are two different mechanisms.

### 1. Personal accounts: `/privacy` Opt in

The Grok CLI treats a `/privacy` **data-retention Opt out** like team ZDR for video tools, even when `authenticate.is_zdr` is still false. Official note: [Video Output Storage under ZDR](https://docs.x.ai/build/settings/zdr-video-storage) — *Video tools will be enabled if the privacy setting is off (`/privacy`).*

To use local `image_to_video` **without S3**:

1. Open a logged-in Grok Build session.
2. Run `/privacy` (the same control also appears under `/settings`).
3. Choose **Opt in**, allowing coding/session data retention.
4. Afterwards `coding_data_retention_opt_out` should be `false`.
5. Start a new turn, then generate video.

This repo's validation: after Opt in, Grok CLI `image_to_video` succeeded with no S3 bucket. The original `~/.grok` files were **not** edited; only the account privacy setting changed.

| `/privacy` choice | Internal state | Local `image_to_video` |
|---|---|---|
| **Opt in** (allow retention) | `coding_data_retention_opt_out = false`; official “privacy setting off” | Available, no S3 required |
| **Opt out** (refuse retention) | `coding_data_retention_opt_out = true`; treated like ZDR | Refused unless console-synced ZDR video storage is configured |

Opt in lets Grok Build retain related data under xAI's then-current policy. For stronger privacy, stay Opt out and use team ZDR storage or `xai-direct`. Changing `/privacy` may delete previously synced coding data; follow xAI's current wording.

### 2. Team Zero Data Retention (ZDR)

Under team ZDR, generated video must land in storage you own. Configure an S3-compatible bucket in the console so `[tools.zdr_video_output_s3]` is **synced into** `managed_config.toml`. Fields and steps: [xAI ZDR Video Storage](https://docs.x.ai/build/settings/zdr-video-storage).

Notes:

- Grok Build `image_to_video` has **no** `output.upload_url` argument. You cannot prompt the tool to upload to an arbitrary URL.
- Dropping an unsigned `managed_config.toml` on disk is not enough. Grok CLI 1.0.10 evicts that file when the server has no managed policy.
- The S3 endpoint must be reachable by xAI over HTTPS and should accept path-style URLs (`https://endpoint/bucket/key`).
- Restart Grok Build after the config changes.

### 3. The same account can still use the direct API

`scripts/xai_rest_video_adapter.py` (provider id `xai-direct`) calls the xAI Videos REST API and **does not** go through Grok Build `image_to_video`. So Grok Build can refuse video tools because of `/privacy` Opt out or team ZDR, while the direct API on the same account still succeeds.

Direct calls need `XAI_API_KEY`. If the API also requires user-owned storage, set `XAI_VIDEO_UPLOAD_URL` plus `XAI_VIDEO_LOCAL_OUTPUT_PATH` or `XAI_VIDEO_DOWNLOAD_URL`. Use `XAI_VIDEO_REQUEST_ID` to resume polling the same job without submitting or billing another generation.

By default the Grok Build adapter strips an ambient `XAI_API_KEY` so it cannot silently replace the grok.com login. Set `GROK_USE_XAI_API_KEY=1` only when that swap is intentional.

### 4. This is not an image or prompt failure

| Symptom | Check first |
|---|---|
| Grok Build: `video tools are unavailable under ZDR` | `/privacy` Opt in; for team accounts, console-synced S3 |
| Direct API works, Grok Build still fails | Expected. The two paths have different privacy/storage rules |
| A local `managed_config.toml` vanishes | The CLI deleted an unsigned file; sync from the console |
| Fully local, no upload | Say so in the request and use `transform-local` |

Implementations: [`scripts/grok_build_video_adapter.py`](scripts/grok_build_video_adapter.py), [`scripts/xai_rest_video_adapter.py`](scripts/xai_rest_video_adapter.py).

## How video capability is chosen

Unless you name a provider, the skill uses this order:

1. A **callable** image-to-video tool in the current session that accepts a reference image (work mode `native-video`; config driver `native-tool`)
2. Configured external providers that satisfy the task, by descending `priority`
3. If image generation is callable: key poses + local assembly (`keypose-local`)
4. If only Pillow/NumPy are available: whole-sticker affine loops (`transform-local`)
5. If none of the above: `prompt-only` — deliver prompts and the route audit, then **stop**. Do not claim a video was generated.

The shipped Grok example sets fallback to `transform-local`, so a no-video setup lands on local affine motion rather than key poses. For keypose, set `routing.fallback` to `keypose-local` and provide a real `runtime-tools.json`.

Text-to-video tools that cannot take a reference image do not satisfy this task.

Probe and route do not incur charges. Only an explicit numbered route attempt submits generation. Before the first paid external call, the agent must name the provider and warn that the request may be billed.

Bundled executable AI SDK adapters: xAI, Kling AI, ByteDance/Seedance, Alibaba/Wan, FAL. Google/Veo, Replicate, MiniMax, and similar platforms can use the same protocol, but they need a host-native tool or a `command` adapter.

## Copy-paste requests

### Full pack from a character image

```text
$motion-sticker-pack Make an animated sticker pack from the attached character.
Include 🎸😍🥹😘🥰 in a rounded 3D toy-sticker style.
Keep every motion small, independent, and loopable. No camera moves or cross-cell effects.
Prefer the current agent video tool. Deliver transparent WebP, GIF, PNG, and a ZIP.
```

Review the static sheet first. “Approve, continue to video” unlocks generation. “Regenerate” discards the previous approval, layout, and video plan.

### Animate an existing sheet

```text
$motion-sticker-pack Animate this sticker sheet.
This is the source I already chose. Do not generate a new sheet and do not ask me to approve it again.
Detect the real grid, then give each cell its own small motion.
```

### Process a grid video

```text
$motion-sticker-pack Split the attached video into independent animated stickers.
If there is no matching static sheet, extract one frame, detect the grid, then crop.
Export 6 fps transparent Animated WebP, GIF, first-frame PNG, and a ZIP.
```

### Independent stickers

```text
$motion-sticker-pack These images are independent transparent stickers. Do not assemble a contact sheet.
Animate each one as a looping sticker and pack them into one ZIP.
```

### Local only

```text
$motion-sticker-pack Use local capabilities only. Do not call any external API.
If there is no local video model, use lightweight local looping animation and tell me which fallback you used.
```

### Pin an external model

```text
$motion-sticker-pack Use my configured seedance-primary provider for video.
If it fails, try at most one more configured provider. Do not repeat paid requests.
```

## Optional: external video providers

Skip this when the host already has image-to-video.

```bash
cp assets/video-providers.example.json video-providers.json
```

Enable the providers you need. Store environment-variable **names** only, never secret values:

```json
{
  "id": "xai-direct",
  "driver": "command",
  "provider": "xai",
  "model": "grok-imagine-video",
  "enabled": true,
  "priority": 80,
  "credentials": {
    "env": ["XAI_API_KEY"]
  },
  "capabilities": ["image-to-video"]
}
```

```bash
export XAI_API_KEY='your-key'
npm ci
```

Tell the agent where the config file lives. Full fields and the adapter contract:

- [`assets/video-providers.example.json`](assets/video-providers.example.json)
- [`assets/video-task.example.json`](assets/video-task.example.json)
- [`references/video-providers.schema.json`](references/video-providers.schema.json)
- [`references/video-task.schema.json`](references/video-task.schema.json)
- [`references/runtime-routing.md`](references/runtime-routing.md)

For an arbitrary relay, write a `command` adapter that takes `--task` and `--output` as absolute paths and writes a normalized result JSON. This skill does not pretend that changing `baseURL` is enough for every vendor.

## Privacy, cost, and credentials

- For a fully local run, say “do not call any external API” in the request
- External video models receive the reference image and prompt, and may bill, including on retries
- Attempts are bounded; the skill does not retry forever
- Config files store environment-variable names only; child processes inherit a small runtime allowlist plus the selected provider's declared credential names
- Secrets must not appear in prompts, reports, command lines, or git
- Grok `/privacy` Opt in and team ZDR are account-level policies; see the section above

## What you get

```text
output/
├── 01.webp ... NN.webp
├── 01.gif  ... NN.gif
├── 01.png  ... NN.png
├── layout.json
├── job-state.json               # when static approval was required
├── prompts.json                 # when generation ran
├── route.json                   # when routing ran
├── processing.json
└── sticker-pack.zip
```

- `.webp`: looping Animated WebP with a fuller alpha channel
- `.gif`: looping GIF for chats that reject WebP; transparency is binary palette, not full alpha
- `.png`: transparent first frame
- `layout.json`: detected grid
- `job-state.json` / `prompts.json` / `route.json`: approval, prompt, and route audit, copied into the final directory and ZIP by `assemble_delivery.py`
- `processing.json`: size, fps, alpha, edge, and loop-quality notes

Files are numbered row-major. `NN` equals `detected_layout.count`, not the layout you originally asked for.

Do not trust a checkerboard that only looks transparent. Keep real alpha when present; otherwise use a uniform high-contrast key and remove only background-like color connected to the crop edge, so interior face or clothing color is not punched out.

## Why the grid is not hardcoded as 3×3

A requested layout is a preference. The model may return fewer cells, more cells, or a different arrangement. Everything downstream reads `detected_layout`:

- `3x3` = 3 columns, 3 rows, 9 cells
- `4x3` = 4 columns, 3 rows, 12 cells

If confidence is below `0.75`, inspect the overlay and confirm or `--override`. Do not crop blindly.

## FAQ

### The agent did not pick up the skill after install

Confirm the project is in the host skills directory and restart the session. You can also invoke `$motion-sticker-pack` or ask the agent to read `SKILL.md`.

### Do I have to configure a video model?

No. Use a local video tool when one is callable; otherwise key poses or `transform-local`.

### Grok says video tools are unavailable under ZDR

Run `/privacy` and **Opt in**. Team ZDR still needs console-synced S3. See [Grok Build privacy](#grok-build-privacy-opt-in-and-zdr). `xai-direct` on the same account may still work.

### The static sheet does not look like my character

Static generation must pass the original image into a host tool that accepts a reference. Text-only generation will invent a new character. Have the agent inspect the tool signature, then bind the reference path or attachment handle.

### Why isn't this the 3×3 I asked for?

The returned image is the source of truth. Check the overlay and `layout.json`.

### Characters affect neighboring cells

Cross-cell attention leak is common on whole-sheet video models. Reduce motion amplitude, or regenerate only the bad cells.

### Can Animated WebP be submitted to every chat app?

No. The generic pack includes WebP, GIF, and first-frame PNG. WeChat usually wants GIF; Telegram animated stickers want WebM; Discord wants APNG. Platform canvases (240 / 512) are still planned.

### The agent only returned prompts, no files

If the route is `prompt-only`, there is no video and no local image processing. That is a deliberate stop, not a half-built video. If Pillow, NumPy, and FFmpeg are available, `transform-local` should at least pack a ZIP.

### Matting ate part of the character

Use a key color farther from the character. Lower the threshold if edges go transparent; raise it slightly if background remains. Do not use an extreme threshold on a complex scene.

## Current limits

- Whole-sheet video can still leak motion across cells
- Grid detection targets even contact sheets; free layouts need a human check or `--override`
- `/privacy` Opt out or team ZDR disables Grok Build video tools until you Opt in or configure storage
- Bundled AI SDK executors cover xAI, Kling, ByteDance, Alibaba, and FAL; re-run the Node contract tests before upgrading those packages
- Key-pose mode has no optical flow or generative interpolation; local mode only applies light whole-sticker transforms
- The generic pack is not auto-converted to every chat app's submission spec
- Identity lock and motion quality still need a human look

## For maintainers and contributors

End users should not need these commands. When debugging providers or reusing scripts, treat `work/` as the only working directory. `probe` / `route` / `execute` must use the same config and task.

### Layout

```text
motion-sticker-pack/
├── SKILL.md
├── LICENSE
├── README.md / README.en.md
├── package.json / package-lock.json
├── requirements.txt
├── agents/openai.yaml
├── assets/                      # example configs and tile-plan template
├── references/                  # agent contracts (intake, prompt, routing, output)
├── scripts/
├── tests/
└── tests-node/
```

Root `process_emoji_grid.py` only forwards to `scripts/process_emoji_grid.py`.

### One work directory

After approval and a per-cell `tile-plan.json`:

```bash
python3 scripts/prepare_workflow.py \
  --work-dir work \
  --image "$PWD/static-sheet.png" \
  --layout "$PWD/layout.json" \
  --prompts "$PWD/prompts.json" \
  --state "$PWD/job-state.json" \
  --tile-plan "$PWD/tile-plan.json"

# Edit work/runtime-tools.json to match tools that are actually callable.
# Then always use the copies under work/:

python3 scripts/probe_video_capabilities.py \
  --config work/video-providers.json \
  --tool-manifest work/runtime-tools.json \
  --output work/capabilities.json

python3 scripts/route_video_provider.py \
  --config work/video-providers.json \
  --capabilities work/capabilities.json \
  --task work/video-task.json \
  --output work/route.json
```

`prepare_workflow.py` rewrites placeholder absolute paths in the example to this repo's `scripts/` directory. Do not point probe at `assets/video-providers.example.json` and execute at a different `video-providers.json`.

Before any animation:

```bash
python3 scripts/manage_job_state.py verify \
  --state work/job-state.json \
  --image work/static-sheet.png \
  --layout work/layout.json
```

User-supplied sheet:

```bash
python3 scripts/manage_job_state.py create \
  --image work/static-sheet.png \
  --layout work/layout.json \
  --source-type user-supplied \
  --output work/job-state.json
```

Do not `approve` a user-supplied state that is already `static-approved`.

Low-confidence override:

```bash
python3 scripts/inspect_sticker_sheet.py sheet.png \
  --override 4x3 \
  --output work/layout.json \
  --overlay work/layout-overlay-confirmed.png
```

If a grid video has no layout yet, extract a frame first:

```bash
ffmpeg -y -i grid.mp4 -frames:v 1 work/representative-frame.png
python3 scripts/inspect_sticker_sheet.py work/representative-frame.png \
  --output work/layout.json \
  --overlay work/layout-overlay.png
```

Independent stickers, local animation, and delivery assembly:

```bash
python3 scripts/process_independent_stickers.py stickers output --fps 6

python3 scripts/keyframe_fallback.py work/static-sheet.png output \
  --state work/job-state.json \
  --layout work/layout.json \
  --fps 6

python3 scripts/assemble_delivery.py \
  --media-dir output \
  --audit-dir work \
  --output delivered \
  --require-job-state \
  --require-prompts \
  --require-route

python3 scripts/assemble_prompt_only.py \
  --static-prompt work/static-prompt.json \
  --tile-plan work/tile-plan.json \
  --prompts work/prompts.json \
  --route work/route.json \
  --output prompt-only
```

Compile, approve, execute, and crop commands are listed under Included commands in [`SKILL.md`](SKILL.md). Before a contribution, run:

```bash
python3 -m pip install -r requirements.txt
npm ci
python3 -m unittest discover -s tests -v
npm test
npm audit --audit-level=high
```

Do not put live secrets, private media, or paid API responses in fixtures.

## Roadmap

- Relay adapter template
- Per-cell video and single-cell retry
- Optional interpolation, temporal alpha smoothing, dedicated video matting
- WeChat 240 GIF, Telegram WebM, Discord APNG canvases
- Size budgets, pack preview, visual QC

Real case write-ups are welcome: input, detected layout, route, failures, and fixes — not only the final frames.

## License

[MIT](LICENSE) © 2026 kobingogo
