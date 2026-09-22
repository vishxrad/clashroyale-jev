"""Host a friendly-match dashboard for two independently controlled emulators.

Bind Jev and Laya to explicit, distinct ADB serials and retain separate recordings.
Share gameplay settings while preserving Jev's full prompt and each screen's
calibration. Players enter the friendly battle manually after starting both bots.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Config
from .device import ADB
from .hud import HUDReader
from .live_demo import LiveDemo, read_json
from .providers import keys, require_credentials
from .recording import atomic_json
from .webui import STATIC, make_server


def match_configs(jev_path: Path, laya_path: Path, max_calls: int) -> tuple[Config, Config]:
    """Share gameplay settings while keeping each provider's supported input format."""
    jev, laya = Config.load(jev_path), Config.load(laya_path)
    decks = [[card.model_dump(exclude={"templates"}) for card in cfg.deck] for cfg in (jev, laya)]
    if decks[0] != decks[1]:
        raise ValueError("Both players must use the same eight-card deck and descriptions")
    if jev.layout.placements != laya.layout.placements:
        raise ValueError("Both players must use the same normalized placement options")
    model, device = laya.runtime.laya_model, laya.runtime.laya_device
    laya.runtime = jev.runtime.model_copy(deep=True)
    laya.runtime.laya_model, laya.runtime.laya_device = model, device
    for cfg, provider in ((jev, "jev"), (laya, "laya")):
        cfg.runtime.decision_provider = provider
        cfg.runtime.staged_decisions = True
        # Jev keeps its original tactical instructions and full state. Only
        # Laya needs the reduced input for its small encoder context.
        cfg.runtime.compact_decisions = provider == "laya"
        cfg.runtime.min_decision_confidence = 0.0
        cfg.runtime.max_api_calls = max_calls
    return jev, laya


class Duel:
    """Coordinate shared controls while keeping both bots' device state isolated."""

    def __init__(self, players: dict[str, LiveDemo], root: Path):
        self.players, self.root = players, root
        self.lock = threading.Lock()

    def status(self):
        result = {}
        for name, player in self.players.items():
            with player.lock:
                summary = read_json(player.run / "summary.json", {}) if player.run else {}
                result[name] = {
                    "running": player.running,
                    "stopping": player.stopping,
                    "connected": player.stream.status()["connected"],
                    "serial": player.device.serial,
                    "error": player.error,
                    "stop_reason": summary.get("stop_reason"),
                    "run": str(player.run) if player.run else None,
                }
        return result

    def start(self):
        with self.lock:
            for player in self.players.values():
                if player.running or player.stopping:
                    raise ValueError("Stop both players before starting a new match")
                if not player.stream.status()["connected"]:
                    raise ValueError("Wait for both emulator video feeds to connect")
                require_credentials(
                    player.config, keys(player.env, player.config.runtime.cerebras_key_env)
                )
            try:
                for player in self.players.values():
                    player.start()
                destination = self.root / "runs" / f"duel-{time.time_ns()}.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(
                    destination,
                    {
                        "players": self.status(),
                        "format": "staged-v2",
                        "decision_inputs": {"jev": "full", "laya": "compact"},
                    },
                )
            except Exception:
                for player in self.players.values():
                    player.stop()
                raise

    def stop(self):
        with self.lock:
            for player in self.players.values():
                player.stop()


def match_server(duel: Duel, port: int, player_ports: dict[str, int]):
    """Serve the combined page and its shared controls on loopback only."""
    page = (STATIC / "duel.html").read_text()
    for name, child_port in player_ports.items():
        page = page.replace("{{" + name.upper() + "_PORT}}", str(child_port))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body, content_type="application/json", status=200):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                self.respond(page, "text/html; charset=utf-8")
            elif self.path == "/api/duel":
                self.respond(duel.status())
            else:
                self.respond({"error": "Not found"}, status=404)

        def do_POST(self):
            origin = self.headers.get("Origin")
            allowed = {
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            }
            if origin and origin not in allowed:
                return self.respond({"error": "Cross-origin control is not allowed"}, status=403)
            try:
                if self.path == "/api/duel/start":
                    duel.start()
                elif self.path == "/api/duel/stop":
                    duel.stop()
                else:
                    return self.respond({"error": "Not found"}, status=404)
                self.respond(duel.status())
            except (ValueError, OSError) as exc:
                self.respond({"error": str(exc)}, status=400)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_duel(args):
    """Validate two devices, preload Laya, and own all three local HTTP servers."""
    if not 0 < args.seconds <= 3600 or not 1 <= args.max_api_calls <= 100000:
        raise ValueError("Use a duration up to 3600 seconds and a positive request budget")
    if not 1 <= args.port <= 65533:
        raise ValueError("The duel needs three consecutive ports between 1 and 65535")
    if args.jev_serial == args.laya_serial:
        raise ValueError("Jev and Laya must target different ADB serials")
    configs = match_configs(args.jev_config, args.laya_config, args.max_api_calls)
    players = {}
    servers, threads = [], []
    try:
        for name, cfg, serial in zip(("jev", "laya"), configs, (args.jev_serial, args.laya_serial)):
            if not cfg.layout.calibrated:
                raise ValueError(f"Calibrate the {name} emulator first")
            missing = HUDReader(cfg, args.root).missing_templates()
            if missing:
                raise ValueError(f"Missing {name} card templates: " + ", ".join(missing))
            require_credentials(cfg, keys(args.env, cfg.runtime.cerebras_key_env))
            device = ADB(serial, args.adb)
            asyncio.run(device.select())
            players[name] = LiveDemo(
                cfg, args.root, args.env, device, execute=args.execute, seconds=args.seconds
            )
        from .laya_policy import load_laya

        print("Preloading Laya before either player can start...", flush=True)
        load_laya(configs[1].runtime.laya_model, configs[1].runtime.laya_device)
        ports = {"jev": args.port + 1, "laya": args.port + 2}
        for name, player in players.items():
            servers.append(make_server(port=ports[name], live=player, root=args.root))
        server = match_server(Duel(players, args.root), args.port, ports)
        servers.append(server)
        for child in servers[:-1]:
            thread = threading.Thread(target=child.serve_forever, daemon=True)
            thread.start()
            threads.append((child, thread))
        for player in players.values():
            player.stream.start()
        print(f"Jev vs Laya: http://127.0.0.1:{args.port}/", flush=True)
        print(
            "Start both players in the dashboard, then enter the friendly battle manually.",
            flush=True,
        )
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for player in players.values():
            player.close()
        for child, thread in threads:
            child.shutdown()
            thread.join(timeout=3)
        for server in servers:
            server.server_close()
