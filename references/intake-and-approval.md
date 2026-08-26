# Intake and approval flow

Read this reference when the user starts from a character image or asks to create a new static sticker sheet.

## Agent-side interaction

Collect only the missing fields. Do not ask a long questionnaire.

Required before static generation:

1. one reference image the user supplied;
2. one style choice;
3. one or more Emoji or short reaction descriptions.

Before compiling the prompt, inspect the reference image for the IP's stable visual features—face, hair or fur, silhouette, proportions, colors, clothing, accessories, existing props, pose language, scene cues, and mood—and carry those features into the prompt. Treat the supplied image as the source of truth. Preserve them by default and only change them when the user asks.

Do not add unsolicited moral, modesty, sexualization, age, wardrobe, pose-cleanup, or scene-removal constraints. Do not rewrite the reference into “得体、日常、非性感化” clothing or remove cars, night scenes, backgrounds, or ambiguous poses unless the user explicitly requests those changes. Use a neutral reference label such as `所附图像`.

## Confirmation for vague requests

If the user provides a character image but leaves style or reactions unspecified, pause before generation and show this concise proposal:

```text
我将按以下设置制作：
风格：3D 卡通风
表情：开心、喜欢、委屈、惊讶、亲亲、谢谢、加油、困困、点赞
布局：3×3，共 9 个
角色：根据附件分析 IP 形象特征，并默认保留人物外形、服装、配饰、道具、姿势语言和整体气质。

回复“确认”开始生成，也可以说“风格改为……”或“表情改为……”。
```

Do not call image generation until the user confirms the current proposal. If the user changes the style or reactions, update the proposal and ask for confirmation again. If both style and reactions were already clear in the original request, skip this intake pause and proceed.

Optional:

- character name, used as `works/<slug>/` for every generated artifact;
- requested layout, default `3x3`;
- custom style description;
- desired symbols or props;
- local-only/privacy requirement.

If the user does not give a character name, derive a short label from the reference (for example the filename stem or a visible identity word) and confirm it before writing files. Create the directory with `scripts/character_workspace.py --name <角色名>` and keep later outputs inside that folder.

If the host supports forms, chips, cards, or other structured inputs, use them for style and expression selection. Otherwise present a short numbered list and accept natural-language replies. The interaction must remain usable in a plain terminal Agent.

## Style choices

Use [style-presets.json](style-presets.json) as the maintained preset source. Present these eight primary choices:

1. `realistic` — 写实还原；
2. `3d` — 3D 卡通风；
3. `hand-drawn` — 手绘风；
4. `chibi` — Q 版；
5. `manga` — 漫画风；
6. `pixel-art` — 像素艺术；
7. `cute` — 可爱风；
8. `retro` — 复古风。

Also accept `custom` followed by a short user style description. Do not force a style menu if the user already named a clear style.

## Expression input

Accept any Unicode Emoji, any short text description, or a mixture of both; the examples below are not a whitelist:

- Emoji: `🎸 😍 🥹 😘 🥰`;
- short text: `开心、委屈、亲亲、震惊、谢谢`.

Emoji are semantic/motif hints, not commands to paste literal Unicode glyphs into every sticker. They may also guide small decorative accents when appropriate: hearts or sparkles for affection, music notes or sound lines for music, tears or rain drops for sadness, blush marks for shyness, and stars or motion lines for excitement. Use accents selectively, keep them inside the cell, and do not invent unrelated large props or force the same decoration into every sticker.

## Static generation

Compile the user selections with `scripts/compile_static_prompt.py --reference-image <source-image>`. Generate one transparent sticker sheet using a callable image-generation or image-editing backend that accepts that exact reference image. Text-only generation is not sufficient for identity preservation. Save the result as `static-sheet.png`; the requested grid is only a preference.

Immediately inspect the returned image with `scripts/inspect_sticker_sheet.py` and create:

- `static-sheet.png`;
- `layout.json`;
- `layout-overlay.png` when review benefits from visible boundaries;
- `static-prompt.json`.

Then run `scripts/manage_job_state.py create` with the image, layout, and static prompt. This creates a hash-bound `static-review` revision. For a user-supplied sheet, use `--source-type user-supplied`; it records that the source was already selected by the user.

## Mandatory static-review gate

Video generation is forbidden until the user explicitly approves the current static sheet.

A static sheet uploaded by the user as the intended source is already user-selected and may enter animation planning directly. Create state with `--source-type user-supplied` and do not run the explicit `approve` command. The gate applies whenever this Skill generates or regenerates the static sheet.

After static generation, show the actual sheet and report:

- detected columns × rows and total item count;
- layout confidence;
- any empty cell, overlap, identity drift, bad gutter, background, or edge warning;
- exactly two next actions:
  - `确认，继续生成视频`;
  - `重新生成` plus optional requested changes.

Treat unambiguous equivalents such as “确认”“可以”“就这版”“继续做视频” as approval. A question, silence, or unrelated message is not approval.

After explicit approval, run `scripts/manage_job_state.py approve --confirmed-by-user`. Provider execution must verify the same image and layout hashes. Never edit `job-state.json` by hand to simulate approval.

If the user requests any visual change, regenerate the static sheet and return to `static-review`.

## State invariants

Use these phases conceptually or persist them in a job report:

```text
intake
  → static-generating
  → static-review
      ├── regenerate → static-generating
      └── approve    → static-approved
                         → video-routing
                         → video-generating
                         → postprocessing
                         → delivered
```

Every new static generation invalidates:

- prior static approval;
- prior `layout.json` and overlay;
- prior tile motion plan and compiled video prompt;
- prior video route and raw video generated from the old sheet.

Do not reuse those artifacts across static revisions.

Once approved, use the exact approved image as the image-to-video source. Do not silently regenerate, enhance, crop, restyle, or replace it before video generation.
