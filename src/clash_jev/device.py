"""Capture timestamped Android frames and send taps through ADB.

Discover and select a device, decode raw screenshots with a PNG fallback, and
provide a capture loop that retains only the latest frame for inference.
Capture timestamps include device transfer time when measuring state age.
"""

from __future__ import annotations

import asyncio
import io
import shutil
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass
class Frame:
    id: int
    captured_at: float
    image: Image.Image


def decode_screencap(data: bytes) -> Image.Image:
    """Read Android's packed raw pixels (12-byte or dataspace-aware 16-byte header)."""
    if len(data) < 12:
        raise ValueError("Incomplete screencap header")
    width, height, pixel_format = struct.unpack_from("<3I", data)
    # Screenshots are opaque; ignore the alpha byte in both RGBA and RGBX.
    formats = {1: ("RGBX", 4), 2: ("RGBX", 4), 3: ("RGB", 3)}
    if not (0 < width <= 8192 and 0 < height <= 8192) or pixel_format not in formats:
        raise ValueError("Unsupported screencap format")
    raw_mode, channels = formats[pixel_format]
    header_size = len(data) - width * height * channels
    if header_size not in (12, 16):
        raise ValueError("Incomplete screencap pixels")
    return Image.frombytes("RGB", (width, height), data[header_size:], "raw", raw_mode)


def adb_path() -> str:
    installed = shutil.which("adb")
    if installed:
        return installed
    bundled = [
        Path(
            "/Applications/MuMuPlayer Pro.app/Contents/MacOS/"
            "MuMu Android Device.app/Contents/MacOS/tools/adb"
        ),
        Path("/Applications/BlueStacks.app/Contents/MacOS/hd-adb"),
    ]
    for executable in bundled:
        if executable.is_file():
            return str(executable)
    raise RuntimeError(
        "ADB not found. Install Android platform-tools, MuMuPlayer or BlueStacks, "
        "or use --adb PATH."
    )


class ADB:
    def __init__(self, serial: str | None = None, executable: str | None = None):
        self.executable = executable or adb_path()
        self.serial = serial
        self.frame_id = 0
        self.raw_capture = True

    async def command(self, *args: str, device: bool = True) -> bytes:
        prefix = [self.executable]
        if device:
            if not self.serial:
                raise RuntimeError("Select an ADB device before capturing or tapping")
            prefix += ["-s", self.serial]
        proc = await asyncio.create_subprocess_exec(
            *prefix, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            # A cancelled screenshot can leave unread pixels in stdout. Drain
            # both pipes after killing ADB so process cleanup cannot stall.
            await proc.communicate()
            raise
        if proc.returncode:
            raise RuntimeError(f"ADB failed: {stderr.decode(errors='replace')[:300]}")
        return stdout

    async def select(self):
        output = (await self.command("devices", device=False)).decode()
        available = [
            line.split()[0]
            for line in output.splitlines()
            if len(line.split()) == 2 and line.split()[1] == "device"
        ]
        if self.serial and self.serial in available:
            return
        if not self.serial and len(available) == 1:
            self.serial = available[0]
            return
        raise RuntimeError(
            f"Expected one connected device, found {available}. Use --serial, or connect ADB first."
        )

    async def capture(self) -> Frame:
        # Stamp before capture: age includes screenshot and transfer latency.
        captured_at = time.monotonic()
        image = None
        if self.raw_capture:
            try:
                image = decode_screencap(await self.command("exec-out", "screencap"))
            except ValueError:
                # Keep PNG compatibility for devices with other raw pixel formats.
                self.raw_capture = False
        if image is None:
            captured_at = time.monotonic()
            png = await self.command("exec-out", "screencap", "-p")
            image = Image.open(io.BytesIO(png)).convert("RGB")
        self.frame_id += 1
        return Frame(self.frame_id, captured_at, image)

    async def tap(self, x: int, y: int):
        await self.command("shell", "input", "tap", str(x), str(y))


class LatestFrames:
    """One capture producer, one inference consumer; replaced frames never queue."""

    def __init__(self, source: ADB, hz: float):
        self.source, self.hz = source, hz
        self.latest: Frame | None = None
        self.error: Exception | None = None
        self.condition = asyncio.Condition()
        self.dropped = 0
        self.consumed_id = -1

    async def produce(self):
        try:
            while True:
                started = time.monotonic()
                frame = await self.source.capture()
                async with self.condition:
                    if self.latest and self.latest.id > self.consumed_id:
                        self.dropped += 1
                    self.latest = frame
                    self.condition.notify_all()
                await asyncio.sleep(max(0, 1 / self.hz - (time.monotonic() - started)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self.condition:
                self.error = exc
                self.condition.notify_all()

    async def next(self, after: int = -1) -> Frame:
        async with self.condition:
            await self.condition.wait_for(
                lambda: self.error is not None or (self.latest and self.latest.id > after)
            )
            if self.error:
                raise self.error
            assert self.latest
            self.consumed_id = self.latest.id
            return self.latest
