"""Generate deterministic synthetic QA frames. These are not Clash Royale gameplay."""

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def main():
    deck = [
        ("knight", 3, "troop", "Ground melee defender; single target."),
        ("archers", 3, "troop", "Two ranged units that target ground and air."),
        ("giant", 5, "troop", "High-health ground unit that targets buildings."),
        ("musketeer", 4, "troop", "Ranged single-target ground and air support."),
        ("mini_pekka", 4, "troop", "High-damage ground melee single-target unit."),
        (
            "minions",
            3,
            "troop",
            "Three flying units that target ground and air; fragile to spells.",
        ),
        ("fireball", 4, "spell", "Area damage against ground and air units and towers."),
        ("arrows", 3, "spell", "Wide area damage against fragile ground and air units."),
    ]
    layout = {
        "reference_size": [540, 960],
        "calibrated": False,
        "arena": {"x": 0.05, "y": 0.08, "w": 0.9, "h": 0.72},
        "hand": [{"x": 0.12 + i * 0.21, "y": 0.83, "w": 0.17, "h": 0.10} for i in range(4)],
        "elixir": {"x": 0.12, "y": 0.95, "w": 0.8, "h": 0.02},
        "timer": None,
        "troop_min_y": 0.55,
        "forbidden": [
            {"x": 0.17, "y": 0.72, "w": 0.16, "h": 0.12},
            {"x": 0.67, "y": 0.72, "w": 0.16, "h": 0.12},
            {"x": 0.4, "y": 0.88, "w": 0.2, "h": 0.12},
        ],
        "placements": [
            {"name": name, "x": x, "y": y, "kinds": ["troop", "building"]}
            for name, x, y in [
                ("left_defense", 0.25, 0.65),
                ("right_defense", 0.75, 0.65),
                ("center_defense", 0.5, 0.65),
                ("left_bridge", 0.25, 0.56),
                ("right_bridge", 0.75, 0.56),
                ("center_pull", 0.5, 0.57),
                ("left_support", 0.15, 0.87),
                ("right_support", 0.85, 0.87),
                ("back_left", 0.3, 0.93),
                ("back_right", 0.7, 0.93),
                ("center_left", 0.4, 0.78),
                ("center_right", 0.6, 0.78),
            ]
        ],
    }
    config = {
        "layout": layout,
        "deck": [
            {"id": name, "cost": cost, "kind": kind, "description": description, "templates": []}
            for name, cost, kind, description in deck
        ],
    }
    (ROOT / "config").mkdir(exist_ok=True)
    (ROOT / "config/example.json").write_text(json.dumps(config, indent=2) + "\n")
    cards_dir = ROOT / "fixtures/synthetic/cards"
    frames_dir = ROOT / "fixtures/synthetic/episode"
    cards_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i, card in enumerate(config["deck"]):
        rng = random.Random(i + 80)
        image = Image.new("RGB", (92, 96), (30, 40, 60))
        draw = ImageDraw.Draw(image)
        for _ in range(24):
            x, y = rng.randrange(80), rng.randrange(84)
            color = tuple(rng.randrange(40, 255) for _ in range(3))
            draw.rectangle((x, y, x + 12, y + 12), fill=color)
        draw.text((3, 5), card["id"], fill="white")
        filename = f"fixtures/synthetic/cards/{card['id']}.png"
        image.save(ROOT / filename)
        card["templates"] = [filename]
    config["layout"]["calibrated"] = True  # Only for the synthetic geometry below.
    config["runtime"] = {"action_cooldown_ms": 100}
    (ROOT / "config/synthetic.json").write_text(json.dumps(config, indent=2) + "\n")
    from clash_jev.config import Config

    cfg = Config.model_validate(config)
    for i, (elixir, unit_y, choice, active) in enumerate(
        [
            (6, 0.48, "PLAY_0_knight_left_defense", True),
            (2, 0.52, "WAIT", True),
            (7, 0.56, "PLAY_0_knight_left_defense", True),
            (8, 0.60, "WAIT", False),
        ],
        1,
    ):
        image = Image.new("RGB", (540, 960), (16, 22, 35))
        draw = ImageDraw.Draw(image)
        draw.text((25, 25), "SYNTHETIC PIPELINE FIXTURE - NOT REAL GAMEPLAY", fill="white")
        draw.rectangle(cfg.layout.arena.pixels(image.size), fill=(33, 67, 51))
        left, top, right, bottom = cfg.layout.arena.pixels(image.size)
        river_y = round(top + (bottom - top) * 0.5)
        draw.rectangle((left, river_y - 12, right, river_y + 12), fill=(45, 98, 137))
        from clash_jev.models import Point

        ux, uy = cfg.layout.arena.screen_point(Point(x=0.25, y=unit_y), image.size)
        draw.ellipse((ux - 13, uy - 13, ux + 13, uy + 13), fill=(232, 76, 93))
        draw.text((ux + 18, uy - 5), "Enemy PEKKA", fill="white")
        for j, rect in enumerate(cfg.layout.hand):
            box = rect.pixels(image.size)
            art = Image.open(ROOT / cfg.deck[j].templates[0]).resize(
                (box[2] - box[0], box[3] - box[1])
            )
            image.paste(art, box[:2])
        box = cfg.layout.elixir.pixels(image.size)
        draw.rectangle(box, fill=(20, 20, 20))
        draw.rectangle(
            (box[0], box[1], box[0] + round((box[2] - box[0]) * elixir / 10) - 1, box[3]),
            fill=(210, 60, 210),
        )
        image.save(frames_dir / f"{i:03d}.png")
        spec = {
            "image": f"{i:03d}.png",
            "choice": choice,
            "battlefield": {
                "battle_active": active,
                "units": [
                    {
                        "type": "pekka",
                        "team": "enemy",
                        "x": 0.25,
                        "y": unit_y,
                        "health_band": "high",
                        "confidence": 0.93,
                    }
                ]
                if active
                else [],
                "towers": [
                    {"id": "ally_left", "x": 0.25, "y": 0.78, "hp": 1840, "destroyed": False},
                    {"id": "enemy_left", "x": 0.25, "y": 0.22, "hp": 2200, "destroyed": False},
                ]
                if active
                else [],
            },
        }
        (frames_dir / f"{i:03d}.json").write_text(json.dumps(spec, indent=2) + "\n")
    print("Wrote synthetic demo and uncalibrated example config.")


if __name__ == "__main__":
    main()
