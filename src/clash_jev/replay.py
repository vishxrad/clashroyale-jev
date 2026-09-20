from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from PIL import Image

from .config import Config
from .control import Controller
from .device import Frame
from .hud import HUDReader
from .models import Battlefield, Decision
from .recording import Recorder
from .runner import Pipeline


class FixtureVision:
    def __init__(self):
        self.board: Battlefield | None = None

    async def observe(self, image):
        assert self.board is not None
        return self.board


class FixturePolicy:
    """Explicitly scripted choices for pipeline QA, never represented as Jev inference."""

    def __init__(self):
        self.choice = "WAIT"

    async def decide(self, state, actions):
        ids = {action.id for action in actions}
        if self.choice not in ids:
            raise ValueError(f"Fixture choice {self.choice} is not a legal candidate")
        return Decision(
            choice=self.choice,
            confidence=1,
            probabilities={key: float(key == self.choice) for key in ids},
            source="fixture-script",
        )


async def replay(config: Config, root: Path, fixtures: Path, output: Path, interval: float = 0.1):
    specs = sorted(fixtures.glob("*.json"))
    if not specs:
        raise ValueError("No fixture JSON files found")
    reader, vision, policy = HUDReader(config, root), FixtureVision(), FixturePolicy()
    recorder = Recorder(output, config, "offline-fixture")
    controller = Controller(config, None, reader, execute=False)
    pipeline = Pipeline(config, reader, vision, policy, controller, recorder)
    try:
        for i, path in enumerate(specs, start=1):
            spec = json.loads(path.read_text())
            image_path = (fixtures / spec["image"]).resolve()
            if not image_path.is_relative_to(fixtures.resolve()):
                raise ValueError("Fixture image must be inside the fixture directory")
            frame = Frame(i, time.monotonic(), Image.open(image_path).convert("RGB"))
            vision.board = Battlefield.model_validate(spec["battlefield"])
            policy.choice = spec.get("choice", "WAIT")
            event = await pipeline.step(frame)
            print(f"frame {i}: {event['decision']['choice']} — {event['status']}", flush=True)
            await asyncio.sleep(interval)
    finally:
        summary = recorder.finish([])
    return summary
