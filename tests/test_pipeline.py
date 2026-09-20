import asyncio
import time

import httpx
import pytest

from clash_jev.actions import candidates
from clash_jev.control import Controller
from clash_jev.device import Frame, LatestFrames
from clash_jev.models import Decision, Point
from clash_jev.replay import replay
from clash_jev.state import Tracker, grid


class Device:
    def __init__(self):
        self.taps = []

    async def tap(self, x, y):
        self.taps.append((x, y))


def choose(config, state):
    action = next(a for a in candidates(config, state) if a.id == "PLAY_0_knight_left_defense")
    decision = Decision(
        choice=action.id, confidence=0.9, probabilities={action.id: 0.9, "WAIT": 0.1}, source="test"
    )
    return action, decision


def test_starter_deck_and_real_local_recognition(config, reader, frame):
    assert set(config.cards) == {
        "giant",
        "knight",
        "archers",
        "minions",
        "mini_pekka",
        "musketeer",
        "fireball",
        "arrows",
    }
    hud = reader.read(frame.image)
    assert [c.card for c in hud.hand] == ["knight", "archers", "giant", "musketeer"]
    assert hud.elixir == 6
    assert reader.ready(hud)


def test_ambiguous_template_is_unknown(config, root, frame):
    from clash_jev.hud import HUDReader

    config.deck[1].templates = config.deck[0].templates.copy()
    reader = HUDReader(config, root)
    assert reader.read(frame.image).hand[0].card is None


def test_artwork_match_tolerates_partial_white_cooldown_overlay():
    import numpy as np

    from clash_jev.hud import artwork_score

    template = np.random.default_rng(42).integers(10, 220, (80, 64), dtype=np.uint8)
    covered = template.copy()
    covered[:40] = 245
    assert artwork_score(covered, template) > 0.95
    unrelated = np.random.default_rng(43).integers(10, 220, (80, 64), dtype=np.uint8)
    assert artwork_score(covered, unrelated) < 0.2


@pytest.mark.parametrize("elixir", range(11))
def test_elixir_fill(reader, elixir):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (400, 20), (10, 10, 10))
    if elixir:
        ImageDraw.Draw(image).rectangle((0, 0, 40 * elixir - 1, 19), fill=(210, 60, 210))
    assert reader.elixir(image) == elixir


def test_aspect_ratio_change_rejected(reader, frame):
    with pytest.raises(ValueError, match="aspect ratio"):
        reader.read(frame.image.resize((960, 540)))


def test_candidates_affordability_and_spell_positions(config, state):
    state.hud.elixir = 3
    choices = candidates(config, state)
    assert choices[0].id == "WAIT"
    assert {a.card for a in choices} == {None, "knight", "archers"}
    assert all(a.position.y >= config.layout.troop_min_y for a in choices[1:])
    state.hud.hand[0].card = "arrows"
    choices = candidates(config, state)
    assert any(a.card == "arrows" and a.position.y < 0.55 for a in choices)
    assert len({a.id for a in choices}) == len(choices) <= 255


def test_tracker_and_grid_preserve_multiple_entities(frame, board, reader):
    tracker = Tracker()
    hud = reader.read(frame.image)
    first = tracker.update(frame, board, hud, [])
    next_board = board.model_copy(deep=True)
    next_board.units[0].y += 0.03
    next_board.units.append(next_board.units[0].model_copy())
    next_frame = Frame(2, frame.captured_at + 0.5, frame.image)
    second = tracker.update(next_frame, next_board, hud, [])
    assert second.units[0].track_id == first.units[0].track_id
    assert second.units[0].vy == pytest.approx(0.06)
    assert second.units[0].track_id != second.units[1].track_id
    assert sum(len(cell) for row in grid(second) for cell in row) == 2
    with pytest.raises(ValueError, match="out-of-order"):
        tracker.update(frame, board, hud, [])


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("old_state", "rejected_stale_decision"),
        ("old_hud", "rejected_old_hud"),
        ("hand", "rejected_hand_changed"),
        ("elixir", "rejected_insufficient_elixir"),
        ("confidence", "rejected_low_confidence"),
        ("position", "rejected_illegal_action"),
        ("calibration", "rejected_uncalibrated"),
        ("choice", "rejected_choice_mismatch"),
    ],
)
async def test_no_taps_for_invalid_decision(config, reader, state, frame, mutation, reason):
    device = Device()
    controller = Controller(config, device, reader, execute=True)
    action, decision = choose(config, state)
    hud = state.hud.model_copy(deep=True)
    if mutation == "old_state":
        state.captured_at = time.monotonic() - 5
    elif mutation == "old_hud":
        frame.id = 0
    elif mutation == "hand":
        hud.hand[0].card = "minions"
    elif mutation == "elixir":
        hud.elixir = 1
    elif mutation == "confidence":
        decision.confidence = 0.01
    elif mutation == "position":
        action.position = Point(x=0.3, y=0.2)
    elif mutation == "calibration":
        config.layout.calibrated = False
    elif mutation == "choice":
        decision.choice = "WAIT"
    assert await controller.apply(action, decision, state, frame, hud) == reason
    assert device.taps == []


async def test_deployment_confirmed_before_more_input(config, reader, state, frame):
    device = Device()
    controller = Controller(config, device, reader, execute=True)
    action, decision = choose(config, state)
    assert await controller.apply(action, decision, state, frame, state.hud) == "sent"
    assert len(device.taps) == 2
    assert device.taps[1] == config.layout.arena.screen_point(action.position, frame.image.size)
    assert (
        await controller.apply(action, decision, state, frame, state.hud)
        == "waiting_for_confirmation"
    )
    assert len(device.taps) == 2
    after = state.hud.model_copy(deep=True)
    after.hand[0].card = "minions"
    after.elixir = 3
    controller.reconcile(after, controller.pending.sent_at + 0.25)
    assert controller.pending is None
    assert controller.history[-1]["status"] == "confirmed"


async def test_confirmation_combines_separate_card_and_elixir_animation_frames(
    config, reader, state, frame
):
    device = Device()
    controller = Controller(config, device, reader, execute=True)
    action, decision = choose(config, state)
    await controller.apply(action, decision, state, frame, state.hud)
    sent = controller.pending.sent_at
    spending = state.hud.model_copy(deep=True)
    spending.hand[0].card = None
    spending.elixir = state.hud.elixir - action.cost
    controller.reconcile(spending, sent + 0.2)
    assert controller.pending is not None
    replacement = state.hud.model_copy(deep=True)
    replacement.hand[0].card = "minions"
    # This frame no longer meets the spending threshold, but spending was seen.
    controller.reconcile(replacement, sent + 0.6)
    assert controller.pending is None
    assert controller.history[-1]["status"] == "confirmed"
    assert len(device.taps) == 2


async def test_confirmation_tracks_elixir_regenerated_before_delayed_deployment(
    config, reader, state, frame
):
    controller = Controller(config, Device(), reader, execute=True)
    action, decision = choose(config, state)
    await controller.apply(action, decision, state, frame, state.hud)
    sent = controller.pending.sent_at
    rising = state.hud.model_copy(deep=True)
    rising.elixir = 9
    controller.reconcile(rising, sent - 0.1)
    assert not controller.pending.saw_replacement
    deployed = state.hud.model_copy(deep=True)
    deployed.hand[0].card = "minions"
    deployed.elixir = 7  # Drop of 3 with one elixir of measurement/regeneration tolerance.
    controller.reconcile(deployed, sent + 0.3)
    assert controller.pending is None
    assert controller.history[-1]["status"] == "confirmed"


async def test_double_elixir_can_hide_part_of_the_card_cost(config, reader, state, frame):
    controller = Controller(config, Device(), reader, execute=True)
    action, decision = choose(config, state)
    await controller.apply(action, decision, state, frame, state.hud)
    sent = controller.pending.sent_at
    after = state.hud.model_copy(deep=True)
    after.hand[0].card = "minions"
    after.elixir = state.hud.elixir
    controller.reconcile(after, sent + 0.1)
    assert controller.pending is not None  # Replacement alone is insufficient.
    after.elixir -= 1
    controller.reconcile(after, sent + 0.2)
    assert controller.pending is None


async def test_uncertain_input_halts_instead_of_retrying(config, reader, state, frame):
    device = Device()
    controller = Controller(config, device, reader, execute=True)
    action, decision = choose(config, state)
    await controller.apply(action, decision, state, frame, state.hud)
    controller.reconcile(state.hud, controller.pending.sent_at + 3)
    assert controller.halted
    assert (await controller.apply(action, decision, state, frame, state.hud)).startswith("halted")
    assert len(device.taps) == 2


async def test_partial_input_failure_halts(config, reader, state, frame):
    class FailingDevice(Device):
        async def tap(self, x, y):
            self.taps.append((x, y))
            if len(self.taps) == 2:
                raise RuntimeError("device disconnected")

    device = FailingDevice()
    controller = Controller(config, device, reader, execute=True)
    action, decision = choose(config, state)
    assert (await controller.apply(action, decision, state, frame, state.hud)).startswith("halted")
    assert controller.history[-1]["status"] == "input_error"


async def test_offline_replay_cannot_send_network_or_input(config, root, tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Offline replay tried a network call or input")

    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    from clash_jev.device import ADB

    monkeypatch.setattr(ADB, "tap", forbidden)
    summary = await replay(
        config, root, root / "fixtures/synthetic/episode", tmp_path / "run", 0.11
    )
    assert summary["frames"] == 4
    assert summary["model_requests"] == summary["actions"] == 0
    assert summary["dry_run_actions"] == 2
    import json

    rows = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
    assert rows[0]["decision"]["source"] == "fixture-script"
    assert rows[-1]["status"] == "unrecognized_or_inactive_screen"


async def test_latest_frame_replaces_backlog(frame):
    class Source:
        i = 0

        async def capture(self):
            self.i += 1
            return Frame(self.i, time.monotonic(), frame.image)

    frames = LatestFrames(Source(), hz=1000)
    task = asyncio.create_task(frames.produce())
    try:
        first = await frames.next()
        await asyncio.sleep(0.025)
        latest = await frames.next(first.id)
        assert latest.id > first.id + 1
        assert frames.dropped > 0
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_capture_failure_propagates():
    class Broken:
        async def capture(self):
            raise RuntimeError("disconnected")

    frames = LatestFrames(Broken(), 4)
    await frames.produce()
    with pytest.raises(RuntimeError, match="disconnected"):
        await frames.next()
