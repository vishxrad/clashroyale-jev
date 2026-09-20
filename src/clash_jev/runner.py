from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from pathlib import Path

from .actions import candidates
from .config import Config
from .control import Controller
from .device import ADB, Frame, LatestFrames
from .hud import HUDReader
from .models import WAIT, Battlefield, Decision
from .providers import BudgetExhausted, CerebrasVision, Gateway, JevPolicy, ProviderFailure
from .recording import Recorder
from .state import Tracker


class Pipeline:
    def __init__(
        self,
        config: Config,
        reader: HUDReader,
        vision,
        policy,
        controller: Controller,
        recorder: Recorder,
    ):
        self.config, self.reader, self.vision, self.policy = config, reader, vision, policy
        self.controller, self.recorder = controller, recorder
        self.tracker = Tracker()

    async def step(self, frame: Frame, latest=lambda: None):
        started = time.monotonic()
        self.config.layout.check_size(frame.image.size)
        hud = await asyncio.to_thread(self.reader.read, frame.image)
        # Local HUD recognition also prevents model calls on menus/results.
        board = (
            await self.vision.observe(frame.image)
            if self.reader.ready(hud)
            else Battlefield(battle_active=False, units=[], towers=[])
        )
        perceived = time.monotonic()
        self.controller.reconcile(hud, frame.captured_at)
        state = self.tracker.update(frame, board, hud, self.controller.history)
        options = candidates(self.config, state)
        reason = None
        if not board.battle_active or not self.reader.ready(hud):
            reason = "unrecognized_or_inactive_screen"
        elif self.controller.halted:
            reason = "halted: " + self.controller.halted
        elif self.controller.pending:
            reason = "waiting_for_confirmation"
        elif (time.monotonic() - frame.captured_at) * 1000 > self.config.runtime.max_state_age_ms:
            reason = "rejected_stale_perception"
        if reason or len(options) == 1:
            decision = Decision(
                choice="WAIT", confidence=1, probabilities={"WAIT": 1}, source="controller"
            )
            options = [WAIT]
        else:
            decision = await self.policy.decide(state, options)
        decided = time.monotonic()
        action = next(a for a in options if a.id == decision.choice)
        current = latest() or frame
        if current.id != frame.id:
            current_hud = await asyncio.to_thread(self.reader.read, current.image)
        else:
            current_hud = hud
        status = reason or await self.controller.apply(
            action, decision, state, current, current_hud
        )
        ended = time.monotonic()
        event = {
            "state": state.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "action": action.model_dump(mode="json"),
            "candidates": [a.model_dump(mode="json") for a in options],
            "status": status,
            "latest_hud": current_hud.model_dump(mode="json"),
            "observation_age_ms": round((ended - frame.captured_at) * 1000, 1),
            "latency_ms": {
                "capture_and_wait": round((started - frame.captured_at) * 1000, 1),
                "perception": round((perceived - started) * 1000, 1),
                "decision": round((decided - perceived) * 1000, 1),
                "execution": round((ended - decided) * 1000, 1),
                "total": round((ended - frame.captured_at) * 1000, 1),
            },
        }
        self.recorder.write(frame, event)
        return event


async def run_live(
    config: Config,
    root: Path,
    output: Path,
    device: ADB,
    gateway: Gateway,
    *,
    execute: bool,
    seconds: float,
):
    if not config.layout.calibrated:
        raise ValueError("Calibrate the screen before live observation or execution")
    reader = HUDReader(config, root)
    if reader.missing_templates():
        raise ValueError("Capture card templates first: " + ", ".join(reader.missing_templates()))
    await device.select()
    recorder = Recorder(output, config, "live" if execute else "live-dry-run")
    controller = Controller(config, device, reader, execute=execute)
    pipeline = Pipeline(
        config, reader, CerebrasVision(gateway), JevPolicy(gateway), controller, recorder
    )
    frames = LatestFrames(device, config.runtime.capture_hz)
    capture = asyncio.create_task(frames.produce())
    after, errors = -1, 0
    seen_battle, inactive_since = False, None
    stop_reason = "finished"
    try:
        async with asyncio.timeout(seconds):
            while not controller.halted:
                frame = await frames.next(after)
                after = frame.id
                try:
                    if controller.pending:
                        hud = await asyncio.to_thread(reader.read, frame.image)
                        pending_action = controller.pending.action.id
                        controller.reconcile(hud, frame.captured_at)
                        recorder.control_event(
                            frame.id,
                            {
                                "action": pending_action,
                                "status": "confirmation_observation",
                                "captured_at": frame.captured_at,
                                "hud": hud.model_dump(mode="json"),
                            },
                        )
                        if controller.pending or controller.halted:
                            if controller.halted:
                                recorder.write(
                                    frame,
                                    {
                                        "status": "unconfirmed_deployment",
                                        "latest_hud": hud.model_dump(mode="json"),
                                    },
                                )
                            continue
                        recorder.control_event(frame.id, controller.history[-1])
                        print(f"frame {frame.id}: deployment confirmed", flush=True)
                    event = await pipeline.step(frame, lambda: frames.latest)
                    errors = 0
                    print(
                        f"frame {frame.id}: {event['decision']['choice']} — {event['status']}",
                        flush=True,
                    )
                    if event["state"]["battle_active"]:
                        seen_battle, inactive_since = True, None
                    elif seen_battle:
                        inactive_since = inactive_since or time.monotonic()
                        if (
                            config.runtime.stop_after_battle
                            and time.monotonic() - inactive_since > 4
                        ):
                            stop_reason = "battle_finished"
                            break
                except ProviderFailure as exc:
                    errors += 1
                    recorder.write(frame, {"status": "provider_error", "error": str(exc)})
                    print(str(exc), flush=True)
                    if errors >= 3:
                        controller.halted = "Three consecutive provider failures"
                    else:
                        await asyncio.sleep(0.5)
        if controller.halted:
            stop_reason = "halted: " + controller.halted
    except (TimeoutError, BudgetExhausted) as exc:
        timed_out = isinstance(exc, TimeoutError)
        stop_reason = "time_limit" if timed_out else "request_budget"
        print("Run time limit reached" if timed_out else str(exc))
    except asyncio.CancelledError:
        stop_reason = "interrupted"
        raise
    except Exception as exc:
        stop_reason = "error: " + type(exc).__name__
        raise
    finally:
        capture.cancel()
        with suppress(asyncio.CancelledError):
            await capture
        summary = recorder.finish(
            gateway.usage,
            frames.dropped,
            stop_reason=stop_reason,
            recent_actions=controller.history,
            pending_action=controller.pending.action.model_dump(mode="json")
            if controller.pending
            else None,
        )
        await gateway.close()
    if controller.halted:
        print(controller.halted)
    return summary
