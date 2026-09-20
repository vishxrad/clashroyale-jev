from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from PIL import Image, ImageDraw

from .config import Config
from .models import WAIT, Action, Battlefield, Decision, State

VISION_PROMPT = """Extract the visible Clash Royale battlefield. Return JSON only.
Coordinates x,y are fractions of THIS IMAGE, origin top left. The player's TOWERS are at
the bottom and enemy TOWERS at the top. Troops of EITHER TEAM can cross the river.
BLUE/cyan health bars and blue level badges mean ALLY, even in the top half.
RED/pink health bars and red level badges mean ENEMY, even in the bottom half.
Never infer troop team from which half they occupy. If ambiguous use team=unknown.
Locate ground contact points, not the top of sprites. For flying troops use the shadow
on the ground. If a numbered coordinate grid is present, use its x/y fractions to locate
each unit in THIS screenshot. Round coordinates to three decimals. Include visible
troops and deployed buildings, but not projectiles, particles, shadows, or tower decorations.
Keep separate nearby units separate. Do not guess hidden units or hidden enemy resources.
Use type=unknown when identity is unclear and health_band=unknown if no readable health bar.
confidence is your recognition estimate, not a calibrated probability. Tower hp must be a
readable integer or null; destroyed requires visible evidence. battle_active is false for
menus, loading, countdowns, results, replays, spectator screens, or an obscured battlefield.
Do not duplicate fixed tower archers or king cannons as troops. An empty battlefield
has units=[]. Do not infer actions, tactics, or future events. No prose outside the schema."""

DECISION_PROMPT = """Choose ONE complete immediate Clash Royale action from the provided
options. Optimize winning the match: defend urgent threats with suitable counters, preserve
elixir, exploit useful counterpushes, and avoid wasting spells. Coordinates are normalized
to the arena; ally is bottom, enemy top. Consider card descriptions, unit positions and
velocities, health, timer and recent actions. Unknowns are unknown, not zero. Do not invent
enemy elixir. At 9-10 elixir, spend on a useful troop instead of leaking elixir by waiting.
With no urgent threat, build a push: Giant tanks for Musketeer, Archers or Minions.
Place Giant behind a friendly tower when building up, or at a bridge for immediate pressure.
Defend enemy pushes in their lane, then support surviving defenders. Use Mini P.E.K.K.A
against ground tanks, ranged troops against air, and Arrows/Fireball on real enemy clusters.
Avoid hitting an undamaged enemy king tower with spells. Prefer WAIT when saving for an
unaffordable useful card or when no useful affordable action exists. Candidate generation
only establishes possible placements; you must choose the tactical play. Each option already
combines a card and placement; select it as a whole."""


class APIBlocked(RuntimeError):
    pass


class BudgetExhausted(RuntimeError):
    pass


class ProviderFailure(RuntimeError):
    pass


def keys(env_path: Path, cerebras_key_env: str | None = None) -> dict[str, str | None]:
    values = {**dotenv_values(env_path), **os.environ}
    return {
        "cerebras": values.get(cerebras_key_env)
        if cerebras_key_env
        else (values.get("CEREBRAS_API_KEY") or values.get("cerebras")),
        "jev": values.get("JEV_API_KEY") or values.get("TYPESAFE_API_KEY") or values.get("jev"),
    }


def vision_schema(unit_types: list[str] | None = None) -> dict:
    schema = Battlefield.model_json_schema()
    if unit_types is not None:
        schema["$defs"]["Unit"]["properties"]["type"]["enum"] = unit_types

    def clean(value):
        if isinstance(value, dict):
            return {
                k: clean(v)
                for k, v in value.items()
                if k not in {"minItems", "maxItems", "minLength", "maxLength", "pattern"}
            }
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return clean(schema)


def restrict_units(board: Battlefield, unit_types: list[str] | None) -> Battlefield:
    """Keep only configured identities before tracking and tactical decisions."""
    if unit_types is None:
        return board

    def normalized(name: str) -> str:
        return re.sub(r"[^a-z]", "", name.lower()).removesuffix("s")

    allowed = {normalized(name): name for name in unit_types}
    units = []
    for unit in board.units:
        if canonical := allowed.get(normalized(unit.type)):
            units.append(unit.model_copy(update={"type": canonical}))
    return board.model_copy(update={"units": units})


class Gateway:
    """A single budget and explicit network gate shared by both model adapters."""

    def __init__(
        self,
        config: Config,
        credentials: dict[str, str | None],
        *,
        allow_api: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.config, self.credentials, self.allow_api = config, credentials, allow_api
        self.calls = 0
        self.usage: list[dict] = []
        self.next_call: dict[str, float] = {}
        self.reservation = asyncio.Lock()
        self.client = httpx.AsyncClient(
            timeout=config.runtime.request_timeout_s,
            transport=transport,
            follow_redirects=False,
        )

    async def close(self):
        await self.client.aclose()

    async def post(self, provider: str, url: str, payload: dict) -> dict:
        if not self.allow_api:
            raise APIBlocked("Model API calls are disabled. No request was sent.")
        if not self.credentials.get(provider):
            raise APIBlocked(f"Missing {provider} API key")
        async with self.reservation:
            if self.calls >= self.config.runtime.max_api_calls:
                raise BudgetExhausted("Model request budget reached")
            await asyncio.sleep(max(0, self.next_call.get(provider, 0) - time.monotonic()))
            self.calls += 1  # Failed and timed-out attempts consume the budget too.
            started = time.monotonic()
            self.next_call[provider] = started + self.config.runtime.min_model_interval_s
        try:
            response = await self.client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.credentials[provider]}"},
            )
            if response.status_code >= 400:
                if response.status_code in {429, 529}:
                    try:
                        delay = float(response.headers.get("Retry-After", "2"))
                    except ValueError:
                        delay = 2
                    self.next_call[provider] = time.monotonic() + min(60, max(2, delay))
                # Never log authorization, image payloads, or raw remote error bodies.
                raise ProviderFailure(f"{provider} HTTP {response.status_code}; frame skipped")
            try:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
            except ValueError:
                raise ProviderFailure(
                    f"{provider} returned a non-JSON or invalid response"
                ) from None
        except httpx.HTTPError as exc:
            raise ProviderFailure(f"{provider} network error ({type(exc).__name__})") from None
        finally:
            self.usage.append(
                {"provider": provider, "latency_ms": round((time.monotonic() - started) * 1000, 1)}
            )
        self.usage[-1]["tokens"] = data.get("usage", {})
        return data


class CerebrasVision:
    def __init__(self, gateway: Gateway):
        self.gateway = gateway

    async def observe(self, image: Image.Image) -> Battlefield:
        cfg = self.gateway.config
        unit_types = cfg.runtime.vision_unit_types
        prompt = VISION_PROMPT
        if unit_types is not None:
            prompt += (
                "\nThis demo recognizes ONLY these unit types: " + ", ".join(unit_types) + ". "
                "Omit every other troop or deployed building. If a unit is unclear, omit it; "
                "do not output unknown. Never relabel an unsupported or ambiguous unit as "
                "the nearest allowed type. Use the exact listed names. "
                "This restriction applies to both teams; fixed towers remain in towers."
            )
        crop = image.crop(cfg.layout.arena.pixels(image.size))
        crop.thumbnail((cfg.runtime.vision_max_side, cfg.runtime.vision_max_side))
        if cfg.runtime.vision_grid:
            draw = ImageDraw.Draw(crop, "RGBA")
            for i in range(1, 10):
                x, y = round(crop.width * i / 10), round(crop.height * i / 10)
                draw.line((x, 0, x, crop.height), fill=(255, 255, 255, 75), width=1)
                draw.line((0, y, crop.width, y), fill=(255, 255, 255, 75), width=1)
                draw.text(
                    (x + 2, 2), f"x={i / 10:.1f}", fill="white", stroke_width=1, stroke_fill="black"
                )
                draw.text(
                    (2, y + 2), f"y={i / 10:.1f}", fill="white", stroke_width=1, stroke_fill="black"
                )
        buffer = io.BytesIO()
        crop.save(buffer, format="JPEG", quality=90)
        uri = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
        result = await self.gateway.post(
            "cerebras",
            "https://api.cerebras.ai/v1/chat/completions",
            {
                "model": cfg.runtime.vision_model,
                "reasoning_effort": "none",
                "temperature": 0,
                "max_completion_tokens": cfg.runtime.vision_max_tokens,
                "messages": [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Extract the current visible battlefield."},
                            {"type": "image_url", "image_url": {"url": uri}},
                        ],
                    },
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "battlefield",
                        "strict": True,
                        "schema": vision_schema(unit_types),
                    },
                },
            },
        )
        try:
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("Truncated or incomplete vision response")
            board = Battlefield.model_validate_json(choice["message"]["content"])
            board = restrict_units(board, unit_types)
            for tower in board.towers:
                if position := cfg.layout.tower_positions.get(tower.id):
                    tower.x, tower.y = position.x, position.y
            return board
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderFailure("Vision returned an invalid or incomplete battlefield") from None


class JevPolicy:
    def __init__(self, gateway: Gateway):
        self.gateway = gateway

    async def decide(self, state: State, actions: list[Action]) -> Decision:
        if not self.gateway.config.runtime.staged_decisions:
            return await self.choose(state, actions, DECISION_PROMPT)
        cards = [WAIT]
        seen = set()
        for action in actions:
            if action.card and action.card not in seen:
                seen.add(action.card)
                card = self.gateway.config.cards[action.card]
                cards.append(
                    Action(
                        id=f"CARD_{action.slot}_{action.card}",
                        card=action.card,
                        slot=action.slot,
                        cost=action.cost,
                        description=f"Play {action.card} now for {action.cost} elixir. "
                        f"{card.description} Placement is chosen next.",
                    )
                )
        card_decision = await self.choose(
            state,
            cards,
            DECISION_PROMPT + "\nFirst choose the best CARD to play NOW, or WAIT. "
            "Ignore placement details for this step. Spending is essential: at 9-10 elixir "
            "choose a troop to attack or defend instead of repeatedly waiting.",
        )
        selected = next(a for a in cards if a.id == card_decision.choice)
        if selected.id == "WAIT":
            card_decision.card_probabilities = card_decision.probabilities.copy()
            card_decision.card_confidence = card_decision.confidence
            return card_decision
        positions = [a for a in actions if a.card == selected.card]
        decision = await self.choose(
            state,
            positions,
            DECISION_PROMPT + f"\nThe selected card is {selected.card}. "
            "Choose its best placement. Put defenders in the lane of approaching enemies. "
            "Attack vulnerable enemy towers and support friendly pushes.",
        )
        decision.card_probabilities = card_decision.probabilities
        decision.card_confidence = card_decision.confidence
        return decision

    async def choose(self, state: State, actions: list[Action], instructions: str) -> Decision:
        if not 1 <= len(actions) <= 255:
            raise ValueError("Jev needs 1–255 complete action choices")
        criteria = {action.id: action.description for action in actions}
        if len(criteria) != len(actions):
            raise ValueError("Action IDs must be unique")
        result = await self.gateway.post(
            "jev",
            "https://api.typesafe.ai/v1/systemone",
            {
                "model": self.gateway.config.runtime.jev_model,
                "state": {
                    "game": state.model_dump(mode="json"),
                    "deck": [
                        {"card": c.id, "cost": c.cost, "kind": c.kind, "description": c.description}
                        for c in self.gateway.config.deck
                    ],
                },
                "questions": {
                    "action": {
                        "type": "choice",
                        "instructions": instructions,
                        "criteria": criteria,
                    }
                },
            },
        )
        try:
            answer = result["answers"]["action"]
            if answer["type"] != "choice":
                raise ValueError("Wrong answer type")
            decision = Decision(
                choice=answer["choice"],
                confidence=answer["confidence"],
                probabilities=answer["probabilities"],
                source="jev",
            )
            if decision.choice not in criteria or set(decision.probabilities) != set(criteria):
                raise ValueError("Unknown or missing action")
            if abs(sum(decision.probabilities.values()) - 1) > 0.02:
                raise ValueError("Invalid distribution")
            return decision
        except (KeyError, TypeError, ValueError):
            raise ProviderFailure("Jev returned an invalid action distribution") from None
