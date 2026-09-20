# Jev plays Clash Royale

A screenshot-driven Clash Royale bot: **Jev chooses the card and placement**, Qwen 3.8 27B on Cerebras reads the battlefield, and OpenCV recognizes the hand and elixir. The game runs on an Android device or emulator, controlled through ADB.

The browser demo puts gameplay beside the current decision, hand, card/placement scores, and action JSON. This repository includes the application, synthetic fixtures, calibration tools, and tests. Bring your own model credentials, device calibration, and card templates for live play.

## Try it without API keys or a device

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git, then run:

```bash
git clone https://github.com/vishxrad/clashroyale-jev.git
cd clashroyale-jev
uv sync --locked --python 3.12
uv run clash-jev --config config/synthetic.json replay fixtures/synthetic/episode --output runs/offline-demo
uv run clash-jev view runs/offline-demo --port 8765
```

Open **http://127.0.0.1:8765/**. This uses synthetic images and scripted decisions, makes no model requests, and sends no game input. Use a new output directory when repeating a replay. Open the server URL, not `index.html` directly.

## How it works

```text
Screenshot + capture timestamp
    ├── card crops and elixir bar → local OpenCV recognition
    └── battlefield crop → Qwen → units and towers
                     ↓
       tracked state + hand + recent actions
                     ↓
          affordable card/placement candidates
                     ↓
         Jev chooses a card, then a placement
                     ↓
       check freshness, hand, cost and position
                     ↓
         ADB card tap → arena tap → confirmation
```

The demo configuration uses two-stage decisions. The generic example also supports a single joint card/placement choice. WAIT is always available. A deployment is confirmed from a replacement card and observed elixir spending. The controller stops on an unresolved deployment rather than blindly repeating input.

The starter deck is **Knight, Archers, Giant, Musketeer, Mini P.E.K.K.A, Minions, Fireball, and Arrows**.

## Run against your game

### 1. Requirements

- Python 3.11+; the setup and CI use Python 3.12.
- An Android device or emulator running Clash Royale, with ADB already enabled and reachable. Live capture and input were tested on macOS with MuMuPlayer Pro.
- `adb` from [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools), or the supported bundled MuMu/BlueStacks executable. Supply `--adb` if automatic discovery cannot find it.
- `ffmpeg` on PATH for the browser's continuous gameplay feed.
- Cerebras access to the configured image-capable Qwen model, and a Jev API key. Model availability depends on your provider account.
- Optional: `tesseract` for the timer crop. Without it, the timer remains unknown.

### 2. Credentials

```bash
cp .env.example .env
```

Fill in `CEREBRAS_API_KEY` and `JEV_API_KEY` locally. The main bot uses these two providers. `OPENAI_API_KEY` is optional and used only by the separate saved-screenshot comparison script.

`.env` is ignored by Git. If you use a custom credential variable, set `runtime.cerebras_key_env` to its name; otherwise leave that setting unset or `null`. Credential values never belong in configuration JSON.

### 3. Capture and calibrate

Use `adb devices -l` to find your device serial. For an emulator exposing a TCP debugging port, connect to the port reported by that emulator:

```bash
adb connect 127.0.0.1:YOUR_ADB_PORT
uv run clash-jev capture --serial YOUR_DEVICE_SERIAL --output captures/battle.png
uv run clash-jev --config config/example.json calibrate captures/battle.png --port 8766
```

Replace the uppercase placeholders. Capture an actual battlefield with the starter deck visible, then open **http://127.0.0.1:8766/**:

1. Mark the battlefield, four card-artwork regions, and the full elixir bar.
2. Review the deployment positions, own-half boundary, and tower exclusion regions.
3. Save artwork templates for the visible cards. Repeat with more captures until all eight cards have templates.
4. Test local recognition, review the layout, and save to `config/local.json`.

Preserve your calibration while adding templates from later captures:

```bash
uv run clash-jev --config config/local.json calibrate captures/next-hand.png --port 8766
uv run clash-jev --config config/local.json inspect captures/next-hand.png
uv run clash-jev --config config/local.json doctor
```

The example geometry and synthetic fixtures are not calibrated for your game. Each installation needs its own screenshots and templates. Recalibrate if framing, orientation, or the game UI changes.

### 4. Choose runtime settings

See [the demonstrated runtime settings](docs/runtime-settings.md) for the existing demo's model, two-stage decisions, freshness limits, and 14-type detection allowlist. Apply them to your own `config/local.json` after calibration. They are documented separately so the generic defaults and your saved layout stay explicit.

### 5. Start the browser demo

```bash
uv run clash-jev --config config/local.json demo --allow-api --execute \
  --serial YOUR_DEVICE_SERIAL --seconds 360 --max-api-calls 300 --port 8767
```

Open **http://127.0.0.1:8767/**, click **Start Jev**, and enter a battle manually. **Stop** cancels model processing and game input while the gameplay stream continues. Omit `--execute` to observe and select moves without sending taps.

`--allow-api` explicitly enables billed model requests. `--max-api-calls` limits total attempts across both providers, including failed attempts. The run also stops at its time limit, a detected battle end, or a controller/provider failure condition.

The default display has a **5-second buffer** to align observations with their source frames. Choose **Live** for immediate video. The buffer does not delay bot input. Model capture and the video feed run independently; old queued frames are skipped. The native stream uses the calibrated reference resolution, verified at 1440×2560 in the original setup.

For terminal-only operation:

```bash
uv run clash-jev --config config/local.json run --allow-api --execute \
  --serial YOUR_DEVICE_SERIAL --seconds 360 --max-api-calls 300
```

## Inspect a recording or test the models

Each live run writes a new directory under `runs/`. Pass that directory to the viewer:

```bash
uv run clash-jev view runs/YOUR_RUN --port 8768
```

The replay viewer is read-only. It uses timestamped screenshots, or continuous `gameplay.mp4` when the run has explicit `playback.json` alignment. It never starts the bot or calls a model API. Historical recordings mentioned in the development notes are local artifacts and are not included in this repository.

For a single perception request:

```bash
uv run clash-jev --config config/local.json perceive captures/battle.png --allow-api
```

For a saved-screenshot perception → Jev comparison, without device input:

```bash
uv run python scripts/probe_live.py captures/battle.png \
  --config config/local.json --provider cerebras --output runs/model-probe --allow-api
```

Use `uv run clash-jev --help` and `uv run python scripts/probe_live.py --help` for the other commands.

## Results and limits

The original project recorded a **3–0 Training Camp win** with 14 Jev-selected deployments using all eight cards, and an earlier complete match ended 1–3. In the winning run, median request latency was 1.45 s for Qwen and 0.37 s for Jev; the complete capture-to-decision/input median was 3.39 s across 30 Jev cycles. Those are measurements from one run, not a general win rate or performance guarantee.

See [gameplay validation](docs/live-gameplay.md) and [model experiment notes](docs/live-api-results.md) for the historical measurements and limitations. Referenced screenshots and recordings remain local.

- Perception can miss or misidentify troops, teams, positions, and tower health.
- The optional allowlist removes unsupported names before tracking and Jev; it also ignores real units outside that list.
- Positions can become stale during inference. The controller rechecks local hand/elixir before sending input.
- Card and recognition confidence values are not calibrated probabilities of winning.
- Enemy elixir is unknown. The bot receives no hidden game state.

## Development

```bash
uv sync --locked --python 3.12
uv run pytest -q
uv run ruff check src scripts tests
uv run ruff format --check src scripts tests
node --check src/clash_jev/static/app.js
node --test tests/replay-state.test.mjs
uv build
```

Node.js 22 is used in CI for the JavaScript checks. Tests use synthetic assets and mocked model transports; no credentials, emulator, or network inference are required. See [CONTRIBUTING.md](CONTRIBUTING.md).

| Path | Responsibility |
| --- | --- |
| `src/clash_jev/providers.py` | Qwen and Jev adapters, prompts, output validation, shared request budget |
| `src/clash_jev/hud.py` | Card templates, elixir, optional timer recognition |
| `src/clash_jev/state.py` | Tracking, movement estimates, optional occupancy grid |
| `src/clash_jev/actions.py` | Card/placement candidates and spell targets |
| `src/clash_jev/control.py` | Freshness checks, taps, deployment confirmation |
| `src/clash_jev/device.py` | ADB capture, taps, latest-frame handling |
| `src/clash_jev/runner.py` | Perception, HUD, decision and control loop |
| `src/clash_jev/live_demo.py` | Continuous device stream and browser Start/Stop controls |
| `src/clash_jev/webui.py`, `static/` | Calibration, replay and live interface |
| `fixtures/synthetic/` | Generated test artwork and scripted frames |

Local credentials, calibration, card templates, screenshots, runs, diagnostics, and generated media are excluded from Git. Synthetic fixtures are included and can be regenerated with `uv run python scripts/make_demo.py`; this also rewrites the example/synthetic configurations.

## Documentation

- [Jev / TypeSafe](https://docs.typesafe.ai/introduction)
- [Cerebras inference](https://inference-docs.cerebras.ai/)
- [OpenCV](https://docs.opencv.org/4.x/)
- [Android Debug Bridge](https://developer.android.com/tools/adb)
