import threading

import httpx
import pytest

from clash_jev.config import Config
from clash_jev.webui import make_server


@pytest.fixture
def calibration_server(root, tmp_path):
    output = tmp_path / "calibration.json"
    server = make_server(
        port=0,
        image=root / "fixtures/synthetic/episode/001.png",
        config_path=root / "config/synthetic.json",
        output=output,
        root=tmp_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            yield client, output
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_calibration_save_and_template_route(calibration_server, config):
    client, output = calibration_server
    body = config.model_dump(mode="json")
    response = client.post("/api/template", json={"config": body, "slot": 0, "card": "knight"})
    assert response.status_code == 200
    body = response.json()
    assert body["deck"][0]["templates"][-1].startswith("assets/cards/knight-")
    body["layout"]["calibrated"] = False
    assert client.post("/api/save", json={"config": body}).status_code == 400
    response = client.post("/api/save", json={"config": body, "confirmed": True})
    assert response.status_code == 200
    saved = Config.load(output)
    assert saved.layout.calibrated
    assert saved.layout.reference_size == (540, 960)


def test_calibration_cannot_serve_env_or_accept_foreign_posts(calibration_server, config):
    client, output = calibration_server
    assert client.get("/.env").status_code == 404
    assert client.get("/api/info").json()["mode"] == "calibrate"
    response = client.post(
        "/api/save",
        headers={"Origin": "https://other.example"},
        json={"config": config.model_dump(mode="json"), "confirmed": True},
    )
    assert response.status_code == 403
    assert not output.exists()


def test_calibration_rejects_invalid_geometry(calibration_server, config):
    client, _ = calibration_server
    body = config.model_dump(mode="json")
    body["layout"]["arena"]["x"] = 2
    assert client.post("/api/save", json={"config": body, "confirmed": True}).status_code == 400


@pytest.fixture
def replay_server(tmp_path):
    import json

    (tmp_path / "events.jsonl").write_text('{"frame_id": 1}\n{"partial":')
    (tmp_path / "control.jsonl").write_text('{"frame_id": 2, "status": "confirmed"}\n')
    (tmp_path / "gameplay.mp4").write_bytes(b"0123456789abcdef")
    (tmp_path / "playback.json").write_text(
        json.dumps({"elapsed_offset_s": -0.8, "capture_clock_offset_s": -100.0})
    )
    server = make_server(port=0, run=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            yield client, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_replay_video_seeking_and_telemetry(replay_server):
    client, _ = replay_server
    assert client.get("/api/media").json()["url"] == "/gameplay.mp4"
    assert client.get("/api/control").json()[0]["status"] == "confirmed"
    assert client.get("/api/events").json() == [{"frame_id": 1}]
    response = client.get("/gameplay.mp4", headers={"Range": "bytes=4-8"})
    assert response.status_code == 206
    assert response.content == b"45678"
    assert response.headers["Content-Range"] == "bytes 4-8/16"
    assert client.get("/gameplay.mp4", headers={"Range": "bytes=-3"}).content == b"def"
    assert client.get("/gameplay.mp4", headers={"Range": "bytes=12-"}).content == b"cdef"
    assert client.get("/gameplay.mp4", headers={"Range": "bytes=20-"}).status_code == 416
    assert client.get("/gameplay.mp4", headers={"Range": "bytes=0-1,4-5"}).status_code == 416
    assert client.get("/gameplay.mp4").content == b"0123456789abcdef"
    assert client.get("/replay-state.js").status_code == 200
    assert client.get("/.env").status_code == 404
    assert client.post("/api/save", json={}).status_code == 403


def test_unaligned_video_does_not_claim_synchronized_playback(replay_server):
    client, directory = replay_server
    (directory / "playback.json").unlink()
    assert client.get("/api/media").json() == {}
