# Runtime settings used by the demo

These are the existing local demo settings, provided as a reference for a new installation. They do not replace device calibration or card templates. The application defaults and `config/example.json` are unchanged.

After calibrating, add or merge this `runtime` object at the top level of your own `config/local.json`:

```json
{
  "runtime": {
    "capture_hz": 4.0,
    "max_state_age_ms": 5500,
    "action_cooldown_ms": 700,
    "confirmation_timeout_ms": 5000,
    "min_decision_confidence": 0.0,
    "min_unit_confidence": 0.5,
    "vision_model": "qwen-3.8-27b",
    "jev_model": "jev-latest",
    "vision_max_tokens": 1600,
    "vision_max_side": 1280,
    "vision_grid": true,
    "stop_after_battle": true,
    "staged_decisions": true,
    "request_timeout_s": 10.0,
    "min_model_interval_s": 0.5,
    "max_api_calls": 360,
    "vision_unit_types": [
      "knight",
      "archers",
      "giant",
      "musketeer",
      "mini_pekka",
      "minions",
      "goblins",
      "spear_goblins",
      "skeletons",
      "bomber",
      "prince",
      "valkyrie",
      "baby_dragon",
      "cannon"
    ]
  }
}
```

Use the standard `CEREBRAS_API_KEY` environment variable. If your local configuration has a `cerebras_key_env` value pointing at somebody else's custom variable, remove it or set it to `null`.

`staged_decisions` asks Jev for a card choice followed by a placement choice. The 5,500 ms state-age allowance accommodates the measured screenshot/model latency but tolerates stale positions; the controller separately rechecks the current hand and elixir. The 5,000 ms confirmation window allows card cycling and spending evidence to arrive in different screenshots. `min_decision_confidence: 0.0` leaves tactical selection to Jev's chosen action; the game has not been used to calibrate these scores.

`vision_unit_types` constrains the vision prompt/schema and filters returned units before tracking and decisions. Unsupported real troops will also be omitted. Set it to `null` for unrestricted detection. Spells remain playable cards, not battlefield units.

A CLI `--max-api-calls` value overrides the request budget in the file. The README command uses 300 attempts. If your provider cannot serve the configured model, change the model setting only to an image-capable model supported by that account and the adapter.
