import base64
import io
import json

import httpx
import pytest
from PIL import Image

from clash_jev.actions import candidates
from clash_jev.providers import (
    APIBlocked,
    BudgetExhausted,
    CerebrasVision,
    Gateway,
    JevPolicy,
    ProviderFailure,
    keys,
    vision_schema,
)


async def test_api_disabled_before_transport(config):
    def forbidden(request):
        pytest.fail("Network gate failed")

    gateway = Gateway(config, {"cerebras": "fake"}, transport=httpx.MockTransport(forbidden))
    try:
        with pytest.raises(APIBlocked):
            await gateway.post("cerebras", "https://example.invalid", {})
        assert gateway.calls == 0
    finally:
        await gateway.close()


async def test_vision_wire_contract_and_crop(config, board, frame):
    def respond(request):
        assert str(request.url) == "https://api.cerebras.ai/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen-3.8-27b"
        assert payload["reasoning_effort"] == "none"
        assert payload["response_format"]["json_schema"]["strict"]
        schema = payload["response_format"]["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert "maxItems" not in json.dumps(schema)
        content = payload["messages"][1]["content"][1]
        assert "detail" not in content["image_url"]
        image = Image.open(io.BytesIO(base64.b64decode(content["image_url"]["url"].split(",")[1])))
        assert image.width < frame.image.width and image.height < frame.image.height
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": board.model_dump_json()}}
                ],
                "usage": {"prompt_tokens": 300},
            },
        )

    gateway = Gateway(
        config, {"cerebras": "test-key"}, allow_api=True, transport=httpx.MockTransport(respond)
    )
    try:
        result = await CerebrasVision(gateway).observe(frame.image)
        assert result == board
        assert gateway.calls == 1
        assert gateway.usage[0]["tokens"]["prompt_tokens"] == 300
    finally:
        await gateway.close()


async def test_restricted_vision_cannot_send_unsupported_units_to_jev(config, board, frame, reader):
    from clash_jev.state import Tracker

    allowed = ["knight", "goblins", "mini_pekka", "prince", "cannon"]
    config.runtime.vision_unit_types = allowed
    # A provider can violate the requested schema; do not trust its labels.
    labels = [
        "Knight",
        "goblin",
        "Mini P.E.K.K.A",
        "Prince",
        "cannons",
        "Ice Golem",
        "ice_golems",
        "Wizard",
        "wizards",
        "ice_wizard",
        "hog_rider",
        "giant_skeleton",
        "unknown",
        "fireball",
    ]
    board.units = [board.units[0].model_copy(update={"type": name}) for name in labels]
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if request.url.host == "api.cerebras.ai":
            schema = payload["response_format"]["json_schema"]["schema"]
            assert schema["$defs"]["Unit"]["properties"]["type"]["enum"] == allowed
            assert ", ".join(allowed) in payload["messages"][0]["content"]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": board.model_dump_json()}}
                    ]
                },
            )
        assert [unit["type"] for unit in payload["state"]["game"]["units"]] == allowed
        options = payload["questions"]["action"]["criteria"]
        return httpx.Response(
            200,
            json={
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "WAIT",
                        "confidence": 1,
                        "probabilities": {key: float(key == "WAIT") for key in options},
                    }
                }
            },
        )

    gateway = Gateway(
        config,
        {"cerebras": "test", "jev": "test"},
        allow_api=True,
        transport=httpx.MockTransport(respond),
    )
    try:
        filtered = await CerebrasVision(gateway).observe(frame.image)
        assert [unit.type for unit in filtered.units] == allowed
        assert filtered.towers == board.towers
        assert len(board.units) == len(labels)  # Preserve the source observation.
        state = Tracker().update(frame, filtered, reader.read(frame.image), [])
        await JevPolicy(gateway).decide(state, candidates(config, state))
        assert len(requests) == 2
        assert "enum" not in vision_schema()["$defs"]["Unit"]["properties"]["type"]
    finally:
        await gateway.close()


@pytest.mark.parametrize("message,finish", [("{", "stop"), ("{}", "length"), ("{}", "stop")])
async def test_invalid_vision_rejected(config, frame, message, finish):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"choices": [{"finish_reason": finish, "message": {"content": message}}]}
        )
    )
    gateway = Gateway(config, {"cerebras": "test"}, allow_api=True, transport=transport)
    try:
        with pytest.raises(ProviderFailure):
            await CerebrasVision(gateway).observe(frame.image)
    finally:
        await gateway.close()


async def test_jev_joint_action_contract(config, state):
    options = candidates(config, state)
    selected = next(a.id for a in options if a.card == "knight")

    def respond(request):
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        payload = json.loads(request.content)
        assert set(payload["questions"]) == {"action"}
        question = payload["questions"]["action"]
        assert question["type"] == "choice"
        assert all(isinstance(v, str) for v in question["criteria"].values())
        assert "x=" in question["criteria"][selected]
        assert payload["state"]["game"]["enemy_elixir"] is None
        return httpx.Response(
            200,
            json={
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": selected,
                        "confidence": 0.8,
                        "probabilities": {a.id: float(a.id == selected) for a in options},
                    }
                }
            },
        )

    gateway = Gateway(
        config, {"jev": "test"}, allow_api=True, transport=httpx.MockTransport(respond)
    )
    try:
        assert (await JevPolicy(gateway).decide(state, options)).choice == selected
    finally:
        await gateway.close()


async def test_staged_jev_selects_card_then_only_that_cards_placements(config, state):
    config.runtime.staged_decisions = True
    calls = []

    def respond(request):
        question = json.loads(request.content)["questions"]["action"]
        options = question["criteria"]
        calls.append(set(options))
        choice = "CARD_0_knight" if len(calls) == 1 else "PLAY_0_knight_left_defense"
        return httpx.Response(
            200,
            json={
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 0.8,
                        "probabilities": {key: float(key == choice) for key in options},
                    }
                }
            },
        )

    gateway = Gateway(
        config, {"jev": "test"}, allow_api=True, transport=httpx.MockTransport(respond)
    )
    try:
        decision = await JevPolicy(gateway).decide(state, candidates(config, state))
        assert len(calls[0]) <= 5 and "WAIT" in calls[0]
        assert all(key.startswith("PLAY_0_knight_") for key in calls[1])
        assert decision.choice == "PLAY_0_knight_left_defense"
        assert decision.card_probabilities["CARD_0_knight"] == 1
        assert sum(decision.probabilities.values()) == 1
    finally:
        await gateway.close()


async def test_jev_unknown_action_is_rejected(config, state):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "BUY_GEMS",
                        "confidence": 1,
                        "probabilities": {"BUY_GEMS": 1},
                    }
                }
            },
        )
    )
    gateway = Gateway(config, {"jev": "test"}, allow_api=True, transport=transport)
    try:
        with pytest.raises(ProviderFailure):
            await JevPolicy(gateway).decide(state, candidates(config, state))
    finally:
        await gateway.close()


async def test_failed_request_consumes_budget_and_redacts_response(config):
    config.runtime.max_api_calls = 1
    transport = httpx.MockTransport(lambda _: httpx.Response(401, text="test-secret-token"))
    gateway = Gateway(config, {"jev": "test-secret-token"}, allow_api=True, transport=transport)
    try:
        with pytest.raises(ProviderFailure) as error:
            await gateway.post("jev", "https://example.invalid", {})
        assert "test-secret-token" not in str(error.value)
        with pytest.raises(BudgetExhausted):
            await gateway.post("jev", "https://example.invalid", {})
        assert gateway.calls == 1
    finally:
        await gateway.close()


def test_existing_env_names_without_exposing_values(tmp_path, monkeypatch):
    for name in ["CEREBRAS_API_KEY", "JEV_API_KEY", "TYPESAFE_API_KEY", "cerebras", "jev"]:
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("cerebras=fake-c\njev=fake-j\nOAI=unused\n")
    assert keys(env) == {"cerebras": "fake-c", "jev": "fake-j"}


def test_explicit_cerebras_key_has_no_silent_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("cerebras-2", raising=False)
    env = tmp_path / ".env"
    env.write_text("cerebras=old\ncerebras-2=selected\njev=fake-j\n")
    assert keys(env, "cerebras-2")["cerebras"] == "selected"
    assert keys(env, "missing-key-name")["cerebras"] is None


async def test_calibrated_tower_positions_override_model_estimates(config, frame, board):
    from clash_jev.models import Point

    config.runtime.vision_grid = True
    name = board.towers[0].id
    config.layout.tower_positions[name] = Point(x=0.2, y=0.26)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": board.model_dump_json()}}
                ]
            },
        )
    )
    gateway = Gateway(config, {"cerebras": "test"}, allow_api=True, transport=transport)
    try:
        result = await CerebrasVision(gateway).observe(frame.image)
        assert result.towers[0].x == 0.2
        assert result.towers[0].y == 0.26
        assert result.towers[0].hp == board.towers[0].hp
    finally:
        await gateway.close()


def test_cli_live_requires_explicit_flag_before_loading_credentials(monkeypatch):
    from clash_jev.cli import main

    def forbidden(*args):
        pytest.fail("Credentials should not be loaded")

    monkeypatch.setattr("clash_jev.cli.keys", forbidden)
    with pytest.raises(SystemExit) as result:
        main(["run"])
    assert result.value.code == 2
