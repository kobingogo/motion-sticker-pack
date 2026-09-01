# CLI reference

End users should normally invoke the skill in conversation. These commands are for debugging and integrations.

## Static source

```bash
python3 scripts/compile_static_prompt.py \
  --style 3d --expressions '开心、喜欢、委屈、惊讶、亲亲、谢谢、加油、困困、点赞' \
  --layout 3x3 --character-description '圆脸、蓝外套的小熊' \
  --output works/小熊/static-prompt.json

python3 scripts/prepare_image_gen_call.py \
  --static-prompt works/小熊/static-prompt.json \
  --supported-argument prompt --supported-argument referenced_image_paths \
  --output works/小熊/static-generation.json

python3 scripts/normalize_static_sheet.py source.png works/小熊/static-sheet.png \
  --report works/小熊/static-alpha.json

python3 scripts/inspect_sticker_sheet.py works/小熊/static-sheet.png \
  --output works/小熊/layout.json --overlay works/小熊/layout-overlay.png
```

## Approval and prompts

```bash
python3 scripts/manage_job_state.py create \
  --image works/小熊/static-sheet.png --layout works/小熊/layout.json \
  --static-prompt works/小熊/static-prompt.json --output works/小熊/job-state.json

python3 scripts/manage_job_state.py approve \
  --state works/小熊/job-state.json --image works/小熊/static-sheet.png \
  --layout works/小熊/layout.json --confirmed-by-user

python3 scripts/prompt_compiler.py \
  --layout works/小熊/layout.json --tile-plan works/小熊/tile-plan.json \
  --static-prompt works/小熊/static-prompt.json --output works/小熊/prompts.json
```

## Provider route

```bash
python3 scripts/prepare_workflow.py --character 小熊 \
  --skill-root "$PWD" --image "$PWD/works/小熊/static-sheet.png" \
  --layout "$PWD/works/小熊/layout.json" --prompts "$PWD/works/小熊/prompts.json" \
  --state "$PWD/works/小熊/job-state.json" --tile-plan "$PWD/works/小熊/tile-plan.json"

python3 scripts/probe_video_capabilities.py \
  --config works/小熊/video-providers.json --tool-manifest works/小熊/runtime-tools.json \
  --output works/小熊/capabilities.json

python3 scripts/route_video_provider.py \
  --config works/小熊/video-providers.json --capabilities works/小熊/capabilities.json \
  --task works/小熊/video-task.json --output works/小熊/route.json
```

Review the route and cost warning before:

```bash
python3 scripts/execute_video_route.py \
  --config works/小熊/video-providers.json --task works/小熊/video-task.json \
  --route works/小熊/route.json --attempt 1 --output works/小熊/video-result.json
```

## Real key poses

```bash
python3 scripts/compile_keypose_plan.py \
  --input-dir works/小熊/cells --reactions '开心,惊讶,难过' \
  --output-dir works/小熊/keypose-plan --manifest works/小熊/artifact-manifest.json

python3 scripts/prepare_keyposes.py \
  --source-cells works/小熊/cells --pose-sheets works/小熊/keypose-sheets \
  --output-dir works/小熊/keyposes --manifest works/小熊/artifact-manifest.json

python3 scripts/render_keypose_pack.py works/小熊/keyposes works/小熊/output \
  --image works/小熊/static-sheet.png --layout works/小熊/layout.json \
  --state works/小熊/job-state.json --manifest works/小熊/artifact-manifest.json
```

## Local light motion and processing

```bash
python3 scripts/light_motion_fallback.py works/小熊/static-sheet.png works/小熊/output \
  --state works/小熊/job-state.json --layout works/小熊/layout.json

python3 scripts/process_emoji_grid.py animation.mp4 works/小熊/output \
  --layout works/小熊/layout.json --settings works/小熊/sticker-production.json \
  --manifest works/小熊/artifact-manifest.json
```

All local routes default to 240×240 at 8fps.

## Delivery

```bash
python3 scripts/assemble_delivery.py \
  --media-dir works/小熊/output --audit-dir works/小熊 \
  --output works/小熊/delivered --require-job-state --require-prompts \
  --require-route --cleanup-media-dir
```
