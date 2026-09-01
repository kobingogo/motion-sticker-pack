# Providers and ZDR

## Route families

The workflow separates three quality/cost levels:

1. **AI video** — a callable image-to-video tool or configured provider creates articulated motion.
2. **Real key poses** — image generation creates anticipation/peak/recovery poses and local code assembles them.
3. **Light motion** — `light-motion-local` applies small affine transforms with zero generation cost. It never claims new limb or facial articulation.

Provider discovery is read-only. A paid request is submitted only by an explicit numbered route attempt.

## Supported adapters

- `native-tool`: a host-provided image-to-video tool that accepts the approved source image.
- `grok-build-local`: logged-in Grok Build CLI.
- `xai-direct`: xAI Videos REST with request-id resume support.
- `ai-sdk`: xAI, Kling, Seedance, Wan, and FAL through the bundled Node gateway.
- `command` / `http-job`: constrained custom adapters declared in `video-providers.json`.

Text-to-video-only tools do not satisfy the character-preservation contract.

## Credentials

Copy `assets/video-providers.example.json` into a character workspace and enable only providers actually configured on that machine. Credentials are environment-variable names, never literal secrets in JSON. Commands inherit only declared credentials and a bounded set of runtime variables.

Common variables:

- `XAI_API_KEY` for xAI direct.
- Provider-specific variables declared in the selected adapter entry.
- `GROK_HOME` only when the logged-in Grok profile is stored outside its default location.

## Grok Build and ZDR

Grok Build's `image_to_video` tool can be unavailable when account privacy is set to retention opt-out or a team uses ZDR without managed video storage. A local unsigned `managed_config.toml` is insufficient when the CLI requires console-synchronized policy.

For managed ZDR video output:

- configure `[tools.zdr_video_output_s3]` in the xAI console-managed configuration;
- use an HTTPS endpoint reachable by xAI;
- ensure the endpoint supports the required path-style object URL;
- restart Grok Build after the managed configuration syncs.

The Grok route always uses exact `#00FF00`. Non-Grok routes select green, blue, magenta, or cyan by measuring foreground color conflict, unless the user explicitly supplies `--key-color`. The selected color, candidate scores, and provider-specific screen inputs are stored in `video-task.json` and `artifact-manifest.json`.

## xAI direct recovery

The direct adapter records the remote request id immediately after submission. If polling is interrupted, resume the same request with the recorded id; never silently submit another billable request. `XAI_VIDEO_UPLOAD_URL` is optional and must use HTTPS when the account requires managed output storage.

## Provider configuration rules

- The task's `provider_chain` is an ordered allow-list.
- `max_attempts` bounds provider choices; `max_retries` bounds adapter-internal retries.
- No fallback is allowed unless `allow_fallback` is true.
- Grok-mandated jobs should use `allow_fallback: false`.
- An accepted provider result is promoted to one canonical source path. Rejected attempts may remain for diagnosis.

See [runtime-routing.md](../../references/runtime-routing.md) and the JSON schemas under [references/](../../references/).
