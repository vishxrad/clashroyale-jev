from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .actions import legal
from .config import Config
from .device import Frame
from .hud import HUDReader
from .models import HUD, Action, Decision, Point, State


@dataclass
class Pending:
    action: Action
    sent_at: float
    before_elixir: int
    saw_spend: bool = False
    saw_replacement: bool = False
    peak_elixir: int = 0


class Controller:
    def __init__(self, config: Config, device, reader: HUDReader, *, execute: bool = False):
        self.config, self.device, self.reader = config, device, reader
        self.execute = execute
        self.pending: Pending | None = None
        self.history: list[dict] = []
        self.last_action_at = -1e12
        self.halted: str | None = None

    def reconcile(self, hud: HUD, observed_at: float):
        pending = self.pending
        if not pending:
            return
        # Capture is stamped at START. A screenshot started before the taps can
        # finish after them, and still show regenerated pre-deployment elixir.
        # Use that value as a baseline, never as post-input confirmation.
        pending.peak_elixir = max(pending.peak_elixir, pending.before_elixir)
        if hud.elixir is not None and not pending.saw_spend:
            pending.peak_elixir = max(pending.peak_elixir, hud.elixir)
        if observed_at <= pending.sent_at:
            return
        slot = next(c for c in hud.hand if c.slot == pending.action.slot)
        card_changed = (
            slot.card is not None
            and slot.card != pending.action.card
            and slot.confidence >= self.config.recognition.card_threshold
        )
        # A sampled net drop can be smaller than the card cost because elixir
        # regenerates during capture/input animations, especially in double elixir.
        # Require an observed drop AND independent confident card replacement.
        spent = hud.elixir is not None and hud.elixir < pending.peak_elixir
        # Elixir and card replacement animate at different times. Remember each
        # visible consequence across fresh frames from this one deployment.
        pending.saw_spend = pending.saw_spend or spent
        pending.saw_replacement = pending.saw_replacement or card_changed
        if pending.saw_replacement and pending.saw_spend:
            self.history[-1]["status"] = "confirmed"
            self.pending = None
        elif (observed_at - pending.sent_at) * 1000 > self.config.runtime.confirmation_timeout_ms:
            self.history[-1]["status"] = "unconfirmed"
            self.halted = "Deployment was not confirmed; stopped to avoid duplicate input"

    async def apply(
        self, action: Action, decision: Decision, state: State, latest: Frame, hud: HUD
    ) -> str:
        now = time.monotonic()
        opts = self.config.runtime
        if self.halted:
            return "halted: " + self.halted
        if self.pending:
            return "waiting_for_confirmation"
        if action.id == "WAIT":
            return "wait"
        if decision.choice != action.id:
            return "rejected_choice_mismatch"
        if not state.battle_active or not self.reader.ready(hud):
            return "rejected_unrecognized_screen"
        if latest.id < state.frame_id:
            return "rejected_old_hud"
        if (now - state.captured_at) * 1000 > opts.max_state_age_ms:
            return "rejected_stale_decision"
        if (now - latest.captured_at) * 1000 > opts.max_state_age_ms:
            return "rejected_stale_hud"
        if decision.confidence < opts.min_decision_confidence:
            return "rejected_low_confidence"
        if (now - self.last_action_at) * 1000 < opts.action_cooldown_ms:
            return "cooldown"
        card = self.config.cards.get(action.card)
        if (
            not card
            or action.slot not in range(4)
            or action.position is None
            or action.cost != card.cost
            or not legal(self.config, card, action.position)
        ):
            return "rejected_illegal_action"
        current = next(h for h in hud.hand if h.slot == action.slot)
        if (
            current.card != action.card
            or current.confidence < self.config.recognition.card_threshold
        ):
            return "rejected_hand_changed"
        if hud.elixir is None or hud.elixir < card.cost:
            return "rejected_insufficient_elixir"
        self.config.layout.check_size(latest.image.size)
        if not self.execute:
            self.last_action_at = now
            return "dry_run"
        if not self.config.layout.calibrated:
            return "rejected_uncalibrated"
        hand_rect = self.config.layout.hand[action.slot]
        source = hand_rect.screen_point(Point(x=0.5, y=0.5), latest.image.size)
        target = self.config.layout.arena.screen_point(action.position, latest.image.size)
        # A failed/partial input must not be retried automatically.
        self.pending = Pending(action, now, hud.elixir)
        self.history.append(
            {
                "action": action.id,
                "card": action.card,
                "position": action.position.model_dump(),
                "status": "sent",
            }
        )
        self.history = self.history[-8:]
        try:
            await self.device.tap(*source)
            if (time.monotonic() - state.captured_at) * 1000 > opts.max_state_age_ms:
                raise RuntimeError("Decision expired after card selection")
            await self.device.tap(*target)
        except asyncio.CancelledError:
            # A timeout or Ctrl+C can interrupt ADB after input reached Android.
            # Preserve the uncertainty and never dispatch another tap on cancellation.
            self.history[-1]["status"] = "input_interrupted"
            self.halted = "Input was interrupted; deployment outcome is unknown"
            raise
        except Exception:
            self.history[-1]["status"] = "input_error"
            self.halted = "Input failed or expired; inspect the game before restarting"
            return "halted: " + self.halted
        self.last_action_at = time.monotonic()
        self.pending.sent_at = self.last_action_at
        return "sent"
