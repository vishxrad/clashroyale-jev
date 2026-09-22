"""Save screenshots, decisions, controller events, and run summaries.

Each run gets its own directory with configuration metadata, timestamped JSONL
events, and JPEG frames. Atomically replace JSON snapshots and summarize action
counts, request usage, and latency for inspection by the browser and CLI.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .config import Config
from .device import Frame


def atomic_json(path: Path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, allow_nan=False, indent=2) + "\n")
    temp.replace(path)


class Recorder:
    def __init__(self, directory: Path, config: Config, mode: str):
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self.events: list[dict] = []
        self.started = time.monotonic()
        self.mode = mode
        self.confirmed_actions = 0
        atomic_json(
            directory / "manifest.json",
            {
                "mode": mode,
                "started_monotonic": self.started,
                "config": config.model_dump(mode="json"),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
        )

    def write(self, frame: Frame, event: dict):
        filename = f"frame-{frame.id:06d}.jpg"
        frame.image.save(self.directory / filename, quality=85)
        event = {
            "frame_id": frame.id,
            "elapsed_s": time.monotonic() - self.started,
            "mode": self.mode,
            "image": filename,
            **event,
        }
        self.events.append(event)
        with (self.directory / "events.jsonl").open("a") as file:
            file.write(json.dumps(event, allow_nan=False) + "\n")
        atomic_json(self.directory / "latest.json", event)

    def control_event(self, frame_id: int, action: dict):
        if action.get("status") == "confirmed":
            self.confirmed_actions += 1
        with (self.directory / "control.jsonl").open("a") as file:
            file.write(
                json.dumps(
                    {"frame_id": frame_id, "elapsed_s": time.monotonic() - self.started, **action},
                    allow_nan=False,
                )
                + "\n"
            )

    def finish(
        self,
        usage: list[dict],
        dropped: int = 0,
        *,
        stop_reason: str = "finished",
        recent_actions: list[dict] | None = None,
        pending_action: dict | None = None,
    ):
        latencies = [e["latency_ms"]["total"] for e in self.events if "latency_ms" in e]
        model_latencies = [
            e["latency_ms"]["total"]
            for e in self.events
            if "latency_ms" in e and e.get("decision", {}).get("source") in {"jev", "laya"}
        ]
        summary = {
            "mode": self.mode,
            "frames": len(self.events),
            "dropped_frames": dropped,
            "actions": sum(e.get("status") == "sent" for e in self.events),
            "dry_run_actions": sum(e.get("status") == "dry_run" for e in self.events),
            "confirmed_actions": self.confirmed_actions,
            "model_requests": len(usage),
            "usage": usage,
            "stop_reason": stop_reason,
            "recent_actions": recent_actions or [],
            "pending_action": pending_action,
            "total_latency_ms": {
                "p50": round(float(np.percentile(latencies, 50)), 1),
                "p95": round(float(np.percentile(latencies, 95)), 1),
            }
            if latencies
            else None,
            "model_pipeline_latency_ms": {
                "p50": round(float(np.percentile(model_latencies, 50)), 1),
                "p95": round(float(np.percentile(model_latencies, 95)), 1),
                "samples": len(model_latencies),
            }
            if model_latencies
            else None,
        }
        atomic_json(self.directory / "summary.json", summary)
        return summary
