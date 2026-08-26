# Runtime discovery and provider routing

Read this reference when deciding how the static image will be animated.

## Discovery has two layers

1. Inspect tools and skills that are actually callable in the current Agent runtime. A directory or installed package alone is not proof that a video tool can be invoked. Record callable native tools in `runtime-tools.json`, including the exact tool id, whether it accepts a reference image, whether it generates video, and whether the call may incur a charge.
2. Probe local executables, configured providers, and credential-variable presence with `scripts/probe_video_capabilities.py`. The script reports only whether an environment variable is set, never its value.

A native tool qualifies for the first route only when it accepts a local/reference image and can generate video. Text-to-video alone does not satisfy an image-to-video task.

## Provider classes

- `native-tool`: a callable Agent/MCP/Skill video operation. Highest default precedence.
- `ai-sdk`: a Vercel AI SDK video Provider such as Kling, ByteDance/Seedance, Alibaba/Wan, Google/Veo, Google Vertex/Veo, xAI/Grok Imagine, FAL, Replicate, MiniMax, or Black Forest Labs.
- `command`: a local adapter executable. It receives a task JSON path and must produce a result JSON path; use this for arbitrary vendor SDKs or API relays.
- `http-job`: an async HTTP adapter configured by an implementation outside this Skill. Routing can select it, but a matching adapter must exist before execution.

Provider support is capability-based, not name-based. Record capabilities such as `image-to-video`, `text-to-video`, `video-edit`, `first-last-frame`, `multi-reference`, and `alpha-output` per configured model. Model versions under one platform may differ.

This repository bundles executable AI SDK adapters for xAI, Kling AI, ByteDance, Alibaba, and FAL. They require `npm install` in the Skill directory and their separate `@ai-sdk/*` packages. Other platforms must use a callable native tool or a `command` adapter until a matching executor is added.

The bundled AI SDK stack requires Node 22 or newer. Provider configuration can select only constrained official regions: ByteDance `international` (BytePlus default) or `china` (Volcengine Ark), Alibaba `international` (Singapore default) or `china` (Beijing), and `global` for xAI/Kling/FAL. Arbitrary base URLs are intentionally not accepted because the selected provider receives a credential.

The Gateway explicitly maps declared environment variables into provider factories. Accepted authentication sets are: `XAI_API_KEY`; `KLINGAI_API_KEY` or the legacy `KLINGAI_ACCESS_KEY` + `KLINGAI_SECRET_KEY`; `ARK_API_KEY`; `DASHSCOPE_API_KEY` or `ALIBABA_API_KEY`; and `FAL_API_KEY` or `FAL_KEY`. Configuration validation rejects unrelated names before execution.

It also includes two command adapters for Grok Imagine:

- `scripts/grok_build_video_adapter.py` launches the logged-in local Grok Build CLI and instructs its internal `image_to_video` tool exactly once. Current Grok CLI flags are `--always-approve` and `--permission-mode bypassPermissions`; `--yolo` is not portable across releases. The adapter pins `--leader-socket` under `GROK_HOME` so a parent Grok/Codex session cannot reuse another leader's config. By default it removes `XAI_API_KEY` so an ambient key cannot silently replace the grok.com login. Set `GROK_USE_XAI_API_KEY=1` only when that behavior is intentional. Optional `GROK_DEBUG_FILE` / `GROK_LOG_FILE` capture CLI diagnostics without putting secrets in the result JSON.
- `scripts/xai_rest_video_adapter.py` calls the xAI Videos REST API directly, polls boundedly, and downloads or copies the resulting MP4. `XAI_VIDEO_REQUEST_ID` resumes polling an existing request without submitting or charging for another generation.

For the standard three-tier setup, assign priorities so the order is `grok-build-local` → `xai-direct` → `transform-local`. The shipped example demonstrates that order.

## xAI Zero Data Retention

When an xAI team uses Zero Data Retention, every video generation needs user-owned output storage. The direct adapter accepts:

- required: `XAI_API_KEY`;
- optional production model/settings: `XAI_VIDEO_MODEL`, `XAI_VIDEO_RESOLUTION`;
- ZDR upload/retrieval: `XAI_VIDEO_UPLOAD_URL` plus either `XAI_VIDEO_LOCAL_OUTPUT_PATH` or `XAI_VIDEO_DOWNLOAD_URL`;
- safe poll recovery: `XAI_VIDEO_REQUEST_ID`.

Grok Build does not expose `output.upload_url` in the `image_to_video` tool arguments. The CLI also treats Grok `/privacy` data-retention opt-out like ZDR for video tools, even when `authenticate.is_zdr` is false. Direct xAI Videos API calls on the same account may still succeed without `output.upload_url`; that does not mean Grok Build will call `image_to_video`.

Configure `[tools.zdr_video_output_s3]` as described in the xAI ZDR Video Storage documentation so the console syncs it into `managed_config.toml`. Grok CLI 1.0.10 evicts unsigned local `managed_config.toml` when the server has no managed policy, so a file that exists only on disk is not enough. The S3 endpoint must be reachable by xAI over HTTPS and should accept path-style URLs (`https://endpoint/bucket/key`). A local Grok invocation that returns “video tools are unavailable under ZDR” is an account/storage/privacy-policy failure, not an image or prompt failure.

## Selection contract

The router filters on all hard requirements, then orders:

1. available native tools;
2. available configured external routes by descending `priority`;
3. `keypose-local` when image generation plus Pillow and NumPy are available;
4. `transform-local` when only Pillow and NumPy are available.

Missing `alpha-output` is not fatal when local post-processing is available and the task permits a key-color background. Camera lock and independent-cell behavior are prompt/QC requirements, not reliable provider capability claims.

An explicit provider selection is honored first. Fallback occurs only when `allow_fallback` is true. Respect `max_attempts`; selection does not authorize unbounded paid retries.

`scripts/execute_video_route.py` executes exactly one numbered attempt. AI SDK attempts are delegated to `scripts/video_gateway.mjs`; command and HTTP-job attempts are delegated to their configured executable. The Gateway verifies the approved image/layout hashes before importing a provider or submitting a request. AI SDK retries default to zero so one route means one paid submission attempt, while bounded status polling continues for that same request. The task output-size limit is applied during download, not only after the response has already been loaded into memory.

## Adapter result contract

For `command` adapters, the gateway appends `--task <absolute-task-json> --output <absolute-result-json>` to the configured command array. The task file uses this minimum request contract:

```json
{
  "operation": "image-to-video",
  "input_image": "/absolute/path/to/sheet.png",
  "prompt_file": "/absolute/path/to/prompts.json",
  "duration_seconds": 5,
  "aspect_ratio": "source",
  "output_directory": "/absolute/path/to/raw"
}
```

The adapter may translate this into any vendor or relay API. It should poll boundedly and then normalize its result to:

```json
{
  "status": "succeeded",
  "provider": "kling-primary",
  "model": "configured-model-id",
  "output": "/absolute/path/to/video.mp4",
  "has_alpha": false,
  "background_key": "#00FF00",
  "request_id": "redacted-or-safe-id"
}
```

Do not include API keys, signed URLs that should remain private, or full vendor request bodies in persisted reports.

The executor starts adapters without a shell and with a restricted environment: standard runtime/network variables plus only names declared in `credentials.env` or `credentials.optional_env`. Required `env` names participate in availability checks; `optional_env` names are passed only when present. An adapter must declare every variable it needs and must write operational detail to its result file without including secret values. Adapter failures include a redacted diagnostic tail instead of suppressing all output.
