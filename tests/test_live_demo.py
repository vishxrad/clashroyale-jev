import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx

from clash_jev import live_demo
from clash_jev.webui import make_server


def test_demo_start_is_single_session_and_stop_cancels_input_loop(monkeypatch, config, tmp_path):
    entered, cancelled = threading.Event(), threading.Event()
    calls = []

    class Stream:
        def __init__(self, device, *, size):
            pass

        def status(self):
            return {"connected": True}

        def close(self):
            pass

    class Gateway:
        def __init__(self, *args, **kwargs):
            pass

        async def close(self):
            pass

    async def runner(*args, **kwargs):
        calls.append(kwargs)
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(live_demo, "ScreenStream", Stream)
    monkeypatch.setattr(live_demo, "Gateway", Gateway)
    monkeypatch.setattr(live_demo, "run_live", runner)
    monkeypatch.setattr(live_demo, "keys", lambda *args: {"cerebras": "fake", "jev": "fake"})
    demo = live_demo.LiveDemo(
        config,
        tmp_path,
        Path(".env"),
        SimpleNamespace(serial="device", executable="adb"),
        execute=True,
        seconds=300,
    )
    try:
        demo.start()
        assert entered.wait(2)
        first_run = demo.run
        demo.start()
        assert demo.run == first_run
        assert len(calls) == 1 and calls[0]["execute"] is True
        assert demo.snapshot()["bot"]["running"]
        demo.stop()
        assert cancelled.wait(2)
        demo.thread.join(2)
        assert not demo.snapshot()["bot"]["running"]
    finally:
        demo.close()


def test_live_controls_reject_foreign_origin_and_do_not_start_on_get():
    class Demo:
        run = None
        starts = 0
        stops = 0

        def snapshot(self, revision=None):
            return {"bot": {"running": bool(self.starts - self.stops)}}

        def start(self):
            self.starts += 1

        def stop(self):
            self.stops += 1

    demo = Demo()
    server = make_server(port=0, live=demo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with httpx.Client(base_url=origin, trust_env=False) as client:
            assert client.get("/api/info").json()["mode"] == "live"
            assert not client.get("/api/live").json()["bot"]["running"]
            assert demo.starts == 0
            assert (
                client.post(
                    "/api/live/start", headers={"Origin": "https://example.com"}, json={}
                ).status_code
                == 403
            )
            assert client.post("/api/live/start", content="").status_code == 415
            assert demo.starts == 0
            assert (
                client.post("/api/live/start", headers={"Origin": origin}, json={}).status_code
                == 200
            )
            assert (
                client.post("/api/live/stop", headers={"Origin": origin}, json={}).status_code
                == 200
            )
            assert demo.starts == demo.stops == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_unchanged_telemetry_is_not_retransmitted(monkeypatch, config, tmp_path):
    monkeypatch.setattr(
        live_demo,
        "ScreenStream",
        lambda _, **kwargs: SimpleNamespace(status=lambda: {"connected": True}),
    )
    demo = live_demo.LiveDemo(config, tmp_path, Path(".env"), None, execute=False, seconds=30)
    initial = demo.snapshot()
    assert "manifest" in initial
    unchanged = demo.snapshot(revision=initial["revision"])
    assert "manifest" not in unchanged and "events" not in unchanged
    assert "bot" in unchanged and "stream" in unchanged

    demo.run = tmp_path / "run-one"
    demo.run.mkdir()
    events = demo.run / "events.jsonl"
    events.write_text(json.dumps({"frame_id": 1}) + "\n")
    first = demo.snapshot(revision=initial["revision"])
    assert first["events"] == [{"frame_id": 1}]
    assert "events" not in demo.snapshot(revision=first["revision"])

    # A partial write must not lose the completed record on the following poll.
    with events.open("a") as file:
        file.write('{"frame_id":')
    partial = demo.snapshot(revision=first["revision"])
    assert partial["events"] == first["events"]
    with events.open("a") as file:
        file.write("2}\n")
    completed = demo.snapshot(revision=partial["revision"])
    assert completed["events"] == [{"frame_id": 1}, {"frame_id": 2}]

    # A finished summary and a new run both invalidate the client's revision.
    (demo.run / "summary.json").write_text('{"stop_reason":"finished"}')
    final = demo.snapshot(revision=completed["revision"])
    assert final["summary"]["stop_reason"] == "finished"
    demo.run = tmp_path / "run-two"
    assert demo.snapshot(revision=final["revision"])["events"] == []


def test_display_buffer_selects_latest_frame_before_cutoff(monkeypatch):
    monkeypatch.setattr(live_demo.shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(live_demo.time, "monotonic", lambda: 100.0)
    stream = live_demo.ScreenStream(None)
    stream.frames.extend([(1, 94.0, b"old"), (2, 94.98, b"buffered"), (3, 99.98, b"live")])
    assert stream.next_frame(-1, delay=5) == (2, b"buffered", 94.98)
    assert stream.next_frame(-1, delay=0) == (3, b"live", 99.98)
    stream.closed.set()
    assert stream.next_frame(3, delay=5) == (3, None, None)


def test_frame_endpoint_returns_timestamp_and_validates_delay():
    calls = []

    def frame(after, delay):
        calls.append((after, delay))
        return (8, b"jpeg", 10.5) if after < 8 else (8, None, None)

    demo = SimpleNamespace(run=None, stream=SimpleNamespace(next_frame=frame))
    server = make_server(port=0, live=demo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            response = client.get("/live-frame.jpg?after=3&delay=5")
            assert response.content == b"jpeg"
            assert response.headers["x-frame-sequence"] == "8"
            assert response.headers["x-frame-time"] == "10.5"
            assert calls == [(3, 5)]
            assert client.get("/live-frame.jpg?after=8&delay=5").status_code == 204
            for delay in ("-1", "30", "NaN", "oops"):
                assert client.get(f"/live-frame.jpg?delay={delay}").status_code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
