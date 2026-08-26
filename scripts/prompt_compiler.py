#!/usr/bin/env python3
"""Compile internally consistent static-sheet and grid-video prompts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def load_layout(path: Path, allow_low_confidence: bool = False) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    detected = data.get("detected_layout", data)
    columns = int(detected["columns"])
    rows = int(detected["rows"])
    count = columns * rows
    if not (1 <= columns <= 12 and 1 <= rows <= 12 and count <= 48):
        raise ValueError("detected layout must be at most 48 cells with dimensions from 1 to 12")
    if int(detected.get("count", count)) != count:
        raise ValueError("layout count must equal columns * rows")
    confidence = detected.get("confidence")
    if confidence is not None and float(confidence) < 0.75 and not allow_low_confidence:
        raise ValueError("layout confidence is below 0.75; confirm an override or pass --allow-low-confidence")
    return {"columns": columns, "rows": rows, "count": count, "confidence": confidence}


def load_tile_plan(path: Path | None, count: int, allow_generic: bool = False) -> list[dict]:
    if path is None:
        if not allow_generic:
            raise ValueError(
                "a per-cell --tile-plan is required; use --allow-generic-motions only for an intentional fallback"
            )
        return [
            {
                "id": f"{index:02d}",
                "motion": "根据该格现有表情和姿势做一个幅度很小的独立动作",
                "loop": "return-to-start",
                "amplitude": "small",
            }
            for index in range(1, count + 1)
        ]
    data = json.loads(path.read_text(encoding="utf-8"))
    tiles = data.get("tiles", data)
    if not isinstance(tiles, list):
        raise ValueError("tile plan must be an array or an object containing a tiles array")
    if len(tiles) != count:
        raise ValueError(f"tile plan has {len(tiles)} items; detected layout requires {count}")
    normalized = []
    for index, tile in enumerate(tiles, start=1):
        if not isinstance(tile, dict):
            raise ValueError(f"tile {index} must be an object")
        motion = tile.get("motion")
        if not isinstance(motion, str) or not motion.strip() or len(motion) > 200:
            raise ValueError(f"tile {index} motion must be 1-200 characters")
        tile_id = str(tile.get("id", f"{index:02d}"))
        if tile_id != f"{index:02d}":
            raise ValueError(f"tile {index} id must be {index:02d}")
        normalized.append({**tile, "id": tile_id, "motion": motion.strip()})
    return normalized


def compile_prompts(layout: dict, tiles: list[dict], key_color: str | None) -> dict:
    columns, rows, count = layout["columns"], layout["rows"], layout["count"]
    grid = f"{columns} 列 × {rows} 行"
    motions = "\n".join(
        f"- 第 {index:02d} 格：{tile['motion']}；幅度 {tile.get('amplitude', 'small')}；循环 {tile.get('loop', 'return-to-start')}。"
        for index, tile in enumerate(tiles, start=1)
    )
    background = (
        f"若无法输出真实透明通道，统一使用纯色 {key_color}，无阴影、渐变或纹理。"
        if key_color
        else "优先输出真实透明通道；若模型不支持，执行前必须选择与所有角色颜色高对比的单一纯色键背景。"
    )
    video_prompt = f"""严格保持输入图像实际检测到的 {grid} 网格布局不变，共 {count} 格，并将这张表情图板动画化。

镜头完全固定：禁止推近、拉远、平移、旋转、倾斜和抖动。每个角色只能在自己的格子内独立运动；禁止任何跨格、全画面同步运动或格子之间互相影响。严格保持每格角色的造型、五官、颜色、服装、身体比例、已有道具、位置和整体构图。不要新增角色、肢体、物体、文字、特效、边框、背景或阴影，不要改变画布比例。

逐格动作（按从左到右、从上到下编号）：
{motions}

每格动作自然、简洁、适合聊天，结束时回到初始姿势或按指定循环策略闭合。任何主体和已有道具都不能触碰或越过格子边界。{background} 不要用棋盘格模拟透明，不要产生白边、黑边、残留背景或半透明脏边。"""
    negative_prompt = (
        "camera motion, zoom, pan, tilt, roll, shake, global animation, synchronized board movement, "
        "cross-cell interaction, layout change, extra character, extra limb, duplicate prop, text, caption, "
        "border, scene, floor, gradient background, backdrop shadow, checkerboard transparency, white fringe, "
        "black fringe, dirty semi-transparent edge"
    )
    return {
        "version": 1,
        "detected_layout": layout,
        "static_sheet_prompt_template": (
            "基于所附角色图像创建一套 3D 贴纸页。优先使用宽而完全透明的间隔；"
            "角色采用圆润玩具造型、光滑材质、柔和棚拍光和轻微环境遮蔽。"
            "生成后不要假定布局正确，必须重新检测实际行列数。"
        ),
        "grid_video_prompt": video_prompt,
        "negative_prompt": negative_prompt,
        "tile_plan": tiles,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--tile-plan", type=Path)
    parser.add_argument("--key-color", type=str)
    parser.add_argument("--allow-generic-motions", action="store_true")
    parser.add_argument("--allow-low-confidence", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.key_color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", args.key_color):
        raise ValueError("key color must use #RRGGBB notation")
    layout = load_layout(args.layout, args.allow_low_confidence)
    tiles = load_tile_plan(args.tile_plan, layout["count"], args.allow_generic_motions)
    compiled = compile_prompts(layout, tiles, args.key_color)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(compiled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "count": layout["count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
