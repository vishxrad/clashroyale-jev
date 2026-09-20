from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from PIL import Image

from .config import Config
from .device import ADB
from .hud import HUDReader
from .providers import CerebrasVision, Gateway, JevPolicy, keys
from .replay import replay
from .runner import run_live
from .webui import serve


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Clash Royale + Jev. Offline by default; no implicit API calls."
    )
    p.add_argument("--config", type=Path, default=Path("config/example.json"))
    p.add_argument("--root", type=Path, default=Path.cwd(), help="Root for card template paths")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "doctor", help="Check configuration, templates and key presence without model calls"
    )
    capture = sub.add_parser("capture", help="Save one ADB screenshot; no model call or game input")
    capture.add_argument("--output", type=Path, default=Path("captures/screen.png"))
    capture.add_argument("--serial")
    capture.add_argument("--adb")
    calibrate = sub.add_parser("calibrate", help="Local browser UI for regions and card templates")
    calibrate.add_argument("image", type=Path)
    calibrate.add_argument("--output", type=Path, default=Path("config/local.json"))
    calibrate.add_argument("--port", type=int, default=8765)
    inspect = sub.add_parser("inspect", help="Run local card/elixir recognition on one screenshot")
    inspect.add_argument("image", type=Path)
    play = sub.add_parser(
        "replay", help="Replay labelled frames with scripted decisions; never calls APIs"
    )
    play.add_argument("fixtures", type=Path)
    play.add_argument("--output", type=Path)
    play.add_argument("--interval", type=float, default=0.2)
    view = sub.add_parser("view", help="Read-only local viewer for a recorded run")
    view.add_argument("run", type=Path)
    view.add_argument("--port", type=int, default=8765)
    run = sub.add_parser("run", help="Observe live gameplay; taps require --execute")
    run.add_argument(
        "--allow-api", action="store_true", help="Explicitly allow billed model requests"
    )
    run.add_argument(
        "--execute", action="store_true", help="Send card-selection and placement taps"
    )
    run.add_argument("--seconds", type=float, default=60)
    run.add_argument("--max-api-calls", type=int)
    run.add_argument("--output", type=Path)
    run.add_argument("--env", type=Path, default=Path(".env"))
    run.add_argument("--serial")
    run.add_argument("--adb")
    demo = sub.add_parser("demo", help="Live device video with browser Start/Stop controls")
    demo.add_argument("--allow-api", action="store_true")
    demo.add_argument("--execute", action="store_true")
    demo.add_argument("--seconds", type=float, default=360)
    demo.add_argument("--max-api-calls", type=int)
    demo.add_argument("--env", type=Path, default=Path(".env"))
    demo.add_argument("--serial")
    demo.add_argument("--adb")
    demo.add_argument("--port", type=int, default=8767)
    perceive = sub.add_parser("perceive", help="One explicitly enabled vision request; no input")
    perceive.add_argument("image", type=Path)
    perceive.add_argument("--allow-api", action="store_true")
    perceive.add_argument("--env", type=Path, default=Path(".env"))
    decide = sub.add_parser(
        "decide", help="One explicitly enabled Jev request on a saved State JSON"
    )
    decide.add_argument("state", type=Path)
    decide.add_argument("--allow-api", action="store_true")
    decide.add_argument("--env", type=Path, default=Path(".env"))
    return p


def output_path(prefix: str) -> Path:
    return (
        Path("runs") / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000:06d}"
    )


async def capture_one(args):
    device = ADB(args.serial, args.adb)
    await device.select()
    frame = await device.capture()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.image.save(args.output)
    print(f"Saved {args.output} ({frame.image.width}×{frame.image.height}); no model calls")


async def probe(args, config):
    from .actions import candidates
    from .models import State

    gateway = Gateway(config, keys(args.env, config.runtime.cerebras_key_env), allow_api=True)
    try:
        if args.command == "perceive":
            image = Image.open(args.image).convert("RGB")
            config.layout.check_size(image.size)
            result = await CerebrasVision(gateway).observe(image)
        else:
            state = State.model_validate_json(args.state.read_text())
            result = await JevPolicy(gateway).decide(state, candidates(config, state))
        print(result.model_dump_json(indent=2))
        print(json.dumps({"usage": gateway.usage}), file=sys.stderr)
    finally:
        await gateway.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {"run", "demo", "perceive", "decide"} and not args.allow_api:
            raise ValueError(
                "Model API calls are disabled. Add --allow-api only when ready to spend."
            )
        if args.command == "capture":
            asyncio.run(capture_one(args))
            return
        if args.command == "view":
            if not (args.run / "manifest.json").is_file():
                raise ValueError("Run directory has no manifest.json")
            serve(port=args.port, run=args.run)
            return
        config = Config.load(args.config)
        if args.command == "demo":
            from .live_demo import LiveDemo

            if not 0 < args.seconds <= 3600:
                raise ValueError("Run duration must be between 0 and 3600 seconds")
            if args.max_api_calls is not None:
                if args.max_api_calls < 1:
                    raise ValueError("--max-api-calls must be positive")
                config.runtime.max_api_calls = args.max_api_calls
            if not config.layout.calibrated:
                raise ValueError("Calibrate the screen first")
            device = ADB(args.serial, args.adb)
            asyncio.run(device.select())
            demo = LiveDemo(
                config, args.root, args.env, device, execute=args.execute, seconds=args.seconds
            )
            try:
                demo.stream.start()
                serve(port=args.port, live=demo)
            finally:
                demo.close()
        elif args.command == "doctor":
            reader = HUDReader(config, args.root)
            print(
                json.dumps(
                    {
                        "calibrated": config.layout.calibrated,
                        "deck": [c.id for c in config.deck],
                        "missing_templates": reader.missing_templates(),
                        "credentials_present": {
                            k: bool(v)
                            for k, v in keys(Path(".env"), config.runtime.cerebras_key_env).items()
                        },
                        "cerebras_key_env": config.runtime.cerebras_key_env,
                        "model_api_calls": 0,
                        "taps_enabled": False,
                    },
                    indent=2,
                )
            )
        elif args.command == "inspect":
            print(
                HUDReader(config, args.root).read(Image.open(args.image)).model_dump_json(indent=2)
            )
        elif args.command == "calibrate":
            serve(
                port=args.port,
                image=args.image,
                config_path=args.config,
                output=args.output,
                root=args.root,
            )
        elif args.command == "replay":
            if not 0 <= args.interval <= 60:
                raise ValueError("Replay interval must be between 0 and 60 seconds")
            output = args.output or output_path("replay")
            summary = asyncio.run(replay(config, args.root, args.fixtures, output, args.interval))
            print(json.dumps(summary, indent=2))
            print(f"View: uv run clash-jev view {output}")
        elif args.command == "run":
            if not 0 < args.seconds <= 3600:
                raise ValueError("Run duration must be between 0 and 3600 seconds")
            if args.max_api_calls is not None:
                if args.max_api_calls < 1:
                    raise ValueError("--max-api-calls must be positive")
                config.runtime.max_api_calls = args.max_api_calls
            credentials = keys(args.env, config.runtime.cerebras_key_env)
            if not all(credentials.values()):
                raise ValueError("Both Cerebras and Jev credentials are required")
            output = args.output or output_path("live")

            async def live():
                gateway = Gateway(config, credentials, allow_api=True)
                try:
                    return await run_live(
                        config,
                        args.root,
                        output,
                        ADB(args.serial, args.adb),
                        gateway,
                        execute=args.execute,
                        seconds=args.seconds,
                    )
                finally:
                    await gateway.close()

            print(json.dumps(asyncio.run(live()), indent=2))
            print(f"View: uv run clash-jev view {output}")
        else:
            asyncio.run(probe(args, config))
    except KeyboardInterrupt:
        print("Stopped.")
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
