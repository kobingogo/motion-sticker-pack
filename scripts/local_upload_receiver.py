#!/usr/bin/env python3
"""One-shot local HTTPS-tunnel target for validating xAI ZDR video uploads."""

from __future__ import annotations

import argparse
import json
import secrets
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class UploadHandler(BaseHTTPRequestHandler):
    server_version = "MotionStickerUpload/1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _allowed(self) -> bool:
        return bool(self.server.s3_compat) or self.path.split("?", 1)[0] == self.server.upload_path  # type: ignore[attr-defined]

    def do_HEAD(self) -> None:
        self.send_response(200 if self._allowed() else 404)
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "PUT, POST, HEAD, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        target = self.server.target  # type: ignore[attr-defined]
        if not self._allowed() or not target.is_file():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with target.open("rb") as reader:
            while chunk := reader.read(1024 * 1024):
                self.wfile.write(chunk)

    def do_POST(self) -> None:
        self._receive()

    def do_PUT(self) -> None:
        self._receive()

    def _read_chunked(self, writer: object, max_bytes: int) -> int:
        total = 0
        while True:
            line = self.rfile.readline(128).strip().split(b";", 1)[0]
            size = int(line, 16)
            if size == 0:
                self.rfile.readline()
                return total
            total += size
            if total > max_bytes:
                raise ValueError("upload too large")
            payload = self.rfile.read(size)  # type: ignore[attr-defined]
            if len(payload) != size:
                raise ValueError("incomplete chunk payload")
            writer.write(payload)  # type: ignore[attr-defined]
            if self.rfile.read(2) != b"\r\n":
                raise ValueError("invalid chunk terminator")

    def _receive(self) -> None:
        if not self._allowed():
            self.send_error(404)
            return
        max_bytes = self.server.max_bytes  # type: ignore[attr-defined]
        target = self.server.target  # type: ignore[attr-defined]
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False) as writer:
                temp_path = Path(writer.name)
                if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                    total = self._read_chunked(writer, max_bytes)
                else:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > max_bytes:
                        raise ValueError("invalid content length")
                    remaining = length
                    total = 0
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("incomplete upload")
                        writer.write(chunk)
                        total += len(chunk)
                        remaining -= len(chunk)
            if total <= 0:
                raise ValueError("empty upload")
            temp_path.replace(target)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            self.server.upload_complete = True  # type: ignore[attr-defined]
        except (OSError, ValueError) as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            self.send_error(400, str(exc))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", default=None)
    parser.add_argument("--max-bytes", type=int, default=200 * 1024 * 1024)
    parser.add_argument("--s3-compat", action="store_true")
    parser.add_argument("--serve-after-upload-seconds", type=float, default=0)
    args = parser.parse_args()
    target = args.output.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing upload: {target}")
    token = args.token or secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), UploadHandler)
    server.target = target  # type: ignore[attr-defined]
    server.upload_path = f"/{token}.mp4"  # type: ignore[attr-defined]
    server.max_bytes = args.max_bytes  # type: ignore[attr-defined]
    server.s3_compat = args.s3_compat  # type: ignore[attr-defined]
    server.upload_complete = False  # type: ignore[attr-defined]
    server.timeout = 1
    print(
        json.dumps(
            {"port": server.server_address[1], "path": server.upload_path, "output": str(target)},
            ensure_ascii=False,
        ),
        flush=True,
    )
    while not server.upload_complete:  # type: ignore[attr-defined]
        server.handle_request()
    deadline = time.monotonic() + max(0, args.serve_after_upload_seconds)
    while time.monotonic() < deadline:
        server.handle_request()
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
