---
name: motion-sticker-pack
description: Create an animated sticker pack from a supplied character image or a text-defined character by collecting a style and Emoji or short reaction descriptions, generating and approving a static sheet, then routing video generation, cleanup, splitting, and packaging. Also process existing static sheets or grid videos. Use for animated emoji or sticker-pack production, not for designing a separate character identity artifact or general video editing.
---

# Motion Sticker Pack｜动态表情包制作器

Create a usable animated sticker pack, not merely a video preview. Preserve the supplied character identity and produce independently looping stickers, transparent first frames, a machine-readable report, and a ZIP.

## IP identity and prompt principles

- Before writing a generation prompt, inspect the supplied image and derive the IP's visible identity features: face, hair or fur, silhouette and proportions, colors, clothing, accessories, existing props, pose language, scene cues, and overall mood.
- Build the prompt from those observed features plus the user's selected style and reactions. Preserve the supplied IP's appearance and source-specific details by default; change them only when the user asks for a change.
- Interpret each Emoji or text reaction semantically. When it helps the expression read clearly, add a small number of matching decorative accents—such as hearts, music notes, sparkles, tears, blush marks, sweat drops, motion lines, or stars—using the selected style's visual language. Use them selectively rather than forcing the same accents into every cell, and do not turn them into unrelated large props.
- Do not add unsolicited moral, modesty, sexualization, age, wardrobe, pose-cleanup, or scene-removal instructions. In particular, do not insert wording such as “改成得体、日常、非性感化的简化服装” or “不要保留汽车、夜景、原照片背景或暧昧姿势” unless the user explicitly requests that transformation.
- Keep the source reference label neutral (for example, `所附图像` or `附件中的角色参考图`). Do not encode an unrequested redesign into `reference_label`.
- A transparent sticker sheet may require removing the source background as a technical canvas operation, but do not otherwise remove existing clothing, props, setting cues, or pose characteristics unless requested. If the user wants the original scene retained, preserve it within each cell where technically feasible.

## Pre-generation fidelity check

Before writing a prompt or an intake/confirmation message, explicitly check that no unrequested transformation has been introduced. Remove any wording or instruction that asks to make the character more “得体、日常、非露骨、非性感化”, to simplify or replace clothing, clean up the pose, remove scene cues, or make the result “适合公开分享”. These are not defaults. Preserve the supplied character's observed appearance, clothing, pose language, props, setting cues, and mood unless the user requests a change or a higher-priority platform safety rule requires one. If a safety-driven change is required, state only the necessary constraint and do not broaden it into an aesthetic redesign.

## Ambiguous-request confirmation

- When the user supplies a character image but does not specify a style, reactions, or both, do not generate immediately. First present a concise confirmation card with the proposed defaults: `3D 卡通风`, a practical set of nine chat reactions (`开心、喜欢、委屈、惊讶、亲亲、谢谢、加油、困困、点赞`), and the default `3×3` layout.
- When no character image is supplied, accept a named or text-defined character and use the same intake defaults when style or reactions are missing. After confirmation, generate the complete static sheet directly. Do not insert a separate single-character concept image, identity card, or character-approval stage.
- State that the prompt will be derived from the image's observed IP features and the selected style, with the character's appearance and source details preserved by default. Do not add redesign or moralizing constraints.
- Accept either an explicit confirmation (`确认` / `开始生成`) or a revision such as `风格改为写实还原` or `表情改为 🎸😍🥹😘🥰`. After a revision, show the updated summary and wait for confirmation again. Once confirmed, generate the static sheet directly, then follow the normal layout inspection and static-review gate before any video generation.
- If the user already supplied both a clear style and reactions, skip this intake confirmation and proceed to static generation. The post-generation static-review approval before video remains mandatory.

## Non-negotiable invariants

- Treat the image model's requested grid as a preference, not observed fact. After image generation, inspect the returned sheet and write `detected_layout`; every later stage must use that result.
- Compile every static request with `image_generation_request.arguments.background` and `image_generation_request.arguments.output_format`. Use a transparent-first, locally verified two-stage policy for both reference-image and text-only generation. Pass those exact fields when the callable `image_gen` schema exposes them; when it does not, omit only the unsupported fields, record them in `static-generation.json`, and still make the first attempt with the real-Alpha prompt. Missing schema fields do not prove that prompt-driven transparency is unavailable and must never select the green fallback by themselves. The model's claim that it produced transparency is also not proof. Only local pixel validation may trigger the recorded `opaque_fallback_call`, once, with the same prompt/reference and exact `#00FF00` key-color instruction; never invent unknown tool arguments at call time.
- Express layout unambiguously as `columns × rows`. Derive `count = columns * rows`; never mix 3×3 with 12 items or 4×3 with 9 items.
- If automatic layout confidence is below `0.75`, inspect the overlay/report and confirm or override the grid before animation or cropping.
- When this Skill generated the static sheet, never generate video until the user explicitly approves that exact sheet. Regeneration invalidates the old approval and all downstream artifacts.
- Persist the review revision with `scripts/manage_job_state.py`. Hash verification is mandatory before bundled Provider execution; a conversational “approved” flag alone is insufficient.
- Keep the camera fixed. Each cell moves only inside its own bounds. Do not invent characters, captions, large props, scenery, or cross-cell effects. Small semantic reaction accents are allowed when they support the requested emotion and remain inside the cell. Preserve source elements when they already exist in the approved source unless the user asks to remove them or transparent-sheet/cell-isolation requirements make that technically necessary.
- Do not trust a video model's apparent transparency. Prefer real alpha when present; otherwise use a uniform key that contrasts with the character (chroma such as green or magenta, not a near-black or near-white plate) and deterministic local matting.
- For Grok image-to-video, the output contract is stricter: use `#00FF00`, never a checkerboard or simulated transparency. The input sent to Grok must already have real alpha (or a verified uniform green plate), and `scripts/grok_build_video_adapter.py` must pass every native returned frame through background QC. Grok is called exactly once (`max_retries: 0`) and must never fall through to local animation.
- For Grok image-to-video, compile a compact execution prompt from the approved per-cell `tile-plan.json`: keep the grid dimensions, identity lock, fixed-camera rule, one action per cell, green-screen contract, and loop timing, while removing repeated prose. Keep the final adapter instruction below 3,800 UTF-8 bytes so Grok's 4,096-byte CLI limit is not reached; reject early with a local validation error if a custom tile plan still exceeds the budget.
- Never put credentials in prompts, config files, reports, command arguments, or logs. Configuration refers to environment-variable names only.
- Keep every generated artifact for one character under `works/<character-slug>/` in this skill directory. Do not write new job files to the skill root or a shared `work/` folder. Resolve the directory with `scripts/character_workspace.py --name <角色名>` before static generation.
- Treat `assets/sticker-production.default.json` as the single editable production-default file. Validate it with `scripts/sticker_production_config.py`; copy it into each work directory as `sticker-production.json` so generation and post-processing use the same immutable job snapshot. Do not duplicate duration, size, fps, color-budget, key-color, or GIF-budget defaults in prompts or scripts.

## Workflow

1. Inspect the input and choose an entry mode:
   - character reference → read [references/intake-and-approval.md](references/intake-and-approval.md); if the request is vague, present the default proposal and wait for confirmation before generating the static sticker sheet;
   - named or text-defined character without an image → compile that definition and generate the full static sheet directly; do not generate a separate character image first;
   - static sheet → detect the actual grid;
   - grid video → obtain the source sheet/layout or extract a representative frame with `ffmpeg -y -i input.mp4 -frames:v 1 representative-frame.png`, then detect the grid;
   - separate static stickers → do not invent a grid; run `scripts/process_independent_stickers.py <input-dir> <output-dir>`;
   - user-supplied static sheet → create state with `--source-type user-supplied` and skip the explicit approve step; it is already the selected source.
2. Choose a short character name and resolve the work directory with `scripts/character_workspace.py --name <角色名>`. Compile the confirmed style and reactions into `<work_dir>/static-prompt.json`: use `--reference-image <source-image>` when supplied, or `--character-description <definition>` when no image exists. The no-image route goes straight to one complete sheet. Inspect the callable `image_gen` schema and run `scripts/prepare_image_gen_call.py`, repeating `--supported-argument` for its exposed fields. Call the transparent-first `call_arguments` even when `background` or `output_format` was omitted as unsupported; the real-Alpha prompt remains the first attempt. The report records requested, passed, omitted, and the bounded `opaque_fallback_call`. A reference-image request must use a backend that accepts that exact image; a text-defined request may use text-only generation. Save the raw result as `<work_dir>/static-sheet-source.png`.
3. Run `scripts/normalize_static_sheet.py <work_dir>/static-sheet-source.png <work_dir>/static-sheet.png --report <work_dir>/static-alpha.json`. Native alpha is preserved. For opaque results, accept only a uniform, high-contrast chroma key suitable for deterministic local matting. Simulated checkerboards/two-tone previews, light plates, gradients, scenery, and ambiguous backgrounds fail closed. Normalization failure—not omitted schema fields and not the presence of a reference image—is the only trigger for calling the recorded `opaque_fallback_call` once. Save the fallback result as the new raw source and re-run normalization; if it still fails, stop and request regeneration rather than sending the bad sheet to video. Inspect only the normalized `<work_dir>/static-sheet.png` with `scripts/inspect_sticker_sheet.py`, then create the hash-bound `static-review` state.
4. For a generated or regenerated sheet, show the normalized sheet, detected layout, and `static-alpha.json` warnings. Offer `确认，继续生成视频` or `重新生成`. Stop and wait. Do not route or call video generation while the sheet is unapproved. For a user-supplied sheet, report the detected layout and continue without asking for a duplicate approval.
5. After explicit approval of a generated sheet, record it with `scripts/manage_job_state.py approve`; for a user-supplied sheet, use the already `static-approved` state created with `--source-type user-supplied`. In both cases use the exact source image. For animation prompt rules, read [references/prompt-contract.md](references/prompt-contract.md) and write a `tile-plan.json` with exactly one vision-informed entry per detected cell. Compile it with `scripts/prompt_compiler.py`. Do not use generic motions unless explicitly accepting the lower-quality fallback.
6. For backend discovery and selection, read [references/runtime-routing.md](references/runtime-routing.md). Inspect callable tools/skills in the current runtime first and record their exact names, reference-image support, video support, and cost status in `<work_dir>/runtime-tools.json`. Then run `scripts/prepare_workflow.py --character <角色名> --skill-root /Users/bingo/Documents/ChatGPT/motion-sticker-pack` when working from this repository, so `video-providers.json` and `video-task.json` land in the same `works/<slug>/` directory; use those same files for probe, route, and execute.
7. Execute the selected mode:
   - `native-video` (`native-tool` in provider configuration): run `manage_job_state.py verify` first, then use a callable local Agent video tool with the approved image and `prompts.json`;
   - `external-video`: execute one selected AI SDK or command route with `scripts/execute_video_route.py`; never execute all attempts automatically. When Grok is required, set `provider: grok-build-local` and `allow_fallback: false`; a failed green-screen QC is a failed Grok job, not permission to use local animation;
   - `keypose-local`: when image generation is callable but video is not, generate 3–5 poses per sticker and assemble them with `scripts/render_keypose_pack.py --image <approved-sheet> --state <job-state>`;
   - `transform-local`: run `manage_job_state.py verify`, then use `scripts/keyframe_fallback.py --state <job-state>` only as the last fully local affine-motion fallback;
   - `postprocess-only`: process a supplied video without generation. If no layout is supplied, extract a representative frame first with `ffmpeg -y -i input.mp4 -frames:v 1 representative-frame.png`, then inspect it.
   - `prompt-only`: when no video or local image-processing capability exists, run `scripts/assemble_prompt_only.py`, deliver its prompt artifacts, and stop without claiming a generated video.
8. Split and matte a grid video with `scripts/process_emoji_grid.py --layout <layout.json> --settings <work_dir>/sticker-production.json`. Probe the actual returned duration, select the matching configured profile, and run native-frame matting, instance assignment, and safety QC before duration sampling. Whole-grid registration is disabled by default because per-frame integer correction can create micro-jitter; use `--registration auto` only when visual inspection confirms actual camera drift. Request duration comes from `generation.provider_duration_seconds`; the shipped map is `grok-build-local: 6` and `xai-direct: 3`, with both outputs at 240×240 and 8 fps. A direct API result matching 3 seconds is preserved as-is and is never duration-compressed. For Grok, preserve the complete 6-second result as the root version and also create `3s/` from the initial 24 sampled frames. Record endpoint pose difference for audit, but do not reject or retime the 3-second derivative when the first and last poses differ. Never replace or discard the full Grok result. First run `--trial` for the configured cell (default `01`) and require both Grok variants to pass encoded-frame and 1 MiB GIF-budget checks. If they pass, reuse the same source grid video for the complete pack—never make a second Grok call. Later full-pack GIF overages are warnings rather than pack blockers; encoded-frame failures remain hard failures. Grok grid inputs are repacked from each cell's real Alpha bounding box with at least a 10% two-sided green corridor. Animated WebP stays lossless; GIF gets an adaptive binary-alpha threshold; both are decoded again for hard frame QC before packaging. A seam crossing is not itself a failure. Only an inseparable merge is withheld, while successful cells and the failure report remain deliverable.
9. Read [references/output-contract.md](references/output-contract.md) before delivery. Run `scripts/assemble_delivery.py --cleanup-media-dir` so media and `job-state.json`, `prompts.json`, and `route.json` are collected into one canonical delivery directory and ZIP, then the intermediate media directory is removed only after packaging succeeds. Composite each GIF onto a light background before showing it. Report any low-confidence layout, alpha damage, interior holes, loop discontinuity, residual hold jitter, provider fallback, or failed cell instead of hiding it.

## Routing behavior

Use this fixed order unless the user explicitly selects a provider:

1. callable native/local image-to-video capability;
2. configured external providers that satisfy the task, in configured priority order;
3. key-pose generation plus local assembly;
4. transform-only local animation when key-pose generation is unavailable;
5. prompt-and-plan-only output when neither video nor local image processing is possible. Deliver the prompts and route artifacts and stop without claiming a generated video.

Before the first external-provider call, state which provider will receive the image and that the request may incur charges, unless the user already explicitly selected that provider and authorized external generation. Run only attempt 1; a later attempt requires a failed prior result and another explicit execution step.

Retry only another configured route or the affected sticker. Do not repeatedly charge the same external provider without a clear transient failure and a bounded attempt count.

For a Grok-mandated task, the route must be explicit (`provider: grok-build-local`, `allow_fallback: false`, `max_retries: 0`). One approved sheet produces one Grok video; no local route and no automatic paid regeneration is eligible as a substitute. Read generation duration from `generation.provider_duration_seconds` for the executing provider and resolution from the job's `sticker-production.json` snapshot (shipped defaults: Grok 6 seconds, xAI 3 seconds, 720p). Preserve the complete returned Grok duration, add the configured 3-second derivative, and apply only the configured uniform 8 fps sampling after native-frame QC. A direct 3-second API result remains a single full-duration output.

## Included commands

```bash
python3 scripts/character_workspace.py --name '小黑猫'
python3 scripts/compile_static_prompt.py --style 3d --expressions '🎸😍🥹😘🥰' --layout 3x3 --reference-image source.png --output works/小黑猫/static-prompt.json
python3 scripts/compile_static_prompt.py --style 3d --expressions '开心、喜欢、委屈、惊讶、亲亲、谢谢、加油、困困、点赞' --layout 3x3 --character-description '金发、深色西装、红领带的公众人物漫画形象' --output works/角色/static-prompt.json
python3 scripts/prepare_image_gen_call.py --static-prompt works/小黑猫/static-prompt.json --supported-argument prompt --supported-argument referenced_image_paths --output works/小黑猫/static-generation.json
python3 scripts/normalize_static_sheet.py works/小黑猫/static-sheet-source.png works/小黑猫/static-sheet.png --report works/小黑猫/static-alpha.json
python3 scripts/inspect_sticker_sheet.py works/小黑猫/static-sheet.png --output works/小黑猫/layout.json --overlay works/小黑猫/layout-overlay.png
python3 scripts/manage_job_state.py create --image works/小黑猫/static-sheet.png --layout works/小黑猫/layout.json --static-prompt works/小黑猫/static-prompt.json --output works/小黑猫/job-state.json
python3 scripts/manage_job_state.py approve --state works/小黑猫/job-state.json --image works/小黑猫/static-sheet.png --layout works/小黑猫/layout.json --confirmed-by-user
python3 scripts/prompt_compiler.py --layout works/小黑猫/layout.json --tile-plan works/小黑猫/tile-plan.json --output works/小黑猫/prompts.json
python3 scripts/prepare_workflow.py --character '小黑猫' --image "$PWD/works/小黑猫/static-sheet.png" --layout "$PWD/works/小黑猫/layout.json" --prompts "$PWD/works/小黑猫/prompts.json" --state "$PWD/works/小黑猫/job-state.json" --tile-plan "$PWD/works/小黑猫/tile-plan.json"
python3 scripts/probe_video_capabilities.py --config works/小黑猫/video-providers.json --tool-manifest works/小黑猫/runtime-tools.json --output works/小黑猫/capabilities.json
python3 scripts/route_video_provider.py --config works/小黑猫/video-providers.json --capabilities works/小黑猫/capabilities.json --task works/小黑猫/video-task.json --output works/小黑猫/route.json
python3 scripts/execute_video_route.py --config works/小黑猫/video-providers.json --task works/小黑猫/video-task.json --route works/小黑猫/route.json --attempt 1 --output works/小黑猫/video-result.json
python3 scripts/process_emoji_grid.py animation.mp4 works/小黑猫/trial --layout works/小黑猫/layout.json --settings works/小黑猫/sticker-production.json --trial
python3 scripts/process_emoji_grid.py animation.mp4 works/小黑猫/output --layout works/小黑猫/layout.json --settings works/小黑猫/sticker-production.json
python3 scripts/render_keypose_pack.py keyposes works/小黑猫/output --image works/小黑猫/static-sheet.png --state works/小黑猫/job-state.json --layout works/小黑猫/layout.json --fps 6
python3 scripts/keyframe_fallback.py works/小黑猫/static-sheet.png works/小黑猫/output --state works/小黑猫/job-state.json --layout works/小黑猫/layout.json --fps 6
python3 scripts/process_independent_stickers.py stickers works/小黑猫/output --fps 6
python3 scripts/assemble_prompt_only.py --static-prompt works/小黑猫/static-prompt.json --tile-plan works/小黑猫/tile-plan.json --prompts works/小黑猫/prompts.json --route works/小黑猫/route.json --output works/小黑猫/prompt-only
python3 scripts/assemble_delivery.py --media-dir works/小黑猫/output --audit-dir works/小黑猫 --output works/小黑猫/delivered --require-job-state --require-prompts --require-route --cleanup-media-dir
```

Use paths relative to this skill directory when invoked from elsewhere. On Windows, run the same scripts with `py -3` (or `python`) if `python3` is not on PATH; `prepare_workflow.py` rewrites example `python3` adapter commands to the current interpreter.
