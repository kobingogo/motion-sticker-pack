# Intake and approval flow

Read this reference when the user starts from a character image, names or describes a character without an image, or asks to create a new static sticker sheet.

## Agent-side interaction

Collect only the missing fields. Do not ask a long questionnaire.

Required before static generation:

1. either one user-supplied reference image or a usable character name/description;
2. one style choice;
3. one or more Emoji or short reaction descriptions.

Optional text preference:

- Ask whether the user wants short reaction text in the stickers.
- If the user does not choose, default to avoiding text in the generation prompt.
- This is a preference, not a quality gate: if the model adds text anyway, keep the sheet eligible for review and record the observed text rather than rejecting or forcing regeneration.

When an image exists, inspect it for the IP's stable visual features—face, hair or fur, silhouette, proportions, colors, clothing, accessories, existing props, pose language, scene cues, and mood—and treat it as the source of truth. Without an image, compile the user's named/textual character definition and keep that identity consistent across every cell. Do not generate a separate character concept image first.

Do not add unsolicited moral, modesty, sexualization, age, wardrobe, pose-cleanup, or scene-removal constraints. Do not rewrite the reference into “得体、日常、非性感化” clothing or remove cars, night scenes, backgrounds, or ambiguous poses unless the user explicitly requests those changes. Use a neutral reference label such as `所附图像`.

## Confirmation for vague requests

If the user provides or defines a character but leaves style or reactions unspecified, pause before generation and show this concise proposal. For a text-defined character, replace the attachment sentence with the supplied character definition and state that the complete sheet will be generated directly:

```text
我将按以下设置制作：
风格：3D（未指定子风格时，可自由选择 3D 动画风或 3D 真实人物风）
呈现：3×3 角色表情插画卡片（无白边、无厚描边、每格轻微独立背景、统一色调）
表情：开心、喜欢、委屈、惊讶、亲亲、谢谢、加油、困困、点赞
布局：3×3，共 9 个
角色：根据附件分析 IP 形象特征，并默认保留人物外形、服装、配饰、道具、姿势语言和整体气质。

回复“确认”开始生成，也可以说“风格改为……”或“表情改为……”。
```

When the text preference is still missing, append one compact choice:

```text
文字：默认不主动添加（如果模型自行生成文字，不拦截；静态审核时由你决定是否保留）
也可以回复“带文字”或“不带文字”。
```

Do not call image generation until the user confirms the current proposal. If the user changes the style or reactions, update the proposal and ask for confirmation again. If both style and reactions were already clear in the original request, skip this intake pause and proceed.

Optional:

- character name, used as `works/<slug>/` for every generated artifact;
- requested layout, default `3x3`;
- custom style description;
- desired symbols or props;
- local-only/privacy requirement.

If the user does not give a character name, derive a short label from the reference or textual definition and confirm it before writing files. Create the directory with `scripts/character_workspace.py --name <角色名>` and keep later outputs inside that folder.

If the host supports forms, chips, cards, or other structured inputs, use them for style and expression selection. Otherwise present a short numbered list and accept natural-language replies. The interaction must remain usable in a plain terminal Agent.

## Style choices

Use [style-presets.json](style-presets.json) and its `core_catalog` as the maintained source. Present only core entries whose status is `route-verified`; the v0.3.1 catalog targets 16 high-distinction directions but keeps pending entries out of the selectable verified list until controlled same-anchor evidence is complete. A pending core style may still be compiled when the user explicitly names it for a controlled trial; mark its contract as unverified and do not present it as a proven gallery style. Do not expose the competitor's 36 names as a hard-coded menu.

Always accept `custom` followed by a short style description for long-tail cultural media, print processes, retro UI, or hybrid treatments. Custom styles are not Gallery presets, but they still use the same static-review gate, transparency contract, and artifact manifest. Do not force a menu if the user already named a clear style.

## Expression input

Accept any Unicode Emoji, any short text description, or a mixture of both; the examples below are not a whitelist:

- Emoji: `🎸 😍 🥹 😘 🥰`;
- short text: `开心、委屈、亲亲、震惊、谢谢`.

Emoji are semantic/motif hints, not commands to paste literal Unicode glyphs into every sticker. They may also guide small decorative accents when appropriate: hearts or sparkles for affection, music notes or sound lines for music, tears or rain drops for sadness, blush marks for shyness, and stars or motion lines for excitement. Use accents selectively, keep them inside the cell, and do not invent unrelated large props or force the same decoration into every sticker.

## Static generation

Compile the user selections with `scripts/compile_static_prompt.py --reference-image <source-image>` when an image exists, or `--character-description <definition>` otherwise. The text-defined route directly generates the complete grid; it must not first generate a standalone character image. A reference-image route must use a backend that accepts that exact image.

The compiled `image_generation_request` always declares `background` and `output_format`. After inspecting the callable schema, run `scripts/prepare_image_gen_call.py` with every exposed field as a repeated `--supported-argument`. The contract selects opaque-green-first for `reference-image` because the current Codex image_gen path does not reliably return native Alpha with a reference, and transparent-first for `text-defined-character` because that path can return native Alpha. The helper records the resolved policy, an opaque retry, and a single-call-per-attempt execution protocol. Before each call, use `scripts/static_generation_guard.py claim` and `invoked`, and write `static-generation-attempts.json`. Resolve tool results in this order: top-level `image_url`/`output_hint`, top-level data URL or bytes, then `content` image block. A missing `content` array is not a failure if a usable top-level result or output artifact exists. The runtime must judge returned pixels, not the model's claim: preserve valid native alpha, locally matte only a uniform high-contrast chroma key, and reject checkerboard/two-tone previews or unsafe opaque backgrounds. A reference-image retry remains standalone opaque `#00FF00`; a text-defined retry is used only after local Alpha validation fails and the first attempt is explicitly rejected in the ledger. Normalize again; if retry validation fails, stop and ask for regeneration rather than sending the sheet downstream.

Immediately inspect the returned image with `scripts/inspect_sticker_sheet.py` and create:

- `static-sheet.png`;
- `static-sheet-source.png`;
- `static-generation.json`;
- `static-generation-attempts.json`;
- `static-alpha.json`;
- `layout.json`;
- `layout-overlay.png` when review benefits from visible boundaries;
- `static-prompt.json`.

The static prompt records the user's text preference as `text_policy`. Text presence is informational after generation: no OCR or visual check may reject a sheet solely because text appeared or did not appear. Existing approved text must be preserved in downstream video prompts; do not add new text during animation.

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
