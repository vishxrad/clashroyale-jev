from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .config import Config
from .models import HUD, HandCard


def artwork(image: Image.Image) -> np.ndarray:
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    return cv2.resize(gray, (64, 80), interpolation=cv2.INTER_AREA)


def artwork_score(patch: np.ndarray, template: np.ndarray) -> float:
    whole = float(cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED)[0, 0])
    # Unaffordable cards have a sweeping white pie overlay. Two unaffected
    # quadrants preserve identity even while the full artwork changes contrast.
    parts = []
    for y in (0, 40):
        for x in (0, 32):
            a, b = patch[y : y + 40, x : x + 32], template[y : y + 40, x : x + 32]
            if a.std() >= 3 and b.std() >= 3:
                parts.append(float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0]))
    partial = sum(sorted(parts, reverse=True)[:2]) / 2 - 0.03 if len(parts) >= 2 else 0
    return max(whole, partial)


class HUDReader:
    def __init__(self, config: Config, root: Path):
        self.config = config
        self.templates: dict[str, list[np.ndarray]] = {}
        for card in config.deck:
            self.templates[card.id] = [
                artwork(Image.open(root / path))
                for path in card.templates
                if (root / path).is_file()
            ]

    def missing_templates(self) -> list[str]:
        return [card for card, templates in self.templates.items() if not templates]

    def classify(self, image: Image.Image, slot: int) -> HandCard:
        patch = artwork(image)
        if patch.std() < 3:
            return HandCard(slot=slot, card=None, confidence=0)
        scores = []
        for name, templates in self.templates.items():
            valid = [t for t in templates if t.std() >= 3]
            if valid:
                score = max(artwork_score(patch, t) for t in valid)
                scores.append((max(0.0, min(1.0, score)), name))
        scores.sort(reverse=True)
        if not scores:
            return HandCard(slot=slot, card=None, confidence=0)
        best, name = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else 0
        opts = self.config.recognition
        recognized = best >= opts.card_threshold and best - runner_up >= opts.card_margin
        return HandCard(slot=slot, card=name if recognized else None, confidence=best)

    def elixir(self, image: Image.Image) -> int | None:
        hsv = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2HSV)
        opts = self.config.recognition
        mask = cv2.inRange(hsv, np.array(opts.elixir_hsv_low), np.array(opts.elixir_hsv_high))
        columns = (mask > 0).mean(axis=0) >= opts.elixir_column_fraction
        if not columns.any():
            return 0
        filled = int(np.flatnonzero(columns)[-1]) + 1
        # The calibrated bar fills left to right; reject isolated purple objects.
        if columns[:filled].mean() < 0.75:
            return None
        return min(10, max(0, int(filled / len(columns) * 10 + 0.04)))

    def timer(self, image: Image.Image) -> int | None:
        if not shutil.which("tesseract"):
            return None
        import io

        buffer = io.BytesIO()
        image.resize((image.width * 3, image.height * 3)).save(buffer, format="PNG")
        try:
            proc = subprocess.run(
                [
                    "tesseract",
                    "stdin",
                    "stdout",
                    "--psm",
                    "7",
                    "-c",
                    "tessedit_char_whitelist=0123456789:",
                ],
                input=buffer.getvalue(),
                capture_output=True,
                timeout=1,
            )
            match = re.search(r"(\d):([0-5]\d)", proc.stdout.decode())
            return int(match[1]) * 60 + int(match[2]) if match else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def read(self, image: Image.Image) -> HUD:
        layout = self.config.layout
        layout.check_size(image.size)
        hand = [
            self.classify(image.crop(rect.pixels(image.size)), i)
            for i, rect in enumerate(layout.hand)
        ]
        timer = self.timer(image.crop(layout.timer.pixels(image.size))) if layout.timer else None
        return HUD(
            hand=hand,
            elixir=self.elixir(image.crop(layout.elixir.pixels(image.size))),
            timer_seconds=timer,
        )

    def ready(self, hud: HUD) -> bool:
        names = [c.card for c in hud.hand if c.card]
        return (
            hud.elixir is not None
            and len(names) >= self.config.recognition.min_known_cards
            and len(names) == len(set(names))
        )
