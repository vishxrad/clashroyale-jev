"""Run Laya locally using the shared staged card and placement choices.

Cache the checkpoint across matches and keep inference off the capture loop.
Reject inputs that the SDK would truncate instead of silently discarding state.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from functools import lru_cache

from .decision_input import compact_question
from .models import WAIT, Action, Decision
from .providers import ChoicePolicy, ProviderFailure


@lru_cache(maxsize=1)
def load_laya(model: str, device: str):
    """Download one checkpoint and reuse it for subsequent local matches."""
    os.environ.setdefault("USE_TF", "0")
    try:
        import laya
    except ImportError as exc:
        raise ValueError("Enable Laya with: uv run --extra laya clash-jev <command>") from exc
    return laya.load(model, device=None if device == "auto" else device), threading.Lock()


class LayaPolicy(ChoicePolicy):
    """Adapt local typed answers to the controller's existing Decision model."""

    def placement_choices(self, actions: list[Action]) -> list[Action]:
        """Let Laya reconsider a spell when its available targets are unhelpful."""
        if actions and self.gateway.config.cards[actions[0].card].kind == "spell":
            return [*actions[:11], WAIT]
        return super().placement_choices(actions)

    async def prepare(self):
        opts = self.gateway.config.runtime
        if not opts.staged_decisions or not opts.compact_decisions:
            raise ValueError("Laya requires staged_decisions and compact_decisions")
        self.agent, self.lock = await asyncio.to_thread(
            load_laya, opts.laya_model, opts.laya_device
        )

    def predict(self, state, question):
        from laya.common import build_sequence, render_options

        with self.lock:
            agent = self.agent
            internal = agent._to_internal(question)
            token = agent.tok
            max_len = agent.cfg.get("max_len", 512)
            head_len = agent.cfg.get("head_max_len", 192)
            if any(
                len(token(" " + option, add_special_tokens=False)["input_ids"]) > 48
                for option in render_options(internal)
            ):
                raise ProviderFailure("Laya option text would be truncated; frame skipped")
            # A larger temporary budget reveals whether the real budget clips any
            # instruction, option, or state tokens. It never changes model settings.
            full, _ = build_sequence(token, state, internal, 100000, 100000)
            actual, markers = build_sequence(token, state, internal, max_len, head_len)
            if actual != full or len(markers) != len(render_options(internal)):
                raise ProviderFailure(
                    "Laya context would truncate the decision input; frame skipped"
                )
            return agent.predict(state, {"action": question})

    async def choose(self, state, actions, instructions):
        if not hasattr(self, "agent"):
            await self.prepare()
        text, question, mapping = compact_question(self.gateway.config, state, actions)
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(self.predict, text, question)
            answer = result["answers"]["action"]
            if answer["type"] != "choice":
                raise ValueError("Wrong answer type")
            decision = Decision(
                choice=mapping[answer["choice"]],
                confidence=answer["confidence"],
                probabilities={
                    mapping[key]: value for key, value in answer["probabilities"].items()
                },
                source="laya",
            )
            if set(decision.probabilities) != set(mapping.values()):
                raise ValueError("Unknown or missing action")
            if abs(sum(decision.probabilities.values()) - 1) > 0.02:
                raise ValueError("Invalid distribution")
            return decision
        except ProviderFailure:
            raise
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise ProviderFailure(
                f"Laya decision failed ({type(exc).__name__}); frame skipped"
            ) from exc
        finally:
            self.gateway.usage.append(
                {
                    "provider": "laya",
                    "model": self.gateway.config.runtime.laya_model,
                    "device": str(self.agent.device),
                    "latency_ms": round((time.monotonic() - started) * 1000, 1),
                }
            )
