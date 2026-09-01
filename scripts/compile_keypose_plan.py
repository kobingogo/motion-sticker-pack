#!/usr/bin/env python3
"""Compile an auditable per-sticker key-pose generation plan.

The plan is deliberately separate from rendering: image generation can happen
in any approved tool, while this script records the exact source cells,
motion intent, and the four-pose contract consumed by ``prepare_keyposes.py``.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from artifact_manifest import record_artifact
from output_safety import begin_output_transaction


def natural_key(path: Path) -> list[tuple[int, object]]:
    return [(0, int(part)) if part.isdigit() else (1, part.casefold()) for part in re.split(r"(\d+)", path.name) if part]


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_reactions(value: str) -> list[str]:
    """Accept a JSON/list file, newline-delimited text, or a compact CSV."""
    candidate = Path(value).expanduser()
    if candidate.is_file():
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("reactions") or payload.get("expressions")
        if not isinstance(payload, list):
            raise ValueError("reaction file must contain a JSON array or a reactions array")
        values = payload
    else:
        values = re.split(r"\s*[,|]\s*|\s*\n\s*", value.strip())
    reactions = [str(item).strip() for item in values if str(item).strip()]
    if not reactions:
        raise ValueError("at least one reaction is required")
    if any(len(item) > 100 for item in reactions):
        raise ValueError("each reaction must be at most 100 characters")
    return reactions


def load_motions(path: Path | None, count: int) -> list[str] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("motions") or payload.get("tiles")
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        payload = [item.get("motion") for item in payload]
    if not isinstance(payload, list) or len(payload) != count:
        raise ValueError(f"motion file must contain exactly {count} items")
    motions = [str(item).strip() for item in payload]
    if any(not item or len(item) > 200 for item in motions):
        raise ValueError("each motion must be 1-200 characters")
    return motions


def suggested_motion(reaction: str) -> str:
    lowered = reaction.casefold()
    rules = [
        (("哭", "😭", "伤心"), "收紧眼睛和嘴角形成更明显的哭泣峰值，再恢复"),
        (("笑", "开心", "😄", "😂"), "先轻微吸气，再让笑脸和脸颊形成清晰峰值，回到原表情"),
        (("庆祝", "🎉", "胜利"), "手臂先收拢蓄力，再抬到庆祝峰值，随后放松"),
        (("惊", "😱", "🤯", "震惊"), "身体和眉眼小幅后缩形成惊讶峰值，再回到起始姿势"),
        (("睡", "😴", "困"), "眼皮和头部缓慢下沉到打盹峰值，再抬回起始姿势"),
    ]
    for tokens, motion in rules:
        if any(token.casefold() in lowered for token in tokens):
            return motion
    return "依据该格现有表情和身体结构做一个小幅、可读的原地动作，随后回到起始姿势"


def build_prompt(index: int, reaction: str, motion: str) -> str:
    return f"""使用附带的第 {index:02d} 格贴纸作为唯一角色和风格参考，反应主题为“{reaction}”。意图动作：{motion}。

输出一个固定镜头、固定角色比例的 2 列 × 2 行姿势页，四格依次为 START、ANTICIPATION、ACTION PEAK、RECOVERY。保留角色身份、五官、服装、颜色、材质、已有道具和可见文字；不要凭反应词新增道具、人物、肢体、场景或字幕。角色中心和脚底基线固定，动作必须来自真实的面部或关节姿势变化，禁止整层平移、旋转、缩放、弹跳或摇摆。

四格之间使用连续、均匀、不透明的纯绿色 #00FF00，禁止棋盘格、渐变、纹理、阴影、描边和发光。每格主体留出安全边距，不得越过格子边界。只输出 2×2 PNG 姿势页；本地流程会把纯色背景转换为真实 Alpha。"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="numbered source cells, e.g. 01.png … NN.png")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reactions", required=True, help="reaction list, text file, or JSON array file")
    parser.add_argument("--motions-file", type=Path)
    parser.add_argument("--manifest", type=Path, help="artifact-manifest.json for hash lineage")
    parser.add_argument("--workspace", type=Path, help="manifest workspace; defaults to the manifest parent")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    source_dir = args.input_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise ValueError("input-dir must be a directory")
    sources = sorted((path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() == ".png"), key=natural_key)
    if not 1 <= len(sources) <= 48:
        raise ValueError("input-dir must contain between 1 and 48 PNG cells")
    reactions = parse_reactions(args.reactions)
    if len(reactions) != len(sources):
        raise ValueError(f"reaction count {len(reactions)} does not match source cell count {len(sources)}")
    motions = load_motions(args.motions_file, len(sources))
    protected = [source_dir]
    if args.motions_file:
        protected.append(args.motions_file.expanduser().resolve())
    if args.manifest:
        protected.append(args.manifest.expanduser().resolve())
    transaction = begin_output_transaction(args.output_dir, overwrite=args.overwrite, protected_paths=protected)
    output = transaction.output
    prompts_dir = output / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    digits = max(2, len(str(len(sources))))
    tiles = []
    for index, (source, reaction) in enumerate(zip(sources, reactions), start=1):
        motion = motions[index - 1] if motions else suggested_motion(reaction)
        prompt_path = prompts_dir / f"{index:0{digits}d}.txt"
        prompt_path.write_text(build_prompt(index, reaction, motion) + "\n", encoding="utf-8")
        tiles.append({
            "id": f"{index:0{digits}d}",
            "reaction": reaction,
            "source_cell": str(source),
            "source_sha256": sha256_file(source),
            "motion": motion,
            "prompt": str(prompt_path.relative_to(output)),
            "expected_pose_sheet": f"{index:0{digits}d}.png",
            "layout": {"columns": 2, "rows": 2, "count": 4},
            "poses": ["start", "anticipation", "peak", "recovery"],
        })
    plan = {
        "version": 1,
        "mode": "keypose-local",
        "source_count": len(sources),
        "pose_count_per_sticker": 4,
        "motion_source": "vision-reviewed-overrides" if motions else "reaction-semantic-suggestion-requires-visual-review",
        "tiles": tiles,
        "forbidden_fallbacks": ["whole-layer-translation", "whole-layer-rotation", "whole-layer-scale", "bounce", "shake", "sway"],
    }
    (output / "keypose-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    transaction.commit()
    manifest_result = None
    if args.manifest:
        manifest = args.manifest.expanduser().resolve()
        workspace = args.workspace.expanduser().resolve() if args.workspace else manifest.parent
        source_ids = [
            record_artifact(manifest, source, kind="keypose-source-cell", stage="keypose-planned", workspace=workspace)
            for source in sources
        ]
        prompt_ids = [
            record_artifact(
                manifest,
                prompts_dir / f"{index:0{digits}d}.txt",
                kind="keypose-prompt",
                stage="keypose-planned",
                dependencies=[source_ids[index - 1]],
            )
            for index in range(1, len(sources) + 1)
        ]
        plan_id = record_artifact(
            manifest,
            output / "keypose-plan.json",
            kind="keypose-plan",
            stage="keypose-planned",
            dependencies=[*source_ids, *prompt_ids],
        )
        manifest_result = {"path": str(manifest), "plan_artifact_id": plan_id}
    print(json.dumps({"plan": str((output / 'keypose-plan.json').resolve()), "count": len(sources), "artifact_manifest": manifest_result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
