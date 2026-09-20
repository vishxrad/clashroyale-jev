from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .models import Fraction, Model, Point, Rect


class Card(Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    cost: int = Field(ge=1, le=10)
    kind: Literal["troop", "building", "spell"]
    description: str
    templates: list[str] = Field(default_factory=list)


class Placement(Point):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kinds: list[Literal["troop", "building", "spell"]] = Field(default_factory=lambda: ["troop"])


class Layout(Model):
    reference_size: tuple[int, int]
    calibrated: bool = False
    arena: Rect
    hand: list[Rect] = Field(min_length=4, max_length=4)
    elixir: Rect
    timer: Rect | None = None
    troop_min_y: Fraction = 0.55
    forbidden: list[Rect] = Field(default_factory=list)
    placements: list[Placement] = Field(min_length=1, max_length=40)
    tower_positions: dict[str, Point] = Field(default_factory=dict)

    @model_validator(mode="after")
    def positive_size(self):
        if min(self.reference_size) <= 0:
            raise ValueError("Reference size must be positive")
        names = [p.name for p in self.placements]
        if len(names) != len(set(names)):
            raise ValueError("Placement names must be unique")
        if set(self.tower_positions) - {
            "ally_left",
            "ally_right",
            "ally_king",
            "enemy_left",
            "enemy_right",
            "enemy_king",
        }:
            raise ValueError("Unknown calibrated tower ID")
        return self

    def check_size(self, size: tuple[int, int]):
        expected = self.reference_size[0] / self.reference_size[1]
        actual = size[0] / size[1]
        if abs(expected - actual) / expected > 0.01:
            raise ValueError("Screenshot aspect ratio changed; recalibrate before running")


class Recognition(Model):
    card_threshold: Fraction = 0.82
    card_margin: Fraction = 0.05
    min_known_cards: int = Field(default=3, ge=1, le=4)
    elixir_hsv_low: tuple[int, int, int] = (125, 70, 70)
    elixir_hsv_high: tuple[int, int, int] = (179, 255, 255)
    elixir_column_fraction: Fraction = 0.35


class Runtime(Model):
    capture_hz: float = Field(default=4, gt=0, le=30)
    max_state_age_ms: int = Field(default=1500, ge=100, le=10000)
    action_cooldown_ms: int = Field(default=700, ge=100)
    confirmation_timeout_ms: int = Field(default=2500, ge=100)
    min_decision_confidence: Fraction = 0.35
    min_unit_confidence: Fraction = 0.5
    vision_model: str = "qwen-3.8-27b"
    jev_model: str = "jev-latest"
    vision_max_tokens: int = Field(default=2400, ge=100, le=8000)
    vision_max_side: int = Field(default=1024, ge=256, le=2048)
    vision_grid: bool = False
    vision_unit_types: list[str] | None = Field(default=None, min_length=1, max_length=100)
    cerebras_key_env: str | None = None
    stop_after_battle: bool = True
    staged_decisions: bool = False
    request_timeout_s: float = Field(default=10, gt=0, le=60)
    min_model_interval_s: float = Field(default=0.5, ge=0.1)
    max_api_calls: int = Field(default=30, ge=1, le=100000)

    @field_validator("vision_unit_types")
    @classmethod
    def valid_vision_unit_types(cls, values):
        if values is not None:
            if any(
                not value
                or not value.isascii()
                or not value.replace("_", "").isalpha()
                or value != value.lower()
                for value in values
            ):
                raise ValueError("Vision unit types must be lowercase names with underscores")
            if len(values) != len(set(values)):
                raise ValueError("Vision unit types must be unique")
        return values


class Config(Model):
    layout: Layout
    deck: list[Card] = Field(min_length=8, max_length=8)
    recognition: Recognition = Field(default_factory=Recognition)
    runtime: Runtime = Field(default_factory=Runtime)

    @model_validator(mode="after")
    def unique_deck(self):
        if len({c.id for c in self.deck}) != 8:
            raise ValueError("Configure exactly eight distinct cards")
        return self

    @property
    def cards(self) -> dict[str, Card]:
        return {card.id: card for card in self.deck}

    @classmethod
    def load(cls, path: Path) -> Config:
        return cls.model_validate_json(path.read_text())

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.model_dump(mode="json"), indent=2) + "\n")
