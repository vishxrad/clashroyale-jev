"""Turn ordered battlefield observations and HUD readings into tracked state.

Match nearby units by type and team to estimate identities and velocities, then
attach recent action history. Only current detections remain actionable; an
optional occupancy grid provides a spatial view of the observed units.
"""

from __future__ import annotations

import math

from .device import Frame
from .models import HUD, Battlefield, State, TrackedUnit


class Tracker:
    def __init__(self):
        self.previous: State | None = None
        self.sequence = 0

    def update(self, frame: Frame, board: Battlefield, hud: HUD, history: list[dict]) -> State:
        previous = self.previous
        if previous and frame.captured_at <= previous.captured_at:
            raise ValueError("Rejecting out-of-order observation")
        dt = frame.captured_at - previous.captured_at if previous else 0
        unmatched = list(previous.units) if previous and dt < 2 and board.battle_active else []
        tracked = []
        for unit in board.units:
            matches = [
                (math.hypot(unit.x - p.x, unit.y - p.y), p)
                for p in unmatched
                if p.type == unit.type and p.team == unit.team
            ]
            distance, match = min(matches, key=lambda pair: pair[0]) if matches else (1, None)
            if match and distance < min(0.2, 0.04 + dt * 0.18):
                unmatched.remove(match)
                tracked.append(
                    TrackedUnit(
                        **unit.model_dump(),
                        track_id=match.track_id,
                        vx=(unit.x - match.x) / dt,
                        vy=(unit.y - match.y) / dt,
                    )
                )
            else:
                self.sequence += 1
                tracked.append(TrackedUnit(**unit.model_dump(), track_id=f"u{self.sequence}"))
        # Occluded entities are not asserted to be alive: only current detections are actionable.
        state = State(
            frame_id=frame.id,
            captured_at=frame.captured_at,
            battle_active=board.battle_active,
            hud=hud,
            units=tracked,
            towers=board.towers,
            recent_actions=history[-8:],
        )
        self.previous = state
        return state


def grid(state: State, columns: int = 18, rows: int = 32) -> list[list[list[str]]]:
    """Optional derived occupancy grid; each cell can hold several entities."""
    if columns <= 0 or rows <= 0:
        raise ValueError("Grid dimensions must be positive")
    result = [[[] for _ in range(columns)] for _ in range(rows)]
    for unit in state.units:
        result[min(rows - 1, int(unit.y * rows))][min(columns - 1, int(unit.x * columns))].append(
            f"{unit.track_id}:{unit.team}:{unit.type}:{unit.health_band}"
        )
    return result
