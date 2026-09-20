# Contributing

Start with the offline demo in [README.md](README.md). Install dependencies with `uv sync --locked --python 3.12`; use Node.js 22 for JavaScript checks.

Before sending a pull request, run:

```bash
uv run pytest -q
uv run ruff check src scripts tests
uv run ruff format --check src scripts tests
node --check src/clash_jev/static/app.js
node --test tests/replay-state.test.mjs
uv build
```

Keep changes focused and explain the behavior changed and how it was verified. Use synthetic fixtures and mocked provider responses for tests. The regular test suite must not need API keys or an emulator.

Do not commit `.env`, real API credentials, `config/local.json`, card templates captured from your installation, screenshots, recordings, or private device logs. If a bug needs a run trace, share only the relevant sanitized excerpt. Clearly separate synthetic/scripted results from live model measurements.

Regenerating fixtures with `uv run python scripts/make_demo.py` rewrites the example and synthetic configurations as well as synthetic images. Review those changes before including them.
