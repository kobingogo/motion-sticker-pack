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
- `scripts/xai_rest_video_adapter.py` calls the xAI Videos REST API directly, polls boundedly, and downloads or copies the resulting MP4. Its model comes from the selected provider config; duration and resolution come from the task snapshot. For key-background tasks it deterministically composites transparent input pixels onto the task's exact key color before upload and appends the same immutable color contract to the prompt, preventing a provider from silently flattening transparency to black. `XAI_VIDEO_REQUEST_ID` resumes polling an existing request without submitting or charging for another generation.

For a general setup, assign priorities so the order is `grok-build-local` → `xai-direct` → `light-motion-local`. For a Grok-mandated job, use the shipped task defaults (`provider: grok-build-local`, `allow_fallback: false`) so a failed Grok generation cannot be replaced by a local animation. The former `transform-local` and `keyframe-local` values remain accepted as compatibility aliases but routing emits the canonical `light-motion-local` id.

## xAI Zero Data Retention

When an xAI team uses Zero Data Retention, every video generation needs user-owned output storage. The direct adapter accepts:

- required: `XAI_API_KEY`;
- model/duration/resolution: not environment variables; configure them in `video-providers.json` and `video-task.json`;
- ZDR upload/retrieval: `XAI_VIDEO_UPLOAD_URL` plus either `XAI_VIDEO_LOCAL_OUTPUT_PATH` or `XAI_VIDEO_DOWNLOAD_URL`;
- safe poll recovery: `XAI_VIDEO_REQUEST_ID`.

Grok Build does not expose `output.upload_url` in the `image_to_video` tool arguments. The CLI also treats Grok `/privacy` data-retention opt-out like ZDR for video tools, even when `authenticate.is_zdr` is false. Direct xAI Videos API calls on the same account may still succeed without `output.upload_url`; that does not mean Grok Build will call `image_to_video`.

Configure `[tools.zdr_video_output_s3]` as described in the xAI ZDR Video Storage documentation so the console syncs it into `managed_config.toml`. Grok CLI 1.0.10 evicts unsigned local `managed_config.toml` when the server has no managed policy, so a file that exists only on disk is not enough. The S3 endpoint must be reachable by xAI over HTTPS and should accept path-style URLs (`https://endpoint/bucket/key`). A local Grok invocation that returns “video tools are unavailable under ZDR” is an account/storage/privacy-policy failure, not an image or prompt failure.

## Selection contract

The router filters on all hard requirements, then orders:

1. available native tools;
2. available configured external routes by descending `priority`;
3. `keypose-local` when image generation plus Pillow and NumPy are available;
4. `light-motion-local` when only Pillow and NumPy are available. This is explicitly a zero-generation-cost affine effect, not synthesized character motion.

Missing `alpha-output` is not fatal when local post-processing is available and the task permits a key-color background. Camera lock and independent-cell behavior are prompt/QC requirements, not reliable provider capability claims.

An explicit provider selection is honored first. `provider_chain` is an ordered allow-list, not a priority hint: its first member is attempted first and only its later members may become external fallbacks. Fallback occurs only when `allow_fallback` is true. Respect `max_attempts`; selection does not authorize unbounded paid retries.

`scripts/execute_video_route.py` executes exactly one numbered route attempt. AI SDK attempts are delegated to `scripts/video_gateway.mjs`; command and HTTP-job attempts are delegated to their configured executable. A native-tool attempt is supplied with the host-generated video through `--native-video`, so the same result receipt, frame QC, and ledger path is used after the host call. The Gateway verifies the approved image/layout hashes before importing a provider or submitting a request. Non-alpha video results are checked on all native frames with `scripts/video_background_qc.py` before post-processing; a result that is not a uniform declared key color is rejected. Seam crossings are recorded for downstream recovery and do not trigger a paid regeneration. Grok requires `max_retries: 0` per execution. The task output-size limit is applied during download, not only after the response has already been loaded into memory.

## Attempt Ledger and paid-call recovery

Routing creates `attempt-ledger.json` beside the route (or at `video-task.json.attempt_ledger_file`). Each numbered attempt begins as `planned`; claiming it records `running` before the adapter starts. A first execution is claimed once; a new execution after failure requires an explicit retry approval. A crash with no provider request ID reconciles to `uncertain`. A crash after xAI submission reconciles to `submitted` because the direct adapter atomically persists the request ID immediately after the one POST.

The live ledger is intentionally not hash-recorded at routing time because execution must mutate it. Each terminal attempt instead writes an immutable content-addressed ledger snapshot and records that snapshot in `artifact-manifest.json`, so later numbered attempts cannot invalidate earlier lineage.

Never delete or edit the ledger to make an attempt runnable again. A terminal `failed`, `rejected`, or `uncertain` attempt can be followed by a new execution only after the user actively requests it and the agent creates a matching retry approval with `scripts/manage_job_state.py approve-video-retry`; invoke the executor with `--retry-approval <video-retry-approval.json>`. A `submitted` or `uncertain` xAI attempt with a request ID may use `execute_video_route.py --resume` to poll the original request; this is distinct from a new retry and never submits another generation. Other providers are intentionally not marked resumable until their adapters can prove equivalent request-ID semantics. A succeeded attempt is idempotent only while both its result path and recorded SHA-256 still match.

When approved inputs intentionally change and a new route is required, use `route_video_provider.py --archive-existing-ledger`. The old ledger and any request-progress files are moved to timestamped sibling audit files before the new route is published; they are never deleted or reset in place.

The route `preflight` object is non-billable and must be reviewed before external execution. It reports billable attempt numbers, whether charge authorization is required, resume support, and local blockers. `cost_estimate` remains unknown because this repository does not query account-specific price, quota, or service health.

## Adapter result contract

For `command` adapters, the gateway appends `--task <absolute-task-json> --output <absolute-result-json>` to the configured command array. The task file uses this minimum request contract:

```json
{
  "operation": "image-to-video",
  "input_image": "/absolute/path/to/sheet.png",
  "prompt_file": "/absolute/path/to/prompts.json",
  "duration_seconds": 6,
  "provider_duration_seconds": {
    "grok-build-local": 6,
    "xai-direct": 3
  },
  "provider_chain": ["xai-direct", "grok-build-local"],
  "provider_execution": {
    "xai-direct": {"duration_seconds": 3, "resolution": "720p"},
    "grok-build-local": {"duration_seconds": 6, "resolution": "720p"}
  },
  "production_settings_file": "/absolute/path/to/sticker-production.json",
  "attempt_ledger_file": "/absolute/path/to/attempt-ledger.json",
  "artifact_manifest_file": "/absolute/path/to/artifact-manifest.json",
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
