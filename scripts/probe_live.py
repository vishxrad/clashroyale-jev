"""Evaluate paid model calls on saved screenshots; never connects to a device."""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import time
from pathlib import Path

from dotenv import dotenv_values
from PIL import Image

from clash_jev.actions import candidates
from clash_jev.config import Config
from clash_jev.device import Frame
from clash_jev.hud import HUDReader
from clash_jev.models import WAIT, Battlefield, Decision
from clash_jev.providers import (
    VISION_PROMPT,
    CerebrasVision,
    Gateway,
    JevPolicy,
    ProviderFailure,
    keys,
    vision_schema,
)
from clash_jev.recording import Recorder
from clash_jev.state import Tracker

TEAM_HINTS = """
Red/pink health bars and red level badges mean ENEMY. Blue bars and blue level
badges mean ALLY. This overrides the half a unit occupies: either team can cross
the river. For flying units locate the shadow/ground projection. Measure x and y
relative to the actual image dimensions; do not use generic board coordinates.
When a health bar is unreadable use unknown, not high. Do not count the tower's
archer decoration as a deployed troop. Do not guess a specific troop type.
"""


class OpenAIProbeVision:
    def __init__(self, gateway: Gateway, model: str):
        self.gateway, self.model = gateway, model

    async def observe(self, image: Image.Image) -> Battlefield:
        cfg = self.gateway.config
        crop = image.crop(cfg.layout.arena.pixels(image.size))
        crop.thumbnail((cfg.runtime.vision_max_side, cfg.runtime.vision_max_side))
        buffer = io.BytesIO()
        crop.save(buffer, format="JPEG", quality=95)
        uri = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
        payload = {
            "model": self.model,
            "max_completion_tokens": cfg.runtime.vision_max_tokens,
            "messages": [
                {"role": "system", "content": VISION_PROMPT + TEAM_HINTS},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract this visible battlefield."},
                        {"type": "image_url", "image_url": {"url": uri, "detail": "high"}},
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "battlefield",
                    "strict": True,
                    "schema": vision_schema(),
                },
            },
        }
        if self.model.startswith("gpt-5"):
            payload["reasoning_effort"] = "none"
        else:
            payload["temperature"] = 0
        result = await self.gateway.post(
            "openai", "https://api.openai.com/v1/chat/completions", payload
        )
        try:
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("Incomplete response")
            return Battlefield.model_validate_json(choice["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderFailure("OpenAI returned an invalid or incomplete battlefield") from None


async def probe(args):
    cfg = Config.load(args.config)
    cfg.runtime.max_api_calls = len(args.images) * (3 if cfg.runtime.staged_decisions else 2)
    cfg.runtime.request_timeout_s = 30
    cfg.runtime.vision_max_side = args.max_side
    credentials = keys(args.env, cfg.runtime.cerebras_key_env)
    if args.provider == "openai":
        values = {**dotenv_values(args.env), **os.environ}
        credentials["openai"] = values.get("OPENAI_API_KEY") or values.get("OAI")
    if not credentials.get(args.provider) or not credentials.get("jev"):
        raise ValueError("The selected vision provider and Jev both need configured keys")
    if args.model:
        cfg.runtime.vision_model = args.model
    model = args.model or cfg.runtime.vision_model
    recorder = Recorder(args.output, cfg, f"saved-screenshot-live-api-{args.provider}-{model}")
    (args.output / "probe.json").write_text(
        json.dumps(
            {
                "provider": args.provider,
                "model": model,
                "max_side": args.max_side,
                "images": [str(p) for p in args.images],
                "game_input": False,
                "key_source": cfg.runtime.cerebras_key_env,
                "vision_grid": cfg.runtime.vision_grid if args.provider == "cerebras" else False,
                "vision_prompt": VISION_PROMPT + (TEAM_HINTS if args.provider == "openai" else ""),
            },
            indent=2,
        )
    )
    gateway = Gateway(cfg, credentials, allow_api=True)
    vision = (
        OpenAIProbeVision(gateway, model) if args.provider == "openai" else CerebrasVision(gateway)
    )
    reader, policy = HUDReader(cfg, Path.cwd()), JevPolicy(gateway)
    stop_reason = "finished"
    try:
        for index, path in enumerate(args.images, 1):
            image = Image.open(path).convert("RGB")
            cfg.layout.check_size(image.size)
            frame = Frame(index, time.monotonic(), image)
            board, hud = await asyncio.gather(
                vision.observe(image), asyncio.to_thread(reader.read, image)
            )
            perceived = time.monotonic()
            # Independent stills must not create fictitious temporal tracks.
            state = Tracker().update(frame, board, hud, [])
            options = candidates(cfg, state)
            if board.battle_active and reader.ready(hud) and len(options) > 1:
                decision = await policy.decide(state, options)
            else:
                options = [WAIT]
                decision = Decision(
                    choice="WAIT", confidence=1, probabilities={"WAIT": 1}, source="controller"
                )
            decided = time.monotonic()
            action = next(a for a in options if a.id == decision.choice)
            event = {
                "source_image": str(path),
                "state": state.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
                "action": action.model_dump(mode="json"),
                "candidates": [a.model_dump(mode="json") for a in options],
                "status": "saved_screenshot_probe_no_input",
                "latency_ms": {
                    "perception": round((perceived - frame.captured_at) * 1000, 1),
                    "decision": round((decided - perceived) * 1000, 1),
                    "execution": 0,
                    "total": round((decided - frame.captured_at) * 1000, 1),
                },
            }
            recorder.write(frame, event)
            print(json.dumps(event, indent=2), flush=True)
    except BaseException as exc:
        stop_reason = "error: " + type(exc).__name__
        raise
    finally:
        recorder.finish(gateway.usage, stop_reason=stop_reason)
        await gateway.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config/local.json"))
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--provider", choices=["cerebras", "openai"], default="cerebras")
    parser.add_argument("--model")
    parser.add_argument("--max-side", type=int, choices=[1024, 1536, 2048], default=1024)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-api", action="store_true")
    args = parser.parse_args()
    if not args.allow_api:
        parser.error("Add --allow-api to enable paid requests; no requests were sent")
    if args.provider == "openai" and not args.model:
        parser.error("Select an OpenAI vision model explicitly with --model")
    asyncio.run(probe(args))


if __name__ == "__main__":
    main()
