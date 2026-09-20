# Live gameplay validation — 19 September 2026

> Historical development notes. Referenced runs, screenshots, device calibration, and recordings are local artifacts and are not bundled with the public repository. Test counts and timings below describe those development sessions.

The application runs Qwen 3.8 27B on Cerebras for battlefield perception, local OpenCV for the starter-deck hand and elixir, and Jev for gameplay choices. No key values are stored in runs or documentation.

## Winning demo

`runs/qwen-demo-verified/` records an autonomous **3–0 win** over Trainer Jonas in Training Camp. The full 180-second, 720×1280 gameplay recording includes the victory screen; `result.png` and `outcome.json` separately preserve the visually checked result. Human input was limited to opening Training Camp.

- 14 Jev-selected deployments, using all eight starter-deck cards.
- 44 Qwen requests and 44 Jev requests. Two invalid/incomplete Qwen states were skipped; subsequent frames continued normally.
- Qwen request latency: median 1.45 s, p95 1.90 s.
- Jev request latency: median 0.37 s, p95 0.92 s.
- Capture-to-action/decision latency across 30 Jev cycles: median 3.39 s, p95 4.73 s. WAIT decisions with no affordable card and menu-only frames are excluded.

The original run confirmed 13 deployments and falsely marked the last Archers deployment unconfirmed: the hand visibly changed to Mini P.E.K.K.A, but elixir regeneration hid part of the three-elixir cost. The deployed army then finished the three-crown win. The original `summary.json` is preserved unchanged, including its halt reason and pending record.

That confirmation bug is fixed in the delivered code. A deployment now requires both an observed elixir drop and a confident replacement card, with evidence retained across frames; the net drop need not equal the full cost. Replaying the original timestamps and HUD observations confirms the last deployment at frame 198, before the original timeout. `confirmation-recheck.json` records this check. No model requests or game inputs were used for the recheck, and the corrected code has not been represented as the code that generated the original summary.

Validation: **59 tests pass**, Ruff checks and formatting pass, JavaScript syntax checks pass, and the package builds. This includes explicit-key selection, calibrated tower geometry, staged card/placement decisions, partial artwork occlusion, menu gating, elixir regeneration, asynchronous confirmation, invalid-action rejection, and interruption handling. The prior complete match below separately verifies normal result-screen termination.

## Verified full match before the final refinements

`runs/qwen-live-practice-03/` contains a complete autonomous Training Camp match against Trainer Jonas. The player was `jevf`. Only navigation into Training Camp was manual; every deployed card and placement came from Jev.

- Result: **loss, 1–3 crowns**, visually checked on the game result screen.
- 10 deployments, all 10 confirmed from card replacement and elixir spending.
- 35 Qwen calls and 35 Jev calls; no provider errors.
- One proposed final action was rejected because the current screen was no longer recognized as gameplay.
- The runner stopped with `battle_finished` and no pending input.
- Qwen request latency: median 1.55 s, p95 2.15 s.
- Jev request latency: median 0.39 s, p95 0.97 s.
- Capture-to-decision/input latency for the 35 Jev cycles: median 3.50 s, p95 4.34 s. Menu-only frames are excluded from these figures.

Evidence includes `events.jsonl`, `control.jsonl`, `summary.json`, `outcome.json`, `result.png`, and `gameplay.mp4` in that run directory. The MP4 is a bounded native Android recording; the timestamped event trace is the complete run record.

## Changes exercised during testing

- Explicit credential selection through `runtime.cerebras_key_env`; a missing named key does not silently fall back to another account.
- Explicit red/blue team instructions and an optional coordinate grid in the Qwen input. Calibrated tower positions replace model-estimated tower geometry.
- Local HUD recognition skips model calls on menus and results.
- Deployment confirmation runs from fresh local screenshots without another model request. It retains spending and replacement-card evidence across separate animation frames.
- Confirmation tracks regenerated elixir, including screenshots whose capture started before the input but completed afterward. Those earlier timestamps may update the baseline but cannot confirm a deployment.
- An observed net elixir drop plus replacement is sufficient even when regeneration hides part of the card's cost; replacement alone remains insufficient.
- Card recognition compares independent artwork quadrants as well as the full crop, tolerating the white radial overlay on unaffordable cards. The six previously labelled validation frames still matched all 24 card identities and all six elixir readings. A real partially covered hand additionally matched Mini P.E.K.K.A, Fireball, Giant, and Arrows.
- Local configuration uses staged Jev decisions: choose one affordable card or WAIT, then choose one legal placement for that card. Both distributions are preserved and displayed separately. This addresses repeated WAIT choices when a single decision included many similar placements.
- The viewer omits pre-battle menu frames from the playback sequence, displays confirmed deployment totals after completion, and distinguishes live from recorded gameplay.

The initial live attempts and `runs/qwen-live-final/` are diagnostic runs that halted on confirmation issues. They are retained as evidence and are not presented as successful complete matches. The final validation uses `runs/qwen-demo-verified/`.

## Reproduce

Keep MuMu open and connect its ADB endpoint. From the project root:

```bash
uv run clash-jev --config config/local.json run --allow-api --execute \
  --seconds 360 --max-api-calls 300
```

Enter Training Camp with the starter deck. The bot waits without model calls until it recognizes the hand. It stops after the match, on Ctrl+C, or at its duration/request limit. The terminal reports the run directory for later playback.

```bash
uv run clash-jev view runs/qwen-demo-verified --port 8767
```

The calibrated local config and real card templates are present on this machine and intentionally ignored by Git. Another machine needs its own layout and artwork calibration.

## Practical limits

Qwen still makes troop-identification, team, health, and localization errors. The visible state is an estimate; this is not a game-engine state extractor. Enemy elixir and unreadable values stay unknown, and timer OCR is unavailable on this machine.

The local demo allows a 5.5-second state age to accommodate measured latency and uses Jev's highest-ranked legal choice without an uncalibrated confidence cutoff. Fresh hand/elixir checks and placement rules remain enforced. These choices make the demo playable but do not establish strong tactical skill or reliable ranked performance. Training Camp results are the stated scope of validation.
