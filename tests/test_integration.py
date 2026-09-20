import asyncio
import json
import time

import httpx
import pytest
from PIL import Image, ImageDraw

from clash_jev.control import Controller
from clash_jev.device import Frame
from clash_jev.providers import BudgetExhausted, CerebrasVision, Gateway, JevPolicy, ProviderFailure
from clash_jev.recording import Recorder
from clash_jev.runner import Pipeline, run_live


class SimulatedDevice:
    def __init__(self, image):
        self.image = image
        self.taps = []
        self.i = 0

    async def select(self):
        pass

    async def capture(self):
        self.i += 1
        return Frame(self.i, time.monotonic(), self.image.copy())

    async def tap(self, x, y):
        self.taps.append((x, y))


async def test_unrecognized_menu_skips_models(config, reader, tmp_path):
    class Uncalled:
        async def observe(self, image):
            pytest.fail("Vision should not be called on a locally unrecognized menu")

        async def decide(self, state, actions):
            pytest.fail("Policy should not be called on a menu")

    image = Image.new("RGB", config.layout.reference_size, "black")
    device = SimulatedDevice(image)
    pipeline = Pipeline(
        config,
        reader,
        Uncalled(),
        Uncalled(),
        Controller(config, device, reader, execute=True),
        Recorder(tmp_path / "run", config, "test"),
    )
    event = await pipeline.step(Frame(1, time.monotonic(), image))
    assert event["status"] == "unrecognized_or_inactive_screen"
    assert not device.taps


async def test_full_pipeline_http_to_input_to_confirmation(
    config, root, reader, frame, board, tmp_path
):
    requests = []

    def endpoint(request):
        requests.append(request.url.host)
        payload = json.loads(request.content)
        if request.url.host == "api.cerebras.ai":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": board.model_dump_json()}}
                    ]
                },
            )
        choices = payload["questions"]["action"]["criteria"]
        choice = "PLAY_0_knight_left_defense" if requests.count("api.typesafe.ai") == 1 else "WAIT"
        return httpx.Response(
            200,
            json={
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 0.9,
                        "probabilities": {key: float(key == choice) for key in choices},
                    }
                }
            },
        )

    gateway = Gateway(
        config,
        {"cerebras": "fake", "jev": "fake"},
        allow_api=True,
        transport=httpx.MockTransport(endpoint),
    )
    device = SimulatedDevice(frame.image)
    controller = Controller(config, device, reader, execute=True)
    recorder = Recorder(tmp_path / "run", config, "integration-test")
    pipeline = Pipeline(
        config, reader, CerebrasVision(gateway), JevPolicy(gateway), controller, recorder
    )
    try:
        first = await pipeline.step(frame)
        assert first["status"] == "sent"
        assert len(device.taps) == 2
        # Simulate the two visible consequences of a successfully deployed Knight.
        box = config.layout.hand[0].pixels(frame.image.size)
        replacement = Image.open(root / config.cards["minions"].templates[0])
        image = frame.image.copy()
        image.paste(replacement.resize((box[2] - box[0], box[3] - box[1])), box[:2])
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = config.layout.elixir.pixels(image.size)
        draw.rectangle((left, top, right, bottom), fill=(10, 10, 10))
        draw.rectangle(
            (left, top, left + round((right - left) * 0.3) - 1, bottom), fill=(210, 60, 210)
        )
        second = await pipeline.step(Frame(2, time.monotonic(), image))
        assert second["status"] == "wait"
        assert second["state"]["recent_actions"][-1]["status"] == "confirmed"
        assert controller.pending is None
        assert len(device.taps) == 2
        summary = recorder.finish(gateway.usage)
        assert summary["actions"] == 1 and summary["model_requests"] == 4
    finally:
        await gateway.close()


async def test_live_loop_stops_at_shared_budget(config, root, frame, board, tmp_path):
    config.runtime.max_api_calls = 2

    def endpoint(request):
        if request.url.host == "api.cerebras.ai":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": board.model_dump_json()}}
                    ]
                },
            )
        choices = json.loads(request.content)["questions"]["action"]["criteria"]
        return httpx.Response(
            200,
            json={
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "WAIT",
                        "confidence": 0.9,
                        "probabilities": {key: float(key == "WAIT") for key in choices},
                    }
                }
            },
        )

    gateway = Gateway(
        config,
        {"cerebras": "fake", "jev": "fake"},
        allow_api=True,
        transport=httpx.MockTransport(endpoint),
    )
    device = SimulatedDevice(frame.image)
    result = await run_live(
        config, root, tmp_path / "run", device, gateway, execute=False, seconds=3
    )
    assert result["frames"] == 1
    assert result["model_requests"] == 2
    assert result["stop_reason"] == "request_budget"
    assert not device.taps


@pytest.mark.parametrize("stop", ["time_limit", "interrupted"])
async def test_stopping_during_input_preserves_unknown_outcome(
    config, root, frame, board, tmp_path, stop
):
    class SlowInputDevice(SimulatedDevice):
        def __init__(self, image):
            super().__init__(image)
            self.placement_started = asyncio.Event()

        async def tap(self, x, y):
            self.taps.append((x, y))
            if len(self.taps) == 2:
                self.placement_started.set()
                await asyncio.Event().wait()

    def endpoint(request):
        if request.url.host == "api.cerebras.ai":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": board.model_dump_json()}}
                    ]
                },
            )
        options = json.loads(request.content)["questions"]["action"]["criteria"]
        choice = "PLAY_0_knight_left_defense"
        return httpx.Response(
            200,
            json={
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 1,
                        "probabilities": {option: float(option == choice) for option in options},
                    }
                }
            },
        )

    gateway = Gateway(
        config,
        {"cerebras": "fake", "jev": "fake"},
        allow_api=True,
        transport=httpx.MockTransport(endpoint),
    )
    device = SlowInputDevice(frame.image)
    output = tmp_path / "run"
    task = asyncio.create_task(
        run_live(
            config,
            root,
            output,
            device,
            gateway,
            execute=True,
            seconds=0.25 if stop == "time_limit" else 10,
        )
    )
    await asyncio.wait_for(device.placement_started.wait(), timeout=1)
    if stop == "interrupted":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    summary = json.loads((output / "summary.json").read_text())
    assert summary["stop_reason"] == stop
    assert summary["recent_actions"][-1]["status"] == "input_interrupted"
    assert summary["pending_action"]["id"] == "PLAY_0_knight_left_defense"
    assert len(device.taps) == 2
    assert summary["model_requests"] == 2


async def test_parallel_requests_cannot_exceed_budget(config):
    config.runtime.max_api_calls = 1
    gateway = Gateway(
        config,
        {"cerebras": "fake", "jev": "fake"},
        allow_api=True,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )
    try:
        results = await asyncio.gather(
            gateway.post("cerebras", "https://example.invalid", {}),
            gateway.post("jev", "https://example.invalid", {}),
            return_exceptions=True,
        )
        assert gateway.calls == 1
        assert sum(isinstance(r, BudgetExhausted) for r in results) == 1
    finally:
        await gateway.close()


@pytest.mark.parametrize("body", ["<html>bad gateway</html>", "[]", "null"])
async def test_non_object_provider_response_is_controlled_failure(config, body):
    gateway = Gateway(
        config,
        {"jev": "fake"},
        allow_api=True,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)),
    )
    try:
        with pytest.raises(ProviderFailure, match="invalid response"):
            await gateway.post("jev", "https://example.invalid", {})
    finally:
        await gateway.close()
