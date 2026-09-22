# Jev plays Clash Royale

A screenshot-driven Clash Royale bot: **Jev chooses the card and placement**, Qwen 3.8 27B on Cerebras reads the battlefield, and OpenCV recognizes the hand and elixir. The game runs on an Android device or emulator, controlled through ADB.

The live browser demo puts gameplay beside Jev's current decision, hand, card/placement scores, and action JSON. Run it on your computer with your own emulator or Android device, API keys, and calibrated card templates. The browser connects to your local game; this repository does not host a public playable session.

## Run the live demo

### 1. Install

You need:

- Python 3.11+; the commands below use Python 3.12.
- An Android device or emulator running Clash Royale, with ADB enabled and reachable. Live capture and input were verified on macOS with MuMuPlayer Pro.
- `adb` from [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools), or the supported bundled MuMu/BlueStacks executable. Supply `--adb` if automatic discovery cannot find it.
- `ffmpeg` on PATH for the browser's continuous gameplay feed.
- Cerebras access to the configured image-capable Qwen model, and a Jev API key. Model availability depends on your provider account.
- Optional: `tesseract` for the timer crop. Without it, the timer remains unknown.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git, then run:

```bash
git clone https://github.com/vishxrad/clashroyale-jev.git
cd clashroyale-jev
uv sync --locked --python 3.12
```

### 2. Credentials

```bash
cp .env.example .env
```

Fill in `CEREBRAS_API_KEY` and `JEV_API_KEY` locally. These are the only API keys required for live gameplay.

`.env` is ignored by Git. If you use a custom credential variable, set `runtime.cerebras_key_env` to its name; otherwise leave that setting unset or `null`. Credential values never belong in configuration JSON.

### 3. Capture and calibrate

Use `adb devices -l` to find your device serial. For an emulator exposing a TCP debugging port, connect to the port reported by that emulator:

```bash
adb connect 127.0.0.1:YOUR_ADB_PORT
uv run clash-jev capture --serial YOUR_DEVICE_SERIAL --output captures/battle.png
uv run clash-jev --config config/example.json calibrate captures/battle.png --port 8766
```

Replace the uppercase placeholders. Use the starter deck: **Knight, Archers, Giant, Musketeer, Mini P.E.K.K.A, Minions, Fireball, and Arrows**. Capture an actual battlefield with the hand visible, then open **http://127.0.0.1:8766/**:

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

The example geometry is not calibrated for your game. Each installation needs its own screenshots and templates. Recalibrate if framing, orientation, or the game UI changes.

### 4. Choose runtime settings

After calibration, merge this `runtime` object into your `config/local.json`, keeping your layout and card templates. These are the settings used by the demo:

```json
{
  "runtime": {
    "capture_hz": 4.0,
    "vision_model": "qwen-3.8-27b",
    "jev_model": "jev-latest",
    "staged_decisions": true,
    "vision_max_tokens": 1600,
    "vision_max_side": 1280,
    "vision_grid": true,
    "max_state_age_ms": 5500,
    "action_cooldown_ms": 700,
    "confirmation_timeout_ms": 5000,
    "min_decision_confidence": 0.0,
    "min_unit_confidence": 0.5,
    "stop_after_battle": true,
    "request_timeout_s": 10.0,
    "min_model_interval_s": 0.5,
    "max_api_calls": 360,
    "vision_unit_types": [
      "knight", "archers", "giant", "musketeer", "mini_pekka", "minions",
      "goblins", "spear_goblins", "skeletons", "bomber", "prince",
      "valkyrie", "baby_dragon", "cannon"
    ]
  }
}
```

- **Jev decisions:** `staged_decisions` asks Jev to choose a card, then a placement. `min_decision_confidence: 0.0` lets its selected legal action proceed without a score cutoff; affordability, freshness, and placement checks still apply.
- **State age:** the 5,500 ms allowance accommodates screenshot and model latency. Positions can still be stale, so the controller rechecks the current hand and elixir before input. This is separate from the browser's display buffer.
- **Confirmation:** the 5,000 ms window allows card replacement and elixir spending evidence to arrive in different screenshots.
- **Detection scope:** the 14-type list restricts the vision prompt/schema and filters results before tracking and Jev. It can reduce irrelevant detections, but real enemy units outside the list are also omitted. Set `vision_unit_types` to `null` for unrestricted detection. Spells remain playable cards, not battlefield units.

The launch command's `--max-api-calls` overrides the budget in this file. Model access depends on your provider account; check access and billing if Qwen requests are rejected.

### 5. Start the browser demo

```bash
uv run clash-jev --config config/local.json demo --allow-api --execute \
  --serial YOUR_DEVICE_SERIAL --seconds 360 --max-api-calls 300 --port 8767
```

Open **http://127.0.0.1:8767/**, click **Start Jev**, and enter a battle manually. Open this server URL rather than `index.html` directly. **Stop** cancels model processing and game input while the gameplay stream continues. Omit `--execute` to observe and select moves without sending taps.

`--allow-api` explicitly enables billed model requests. `--max-api-calls` limits total attempts across both providers, including failed attempts. The run also stops at its time limit, a detected battle end, or a controller/provider failure condition.

The **5-second display buffer** gives Qwen and Jev time to process a screenshot before its video frame reaches the viewer. Without it, labels from an older observation can appear over gameplay where troops have already moved. The browser delays the video and aligns observations with their source frames, while decisions follow their recorded action times. Five seconds provides headroom over the measured 3.39-second median processing cycle. It does not add a five-second wait to bot input or speed up inference. Choose **Live** to remove the display buffer.

Model capture and the video feed run independently; old queued frames are skipped. The native stream uses the calibrated reference resolution, verified at 1440×2560 in the original setup.

For terminal-only operation:

```bash
uv run clash-jev --config config/local.json run --allow-api --execute \
  --serial YOUR_DEVICE_SERIAL --seconds 360 --max-api-calls 300
```

Each live run saves its observations and decisions under `runs/`. Use `uv run clash-jev --help` for the capture, calibration, inspection, and recording commands.

## Jev versus Laya in a friendly battle

The experimental `duel` command runs two independent bot sessions with a shared dashboard: Jev controls one emulator and [Laya](https://huggingface.co/convaiinnovations/laya) controls the other. Both use Qwen for battlefield perception and OpenCV for the hand and elixir. Laya runs locally and needs no decision API key.

1. Open two MuMu instances with separate Clash Royale accounts. Both accounts need access to a common friendly-battle mode. Connect each instance's ADB port and use `adb devices -l` to get the two distinct serials.
2. Equip the same starter deck on both accounts. Calibrate each screen separately, saving `config/local.json` for Jev and `config/local-laya.json` for Laya. Use the same normalized placement options and card descriptions. Each configuration can have its own screenshot regions and card templates.

For the second screen, start from Jev's configuration so the deck and normalized placements stay matched, then adjust its capture regions and templates:

```bash
uv run clash-jev capture --serial LAYA_DEVICE_SERIAL --output captures/laya-battle.png
uv run clash-jev --config config/local.json calibrate captures/laya-battle.png \
  --output config/local-laya.json --port 8766
```

For later calibration captures, load `--config config/local-laya.json` and keep `--output config/local-laya.json`. Both local configuration files are ignored by Git.

3. Download and check the local model before starting a match:

```bash
uv run --extra laya clash-jev laya-warmup
```

The first launch downloads the English Laya checkpoint and its Python dependencies. The SDK selects an available GPU, including Apple MPS, or CPU. Use `--device cpu` with the warmup command to check CPU loading. To select the device for matches, set `runtime.laya_device` in the Laya configuration to `auto`, `mps`, `cuda`, or `cpu`; `runtime.laya_model` selects the checkpoint and defaults to `convaiinnovations/laya`.

4. Start both dashboards:

```bash
uv run --extra laya clash-jev duel --allow-api --execute \
  --jev-config config/local.json --laya-config config/local-laya.json \
  --jev-serial JEV_DEVICE_SERIAL --laya-serial LAYA_DEVICE_SERIAL \
  --seconds 360 --max-api-calls 600 --port 8780
```

Open **http://127.0.0.1:8780/**, wait for both feeds, click **Start both**, and enter a friendly battle manually. **Stop both** stops model processing and taps. The individual dashboards run on ports 8781 and 8782. These three ports must be available. Omit `--execute` to observe decisions without sending taps.

The launcher checks that the serials differ and that the decks and placement options match. It copies Jev's runtime settings to both players, except for Laya's model/device selection, then enables two-stage choices and removes the score cutoff for both. Jev retains its original full game state and tactical instructions, including spell targeting guidance. Only Laya uses compact input. Each player has a separate controller, capture loop, recording, and API budget; `--max-api-calls` applies per player. Cold model loading happens before either player starts. Match manifests under `runs/duel-*.json` link the two run directories and identify each provider's input format.

Laya's root checkpoint has a 512-token context, so its input keeps the hand and towers plus at most six observed units, prioritizing enemies closest to our side, with at most 12 candidate placements for the selected card. Laya rejects requests that its tokenizer would truncate. Jev receives the full observed state, action history, card descriptions, and available placement candidates in both single-player and duel modes.

For a useful comparison, keep decks, levels, perception settings, and placement options matched, then swap which account each model controls for another round. Each model sees only its own screen. The five-second buffers affect the displayed video, and each bot acts as soon as its own decision is ready. This compares the complete running agents, including inference speed, perception errors, and their different input formats. Early runs recorded as `compact-staged-v1` also compressed Jev's input and omitted its tactical prompt, so they do not represent the original Jev setup.

Laya is an experimental opponent here. Its authors report weak base-checkpoint performance on unfamiliar typed-decision tasks, and their benchmark-tuned checkpoint was not trained for Clash Royale. A valid card choice does not establish tactical skill. Local saved-state probes verify the adapter; a live head-to-head match still requires two prepared accounts.

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

## Results and limits

On 19 September 2026, a recorded run ended in a **3-0 Training Camp win** with 14 Jev-selected deployments using all eight cards. Human input was limited to entering the battle. An earlier complete match ended in a 1-3 loss.

| Measurement from the winning run | Median |
| --- | --- |
| Qwen perception request | 1.45 s |
| Jev decision request | 0.37 s |
| Complete capture-to-decision/input cycle | 3.39 s |

The complete-cycle figure covers 30 Jev cycles, excluding menu frames and waits with no affordable card. The run made 44 Qwen requests and 44 Jev requests; two invalid or incomplete Qwen states were skipped. These are measurements from one run, not a general win rate or performance guarantee. Recordings and traces remain local and are not included in this repository.

The winning run exposed a final-deployment confirmation issue when elixir regeneration obscured part of the spending. The current controller handles partial spending evidence together with card replacement. The figures above describe the original run.

- Perception can miss or misidentify troops, teams, positions, and tower health.
- The optional allowlist removes unsupported names before tracking and Jev; it also ignores real units outside that list.
- Positions can become stale during inference. The controller rechecks local hand/elixir before sending input.
- Card and recognition confidence values are not calibrated probabilities of winning.
- Enemy elixir is unknown. The bot receives no hidden game state.

## Code map

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
| `src/clash_jev/laya_policy.py` | Optional local Laya inference and context checks |
| `src/clash_jev/decision_input.py` | Shared compact state and choices for duel mode |
| `src/clash_jev/duel.py` | Two emulator sessions and shared match controls |
| `src/clash_jev/webui.py`, `static/` | Calibration, replay and live interface |

Local credentials, calibration, card templates, screenshots, runs, diagnostics, and generated media are excluded from Git.

## Documentation

- [Jev / TypeSafe](https://docs.typesafe.ai/introduction)
- [Cerebras inference](https://inference-docs.cerebras.ai/)
- [OpenCV](https://docs.opencv.org/4.x/)
- [Android Debug Bridge](https://developer.android.com/tools/adb)
