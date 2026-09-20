"""Stream live device video and manage one browser-controlled bot session.

Decode Android video into timestamped JPEG frames with an optional display
buffer. Run model processing independently of the video feed, expose recorded
state to the browser, and coordinate Start, Stop, and resource cleanup.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from .config import Config
from .device import ADB
from .providers import Gateway, keys
from .runner import run_live


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def read_rows(path: Path):
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


class ScreenStream:
    """A shared live MJPEG feed; viewers never start their own device capture."""

    def __init__(self, device: ADB, size: tuple[int, int] = (1440, 2560)):
        self.device = device
        self.size = size
        self.ffmpeg = shutil.which("ffmpeg")
        if not self.ffmpeg:
            raise RuntimeError("Install ffmpeg to display the live device stream")
        self.condition = threading.Condition()
        self.closed = threading.Event()
        self.jpeg: bytes | None = None
        self.sequence = 0
        self.updated_at = 0.0
        self.frames: deque[tuple[int, float, bytes]] = deque(maxlen=240)
        self.error: str | None = None
        self.processes: list[subprocess.Popen] = []
        self.thread = threading.Thread(target=self._produce, daemon=True)

    def start(self):
        self.thread.start()

    def status(self):
        with self.condition:
            age = time.monotonic() - self.updated_at if self.updated_at else None
            return {
                "frame": self.sequence,
                "age_ms": round(age * 1000) if age is not None else None,
                "connected": age is not None and age < 3,
                "error": self.error,
            }

    def next(self, after: int):
        sequence, jpeg, _ = self.next_frame(after)
        return sequence, jpeg

    def next_frame(self, after: int, delay: float = 0):
        def eligible():
            cutoff = time.monotonic() - delay
            return next(
                (
                    frame
                    for frame in reversed(self.frames)
                    if frame[0] > after and frame[1] <= cutoff
                ),
                None,
            )

        with self.condition:
            self.condition.wait_for(
                lambda: self.closed.is_set() or eligible() is not None,
                timeout=1,
            )
            frame = eligible()
            if self.closed.is_set() or frame is None:
                return after, None, None
            sequence, captured_at, jpeg = frame
            return sequence, jpeg, captured_at

    @staticmethod
    def terminate(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def _produce(self):
        while not self.closed.is_set():
            processes = []
            try:
                with tempfile.TemporaryFile() as errors:
                    adb = subprocess.Popen(
                        [
                            self.device.executable,
                            "-s",
                            self.device.serial,
                            "exec-out",
                            "screenrecord",
                            "--output-format=h264",
                            "--size",
                            f"{self.size[0]}x{self.size[1]}",
                            "--bit-rate",
                            "16000000",
                            "--time-limit",
                            "180",
                            "-",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=errors,
                    )
                    processes.append(adb)
                    decoder = subprocess.Popen(
                        [
                            self.ffmpeg,
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-flags",
                            "low_delay",
                            "-threads",
                            "1",
                            "-probesize",
                            "32",
                            "-analyzeduration",
                            "0",
                            "-f",
                            "h264",
                            "-use_wallclock_as_timestamps",
                            "1",
                            "-i",
                            "pipe:0",
                            "-an",
                            "-vf",
                            "select='isnan(prev_selected_t)+gte(t-prev_selected_t,0.03)'",
                            "-fps_mode",
                            "passthrough",
                            "-enc_time_base",
                            "1:1000000",
                            "-pix_fmt",
                            "yuvj420p",
                            "-c:v",
                            "mjpeg",
                            "-threads",
                            "1",
                            "-q:v",
                            "2",
                            "-f",
                            "image2pipe",
                            "-flush_packets",
                            "1",
                            "pipe:1",
                        ],
                        stdin=adb.stdout,
                        stdout=subprocess.PIPE,
                        stderr=errors,
                    )
                    processes.append(decoder)
                    adb.stdout.close()
                    self.processes = processes
                    buffer = bytearray()
                    while not self.closed.is_set():
                        chunk = decoder.stdout.read1(65536)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        while (end := buffer.find(b"\xff\xd9")) >= 0:
                            start = buffer.find(b"\xff\xd8", 0, end)
                            if start >= 0:
                                with self.condition:
                                    self.jpeg = bytes(buffer[start : end + 2])
                                    self.sequence += 1
                                    self.updated_at = time.monotonic()
                                    self.frames.append((self.sequence, self.updated_at, self.jpeg))
                                    while self.frames and self.frames[0][1] < self.updated_at - 7:
                                        self.frames.popleft()
                                    self.error = None
                                    self.condition.notify_all()
                            del buffer[: end + 2]
                        if len(buffer) > 8 * 1024 * 1024:
                            raise RuntimeError("Invalid device video stream")
                    if not self.closed.is_set():
                        self.error = "Reconnecting device video"
            except (OSError, RuntimeError) as exc:
                self.error = str(exc)
            finally:
                for process in reversed(processes):
                    self.terminate(process)
                self.processes = []
            self.closed.wait(0.5)

    def close(self):
        self.closed.set()
        with self.condition:
            self.condition.notify_all()
        for process in reversed(self.processes.copy()):
            self.terminate(process)
        if self.thread.is_alive():
            self.thread.join(timeout=5)


class LiveDemo:
    def __init__(
        self, config: Config, root: Path, env: Path, device: ADB, *, execute: bool, seconds: float
    ):
        self.config, self.root, self.env, self.device = config, root, env, device
        self.execute, self.seconds = execute, seconds
        self.stream = ScreenStream(device, size=config.layout.reference_size)
        self.lock = threading.RLock()
        self.run: Path | None = None
        self.running = False
        self.stopping = False
        self.error: str | None = None
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task | None = None
        self.closed = False

    def start(self):
        with self.lock:
            if self.closed:
                raise ValueError("Live demo is closed")
            if self.running:
                return
            if not self.stream.status()["connected"]:
                raise ValueError("Wait for the live device feed to connect")
            # Credential values never enter the browser or recordings.
            if not all(keys(self.env, self.config.runtime.cerebras_key_env).values()):
                raise ValueError("Both Cerebras and Jev credentials are required")
            self.run = self.root / "runs" / f"live-demo-{time.time_ns()}"
            self.running, self.stopping, self.error = True, False, None
            self.thread = threading.Thread(target=self._worker, daemon=True)
            self.thread.start()

    def _worker(self):
        async def work():
            with self.lock:
                self.loop = asyncio.get_running_loop()
                self.task = asyncio.current_task()
                cancelled = self.stopping
            if cancelled:
                return
            gateway = Gateway(
                self.config, keys(self.env, self.config.runtime.cerebras_key_env), allow_api=True
            )
            try:
                await run_live(
                    self.config,
                    self.root,
                    self.run,
                    ADB(self.device.serial, self.device.executable),
                    gateway,
                    execute=self.execute,
                    seconds=self.seconds,
                )
            finally:
                await gateway.close()

        try:
            asyncio.run(work())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.running, self.stopping = False, False
                self.loop = self.task = None

    def stop(self):
        with self.lock:
            if self.running:
                self.stopping = True
                if self.loop and self.task:
                    self.loop.call_soon_threadsafe(self.task.cancel)

    def snapshot(self, revision: str | None = None):
        with self.lock:
            run = self.run
            bot = {
                "running": self.running,
                "stopping": self.stopping,
                "error": self.error,
                "execute": self.execute,
            }
        # Poll clocks/status cheaply; only reread and transmit telemetry when it changes.
        stamps = []
        if run:
            for name in ("manifest.json", "events.jsonl", "control.jsonl", "summary.json"):
                try:
                    stat = (run / name).stat()
                    stamps.append((name, stat.st_mtime_ns, stat.st_size))
                except OSError:
                    stamps.append((name, None, None))
        token = hashlib.sha256(repr((str(run), stamps)).encode()).hexdigest()[:20]
        snapshot = {
            "now": time.monotonic(),
            "run": run.name if run else None,
            "bot": bot,
            "stream": self.stream.status(),
            "revision": token,
        }
        if revision == token:
            return snapshot
        manifest = {"mode": "live", "config": self.config.model_dump(mode="json")}
        return {
            **snapshot,
            "manifest": read_json(run / "manifest.json", manifest) if run else manifest,
            "events": read_rows(run / "events.jsonl") if run else [],
            "control": read_rows(run / "control.jsonl") if run else [],
            "summary": read_json(run / "summary.json", {}) if run else {},
        }

    def close(self):
        with self.lock:
            self.closed = True
        self.stop()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)
        self.stream.close()
