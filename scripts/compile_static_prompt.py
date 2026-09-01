#!/usr/bin/env python3
"""Compile reference image, style, and reaction input into a static-sheet prompt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


DEFAULT_PRESETS = Path(__file__).resolve().parents[1] / "references" / "style-presets.json"
IMAGE_BACKGROUNDS = ("transparent", "opaque", "auto")
IMAGE_OUTPUT_FORMATS = ("png", "webp", "jpeg")
STICKER_OUTLINE_MODES = ("auto", "none", "white")


def infer_3d_variant(value: str | None) -> str | None:
    """Return an explicitly requested 3D treatment, if one is unambiguous.

    Bare ``3D`` deliberately returns ``None``: the model may choose one coherent
    3D treatment.  Only high-signal pairings such as ``3D 卡通`` or ``3D 写实``
    become hard style constraints.
    """
    if not value:
        return None
    compact = re.sub(r"[\s_\-]+", "", value.strip().lower())
    realistic_markers = (
        "3d写实", "写实3d", "3d真实", "真实3d", "3d真人", "真人3d",
        "3drealistic", "realistic3d", "3dphotorealistic", "photorealistic3d",
    )
    animation_markers = (
        "3d卡通", "卡通3d", "3d动画", "动画3d", "3d动漫", "动漫3d",
        "3d玩具", "玩具3d", "3d角色", "角色3d", "3dcartoon", "cartoon3d",
        "3danimation", "animation3d", "3dtoy", "toy3d", "toy", "玩具", "立体",
    )
    realistic = any(marker in compact for marker in realistic_markers)
    animation = any(marker in compact for marker in animation_markers)
    if realistic and animation:
        raise ValueError("3D style input contains conflicting animation and realistic sub-styles")
    if realistic:
        return "realistic"
    if animation:
        return "animation"
    return None


def infer_sticker_outline(value: str | None) -> str | None:
    """Infer an explicitly requested white sticker outline from natural text."""
    if not value:
        return None
    compact = re.sub(r"[\s_\-+]+", "", value.strip().lower())
    markers = (
        "白边", "白色描边", "白色贴纸", "贴纸边框", "贴纸白边",
        "whiteoutline", "whitesticker", "stickeroutline",
    )
    return "white" if any(marker in compact for marker in markers) else None


def _resolve_outline(
    requested: str,
    style_input: str | None,
    style_label: str,
    character_description: str | None,
) -> tuple[str, dict]:
    if requested not in STICKER_OUTLINE_MODES:
        raise ValueError(f"sticker_outline must be one of {', '.join(STICKER_OUTLINE_MODES)}")
    if requested != "auto":
        return requested, {
            "requested": requested,
            "resolved": requested,
            "source": "explicit-parameter",
        }
    for value, source in (
        (style_input, "style-input"),
        (style_label, "resolved-style-label"),
        (character_description, "character-description"),
    ):
        if infer_sticker_outline(value):
            return "white", {"requested": "auto", "resolved": "white", "source": source}
    return "none", {"requested": "auto", "resolved": "none", "source": None}


def _style_policy(
    style_id: str,
    style_label: str,
    style_prompt: str,
    style_input: str | None,
    character_description: str | None,
    sticker_outline: str,
) -> tuple[str, dict]:
    """Add explicit-vs-default style priority without overconstraining 3D."""
    outline_instruction = (
        "呈现优先级（硬约束）：用户选择了白边贴纸风。每格角色使用窄、均匀、纯白且连续的外轮廓，"
        "白边只包围本格主体与本格装饰，不跨越透明间隔；边缘保持干净的真实 alpha，不产生半透明白色毛边。\n"
        if sticker_outline == "white"
        else "呈现优先级（默认）：不主动添加白色贴纸外轮廓，只保留角色本身的自然边缘和透明留白。\n"
    )
    if style_id != "3d":
        return outline_instruction + style_prompt, {
            "mode": "fixed",
            "variant": None,
            "explicit": True,
            "source": "preset",
            "sticker_outline": sticker_outline,
        }

    candidates = [
        (infer_3d_variant(style_input), "style-input"),
        (infer_3d_variant(style_label), "resolved-style-label"),
        (infer_3d_variant(character_description), "character-description"),
    ]
    explicit = [(variant, source) for variant, source in candidates if variant]
    variants = {variant for variant, _ in explicit}
    if len(variants) > 1:
        raise ValueError("3D style input contains conflicting animation and realistic sub-styles")

    if not explicit:
        return (
            "风格选择规则：用户明确选择了 3D，但没有指定 3D 子风格；可自由选择一种统一的 3D 动画风或 3D 真实人物风格。"
            "两者都允许，不要把此默认规则误写成额外限制；一旦用户在风格输入或角色描述中明确指定子风格，必须严格遵守。"
            "在整张表情包中保持同一种 3D 处理，不要在写实人物和卡通角色之间混用。\n"
            + outline_instruction
            + style_prompt,
            {
                "mode": "free-choice",
                "variant": None,
                "explicit": False,
                "source": None,
                "sticker_outline": sticker_outline,
            },
        )

    variant, source = explicit[0]
    if variant == "animation":
        border_rule = (
            "白边贴纸轮廓必须保持窄、均匀、连续，不得变成厚重卡通边框。"
            if sticker_outline == "white"
            else "禁止照片质感、真人摄影式肖像和照片剪纸效果。"
        )
        strict = (
            outline_instruction
            + "风格优先级（硬约束）：用户明确指定 3D 动画/卡通风格，必须严格遵守。"
            "使用明确的非写实 3D 动画角色渲染、圆润雕塑感形体、统一的角色比例与柔和材质；"
            + border_rule
            + "\n"
        )
        label = "3D 动画风"
    else:
        border_rule = (
            "白边贴纸轮廓必须保持窄、均匀、连续，不得破坏写实人物边缘。"
            if sticker_outline == "white"
            else "禁止 Q 版、夸张头身比、卡通塑料质感和插画式白色贴纸轮廓。"
        )
        strict = (
            outline_instruction
            + "风格优先级（硬约束）：用户明确指定 3D 真实人物/写实风格，必须严格遵守。"
            "使用自然的人体比例、真实皮肤与服装材质、可信的三维灯光和写实人物渲染；"
            + border_rule
            + "\n"
        )
        label = "3D 真实人物风"
    return strict + style_prompt, {
        "mode": "explicit",
        "variant": variant,
        "explicit": True,
        "source": source,
        "label": label,
        "sticker_outline": sticker_outline,
    }


def parse_grid(value: str) -> tuple[int, int]:
    try:
        columns, rows = (int(part) for part in value.lower().split("x", 1))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("layout must use columnsxrows, for example 3x3") from exc
    if not (1 <= columns <= 12 and 1 <= rows <= 12) or columns * rows > 48:
        raise argparse.ArgumentTypeError("layout dimensions must be between 1 and 12 with at most 48 cells")
    return columns, rows


def chinese_number(value: int) -> str:
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 20:
        return "十" + (digits[value % 10] if value % 10 else "")
    if value < 100:
        return digits[value // 10] + "十" + (digits[value % 10] if value % 10 else "")
    return str(value)


def load_presets(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    presets = value.get("presets")
    if not isinstance(presets, dict) or not presets:
        raise ValueError("style presets must contain a non-empty presets object")
    for style_id, preset in presets.items():
        if not isinstance(style_id, str) or not isinstance(preset, dict):
            raise ValueError("every style preset must be an object with a string id")
        for field in ("label", "prompt"):
            if not isinstance(preset.get(field), str) or not preset[field].strip():
                raise ValueError(f"style preset {style_id!r} is missing {field}")
    return presets


def resolve_style(presets: dict, value: str, custom_prompt: str | None) -> tuple[str, str, str]:
    normalized = value.strip().lower()
    for style_id, preset in presets.items():
        aliases = {str(alias).strip().lower() for alias in preset.get("aliases", [])}
        if normalized == style_id.lower() or normalized in aliases:
            if style_id == "3d":
                variant = infer_3d_variant(value)
                if variant == "animation":
                    return style_id, "3D 动画风", preset["prompt"]
                if variant == "realistic":
                    return style_id, "3D 真实人物风", preset["prompt"]
            return style_id, preset["label"], preset["prompt"]
    # Accept natural combined inputs such as “3D 卡通白边贴纸” without
    # making every presentation option a separate preset alias.
    compact = re.sub(r"[\s_\-]+", "", normalized)
    if "3d" in compact:
        variant = infer_3d_variant(value)
        if variant == "animation":
            return "3d", "3D 动画风", presets["3d"]["prompt"]
        if variant == "realistic":
            return "3d", "3D 真实人物风", presets["3d"]["prompt"]
        if infer_sticker_outline(value):
            return "3d", "3D", presets["3d"]["prompt"]
    if normalized == "custom" and custom_prompt:
        if len(custom_prompt.strip()) > 500:
            raise ValueError("custom style prompt must not exceed 500 characters")
        return "custom", "自定义", custom_prompt.strip()
    choices = ", ".join(sorted(presets))
    raise ValueError(f"unknown style {value!r}; choose one of {choices}, or use custom with --style-prompt")


def compile_prompt(
    reference_label: str,
    style_id: str,
    style_label: str,
    style_prompt: str,
    expressions: list[str],
    columns: int,
    rows: int,
    reference_image: str | None = None,
    background: str = "transparent",
    output_format: str = "png",
    character_description: str | None = None,
    include_text: bool = False,
    style_input: str | None = None,
    sticker_outline: str = "auto",
) -> dict:
    cleaned = [" ".join(item.split()) for item in expressions if item.strip()]
    if not cleaned:
        raise ValueError("at least one Emoji or short reaction description is required")
    if len(cleaned) > 24 or any(len(item) > 100 for item in cleaned):
        raise ValueError("use at most 24 reactions, each no longer than 100 characters")
    if len(reference_label) > 200:
        raise ValueError("reference label must not exceed 200 characters")
    if reference_image and character_description:
        raise ValueError("choose exactly one character source: reference image or character description")
    if character_description and len(character_description.strip()) > 1000:
        raise ValueError("character description must not exceed 1000 characters")
    if background not in IMAGE_BACKGROUNDS:
        raise ValueError(f"background must be one of {', '.join(IMAGE_BACKGROUNDS)}")
    if output_format not in IMAGE_OUTPUT_FORMATS:
        raise ValueError(f"output format must be one of {', '.join(IMAGE_OUTPUT_FORMATS)}")
    if background == "transparent" and output_format == "jpeg":
        raise ValueError("transparent image generation requires png or webp output")
    count = columns * rows
    expression_text = "、".join(cleaned)
    count_text = chinese_number(count)
    sheet_name = "九宫格" if (columns, rows) == (3, 3) else f"{columns}×{rows}、共{count_text}格的网格"
    cells_name = "九格" if count == 9 else f"{count_text}格"
    text_instruction = (
        "文字策略：允许在合适的格子加入简短、清晰的反应文字，但文字不是硬性要求；"
        "不要让文字遮挡角色，也不要把 Unicode Emoji 字符直接当作文字贴上去。"
        if include_text
        else (
            "文字策略：默认不主动添加文字，只用表情、姿势和少量图形化装饰表达反应；"
            "如果模型仍然生成了文字，不把它视为生成失败，不拦截、不强制重生成，交由用户在静态审核时决定是否保留。"
        )
    )
    cleaned_description = " ".join(character_description.split()) if character_description else None
    effective_outline, outline_policy = _resolve_outline(
        sticker_outline,
        style_input,
        style_label,
        cleaned_description,
    )
    effective_style_prompt, style_policy = _style_policy(
        style_id,
        style_label,
        style_prompt,
        style_input,
        cleaned_description,
        effective_outline,
    )
    effective_style_label = style_policy.get("label", style_label)
    if reference_image:
        identity_source = f"基于 {reference_label}"
        direct_sheet_instruction = ""
    else:
        identity_source = f"直接根据角色定义“{cleaned_description or reference_label}”"
        direct_sheet_instruction = (
            f"不要先生成单张角色图、角色设定图或中间定稿图；直接输出完整{sheet_name}源图。"
            "所有格子必须呈现同一个角色，并保持可辨识的脸部、发型、体型、服装和配色一致。"
        )
    prompt = (
        f"{identity_source} 创建一套 {effective_style_label} 动态表情包的静态{sheet_name}源图，并融入 {expression_text}。"
        f" {effective_style_prompt}\n\n"
        f"{direct_sheet_instruction}"
        f"创建一张正方形 (1:1) 透明{sheet_name}插画卡片源图，优先包含{count_text}个各不相同的表情卡片，"
        f"按 {columns}×{rows} 网格排列，每格呈现不同的表情、姿势或反应。"
        + ("使用统一、窄且干净的白色贴纸外轮廓；" if effective_outline == "white" else "默认采用无白边、无厚描边的卡片呈现；")
        + f"每格加入轻微、局部、与情绪匹配的背景点缀，{cells_name}保持统一色调，"
        "【真实透明度硬约束】首次调用必须优先输出保留真实 alpha 通道的 RGBA PNG；所有透明区域（包括整张画布边缘、格间留白和每格主体外侧留白）的 alpha 必须为 0，不能把透明效果画成可见图案。首次透明调用严禁绘制棋盘格、灰白方格、透明预览底、黑底、白底、渐变底、彩色纯色底、地面、背景板、相框或大面积背景阴影；不要将图像扁平化成 RGB/JPEG。若首次调用无法产生真实 alpha，备用调用才允许按照备用指令使用完全一致的纯色抠像底，且不得用棋盘格或其他模拟透明效果冒充透明输出。\n"
        "卡片之间留出较宽且完全透明的间隔。根据每个表情的语义和选定风格，合理加入少量匹配的装饰性反应元素，例如爱心、音符、星光、泪滴、腮红、汗滴或动作线；仅在合适的格子使用，不要每格强行添加，也不要引入与表情无关的大型新物体。"
        f"{text_instruction} 背景点缀必须轻微、局部、留在自己的格子内，不得形成整格矩形底板、跨格重叠或大面积阴影。\n\n"
        "严格保持参考角色的身份、五官、发型或毛发、颜色、服装、身体比例和标志性特征。"
        "Emoji 和短描述用于表达情绪、动作或已有道具，不要把 Unicode Emoji 字符直接画成文字。"
        "如果提供的反应少于贴纸数量，在相同语义范围内补充互不重复、适合聊天的自然反应。"
        "每个主体和道具必须完整留在自己的格子内，并保留安全留白。"
    )
    # Keep the opaque fallback as a standalone prompt.  Appending a green-key
    # suffix to the transparent-first prompt leaves contradictory instructions
    # (for example, "real alpha" and "transparent sheet") in the request, so
    # image models may continue rendering a checkerboard preview.  The
    # fallback preserves identity/style/reactions but has one unambiguous
    # background contract: a uniform #00FF00 plate.
    opaque_fallback_prompt = (
        f"{identity_source} 创建一套 {style_label} 动态表情包的不透明纯色抠像源图，并融入 {expression_text}。"
        f" {style_prompt}\n\n"
        f"{direct_sheet_instruction}"
        f"创建一张正方形 (1:1) 的 {sheet_name} 插画卡片源图，包含{count_text}个各不相同的表情卡片，"
        f"按 {columns}×{rows} 网格排列，每格呈现不同的表情、姿势或反应。默认采用无白边、无厚描边的卡片呈现；"
        f"每格加入轻微、局部、与情绪匹配的背景点缀，{cells_name}保持统一色调。"
        "【纯色抠像硬约束】整张画布的留白和格间必须是完全一致的纯 #00FF00；"
        "背景只能使用这一种颜色，禁止棋盘格、灰白方格、渐变、纹理、阴影、地面、背景板、相框或其他颜色。"
        "不要改变角色身份、五官、发型、体型、服装、配色、已有道具或动作语义。"
        "卡片之间留出清晰间隔，所有主体和道具必须完整留在自己的格子内并保留安全留白。"
        f"{text_instruction} 背景点缀必须轻微、局部、留在自己的格子内，不得形成整格矩形底板、跨格重叠或大面积阴影。"
    )
    reference = None
    if reference_image:
        resolved = Path(reference_image).expanduser().resolve(strict=True)
        reference = {"path": str(resolved), "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest()}
    return {
        "version": 2,
        "phase": "static-generation",
        "reference_label": reference_label,
        "source_mode": "reference-image" if reference_image else "text-defined-character",
        "character_description": cleaned_description,
        "style": {"id": style_id, "label": effective_style_label, "prompt": style_prompt},
        "style_policy": style_policy,
        "outline_policy": outline_policy,
        "expressions": cleaned,
        "text_policy": {
            "user_requested_text": include_text,
            "default": "allow" if include_text else "avoid",
            "post_generation": "record-only",
            "generated_text_is_not_a_failure": True,
        },
        "requested_layout": {"columns": columns, "rows": rows, "count": count},
        "static_sheet_prompt": prompt,
        "image_generation_request": {
            "preferred_tool": "image_gen",
            "arguments": {
                "background": background,
                "output_format": output_format,
            },
            "argument_policy": "pass-when-supported",
            "unsupported_argument_policy": "omit-and-record",
            "result_contract": {
                "require_real_alpha": background == "transparent",
                "reject_simulated_transparency": True,
                "normalize_to": "image/png",
            },
            "opaque_fallback": {
                "arguments": {
                    "background": "opaque",
                    "output_format": "png",
                },
                "key_color": "#00FF00",
                "prompt": opaque_fallback_prompt,
                "prompt_mode": "standalone-opaque",
                "prompt_suffix": (
                    "备用调用（首次真实透明输出未通过本地检查）：不要尝试透明输出，"
                    "将所有空白区域渲染为完全一致的 #00FF00 纯绿色，"
                    "不要棋盘格、纹理、渐变、阴影、地面或环境背景。"
                ),
            },
        },
        "next_phase": "static-review",
        "requires_user_approval_before_video": True,
        "reference_image": reference,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--style", required=True, help="3d, chibi, cute, hand-drawn, manga, pixel-art, realistic, retro, or custom")
    parser.add_argument("--style-prompt", help="required when --style custom")
    parser.add_argument("--expression", action="append", default=[], help="repeat for each Emoji or reaction")
    parser.add_argument("--expressions", help="one combined Emoji or short-description string")
    parser.add_argument("--layout", type=parse_grid, default=(3, 3))
    parser.add_argument("--reference-label", default="所附图像")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--reference-image", type=Path, help="source image to bind to the generated static-sheet prompt")
    source.add_argument(
        "--character-description",
        help="character name or concise visual definition when no reference image is supplied",
    )
    parser.add_argument(
        "--include-text",
        action="store_true",
        help="allow short reaction text; default is to avoid text without rejecting model-added text",
    )
    parser.add_argument("--background", choices=IMAGE_BACKGROUNDS, default="transparent")
    parser.add_argument("--output-format", choices=IMAGE_OUTPUT_FORMATS, default="png")
    parser.add_argument(
        "--sticker-outline",
        choices=STICKER_OUTLINE_MODES,
        default="auto",
        help="sticker outline policy: auto-detect explicit white-sticker wording, or force none/white",
    )
    parser.add_argument("--presets", type=Path, default=DEFAULT_PRESETS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    expressions = list(args.expression)
    if args.expressions:
        expressions.append(args.expressions)
    presets = load_presets(args.presets)
    style_id, style_label, style_prompt = resolve_style(
        presets, args.style, args.style_prompt
    )
    columns, rows = args.layout
    result = compile_prompt(
        args.reference_label,
        style_id,
        style_label,
        style_prompt,
        expressions,
        columns,
        rows,
        str(args.reference_image) if args.reference_image else None,
        args.background,
        args.output_format,
        args.character_description,
        args.include_text,
        args.style,
        args.sticker_outline,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "requested_count": columns * rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
