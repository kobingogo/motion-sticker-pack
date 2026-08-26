---
name: motion-sticker-pack
description: Create an animated sticker pack from a user-uploaded character image by collecting a style and Emoji or short reaction descriptions, generating and approving a static sheet, then routing video generation, cleanup, splitting, and packaging. Also process existing static sheets or grid videos. Use for animated emoji or sticker-pack production, not for designing a new character identity or general video editing.
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
- State that the prompt will be derived from the image's observed IP features and the selected style, with the character's appearance and source details preserved by default. Do not add redesign or moralizing constraints.
- Accept either an explicit confirmation (`确认` / `开始生成`) or a revision such as `风格改为写实还原` or `表情改为 🎸😍🥹😘🥰`. After a revision, show the updated summary and wait for confirmation again. Once confirmed, generate the static sheet directly, then follow the normal layout inspection and static-review gate before any video generation.
- If the user already supplied both a clear style and reactions, skip this intake confirmation and proceed to static generation. The post-generation static-review approval before video remains mandatory.

## Non-negotiable invariants

- Treat the image model's requested grid as a preference, not observed fact. After image generation, inspect the returned sheet and write `detected_layout`; every later stage must use that result.
- Express layout unambiguously as `columns × rows`. Derive `count = columns * rows`; never mix 3×3 with 12 items or 4×3 with 9 items.
- If automatic layout confidence is below `0.75`, inspect the overlay/report and confirm or override the grid before animation or cropping.
- When this Skill generated the static sheet, never generate video until the user explicitly approves that exact sheet. Regeneration invalidates the old approval and all downstream artifacts.
- Persist the review revision with `scripts/manage_job_state.py`. Hash verification is mandatory before bundled Provider execution; a conversational “approved” flag alone is insufficient.
- Keep the camera fixed. Each cell moves only inside its own bounds. Do not invent characters, captions, large props, scenery, or cross-cell effects. Small semantic reaction accents are allowed when they support the requested emotion and remain inside the cell. Preserve source elements when they already exist in the approved source unless the user asks to remove them or transparent-sheet/cell-isolation requirements make that technically necessary.
- Do not trust a video model's apparent transparency. Prefer real alpha when present; otherwise use a uniform high-contrast key background and deterministic local matting.
- Never put credentials in prompts, config files, reports, command arguments, or logs. Configuration refers to environment-variable names only.
- Keep every generated artifact for one character under `works/<character-slug>/` in this skill directory. Do not write new job files to the skill root or a shared `work/` folder. Resolve the directory with `scripts/character_workspace.py --name <角色名>` before static generation.

## Workflow

1. Inspect the input and choose an entry mode:
   - character reference → read [references/intake-and-approval.md](references/intake-and-approval.md); if the request is vague, present the default proposal and wait for confirmation before generating the static sticker sheet;
   - static sheet → detect the actual grid;
   - grid video → obtain the source sheet/layout or extract a representative frame with `ffmpeg -y -i input.mp4 -frames:v 1 representative-frame.png`, then detect the grid;
   - separate static stickers → do not invent a grid; run `scripts/process_independent_stickers.py <input-dir> <output-dir>`;
   - user-supplied static sheet → create state with `--source-type user-supplied` and skip the explicit approve step; it is already the selected source.
2. For a character reference, choose a short character name (user-supplied, or a label taken from the reference). Run `scripts/character_workspace.py --name <角色名>` and treat the printed `work_dir` as the only generated-asset root. Compile the confirmed style and Emoji/text reactions with `scripts/compile_static_prompt.py --reference-image <source-image> --output <work_dir>/static-prompt.json`. Call a currently callable image-generation/editing tool that accepts that exact reference image (text-only generation is not sufficient), save the returned sheet as `<work_dir>/static-sheet.png`, then inspect it with `scripts/inspect_sticker_sheet.py`. Create a `static-review` job state bound to the image and layout hashes, also inside that directory.
3. For a generated or regenerated sheet, show the static sheet and detected layout. Offer `确认，继续生成视频` or `重新生成`. Stop and wait. Do not route or call video generation while the sheet is unapproved. For a user-supplied sheet, report the detected layout and continue without asking for a duplicate approval.
4. After explicit approval of a generated sheet, record it with `scripts/manage_job_state.py approve`; for a user-supplied sheet, use the already `static-approved` state created with `--source-type user-supplied`. In both cases use the exact source image. For animation prompt rules, read [references/prompt-contract.md](references/prompt-contract.md) and write a `tile-plan.json` with exactly one vision-informed entry per detected cell. Compile it with `scripts/prompt_compiler.py`. Do not use generic motions unless explicitly accepting the lower-quality fallback.
5. For backend discovery and selection, read [references/runtime-routing.md](references/runtime-routing.md). Inspect callable tools/skills in the current runtime first and record their exact names, reference-image support, video support, and cost status in `<work_dir>/runtime-tools.json`. Then run `scripts/prepare_workflow.py --character <角色名>` so `video-providers.json` and `video-task.json` land in the same `works/<slug>/` directory; use those same files for probe, route, and execute.
6. Execute the selected mode:
   - `native-video` (`native-tool` in provider configuration): run `manage_job_state.py verify` first, then use a callable local Agent video tool with the approved image and `prompts.json`;
   - `external-video`: execute one selected AI SDK or command route with `scripts/execute_video_route.py`; never execute all attempts automatically;
   - `keypose-local`: when image generation is callable but video is not, generate 3–5 poses per sticker and assemble them with `scripts/render_keypose_pack.py --image <approved-sheet> --state <job-state>`;
   - `transform-local`: run `manage_job_state.py verify`, then use `scripts/keyframe_fallback.py --state <job-state>` only as the last fully local affine-motion fallback;
   - `postprocess-only`: process a supplied video without generation. If no layout is supplied, extract a representative frame first with `ffmpeg -y -i input.mp4 -frames:v 1 representative-frame.png`, then inspect it.
   - `prompt-only`: when no video or local image-processing capability exists, run `scripts/assemble_prompt_only.py`, deliver its prompt artifacts, and stop without claiming a generated video.
7. Split and matte a grid video with `scripts/process_emoji_grid.py --layout <layout.json>`. It exports numbered Animated WebP and GIF files, matching first-frame PNG files, `processing.json`, and a ZIP. GIF uses a 255-color palette with binary transparency for chat apps that reject WebP.
8. Read [references/output-contract.md](references/output-contract.md) before delivery. Run `scripts/assemble_delivery.py` so media and `job-state.json`, `prompts.json`, and `route.json` are copied into one output directory and ZIP. Report any low-confidence layout, alpha damage, loop discontinuity, provider fallback, or failed cell instead of hiding it.

## Routing behavior

Use this fixed order unless the user explicitly selects a provider:

1. callable native/local image-to-video capability;
2. configured external providers that satisfy the task, in configured priority order;
3. key-pose generation plus local assembly;
4. transform-only local animation when key-pose generation is unavailable;
5. prompt-and-plan-only output when neither video nor local image processing is possible. Deliver the prompts and route artifacts and stop without claiming a generated video.

Before the first external-provider call, state which provider will receive the image and that the request may incur charges, unless the user already explicitly selected that provider and authorized external generation. Run only attempt 1; a later attempt requires a failed prior result and another explicit execution step.

Retry only another configured route or the affected sticker. Do not repeatedly charge the same external provider without a clear transient failure and a bounded attempt count.

## Included commands

```bash
python3 scripts/character_workspace.py --name '小黑猫'
python3 scripts/compile_static_prompt.py --style 3d --expressions '🎸😍🥹😘🥰' --layout 3x3 --reference-image source.png --output works/小黑猫/static-prompt.json
python3 scripts/inspect_sticker_sheet.py works/小黑猫/static-sheet.png --output works/小黑猫/layout.json --overlay works/小黑猫/layout-overlay.png
python3 scripts/manage_job_state.py create --image works/小黑猫/static-sheet.png --layout works/小黑猫/layout.json --static-prompt works/小黑猫/static-prompt.json --output works/小黑猫/job-state.json
python3 scripts/manage_job_state.py approve --state works/小黑猫/job-state.json --image works/小黑猫/static-sheet.png --layout works/小黑猫/layout.json --confirmed-by-user
python3 scripts/prompt_compiler.py --layout works/小黑猫/layout.json --tile-plan works/小黑猫/tile-plan.json --output works/小黑猫/prompts.json
python3 scripts/prepare_workflow.py --character '小黑猫' --image "$PWD/works/小黑猫/static-sheet.png" --layout "$PWD/works/小黑猫/layout.json" --prompts "$PWD/works/小黑猫/prompts.json" --state "$PWD/works/小黑猫/job-state.json" --tile-plan "$PWD/works/小黑猫/tile-plan.json"
python3 scripts/probe_video_capabilities.py --config works/小黑猫/video-providers.json --tool-manifest works/小黑猫/runtime-tools.json --output works/小黑猫/capabilities.json
python3 scripts/route_video_provider.py --config works/小黑猫/video-providers.json --capabilities works/小黑猫/capabilities.json --task works/小黑猫/video-task.json --output works/小黑猫/route.json
python3 scripts/execute_video_route.py --config works/小黑猫/video-providers.json --task works/小黑猫/video-task.json --route works/小黑猫/route.json --attempt 1 --output works/小黑猫/video-result.json
python3 scripts/process_emoji_grid.py animation.mp4 works/小黑猫/output --layout works/小黑猫/layout.json --fps 6
python3 scripts/render_keypose_pack.py keyposes works/小黑猫/output --image works/小黑猫/static-sheet.png --state works/小黑猫/job-state.json --layout works/小黑猫/layout.json --fps 6
python3 scripts/keyframe_fallback.py works/小黑猫/static-sheet.png works/小黑猫/output --state works/小黑猫/job-state.json --layout works/小黑猫/layout.json --fps 6
python3 scripts/process_independent_stickers.py stickers works/小黑猫/output --fps 6
python3 scripts/assemble_prompt_only.py --static-prompt works/小黑猫/static-prompt.json --tile-plan works/小黑猫/tile-plan.json --prompts works/小黑猫/prompts.json --route works/小黑猫/route.json --output works/小黑猫/prompt-only
python3 scripts/assemble_delivery.py --media-dir works/小黑猫/output --audit-dir works/小黑猫 --output works/小黑猫/delivered --require-job-state --require-prompts --require-route
```

Use paths relative to this skill directory when invoked from elsewhere. On Windows, run the same scripts with `py -3` (or `python`) if `python3` is not on PATH; `prepare_workflow.py` rewrites example `python3` adapter commands to the current interpreter.
