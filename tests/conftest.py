import json
import time
from pathlib import Path

import pytest
from PIL import Image

from clash_jev.config import Config
from clash_jev.device import Frame
from clash_jev.hud import HUDReader
from clash_jev.models import Battlefield
from clash_jev.state import Tracker

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root():
    return ROOT


@pytest.fixture
def config():
    return Config.load(ROOT / "config/synthetic.json")


@pytest.fixture
def frame():
    return Frame(1, time.monotonic(), Image.open(ROOT / "fixtures/synthetic/episode/001.png"))


@pytest.fixture
def board():
    spec = json.loads((ROOT / "fixtures/synthetic/episode/001.json").read_text())
    return Battlefield.model_validate(spec["battlefield"])


@pytest.fixture
def reader(config, root):
    return HUDReader(config, root)


@pytest.fixture
def state(frame, board, reader):
    return Tracker().update(frame, board, reader.read(frame.image), [])
