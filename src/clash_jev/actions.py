from __future__ import annotations

import math

from .config import Card, Config
from .models import WAIT, Action, Point, State


def legal(config: Config, card: Card, position: Point) -> bool:
    if card.kind == "spell":
        return True
    layout = config.layout
    return position.y >= layout.troop_min_y and not any(
        region.contains(position) for region in layout.forbidden
    )


def candidates(config: Config, state: State) -> list[Action]:
    result = [WAIT]
    if not state.battle_active or state.hud.elixir is None:
        return result
    for hand in sorted(state.hud.hand, key=lambda h: h.slot):
        card = config.cards.get(hand.card)
        if (
            not card
            or card.cost > state.hud.elixir
            or hand.confidence < config.recognition.card_threshold
        ):
            continue
        positions = [
            (p.name, Point(x=p.x, y=p.y)) for p in config.layout.placements if card.kind in p.kinds
        ]
        if card.kind == "spell":
            enemies = [
                u
                for u in state.units
                if u.team == "enemy" and u.confidence >= config.runtime.min_unit_confidence
            ]
            for enemy in enemies:
                neighbors = [u for u in enemies if math.hypot(u.x - enemy.x, u.y - enemy.y) < 0.12]
                positions.append(
                    (
                        f"cluster_{enemy.track_id}",
                        Point(
                            x=sum(u.x for u in neighbors) / len(neighbors),
                            y=sum(u.y for u in neighbors) / len(neighbors),
                        ),
                    )
                )
            positions += [
                (tower.id, Point(x=tower.x, y=tower.y))
                for tower in state.towers
                if tower.id.startswith("enemy") and not tower.destroyed
            ]
        seen = set()
        for name, point in positions:
            key = (round(point.x, 2), round(point.y, 2))
            if key in seen or not legal(config, card, point):
                continue
            seen.add(key)
            result.append(
                Action(
                    id=f"PLAY_{hand.slot}_{card.id}_{name}",
                    card=card.id,
                    slot=hand.slot,
                    position=point,
                    cost=card.cost,
                    description=f"Play {card.id} from slot {hand.slot} at {name} "
                    f"(x={point.x:.3f}, y={point.y:.3f}); costs {card.cost} elixir. "
                    f"{card.description}",
                )
            )
            if len(result) >= 255:
                return result
    return result
