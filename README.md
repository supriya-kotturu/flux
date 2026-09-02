# flux

Computer-use automation for legacy banking back-office UIs: an LLM discovers how to do a task
once, the run becomes a typed reusable **capability artifact**, and after that the artifact
**replays deterministically** — no model in the loop — with real error handling and a way to
pull a human in when it can't safely proceed.

Built for the interface.ai take-home; see [`docs/BRIEF.md`](docs/BRIEF.md) for the assignment
and [`docs/ROADMAP.md`](docs/ROADMAP.md) for the design/build plan. **This README is a work in
progress and will be replaced with the full setup/demo instructions once the core loop
(Phase 3-5) exists.**

## Status

- [x] Phase 0 — repo scaffolding, CLI skeleton
- [x] Phase 1 — `mock_bank/`, the target surface
- [x] Phase 2 — `Surface` abstraction + Playwright driver
- [ ] Phase 3 — LLM discovery loop
- [ ] Phase 4 — artifact schema + recorder
- [ ] Phase 5 — deterministic replay engine
- [ ] Phase 6 — safety guardrails
- [ ] Phase 7 — observability/evidence
- [ ] Phase 8 — escalation & handoff
- [ ] Phase 9 — final README/REPORT + curated `/evidence/`

## Running the mock bank portal today

The target surface — a deliberately legacy-hostile banking back-office console
(server-rendered, table layout, no test IDs) — already runs standalone.

```bash
python -m venv .venv
.venv/Scripts/activate   # .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
python -m flask --app mock_bank.app run --port 5055
```

Open `http://127.0.0.1:5055`, sign in with `operator` / `letmein` (fake credentials, local
only — see `mock_bank/app.py`), and search for member `10001`.

Reserved member IDs deterministically trigger the runtime conditions the replay engine will
need to handle (see `mock_bank/data.py`):

| Member ID | Behavior |
|---|---|
| `10001`, `10002`, `10003` | Normal active members with balances |
| `20001` | Permission denied opening the record |
| `30001` | Slow load (~4s) on the detail page |
| `40001` | Session silently expires on access, back to login |
| anything else | Not found |

## Tests

```bash
pytest
```

## License

[GNU AGPL v3](LICENSE).
