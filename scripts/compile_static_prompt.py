#!/usr/bin/env python3
"""Compile reference image, style, and reaction input into a static-sheet prompt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


DEFAULT_PRESETS = Path(__file__).resolve().parents[1] / "references" / "style-presets.json"


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
            return style_id, preset["label"], preset["prompt"]
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
) -> dict:
    cleaned = [" ".join(item.split()) for item in expressions if item.strip()]
    if not cleaned:
        raise ValueError("at least one Emoji or short reaction description is required")
    if len(cleaned) > 24 or any(len(item) > 100 for item in cleaned):
        raise ValueError("use at most 24 reactions, each no longer than 100 characters")
    if len(reference_label) > 200:
        raise ValueError("reference label must not exceed 200 characters")
    count = columns * rows
    expression_text = "、".join(cleaned)
    count_text = chinese_number(count)
    prompt = (
        f"基于 {reference_label} 创建一套 {style_label} 贴纸包，并融入 {expression_text}。"
        f" {style_prompt}\n\n"
        f"创建一张正方形 (1:1) 透明贴纸页，优先包含{count_text}个各不相同的贴纸，"
        f"按 {columns}×{rows} 网格排列，每个贴纸呈现不同的表情、姿势或反应。"
        "贴纸之间留出较宽且完全透明的间隔。根据每个表情的语义和选定风格，合理加入少量匹配的装饰性反应元素，例如爱心、音符、星光、泪滴、腮红、汗滴或动作线；仅在合适的格子使用，不要每格强行添加，也不要引入与表情无关的大型新物体。无大面积背景、文字或跨格重叠元素。\n\n"
        "严格保持参考角色的身份、五官、发型或毛发、颜色、服装、身体比例和标志性特征。"
        "Emoji 和短描述用于表达情绪、动作或已有道具，不要把 Unicode Emoji 字符直接画成文字。"
        "如果提供的反应少于贴纸数量，在相同语义范围内补充互不重复、适合聊天的自然反应。"
        "每个主体和道具必须完整留在自己的格子内，并保留安全留白。"
    )
    reference = None
    if reference_image:
        resolved = Path(reference_image).expanduser().resolve(strict=True)
        reference = {"path": str(resolved), "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest()}
    return {
        "version": 1,
        "phase": "static-generation",
        "reference_label": reference_label,
        "style": {"id": style_id, "label": style_label, "prompt": style_prompt},
        "expressions": cleaned,
        "requested_layout": {"columns": columns, "rows": rows, "count": count},
        "static_sheet_prompt": prompt,
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
    parser.add_argument("--reference-image", type=Path, help="source image to bind to the generated static-sheet prompt")
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
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "requested_count": columns * rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
