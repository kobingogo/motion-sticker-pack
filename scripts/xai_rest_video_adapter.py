#!/usr/bin/env python3
"""Direct xAI Videos API image-to-video adapter with ZDR upload support."""

from __future__ import annotations

import argparse
import base64
import http.client
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image

from config_contract import ContractError, read_json_object, validate_provider_config
from sticker_production_config import default_settings_path, load_production_settings
from video_adapter_common import (
    copy_video,
    download_video,
    duration_for_provider,
    load_task_and_prompt,
    resolution_for_provider,
    write_result,
)


PROVIDER_ID = "xai-direct"
API_BASE = "https://api.x.ai/v1"


class TransientNetworkError(ContractError):
    """A safe-to-retry read/poll failure; submission calls are never retried."""


def data_url(path: Path) -> str:
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
        path.suffix.lower(), "image/png"
    )
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def parse_key_color(value: str) -> tuple[int, int, int]:
    normalized = value.strip().upper()
    if len(normalized) != 7 or not normalized.startswith("#"):
        raise ContractError("key_color must use #RRGGBB format")
    try:
        return tuple(int(normalized[index:index + 2], 16) for index in (1, 3, 5))
    except ValueError as exc:
        raise ContractError("key_color must use #RRGGBB format") from exc


def keyed_image_data_url(path: Path, key_color: str) -> tuple[str, bool]:
    """Encode an opaque PNG so providers cannot flatten transparency to black."""
    rgb = parse_key_color(key_color)
    try:
        with Image.open(path) as source:
            rgba = source.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise ContractError(f"cannot decode xAI input image: {exc}") from exc
    alpha = rgba.getchannel("A")
    materialized = alpha.getextrema()[0] < 255
    alpha = alpha.point([0 if opacity <= 8 else opacity for opacity in range(256)])
    rgba.putalpha(alpha)
    background = Image.new("RGBA", rgba.size, (*rgb, 255))
    background.alpha_composite(rgba)
    payload = io.BytesIO()
    background.convert("RGB").save(payload, format="PNG", optimize=False)
    encoded = base64.b64encode(payload.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}", materialized


def xai_prompt(prompt: str, key_color: str | None) -> str:
    if key_color is None:
        return prompt.strip()
    return (
        f"{prompt.strip()}\n\n"
        "HARD OUTPUT CONTRACT FOR EVERY FRAME: keep every pixel outside the sticker "
        f"artwork as the exact flat uniform RGB color {key_color}. Preserve this color "
        "to the image edges and between every grid cell. Never use black, white, gray, "
        "a checkerboard, gradients, texture, lighting, shadows, or camera/background "
        "movement outside the artwork. Keep the camera fixed and each cell isolated."
    )


def json_request(url: str, api_key: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    encoded = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "motion-sticker-pack/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ContractError("xAI response exceeded 2 MiB")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ContractError("xAI response must be a JSON object")
            return value, {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read(64 * 1024).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            detail = payload.get("error") or payload.get("message") or raw
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
        except json.JSONDecodeError:
            detail = raw
        raise ContractError(f"xAI HTTP {exc.code}: {str(detail).strip() or exc.reason}") from exc
    except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise TransientNetworkError(f"xAI network request failed: {reason}") from exc


def request_id_from(value: dict[str, Any]) -> str:
    request_id = value.get("request_id") or value.get("id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ContractError("xAI submission response is missing request_id")
    return request_id.strip()


def status_name(value: dict[str, Any]) -> str:
    status = value.get("status")
    return status.lower() if isinstance(status, str) else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--provider-id", default=PROVIDER_ID)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request_id: str | None = None
    try:
        api_key = os.environ.get("XAI_API_KEY")
        if not api_key:
            raise ContractError("XAI_API_KEY is required")
        task, prompt = load_task_and_prompt(args.task)
        config = validate_provider_config(read_json_object(args.config))
        matches = [item for item in config["providers"] if item["id"] == args.provider_id]
        if len(matches) != 1 or matches[0].get("provider") != "xai":
            raise ContractError(f"provider {args.provider_id!r} is not one configured xAI provider")
        provider_config = matches[0]
        if provider_config.get("driver") != "command" or not provider_config.get("enabled"):
            raise ContractError(f"provider {args.provider_id!r} is not an enabled command provider")
        image = Path(task["input_image"]).resolve()
        duration = duration_for_provider(task, args.provider_id, default=3)
        model = provider_config.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ContractError(f"provider {args.provider_id!r} is missing its model")
        settings_path = Path(task.get("production_settings_file") or default_settings_path())
        configured_resolution = load_production_settings(settings_path)["generation"]["resolution"]
        resolution = resolution_for_provider(task, args.provider_id, default=configured_resolution)
        key_color = str(task.get("key_color") or "#00FF00").upper() if task.get("allow_key_background") else None
        if key_color is not None:
            image_url, input_background_materialized = keyed_image_data_url(image, key_color)
        else:
            image_url = data_url(image)
            input_background_materialized = False
        body: dict[str, Any] = {
            "model": model,
            "prompt": xai_prompt(prompt["grid_video_prompt"], key_color),
            "image": {"url": image_url},
            "duration": duration,
            "resolution": resolution,
        }
        upload_url = os.environ.get("XAI_VIDEO_UPLOAD_URL")
        if upload_url:
            if not upload_url.startswith("https://"):
                raise ContractError("XAI_VIDEO_UPLOAD_URL must use HTTPS")
            body["output"] = {"upload_url": upload_url}
        resume_request_id = os.environ.get("XAI_VIDEO_REQUEST_ID")
        headers: dict[str, str] = {}
        if resume_request_id:
            request_id = resume_request_id.strip()
            if not request_id:
                raise ContractError("XAI_VIDEO_REQUEST_ID must not be blank")
            submitted = {"status": "pending", "request_id": request_id}
        else:
            submitted, headers = json_request(
                f"{API_BASE}/videos/generations",
                api_key,
                method="POST",
                body=body,
            )
            request_id = request_id_from(submitted)
        deadline = time.monotonic() + float(task.get("timeout_seconds", 900))
        state = submitted
        consecutive_poll_errors = 0
        while status_name(state) not in {"done", "completed", "succeeded", "failed", "expired", "cancelled"}:
            if time.monotonic() >= deadline:
                raise ContractError(f"xAI video request {request_id} timed out")
            time.sleep(5)
            try:
                state, poll_headers = json_request(f"{API_BASE}/videos/{request_id}", api_key)
                consecutive_poll_errors = 0
            except TransientNetworkError:
                consecutive_poll_errors += 1
                if consecutive_poll_errors >= 5:
                    raise
                continue
            headers.update(poll_headers)
        if status_name(state) in {"failed", "expired", "cancelled"}:
            detail = state.get("error") or state.get("message") or status_name(state)
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
            raise ContractError(f"xAI video request {request_id} {status_name(state)}: {detail}")

        output_dir = Path(task["output_directory"]).resolve()
        target = output_dir / "xai-direct.mp4"
        if target.exists():
            raise ContractError(f"refusing to overwrite existing video: {target}")
        max_bytes = int(task.get("max_output_bytes", 200 * 1024 * 1024))
        video_data = state.get("video") if isinstance(state.get("video"), dict) else {}
        remote_url = video_data.get("url") or state.get("url")
        local_upload = os.environ.get("XAI_VIDEO_LOCAL_OUTPUT_PATH")
        download_url = os.environ.get("XAI_VIDEO_DOWNLOAD_URL")
        if local_upload and Path(local_upload).expanduser().is_file():
            video = copy_video(Path(local_upload).expanduser(), target, max_bytes)
        elif isinstance(remote_url, str) and remote_url.startswith(("https://", "http://")):
            video = download_video(remote_url, target, max_bytes)
        elif local_upload:
            local_path = Path(local_upload).expanduser()
            while not local_path.is_file() and time.monotonic() < deadline:
                time.sleep(0.5)
            video = copy_video(local_path, target, max_bytes)
        elif download_url:
            video = download_video(download_url, target, max_bytes)
        else:
            raise ContractError(
                "xAI completed without a retrievable video URL; for ZDR set both "
                "XAI_VIDEO_UPLOAD_URL and XAI_VIDEO_LOCAL_OUTPUT_PATH (or XAI_VIDEO_DOWNLOAD_URL)"
            )
        write_result(
            args.output,
            {
                "status": "succeeded",
                "provider": args.provider_id,
                "model": model,
                "output": str(video),
                "request_id": request_id,
                "duration_seconds": duration,
                "resolution": resolution,
                "zero_data_retention": headers.get("x-zero-data-retention"),
                "has_alpha": False,
                "input_background_materialized": input_background_materialized,
                "background_key": key_color,
            },
        )
        print(json.dumps({"status": "succeeded", "request_id": request_id, "output": str(video)}, ensure_ascii=False))
        return 0
    except (ContractError, OSError, ValueError) as exc:
        message = str(exc)
        write_result(
            args.output,
            {"status": "failed", "provider": args.provider_id, "request_id": request_id, "error": message},
        )
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
