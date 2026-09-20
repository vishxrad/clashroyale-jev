import io
import struct
import time

import pytest
from PIL import Image

from clash_jev.device import ADB, decode_screencap


@pytest.mark.parametrize("header_size", [12, 16])
@pytest.mark.parametrize("pixel_format", [1, 2, 3])
def test_raw_capture_keeps_pixel_order_and_dimensions(header_size, pixel_format):
    header = struct.pack("<3I", 2, 1, pixel_format)
    if header_size == 16:
        header += struct.pack("<I", 1)
    pixels = bytes([255, 0, 0, 0, 255, 0])
    if pixel_format in (1, 2):
        pixels = bytes([255, 0, 0, 255, 0, 255, 0, 128])
    image = decode_screencap(header + pixels)
    assert image.size == (2, 1)
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert image.getpixel((1, 0)) == (0, 255, 0)


@pytest.mark.parametrize(
    "data",
    [b"", struct.pack("<3I", 2, 1, 1), struct.pack("<3I", 2, 1, 99) + bytes(8)],
)
def test_invalid_raw_capture_is_rejected(data):
    with pytest.raises(ValueError):
        decode_screencap(data)


async def test_unsupported_raw_device_falls_back_to_png(monkeypatch):
    device = ADB("test-device", "adb")
    png = io.BytesIO()
    Image.new("RGB", (2, 1), (12, 34, 56)).save(png, "PNG")
    calls = []

    async def command(*args):
        calls.append(args)
        return png.getvalue() if args[-1] == "-p" else b"unsupported"

    monkeypatch.setattr(device, "command", command)
    started = time.monotonic()
    first, second = await device.capture(), await device.capture()
    assert [first.id, second.id] == [1, 2]
    assert started <= first.captured_at <= second.captured_at <= time.monotonic()
    assert first.image.getpixel((0, 0)) == (12, 34, 56)
    assert calls == [
        ("exec-out", "screencap"),
        ("exec-out", "screencap", "-p"),
        ("exec-out", "screencap", "-p"),
    ]
