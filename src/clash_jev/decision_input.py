"""Build compact, shared decision inputs for Jev versus Laya matches.

Keep tactical guidance, threats, towers, and the hand within a small encoder context. Short
option keys map back to complete controller actions without inventing game data.
"""

from __future__ import annotations

from .config import Config
from .models import Action, State


def compact_question(config: Config, state: State, actions: list[Action]):
    """Return the same state and choice schema for either decision provider."""
    lines = [
        "Clash Royale. You=ally, bottom. Enemy=top. Positions x,y in 0..1; river y=0.5.",
        "Defend threats; at 9-10 elixir build a troop push. Save spells for enemy troops "
        "or finishing a low-HP tower. Do not waste spells on a healthy king. "
        "WAIT if no useful play.",
        f"Elixir={state.hud.elixir}; seconds={state.hud.timer_seconds}.",
        "Hand: " + ", ".join(card.card or "unknown" for card in state.hud.hand) + ".",
    ]
    for tower in sorted(state.towers, key=lambda tower: tower.id):
        health = "destroyed" if tower.destroyed else (
            f"{tower.hp} HP" if tower.hp is not None else "HP unknown"
        )
        lines.append(f"{tower.id}: {health}.")
    units = sorted(
        state.units,
        key=lambda unit: (unit.team != "enemy", -unit.y if unit.team == "enemy" else unit.y),
    )
    for unit in units[:6]:
        lines.append(f"{unit.team} {unit.type} at {unit.x:.2f},{unit.y:.2f} {unit.health_band}.")
    if not any(unit.team == "enemy" for unit in units):
        lines.append("No enemy troops observed.")
    lines.append(f"Other observed units omitted={max(0, len(units) - 6)}. Enemy elixir unknown.")
    placed = next((action for action in actions if action.position is not None), None)
    placing = placed is not None
    criteria = {}
    mapping = {}
    for index, action in enumerate(actions):
        key = str(index)
        mapping[key] = action.id
        if action.id == "WAIT":
            criteria[key] = "Cancel spell; save elixir" if placing else "Wait and save elixir"
        elif placing:
            prefix = f"PLAY_{action.slot}_{action.card}_"
            name = action.id.removeprefix(prefix).replace("_", " ")
            criteria[key] = f"{name} {action.position.x:.2f},{action.position.y:.2f}"
        else:
            card = config.cards[action.card]
            criteria[key] = f"{card.id} costs {card.cost}. {card.description}"
    instructions = (
        f"Place {placed.card} to win Clash Royale."
        if placing
        else "Choose a card to win Clash Royale."
    )
    if placing and any(action.id == "WAIT" for action in actions):
        instructions = f"Place {placed.card} or save elixir."
    return (
        "\n".join(lines),
        {"type": "choice", "instructions": instructions, "criteria": criteria},
        mapping,
    )
