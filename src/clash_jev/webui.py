"""Serve the local browser interface and its calibration and gameplay endpoints.

Provide static assets, saved run data, live frames, and bot controls. Calibration
routes inspect screenshots, save card templates, and persist layout settings;
the same server also supports viewing recorded sessions.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

from .config import Config
from .hud import HUDReader

STATIC = Path(__file__).parent / "static"


def make_server(
    *,
    port: int,
    run: Path | None = None,
    image: Path | None = None,
    config_path: Path | None = None,
    output: Path | None = None,
    root: Path | None = None,
    live=None,
):
    root = (root or Path.cwd()).resolve()
    config = Config.load(config_path) if config_path else None
    screenshot = Image.open(image).convert("RGB") if image else None

    class Handler(BaseHTTPRequestHandler):
        # Reuse the connection for JPEG frames instead of opening one per frame.
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True
        timeout = 10

        def log_message(self, *args):
            pass

        def respond(self, content: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def json(self, value, status=200):
            self.respond(json.dumps(value).encode(), "application/json", status)

        def video(self, file: Path):
            """Stream the selected run's video, including browser seek requests."""
            size = file.stat().st_size
            start, end, status = 0, size - 1, 200
            requested = self.headers.get("Range")
            if requested:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                if not match or not any(match.groups()):
                    return self.json({"error": "Invalid range"}, 416)
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), end) if right else end
                else:
                    start = max(0, size - int(right))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
            self.send_response(status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with file.open("rb") as source:
                    source.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = source.read(min(256 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Seeking cancels the preceding video request.

        def do_GET(self):
            path = urlparse(self.path).path
            active_run = live.run if live else run
            if live and path == "/api/live":
                revision = parse_qs(urlparse(self.path).query).get("revision", [None])[0]
                return self.json(live.snapshot(revision=revision))
            if live and path == "/live-frame.jpg":
                query = parse_qs(urlparse(self.path).query)
                try:
                    after = int(query.get("after", ["-1"])[0])
                    delay = float(query.get("delay", ["0"])[0])
                    if delay not in (0, 5):
                        raise ValueError("Unsupported display delay")
                except ValueError:
                    return self.json({"error": "Invalid frame parameters"}, 400)
                sequence, jpeg, captured_at = live.stream.next_frame(after, delay)
                if jpeg is None:
                    return self.respond(b"", "image/jpeg", 204)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Sequence", str(sequence))
                self.send_header("X-Frame-Time", str(captured_at))
                self.end_headers()
                try:
                    self.wfile.write(jpeg)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if live and path == "/live.mjpg":
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                after = -1
                try:
                    while not live.stream.closed.is_set():
                        sequence, jpeg = live.stream.next(after)
                        if jpeg is None or sequence == after:
                            continue
                        self.wfile.write(
                            b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                            + str(len(jpeg)).encode()
                            + b"\r\n\r\n"
                            + jpeg
                            + b"\r\n"
                        )
                        self.wfile.flush()
                        after = sequence
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if path in {"/", "/app.js", "/replay-state.js", "/style.css"}:
                file = STATIC / ("index.html" if path == "/" else path[1:])
                self.respond(file.read_bytes(), mimetypes.guess_type(file)[0] or "text/plain")
            elif path == "/api/info":
                self.json({"mode": "live" if live else "calibrate" if image else "viewer"})
            elif path == "/api/config" and config:
                self.json(config.model_dump(mode="json"))
            elif path == "/capture.png" and screenshot:
                import io

                buffer = io.BytesIO()
                screenshot.save(buffer, "PNG")
                self.respond(buffer.getvalue(), "image/png")
            elif active_run and path in {
                "/api/manifest",
                "/api/latest",
                "/api/summary",
                "/api/outcome",
            }:
                file = active_run / (path.split("/")[-1] + ".json")
                self.json(json.loads(file.read_text()) if file.exists() else {})
            elif active_run and path == "/api/media":
                metadata = active_run / "playback.json"
                video = active_run / "gameplay.mp4"
                # A video is only synchronized when an explicit alignment exists.
                if metadata.is_file() and video.is_file():
                    self.json({**json.loads(metadata.read_text()), "url": "/gameplay.mp4"})
                else:
                    self.json({})
            elif active_run and path == "/gameplay.mp4" and (active_run / "gameplay.mp4").is_file():
                self.video(active_run / "gameplay.mp4")
            elif active_run and path in {"/api/events", "/api/control"}:
                file = active_run / ("events.jsonl" if path == "/api/events" else "control.jsonl")
                rows = []
                if file.exists():
                    for line in file.read_text().splitlines():
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            pass  # A live writer may not have finished the final line yet.
                self.json(rows)
            elif active_run and path.startswith("/frames/"):
                name = path.removeprefix("/frames/")
                file = (active_run / name).resolve()
                if (
                    file.parent == active_run.resolve()
                    and name.startswith("frame-")
                    and file.suffix == ".jpg"
                    and file.is_file()
                ):
                    self.respond(file.read_bytes(), "image/jpeg")
                else:
                    self.json({"error": "Not found"}, 404)
            else:
                self.json({"error": "Not found"}, 404)

        def do_POST(self):
            nonlocal config
            # Control routes need no request body; don't reuse a socket with
            # unread bytes after accepting or rejecting one of these requests.
            self.close_connection = True
            # Writes are limited to calibration or explicitly enabled live controls.
            origin = self.headers.get("Origin")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if origin and origin != expected:
                return self.json({"error": "Invalid origin"}, 403)
            path = urlparse(self.path).path
            if live and path in {"/api/live/start", "/api/live/stop"}:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.json({"error": "Expected JSON"}, 415)
                try:
                    if path.endswith("/start"):
                        live.start()
                    else:
                        live.stop()
                    return self.json(live.snapshot())
                except (ValueError, RuntimeError) as exc:
                    return self.json({"error": str(exc)}, 400)
            if not screenshot or not config or not output:
                return self.json({"error": "Read-only viewer"}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100000:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                incoming = Config.model_validate(body["config"])
                incoming.layout.reference_size = screenshot.size
                if path == "/api/inspect":
                    reader = HUDReader(incoming, root)
                    return self.json(
                        {
                            "hud": reader.read(screenshot).model_dump(mode="json"),
                            "missing_templates": reader.missing_templates(),
                        }
                    )
                if path == "/api/template":
                    card = incoming.cards[body["card"]]
                    slot = int(body["slot"])
                    if slot not in range(4):
                        raise ValueError("Slot must be 0–3")
                    crop = screenshot.crop(incoming.layout.hand[slot].pixels(screenshot.size))
                    fingerprint = hashlib.sha256(crop.tobytes()).hexdigest()[:10]
                    relative = f"assets/cards/{card.id}-{fingerprint}.png"
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    crop.save(target)
                    if relative not in card.templates:
                        card.templates.append(relative)
                    config = incoming
                    config.save(output)
                    return self.json(config.model_dump(mode="json"))
                if path == "/api/save":
                    if not body.get("confirmed"):
                        raise ValueError("Review the regions before marking calibration complete")
                    incoming.layout.calibrated = True
                    incoming.save(output)
                    config = incoming
                    return self.json(
                        {"saved": str(output), "config": config.model_dump(mode="json")}
                    )
                self.json({"error": "Not found"}, 404)
            except (ValueError, KeyError, OSError, TypeError) as exc:
                self.json({"error": str(exc)}, 400)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(**kwargs):
    server = make_server(**kwargs)
    print(f"Open http://127.0.0.1:{server.server_port} — Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
