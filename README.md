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
- [x] Phase 3 — LLM discovery loop
- [x] Phase 4 — artifact schema + recorder
- [x] Phase 5 — deterministic replay engine
- [x] Phase 6 — safety guardrails
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

## Running a live discovery (needs an Anthropic API key)

```bash
cp .env.example .env   # then fill in ANTHROPIC_API_KEY
python -m flask --app mock_bank.app run --port 5055 &   # or in a separate terminal
flux discover --goal "look up member 10001 and read their savings balance" \
  --target http://127.0.0.1:5055/login --name lookup_member_savings_balance \
  --param member_id=10001
```

On success this saves a typed, versioned capability artifact to
`artifacts/lookup_member_savings_balance.json` — `--param name=value` tells the
recorder which concrete values used during this run should become `{{name}}`
placeholders in the saved artifact (so the same capability can be replayed
against a different member later).

## Replaying a saved artifact (no LLM, no API key needed)

```bash
flux replay --artifact lookup_member_savings_balance --param member_id=10002 --secret password=letmein
```

Prints a structured result: `success` with typed outputs, a declared
`business_outcome` (e.g. `--param member_id=77777` — no such member, a
legitimate answer, not a crash), or a `failure` with the step index, what was
expected, and what was observed. An artifact with any irreversible step
(dialog-confirmed, e.g. opening a sub-account) refuses to run unattended
unless you pass `--approve`.

## Safety

- **Allowlist.** Both commands enforce a domain allowlist inside
  `BrowserSurface.act()` itself, not just at the call site — `flux discover`
  defaults it to `--target`'s own host, `flux replay` to the saved
  artifact's `app_target.base_url`; `--allow-domain` adds more (e.g. an SSO
  provider). A blocked navigate never issues the request; a same-page click
  that happens to land off-domain is caught on the way out too.
- **Never persisted, never logged.** A step whose target field looks like a
  credential (`password`, `ssn`, `token`, …) never gets its typed value
  recorded — the artifact stores a `{{secret:name}}` reference instead
  (`required_secrets` lists what's needed), resolved only from `--secret` /
  `FLUX_SECRET_<NAME>` env vars at replay time, never from the artifact file.
  The structured logger redacts the same way by default, for both the log
  file and the console.
- **Approval gate.** Any step recorded with a confirmed dialog (the mock
  bank's "this cannot be undone") marks the whole artifact
  `requires_approval`; unattended replay refuses to run it without
  `--approve`.

The loop, the recorder, the replay executor, and the safety layer are all
covered against a scripted fake LLM and the live mock bank in
`tests/integration/`, so the test suite never needs a real API key.

## Tests

```bash
pytest
```

## License

[GNU AGPL v3](LICENSE).
