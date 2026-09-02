# flux

Computer-use automation for legacy banking back-office UIs: an LLM discovers how to do a task
once, the run becomes a typed reusable **capability artifact**, and after that the artifact
**replays deterministically** — no model in the loop — with real error handling and a way to
pull a human in when it can't safely proceed.

Built for the interface.ai take-home. See [`REPORT.md`](REPORT.md) for the design write-up
(architecture, artifact schema, determinism/error handling, heterogeneity & multi-tenant,
escalation & handoff, safety, cuts), [`docs/ROADMAP.md`](docs/ROADMAP.md) for the phase-by-phase
build plan this was developed against, and [`/evidence/`](evidence/) for curated run logs.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate          # .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
python -m playwright install chromium
```

No API key is needed for any of the above, for the mock bank, or for the test suite (`pytest`) —
the discovery loop and the recorder are both covered against a scripted fake LLM client driving
the real browser and the real mock bank, so nothing in CI needs live credentials. An
`ANTHROPIC_API_KEY` is only needed for an actual live `flux discover` run (see below).

## Demo path

**1. Start the target surface** — a deliberately legacy-hostile banking back-office console
(server-rendered, table layout, no test IDs):

```bash
python -m flask --app mock_bank.app run --port 5055
```

Sign in at `http://127.0.0.1:5055` with `operator` / `letmein` (fake, local-only credentials —
see `mock_bank/app.py`) to look around. Reserved member IDs deterministically trigger the runtime
conditions replay has to handle (see `mock_bank/data.py`):

| Member ID | Behavior |
|---|---|
| `10001`, `10002`, `10003` | Normal active members with balances |
| `20001` | Permission denied opening the record |
| `30001` | Slow load (~4s) on the detail page |
| `40001` | Session silently expires on access, back to login |
| anything else | Not found |

**2. Run the agent on a goal** (needs `ANTHROPIC_API_KEY` — copy `.env.example` to `.env` or
`.env.local` and fill it in):

```bash
flux discover --goal "look up member 10001 and read their savings balance" \
  --target http://127.0.0.1:5055/login --name lookup_member_savings_balance \
  --param member_id=10001
```

On success this saves a typed, versioned capability artifact to
`artifacts/lookup_member_savings_balance.json`. `--param name=value` tells the recorder which
concrete value used *this* run should become a `{{name}}` placeholder in the saved artifact, so
the same capability replays correctly against a different member later.

**3. Replay the resulting artifact — no LLM, no API key:**

```bash
flux replay --artifact lookup_member_savings_balance --param member_id=10002 --secret password=letmein
```

Prints a structured result: `success` with typed outputs (member 10002's real balance, not the
member it was recorded against — proving the parameterization is real), a declared
`business_outcome` (try `--param member_id=77777` — no such member, a legitimate answer, not a
crash), or a `failure` with the step index, what was expected, and what was observed.

Don't have a live API key handy? [`evidence/`](evidence/) has a real live discovery + replay pair
against the actual Anthropic API, plus four further curated runs (a discovery run, a successful
replay, a business-outcome replay, and a hard-failure replay with screenshot + accessibility-tree
evidence) generated the same deterministic way the test suite is — see
[`evidence/README.md`](evidence/README.md).

## Escalation & handoff

When replay hits a real failure (not a policy block — see Safety below),
`--escalate-on-failure` keeps the session open instead of closing it, raises an intervention
request, and waits:

```bash
flux replay --artifact lookup_member_savings_balance --param member_id=10002 \
  --secret password=letmein --escalate-on-failure --headed
```

```
Escalating — intervention request b5e76cc8
  reason: action: no locator candidate resolved to exactly one element
  take control: https://chrome-devtools-frontend.appspot.com/serve_rev/.../inspector.html?ws=127.0.0.1:9222/...
  once you've bridged the gap, run: flux operator resume b5e76cc8
  waiting up to 300s...
```

The "take control" URL is the *same live browser session's* own DevTools front-end, resolved from
its CDP endpoint (`BrowserSurface` launches with `--remote-debugging-port` by default) — not a
fresh tab, not a screenshot. A person opens it in any Chromium browser, sees and drives the real
page, does whatever the automation couldn't, then — from a separate terminal —:

```bash
flux operator list             # see what's pending, and its devtools URL
flux operator resume b5e76cc8 --note "typed the member ID manually"
```

The waiting process picks that up (`flux.escalation.handoff.ControlPlaneStore` is a small on-disk
record under `evidence/escalations/`, so the two processes need no shared memory) and resumes the
*remaining* recorded steps on that same session (`replay(..., resume_from_step=...)`), verifying
the checkpoint like any other run. Playwright's own event listeners keep logging throughout, so
the evidence trail is continuous across the handoff.

Detection (`flux.escalation.detector`) only escalates real failures — a policy block (missing
`--approve`, a missing secret) is a configuration problem an operator fixes by re-running with
the right flags, not something a live browser helps with. `flux discover` detects the same stuck
states but doesn't yet wire up live mid-loop handoff — see Cuts in `REPORT.md`.

## Safety

- **Allowlist.** Both commands enforce a domain allowlist inside `BrowserSurface.act()` itself,
  not just at the call site — `flux discover` defaults it to `--target`'s own host, `flux replay`
  to the saved artifact's `app_target.base_url`; `--allow-domain` adds more (e.g. an SSO
  provider). A blocked navigate never issues the request; a same-page click that happens to land
  off-domain is caught on the way out too.
- **Never persisted, never logged.** A step whose target field looks like a credential
  (`password`, `ssn`, `token`, …) never gets its typed value recorded — the artifact stores a
  `{{secret:name}}` reference instead (`required_secrets` lists what's needed), resolved only
  from `--secret` / `FLUX_SECRET_<NAME>` env vars at replay time, never from the artifact file.
  The structured logger redacts the same way by default, for both the log file and the console.
- **Approval gate.** Any step recorded with a confirmed dialog (the mock bank's "this cannot be
  undone") marks the whole artifact `requires_approval`; unattended replay refuses to run it
  without `--approve`.

## Evidence

Every discovery and replay run gets its own directory under `evidence/runs/<run_id>/` (gitignored
scratch): a structured, redacted `log.jsonl` (one line per decision/action, "what did the agent
do and why"), plus — on any failure — a screenshot and an accessibility-tree snapshot of the page
at that moment (`flux.observability.evidence`). Four runs are promoted into the tracked
[`/evidence/`](evidence/) directory as the submission's demonstration; see its own README for
what each one shows.

## Tests

```bash
pytest
```

## License

[GNU AGPL v3](LICENSE).
