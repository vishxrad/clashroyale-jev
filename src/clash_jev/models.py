"""Define the validated data exchanged across perception, decisions, and input.

Represent normalized geometry, battlefield observations, the HUD, tracked state,
candidate actions, and Jev decisions with Pydantic models. Shared constraints
reject invalid coordinates, duplicate identifiers, and unexpected fields.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Team = Literal["ally", "enemy", "unknown"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Point(Model):
    x: Fraction
    y: Fraction


class Rect(Model):
    x: Fraction
    y: Fraction
    w: Annotated[float, Field(gt=0, le=1)]
    h: Annotated[float, Field(gt=0, le=1)]

    @model_validator(mode="after")
    def inside(self):
        if self.x + self.w > 1.000001 or self.y + self.h > 1.000001:
            raise ValueError("Rectangle extends beyond the image")
        return self

    def pixels(self, size: tuple[int, int]) -> tuple[int, int, int, int]:
        width, height = size
        left, top = round(self.x * width), round(self.y * height)
        right = min(width, max(left + 1, round((self.x + self.w) * width)))
        bottom = min(height, max(top + 1, round((self.y + self.h) * height)))
        return left, top, right, bottom

    def contains(self, point: Point) -> bool:
        return self.x <= point.x <= self.x + self.w and self.y <= point.y <= self.y + self.h

    def screen_point(self, point: Point, size: tuple[int, int]) -> tuple[int, int]:
        width, height = size
        return (
            min(width - 1, round((self.x + self.w * point.x) * width)),
            min(height - 1, round((self.y + self.h * point.y) * height)),
        )


class Unit(Model):
    type: str = Field(min_length=1, max_length=60)
    team: Team
    x: Fraction
    y: Fraction
    health_band: Literal["high", "medium", "low", "unknown"]
    confidence: Fraction


class Tower(Model):
    id: Literal["ally_left", "ally_right", "ally_king", "enemy_left", "enemy_right", "enemy_king"]
    x: Fraction
    y: Fraction
    hp: Annotated[int, Field(ge=0, le=100000)] | None
    destroyed: bool


class Battlefield(Model):
    battle_active: bool
    units: list[Unit] = Field(max_length=100)
    towers: list[Tower] = Field(max_length=6)

    @model_validator(mode="after")
    def unique_towers(self):
        if len({t.id for t in self.towers}) != len(self.towers):
            raise ValueError("Duplicate tower IDs")
        return self


class HandCard(Model):
    slot: Annotated[int, Field(ge=0, le=3)]
    card: str | None
    confidence: Fraction


class HUD(Model):
    hand: list[HandCard] = Field(min_length=4, max_length=4)
    elixir: Annotated[int, Field(ge=0, le=10)] | None
    timer_seconds: Annotated[int, Field(ge=0, le=600)] | None = None

    @model_validator(mode="after")
    def unique_slots(self):
        if {c.slot for c in self.hand} != {0, 1, 2, 3}:
            raise ValueError("Hand must contain slots 0, 1, 2, 3")
        return self


class TrackedUnit(Unit):
    track_id: str
    vx: float = 0
    vy: float = 0


class State(Model):
    frame_id: int
    captured_at: float
    battle_active: bool
    hud: HUD
    units: list[TrackedUnit]
    towers: list[Tower]
    recent_actions: list[dict] = Field(default_factory=list)
    enemy_elixir: int | None = None


class Action(Model):
    id: str
    card: str | None = None
    slot: int | None = None
    position: Point | None = None
    cost: int = 0
    description: str


class Decision(Model):
    choice: str
    confidence: Fraction
    probabilities: dict[str, Fraction]
    source: str
    card_probabilities: dict[str, Fraction] = Field(default_factory=dict)
    card_confidence: Fraction | None = None


WAIT = Action(id="WAIT", description="Wait for a newer observation and save elixir.")
