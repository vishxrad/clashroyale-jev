"""Build compact, shared decision inputs for Jev versus Laya matches.

Keep visible threats, towers, and the hand within a small encoder context. Short
option keys map back to complete controller actions without inventing game data.
"""

from __future__ import annotations

from .config import Config
from .models import Action, State


def compact_question(config: Config, state: State, actions: list[Action]):
    """Return the same state and choice schema for either decision provider."""
    hand = ", ".join(card.card or "unknown" for card in state.hud.hand)
    lines = [
        "Clash Royale. You=ally, bottom. Enemy=top. Positions x,y in 0..1; river y=0.5.",
        f"Elixir={state.hud.elixir}; hand={hand}; seconds={state.hud.timer_seconds}.",
    ]
    for tower in sorted(state.towers, key=lambda tower: tower.id):
        lines.append(f"{tower.id}: {'destroyed' if tower.destroyed else tower.hp} HP.")
    units = sorted(
        state.units,
        key=lambda unit: (unit.team != "enemy", -unit.y if unit.team == "enemy" else unit.y),
    )
    for unit in units[:6]:
        lines.append(f"{unit.team} {unit.type} at {unit.x:.2f},{unit.y:.2f} {unit.health_band}.")
    lines.append(f"Other observed units omitted={max(0, len(units) - 6)}. Enemy elixir unknown.")
    placing = all(action.position is not None for action in actions)
    criteria = {}
    mapping = {}
    for index, action in enumerate(actions):
        key = str(index)
        mapping[key] = action.id
        if action.id == "WAIT":
            criteria[key] = "Wait and save elixir"
        elif placing:
            prefix = f"PLAY_{action.slot}_{action.card}_"
            name = action.id.removeprefix(prefix).replace("_", " ")
            criteria[key] = f"{name} {action.position.x:.2f},{action.position.y:.2f}"
        else:
            card = config.cards[action.card]
            criteria[key] = f"{card.id} costs {card.cost}. {card.description}"
    instructions = (
        f"Place {actions[0].card} to win Clash Royale."
        if placing
        else "Choose a card to win Clash Royale."
    )
    return (
        "\n".join(lines),
        {"type": "choice", "instructions": instructions, "criteria": criteria},
        mapping,
    )
