# Motion Sticker Pack

[中文](README.md) · [MIT License](LICENSE)

> In Codex, upload a character image or describe one in text, generate a real-alpha transparent sticker sheet with GPT-image-2, approve it, then pack looping animated stickers you can actually send.

`motion-sticker-pack` is a **Codex-first** [Agent Skill](https://agentskills.io). The recommended path is Codex + **GPT-image-2**, which can emit a sticker sheet with a real alpha channel. Grok Imagine and typical text-to-image / image-to-image models usually return opaque backgrounds; local color-key matting then has to guess, and hair, shadows, and translucent accents suffer.

After install, use it as a conversation: upload an image or describe a character → choose a style → choose Emoji or a short description → **approve the static sheet** → generate video → split, export WebP/GIF/PNG, and zip. You do not need to run the Python scripts by hand. The agent should follow [`SKILL.md`](SKILL.md) end to end.

```text
$motion-sticker-pack
```

## What's new in v0.3.0

- **Idempotent paid execution:** every route creates `attempt-ledger.json`; an attempt can be submitted only once, and an interrupted run becomes `uncertain` instead of being silently replayed.
- **xAI request resumption:** the request ID is atomically persisted immediately after submission; only explicit `--resume` continues that same request without a second paid generation.
- **Transactional outputs:** primary output paths keep a recovery journal and prior-directory backup until commit, so exceptions and abandoned runs do not publish partial replacements.
- **Hash lineage:** `artifact-manifest.json` records SHA-256 dependencies across approved inputs, routing, generated video, and delivery artifacts, and detects current-file tampering.
- **Execution preflight:** route reports now expose the selected provider, billable attempts, resume support, blockers, and the fact that remote quota and health remain unknown.

## What's new in v0.2.1

- Task-level provider selection supports an explicit preferred provider and ordered fallback chain without editing shared defaults.
- Each provider attempt now carries its own duration and resolution; xAI model selection comes from provider configuration instead of environment-variable overrides.
- Routes bind the image, layout, prompt, approval state, and production-settings hashes, so changed inputs invalidate stale routes.
- Returned videos are decoded locally to detect real alpha before key-background QC.
- Output/input overlap is rejected before overwrite cleanup, and non-3×3 prompts no longer contain nine-grid wording.

## What's new in v0.2.0

- **Two character-entry paths:** use a reference image or define a character in text; the text-only path generates the complete sheet directly.
- **Verifiable transparency:** request real alpha first, inspect the returned pixels locally, and use one `#00FF00` fallback only when that inspection fails.
- **More stable video processing:** provider-specific durations, a complete 6-second Grok output plus a 24-frame 3-second derivative, registration disabled by default for locked cameras, and hold-jitter metrics in the report.
- **No duplicate delivery trees:** keep one canonical source video and one `delivered/` directory; the final ZIP no longer contains a nested `3s/sticker-pack.zip`.
- **Stronger quality gates:** native-frame key-screen checks, instance-aware seam handling, stable canvases, decoded WebP/GIF validation, and GIF size budgets.

See [`RELEASE_NOTES.md`](RELEASE_NOTES.md) for the complete release notes and upgrade details.

## Example gallery

> These are real outputs from this repository. GIFs loop automatically; click any image to open the original file. Each case shows 3 selected reactions, while the linked folder contains the complete GIF, WebP, and PNG set.

<p align="center"><strong>🐈‍⬛ Black cat · 3D toy sticker</strong> · <a href="examples/black-cat/">View the complete 9-cell case →</a></p>
<p align="center">
  <a href="examples/black-cat/01.gif"><img src="examples/black-cat/01.gif" height="150" loading="lazy" alt="Black cat animated sticker: happy"></a>
  <a href="examples/black-cat/02.gif"><img src="examples/black-cat/02.gif" height="150" loading="lazy" alt="Black cat animated sticker: heart"></a>
  <a href="examples/black-cat/03.gif"><img src="examples/black-cat/03.gif" height="150" loading="lazy" alt="Black cat animated sticker: crying"></a>
</p>

<p align="center"><strong>👶 Baby · Cute character</strong> · <a href="examples/child/">View the complete case →</a></p>
<p align="center">
  <a href="examples/child/01.gif"><img src="examples/child/01.gif" height="150" loading="lazy" alt="Baby animated sticker: pouty"></a>
  <a href="examples/child/02.gif"><img src="examples/child/02.gif" height="150" loading="lazy" alt="Baby animated sticker: holding a heart"></a>
  <a href="examples/child/03.gif"><img src="examples/child/03.gif" height="150" loading="lazy" alt="Baby animated sticker: happy"></a>
</p>

<p align="center"><strong>👩 Girl in gold · Realistic portrait</strong> · <a href="examples/gold-dress-girl/">View the complete 9-cell case →</a></p>
<p align="center">
  <a href="examples/gold-dress-girl/01-dup.gif"><img src="examples/gold-dress-girl/01-dup.gif" height="150" loading="lazy" alt="Girl in gold animated sticker: waving"></a>
  <a href="examples/gold-dress-girl/02-dup.gif"><img src="examples/gold-dress-girl/02-dup.gif" height="150" loading="lazy" alt="Girl in gold animated sticker: heart gesture"></a>
  <a href="examples/gold-dress-girl/03-dup.gif"><img src="examples/gold-dress-girl/03-dup.gif" height="150" loading="lazy" alt="Girl in gold animated sticker: sad"></a>
</p>

<p align="center"><strong>🧔 Musk · 3D character reactions</strong> · <a href="examples/musk-3d/">View the complete 9-cell case →</a></p>
<p align="center">
  <a href="examples/musk-3d/01.gif"><img src="examples/musk-3d/01.gif" height="150" loading="lazy" alt="Musk animated sticker: arms open"></a>
  <a href="examples/musk-3d/02.gif"><img src="examples/musk-3d/02.gif" height="150" loading="lazy" alt="Musk animated sticker: surprised"></a>
  <a href="examples/musk-3d/03.gif"><img src="examples/musk-3d/03.gif" height="150" loading="lazy" alt="Musk animated sticker: angry"></a>
</p>

<p align="center"><strong>🇺🇸 Trump · Comic character reactions</strong> · <a href="examples/trump/">View the complete 9-cell case →</a></p>
<p align="center">
  <a href="examples/trump/01.gif"><img src="examples/trump/01.gif" height="150" loading="lazy" alt="Trump animated sticker: thumbs up"></a>
  <a href="examples/trump/02.gif"><img src="examples/trump/02.gif" height="150" loading="lazy" alt="Trump animated sticker: surprised"></a>
  <a href="examples/trump/03.gif"><img src="examples/trump/03.gif" height="150" loading="lazy" alt="Trump animated sticker: angry"></a>
</p>

## Why Codex

A sticker pack needs **real transparency**, not a checkerboard preview and not aggressive post-hoc cutout.

| Host / model | Static sticker sheet | Notes |
|---|---|---|
| **Codex + GPT-image-2** (recommended) | Can write a PNG sheet with real alpha | Identity lock, transparent plate, and later crops all follow this path |
| Grok Build / other common image models | Generally **no** real alpha | Key color plus local matting only; fringes and hair are fragile |
| A user-supplied transparent sheet or singles | No image model required | Any host can detect, animate, and pack |

Grok Build is still useful for **image-to-video** after the sheet is already transparent. It is not the source of a transparent static plate. Other agents (Claude Code, Cursor, and so on) can install this skill for post-processing; do not expect their default image models to produce a usable transparent sticker background.

## One-line install

Install for Codex (recommended):

```bash
npx skills add kobingogo/motion-sticker-pack -g -y -a codex
```

Auto-detect agents on this machine (still use Codex as the primary host):

```bash
npx skills add kobingogo/motion-sticker-pack -g -y
```

Also install for Grok if you want it for video:

```bash
npx skills add kobingogo/motion-sticker-pack -g -y -a codex -a grok
```

On Windows, add `--copy`. Update:

```bash
npx skills update motion-sticker-pack -g -y
```

After installing for Codex, the Skill lives at `~/.codex/skills/motion-sticker-pack`. For local development you can symlink a clone:

```bash
git clone https://github.com/kobingogo/motion-sticker-pack.git
ln -s "$PWD/motion-sticker-pack" ~/.codex/skills/motion-sticker-pack
```

## Getting started

After installing the Skill above, prepare the local media dependencies and start the conversation:

### 1. Install local media dependencies

A full pack needs Python 3.10+, Pillow, NumPy, FFmpeg, and FFprobe:

#### Let an agent configure everything in one pass (recommended)

After installing the Skill, paste the full instruction below into Codex or another terminal-capable agent. It tells the agent to locate the Skill, install only missing dependencies, and verify the result. It must not call an image or video provider, so this setup step does not incur generation charges.

```text
Configure the local runtime dependencies for the motion-sticker-pack Skill I just installed.

1. Locate the installed Skill root first; do not assume the current directory is the Skill root.
2. Check for Python 3.10+, pip, Pillow, NumPy, FFmpeg, and FFprobe.
3. Install only missing items. Use this Skill's requirements.txt for Python packages and an available package manager for system media tools.
4. If a system install needs sudo, administrator access, or a global environment change, show me the command first.
5. Only if I need the xAI, Kling, Seedance, Wan, or FAL executors, check for Node 22+ and run npm ci in the Skill root.
6. Print the installed versions of Python, Pillow, NumPy, FFmpeg, FFprobe, and optional Node/npm, then run the repository tests.
7. Do not call any image or video generation provider. Do not read or change API keys, Grok /privacy, or other account settings.

When finished, report what was installed, what was skipped, whether verification passed, and any permissions I still need to handle.
```

For local splitting, matting, and lightweight motion only, keep step 5 disabled; Node dependencies are unnecessary.

#### Manual installation

```bash
python3 -m pip install -r requirements.txt
```

macOS: `brew install ffmpeg`. Ubuntu/Debian: `sudo apt update && sudo apt install ffmpeg`.

```bash
python3 -c "import PIL, numpy; print(PIL.__version__, numpy.__version__)"
ffmpeg -version && ffprobe -version
```

Verified with Python 3.10.12, Pillow 12.3.0, NumPy 2.2.6, and FFmpeg 8.1.2.

To use the bundled xAI / Kling / Seedance / Wan / FAL executors, also run `npm ci` at the skill root (Node 22+). Skip Node if you only probe local agent tools or use fully local animation.

### 2. Start the conversation

```text
$motion-sticker-pack
```

In Codex, run `$motion-sticker-pack`, upload a character reference or describe the character in text, pick a style, and type Emoji or a short reaction list. A text-only request generates the complete sheet directly. For the static sheet, use **GPT-image-2**: pass `background: transparent` and `output_format: png` when the runtime exposes those fields, but still request real alpha in the prompt when it does not. Only local pixel inspection may select the one-shot `#00FF00` fallback.

If video goes to Grok Build, read [Privacy Opt in and ZDR](#grok-build-privacy-opt-in-and-zdr) first. Without Opt in, local `image_to_video` often fails with a ZDR/privacy error. That is not a prompt bug.

## Status

The Codex path is usable: GPT-image-2 transparent sheets, grid detection, hash-bound static approval, whole-sheet animation, splitting, Animated WebP, looping GIF, first-frame PNG, and ZIP. If the Codex session has no image-to-video tool, video can fall through to a configured external provider, Grok Build / xAI Videos, or local `transform-local`:

```text
Codex + GPT-image-2 transparent static sheet
        ↓ user approval
Host or external image-to-video
        ↓ unavailable or failed
Grok Build / xAI Videos API
        ↓ unavailable or failed
Local transform-local fallback motion
```

Stable reproduction on another agent depends on the work-directory and approval contracts, not on one successful manual run. The important rules:

- When generating a sheet from a character image, prefer callable **GPT-image-2** in Codex and ask for real alpha. Do not pretend other models already produced a transparent plate.
- Static generation must use a **host tool that actually accepts the reference image**. Do not assume `image_edit` or `image_gen` exists.
- A generated sheet must be approved by the user. A user-supplied sheet uses `--source-type user-supplied` and must not be approved a second time.
- Every animation path (host native video, external provider, key poses, local transform) must run `manage_job_state.py verify` first.
- Keep every generated artifact for one character under `works/<character-slug>/`. Do not write new job files to the skill root or a shared `work/` folder.
- `probe` → `route` → `execute` must share the same `video-providers.json` and `video-task.json` inside that character directory.
- Independent stickers use `scripts/process_independent_stickers.py`. Do not invent a fake `1×1 layout.json` per file.
- `native-video` is the work mode; the provider driver name is `native-tool`. They are not two different routes.

Threat model, fixed issues, and remaining boundaries: [`docs/adversarial-audit.md`](docs/adversarial-audit.md).

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
5. Compile the prompt; in Codex, generate a transparent static grid with GPT-image-2 (must accept the reference image and emit real alpha)
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
| One character reference | Codex + GPT-image-2 transparent sheet → detect the grid → wait for approval → animate and pack |
| One finished static sheet | Detect the grid, `--source-type user-supplied`, no second approval |
| Several independent transparent stickers | `process_independent_stickers.py`, no fake contact sheet |
| One grid video | Extract a representative frame if needed, then split, matte, pack |
| Several independent videos | Skip grid splitting, post-process each clip |

This skill can define a reusable character from text for one sticker-pack job, but it is not a general identity-management studio or a general NLE.

Useful extras: reactions or Emoji, style, whether paid external APIs are allowed, local-only, layout preference, duration, and fps. Defaults come from `assets/sticker-production.default.json`: Grok Build requests 6 seconds, xAI direct requests 3 seconds, and both export at 240×240, 8 fps, with a 192-color GIF ceiling. Grok keeps the complete 6-second result and derives a 3-second version from the first 24 sampled frames of that same source—no acceleration, reverse synthesis, or second paid generation. The direct API keeps its native 3-second result. Run the configured trial cell before the full pack. On Codex, default to a real transparent plate and fall back to a key color only after pixel validation shows that alpha is unusable.

In the currently tested Grok Build CLI, generation accepts **6 or 10 seconds** and rejects a 4-second request before submission; the official [xAI Videos API documentation](https://docs.x.ai/developers/model-capabilities/video/generation#duration) allows 1–15 seconds. Keep request duration provider-specific and stop on an unexpected returned duration.

## Grok Build privacy: Opt in and ZDR

Skip this section if you only generate the transparent sheet in Codex and send video to Codex or an external provider. The notes below apply only when **video** goes to Grok Build.

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
Use GPT-image-2 for a real-alpha transparent static sheet. Include 🎸😍🥹😘🥰 in a rounded 3D toy-sticker style.
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
```

The bundled AI SDK routes have executable defaults below. The model IDs match the SDK versions pinned in `package-lock.json`; re-check the model list for that region after dependency upgrades or region changes. The API key, endpoint, and model must belong to the same region.

| Provider | Default I2V model | Credential environment | `region` |
| --- | --- | --- | --- |
| Kling | `kling-v2.6-i2v` | `KLINGAI_API_KEY` | `global` |
| Seedance | `seedance-1-5-pro-251215` | `ARK_API_KEY` | `international` (BytePlus) or `china` (Volcengine Ark) |
| Wan | `wan2.6-i2v-flash` | `DASHSCOPE_API_KEY` or `ALIBABA_API_KEY` | `international` (Singapore) or `china` (Beijing) |
| FAL | `luma-dream-machine/ray-2/image-to-video` | `FAL_API_KEY` or `FAL_KEY` | `global` |

The Gateway explicitly passes the declared credential to the SDK, so Alibaba's common `DASHSCOPE_API_KEY` name and the AI SDK's default `ALIBABA_API_KEY` name both work. `region` maps only to official shared vendor endpoints. Use a `command` adapter for an arbitrary relay or workspace-specific domain so credentials cannot be redirected to an unconstrained URL.

`video-task.json` defaults to `max_retries: 0` to prevent one route from resubmitting a paid generation inside the SDK. `poll_interval_ms` only controls status checks for the same task. A probe proves Node, SDK, config, and credential-variable presence; it does not spend credits and does not prove remote quota or model access. Confirm those with one explicitly authorized execution.

Tell the agent where the config file lives. Full fields and the adapter contract:

- [`assets/video-providers.example.json`](assets/video-providers.example.json)
- [`assets/video-task.example.json`](assets/video-task.example.json)
- [`references/video-providers.schema.json`](references/video-providers.schema.json)
- [`references/video-task.schema.json`](references/video-task.schema.json)
- [`references/attempt-ledger.schema.json`](references/attempt-ledger.schema.json)
- [`references/artifact-manifest.schema.json`](references/artifact-manifest.schema.json)
- [`references/runtime-routing.md`](references/runtime-routing.md)

For an arbitrary relay, write a `command` adapter that takes `--task` and `--output` as absolute paths and writes a normalized result JSON. This skill does not pretend that changing `baseURL` is enough for every vendor.

The Kling / Seedance / Wan / FAL routes here are image-to-video (`.video()`) integrations for the animation stage. Static sticker sheets still come from a callable host image tool that accepts the exact reference image; this Gateway does not treat video providers as a generic still-image API.

## Privacy, cost, and credentials

- For a fully local run, say “do not call any external API” in the request
- External video models receive the reference image and prompt, and a later route attempt may incur another charge
- The same paid attempt is never replayed automatically; uncertain state requires explicit request resumption or a different route attempt
- Config files store environment-variable names only; child processes inherit a small runtime allowlist plus the selected provider's declared credential names
- Secrets must not appear in prompts, reports, command lines, or git
- Grok `/privacy` Opt in and team ZDR are account-level policies; see the section above

## What you get

```text
works/<character-slug>/
├── attempt-ledger.json          # paid-attempt state and immutable transition history
├── artifact-manifest.json       # artifact hashes and dependency lineage
├── raw-video/
│   └── <provider>.mp4           # one accepted canonical source video
└── delivered/
    ├── 01.webp ... NN.webp
    ├── 01.gif  ... NN.gif
    ├── 01.png  ... NN.png
    ├── 3s/                      # Grok: 24-frame derivative, no nested ZIP
    ├── layout.json
    ├── job-state.json           # when static approval was required
    ├── prompts.json             # when generation ran
    ├── route.json               # when routing ran
    ├── attempt-ledger.json
    ├── artifact-manifest.json
    ├── processing.json
    └── sticker-pack.zip
```

- `.webp`: looping Animated WebP with a fuller alpha channel
- `.gif`: looping GIF for chats that reject WebP; transparency is binary palette, not full alpha
- `.png`: transparent first frame
- `layout.json`: detected grid
- `job-state.json` / `prompts.json` / `route.json`: approval, prompt, and route audit, copied into the final directory and ZIP by `assemble_delivery.py`
- `attempt-ledger.json` / `artifact-manifest.json`: paid-attempt state and SHA-256 artifact lineage
- `processing.json`: size, fps, alpha, edge, hold-jitter, and loop-quality notes

`output/` is an encoding staging directory. After the final ZIP succeeds, `assemble_delivery.py --cleanup-media-dir` removes it so normal delivery leaves only `delivered/`. An accepted Grok attempt is promoted to the canonical filename rather than copied into a byte-identical duplicate; rejected attempts may remain for diagnosis.

Files are numbered row-major. `NN` equals `detected_layout.count`, not the layout you originally asked for.

Treat transparency as two layers. Do not mix them:

1. **Still sheet:** Codex + GPT-image-2 can emit a PNG plate with real alpha. That is this skill's main path. Other image models usually have no real alpha; “looks transparent” is not transparent.
2. **Video:** do not trust a model's checkerboard preview. Keep real alpha when the source frames already have it; otherwise use a uniform high-contrast key and remove only background-like color connected to the crop edge, so interior face or clothing color is not punched out.

## Why the grid is not hardcoded as 3×3

A requested layout is a preference. The model may return fewer cells, more cells, or a different arrangement. Everything downstream reads `detected_layout`:

- `3x3` = 3 columns, 3 rows, 9 cells
- `4x3` = 4 columns, 3 rows, 12 cells

If confidence is below `0.75`, inspect the overlay and confirm or `--override`. Do not crop blindly.

## FAQ

### The agent did not pick up the skill after install

Confirm the project is in the Codex skills directory (`~/.codex/skills/motion-sticker-pack`) and restart the session. Invoke `$motion-sticker-pack`, or ask the agent to read `SKILL.md`.

### Do I have to use Codex? Do I have to use GPT-image-2?

For a **new** transparent pack from a character image, yes: GPT-image-2 is currently the model that can reliably emit real alpha. If you already have a transparent sheet, or you accept color-key matting, other hosts can still run post-processing.

### Do I have to configure a video model?

No. Use a local video tool when one is callable; otherwise key poses or `transform-local`.

### Grok says video tools are unavailable under ZDR

Run `/privacy` and **Opt in**. Team ZDR still needs console-synced S3. See [Grok Build privacy](#grok-build-privacy-opt-in-and-zdr). `xai-direct` on the same account may still work.

### The static sheet does not look like my character

In Codex, pass the original image into GPT-image-2 (it must accept a reference). Text-only generation will invent a new character. Do not fall back to a model that cannot take a reference and cannot emit alpha.

### Why isn't this the 3×3 I asked for?

The returned image is the source of truth. Check the overlay and `layout.json`.

### Characters affect neighboring cells

Cross-cell attention leak is common on whole-sheet video models. Reduce motion amplitude, or regenerate only the bad cells.

### Can Animated WebP be submitted to every chat app?

No. The generic pack includes WebP, GIF, and first-frame PNG. WeChat usually wants GIF; Telegram animated stickers want WebM; Discord wants APNG. Platform canvases (240 / 512) are still planned.

### The agent only returned prompts, no files

If the route is `prompt-only`, there is no video and no local image processing. That is a deliberate stop, not a half-built video. If Pillow, NumPy, and FFmpeg are available, `transform-local` should at least pack a ZIP.

### Matting ate part of the character

If the sheet came from GPT-image-2 with real alpha, keep that source alpha; do not run an aggressive second color key. Only on an opaque plate, pick a key farther from the character: lower the threshold if edges go transparent, raise it slightly if background remains. Do not use an extreme threshold on a complex scene.

## Current limits

- The transparent-sheet path is Codex + GPT-image-2; other image models generally have no real alpha
- Whole-sheet video can still leak motion across cells
- Grid detection targets even contact sheets; free layouts need a human check or `--override`
- `/privacy` Opt out or team ZDR disables Grok Build video tools until you Opt in or configure storage
- Bundled AI SDK executors cover xAI, Kling, ByteDance, Alibaba, and FAL; re-run the Node contract tests before upgrading those packages
- Key-pose mode has no optical flow or generative interpolation; local mode only applies light whole-sticker transforms
- The generic pack is not auto-converted to every chat app's submission spec
- Identity lock and motion quality still need a human look

## For maintainers and contributors

End users should not need these commands. When debugging providers or reusing scripts, treat `works/<character-slug>/` as that character's only working directory. `probe` / `route` / `execute` must use the same config and task. Do not keep stacking files in a shared `work/` folder.

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
├── works/                       # per-character generated assets
├── tests/
└── tests-node/
```

Root `process_emoji_grid.py` only forwards to `scripts/process_emoji_grid.py`.

### Per-character work directory

Create the folder from the character name first (Chinese names are kept):

```bash
python3 scripts/character_workspace.py --name '小黑猫'
```

Write the static sheet, layout, approval, prompts, video, crops, and ZIP into the printed `work_dir`, for example `works/小黑猫/`. See [`works/README.md`](works/README.md).

After approval and a per-cell `tile-plan.json`:

```bash
python3 scripts/prepare_workflow.py \
  --character '小黑猫' \
  --image "$PWD/works/小黑猫/static-sheet.png" \
  --layout "$PWD/works/小黑猫/layout.json" \
  --prompts "$PWD/works/小黑猫/prompts.json" \
  --state "$PWD/works/小黑猫/job-state.json" \
  --tile-plan "$PWD/works/小黑猫/tile-plan.json"

python3 scripts/probe_video_capabilities.py \
  --config works/小黑猫/video-providers.json \
  --tool-manifest works/小黑猫/runtime-tools.json \
  --output works/小黑猫/capabilities.json

python3 scripts/route_video_provider.py \
  --config works/小黑猫/video-providers.json \
  --capabilities works/小黑猫/capabilities.json \
  --task works/小黑猫/video-task.json \
  --output works/小黑猫/route.json
```

`prepare_workflow.py` rewrites placeholder absolute paths in the example to this repo's `scripts/` directory. Do not point probe at `assets/video-providers.example.json` and execute at a different `video-providers.json`.

Before any animation:

```bash
python3 scripts/manage_job_state.py verify \
  --state works/小黑猫/job-state.json \
  --image works/小黑猫/static-sheet.png \
  --layout works/小黑猫/layout.json
```

User-supplied sheet:

```bash
python3 scripts/manage_job_state.py create \
  --image works/小黑猫/static-sheet.png \
  --layout works/小黑猫/layout.json \
  --source-type user-supplied \
  --output works/小黑猫/job-state.json
```

Do not `approve` a user-supplied state that is already `static-approved`.

Low-confidence override:

```bash
python3 scripts/inspect_sticker_sheet.py sheet.png \
  --override 4x3 \
  --output works/小黑猫/layout.json \
  --overlay works/小黑猫/layout-overlay-confirmed.png
```

If a grid video has no layout yet, extract a frame first:

```bash
ffmpeg -y -i grid.mp4 -frames:v 1 works/小黑猫/representative-frame.png
python3 scripts/inspect_sticker_sheet.py works/小黑猫/representative-frame.png \
  --output works/小黑猫/layout.json \
  --overlay works/小黑猫/layout-overlay.png
```

Independent stickers, local animation, and delivery assembly:

```bash
python3 scripts/process_independent_stickers.py stickers works/小黑猫/output --fps 6

python3 scripts/keyframe_fallback.py works/小黑猫/static-sheet.png works/小黑猫/output \
  --state works/小黑猫/job-state.json \
  --layout works/小黑猫/layout.json \
  --fps 6

python3 scripts/assemble_delivery.py \
  --media-dir works/小黑猫/output \
  --audit-dir works/小黑猫 \
  --output works/小黑猫/delivered \
  --require-job-state \
  --require-prompts \
  --require-route \
  --cleanup-media-dir

python3 scripts/assemble_prompt_only.py \
  --static-prompt works/小黑猫/static-prompt.json \
  --tile-plan works/小黑猫/tile-plan.json \
  --prompts works/小黑猫/prompts.json \
  --route works/小黑猫/route.json \
  --output works/小黑猫/prompt-only
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
- Platform-specific size profiles, animated pack preview, and richer visual QC

Real case write-ups are welcome: input, detected layout, route, failures, and fixes — not only the final frames.

## License

[MIT](LICENSE) © 2026 kobingogo
