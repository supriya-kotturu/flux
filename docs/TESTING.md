# Manual Testing Guide

Step-by-step commands to retest every scenario this project covers, by hand. This is a reference
for you, not a deliverable — `/README.md` has the short demo path graders need;
[`evidence/README.md`](../evidence/README.md) explains the curated runs already in the repo. This
doc is for when you want to re-run any of it yourself, or watch it happen live.

Every command below was actually run and verified while writing this doc — none of it is
speculative.

## Prerequisites (once)

```bash
cd D:\northeastern-workspace\flux
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m playwright install chromium
```

For the live-LLM scenarios: copy `.env.example` to `.env` or `.env.local` and fill in
`ANTHROPIC_API_KEY` (a **workspace-scoped** key — the Console's "Default" workspace, not a
personal/identity-linked key, or you'll hit an `anthropic-workspace-id` error).

## Fastest full check: the automated suite

```bash
pytest
```

70+ tests, no API key needed — covers every scenario below except the two genuinely-live-LLM ones
(discovery is driven by a scripted fake client in tests; replay, safety, and escalation are all
tested against the real mock bank and real Playwright).

## Start the target app (needed for everything else)

**Terminal 1** — leave running for the rest of this doc:

```bash
cd D:\northeastern-workspace\flux
.venv\Scripts\Activate.ps1
python -m flask --app mock_bank.app run --port 5055
```

All other terminals below: `cd D:\northeastern-workspace\flux` then `.venv\Scripts\Activate.ps1` first.

---

## Scenario A — Live discovery (real Claude)

```bash
flux discover --goal "Log in with username 'operator' and password 'letmein'. Then look up member 10001 and read their savings balance." \
  --target http://127.0.0.1:5055/login --name demo_run --param member_id=10001 --headed
```

Watch the actual browser drive itself. Ends with `stop_reason=goal_complete success=True` and
saves `artifacts/demo_run.json`. Drop `--headed` to run headless.

## Scenario B — Deterministic replay: success

Replays `demo_run` (or the checked-in `lookup_member_savings_balance`) against a **different**
member than it was recorded on — proves the `{{param}}` templating is real, not coincidental:

```bash
flux replay --artifact lookup_member_savings_balance --param member_id=10002 --secret password=letmein --headed
```

Expect `result=success` and `outputs={'savings_balance': '$980.00'}` — member 10002's real
balance.

## Scenario C — Deterministic replay: business outcome (not found)

```bash
flux replay --artifact lookup_member_savings_balance --param member_id=77777 --secret password=letmein --headed
```

Expect `result=business_outcome` / `outcome=member_not_found: ...` — a typed result, not a crash,
because this artifact was recorded with `known_outcomes` declared (see REPORT.md → Cuts for why a
purely CLI-recorded artifact wouldn't have this).

## Scenario D — Deterministic replay: hard failure (with evidence capture)

Corrupt a copy of the artifact so a step can never resolve, then replay it:

```bash
python -c "
import json
from pathlib import Path
data = json.loads(Path('artifacts/lookup_member_savings_balance.json').read_text())
data['name'] = data['id'] = 'demo_broken'
for step in data['steps']:
    if step['kind'] == 'extract':
        step['locator']['candidates'] = [{
            'strategy': 'text', 'role': None, 'name': None, 'text': 'Definitely Not On This Page',
            'css': None, 'x': None, 'y': None, 'exact': False, 'confidence': 0.9,
        }]
Path('artifacts/demo_broken.json').write_text(json.dumps(data, indent=2))
"
flux replay --artifact demo_broken --param member_id=10002 --secret password=letmein --headed
```

Expect `result=failure`, `category=action`, the step index, expected/observed text, and every
locator candidate tried. Check `evidence/runs/replay-<timestamp>/` for `failure-step*.png` and
`failure-step*.ax.txt` — the richer signal Phase 7 captures specifically on failure.

```bash
rm artifacts/demo_broken.json   # cleanup — this one's just for the demo
```

## Scenario E — Safety: allowlist blocks an out-of-scope domain

```bash
python -c "
from flux.safety.allowlist import Allowlist
from flux.surface.browser import BrowserSurface
from flux.surface.base import Action

allowlist = Allowlist.for_domain('http://127.0.0.1:5055')
surface = BrowserSurface.launch(headless=True, allowlist=allowlist)
result = surface.act(Action(kind='navigate', value='http://example.com'))
print('blocked:', result.ok, '-', result.error)
result2 = surface.act(Action(kind='navigate', value='http://127.0.0.1:5055/login'))
print('allowed:', result2.ok)
surface.close()
"
```

Expect `blocked: False - blocked_by_allowlist: domain 'example.com' is not in the allowlist
(['127.0.0.1'])` and `allowed: True`. The block happens inside `BrowserSurface.act()` itself — no
network request for `example.com` is ever issued.

## Scenario F — Safety: redaction (no secret ever persisted or logged)

Re-run Scenario A or the CLI-driven discovery, then check:

```bash
grep "letmein" artifacts/demo_run.json          # nothing — value_template is {{secret:password}}
grep '"text": "letmein"' evidence/runs/discover-*/log.jsonl   # nothing — logged as [REDACTED]
```

## Scenario G — Safety: approval gate (irreversible action)

`artifacts/open_sub_account_demo.json` is a checked-in artifact recorded from opening a
sub-account (a dialog-confirmed, irreversible step) — `requires_approval: true`. Without
`--approve`:

```bash
flux replay --artifact open_sub_account_demo --param member_id=10002 --secret password=letmein --headless
```

Expect `result=failure`, `category=policy`, `expected: artifact approved for unattended replay`.
With `--approve`, it completes and actually opens a sub-account for member 10002:

```bash
flux replay --artifact open_sub_account_demo --param member_id=10002 --secret password=letmein --approve --headless
```

To regenerate this artifact from scratch (deterministic, no API key needed) see the script in the
git history of the commit that added it, or record it live:

```bash
flux discover --goal "Log in with username 'operator' and password 'letmein'. Then open a new sub-account for member 10001 with an initial deposit of $50." \
  --target http://127.0.0.1:5055/login --name open_sub_account_live --param member_id=10001 --headed
```

(Live-LLM outcome not guaranteed identical every run — the scripted version above is the
reliable one.)

## Scenario H — Human escalation & handoff (full walkthrough)

Needs the broken artifact from Scenario D, or make a fresh one that breaks an *earlier* step so
there's something left for a human to bridge:

```bash
python -c "
import json
from pathlib import Path
data = json.loads(Path('artifacts/lookup_member_savings_balance.json').read_text())
data['name'] = data['id'] = 'demo_handoff'
for step in data['steps']:
    if step['locator'] and step['locator']['candidates'][0].get('name') == 'Member ID or last name':
        step['locator']['candidates'] = [{
            'strategy': 'label', 'role': None, 'name': 'This Field Was Renamed On This Tenant',
            'text': None, 'css': None, 'x': None, 'y': None, 'exact': False, 'confidence': 0.85,
        }]
Path('artifacts/demo_handoff.json').write_text(json.dumps(data, indent=2))
"
```

**Terminal 2:**

```bash
flux replay --artifact demo_handoff --param member_id=10002 --secret password=letmein --escalate-on-failure --headed
```

It fails at the "type member ID" step and prints an intervention request ID + a `take control`
DevTools URL, then waits. Since you're headed, the browser window is already visible — click into
it directly (or open the DevTools URL for the fuller experience). **Type `10002` into the "Member
ID or last name" field and stop there** — don't click Search yourself (see REPORT.md → Cuts:
`resume_from_step` assumes minimal intervention — exactly one step past the failure — so going
further than that specific field breaks the resume).

**Terminal 3:**

```bash
flux operator list
flux operator resume <the-id-from-terminal-2> --note "typed the member ID manually"
```

Terminal 2 wakes up, finishes the remaining steps, and reports `result=success`.

```bash
rm artifacts/demo_handoff.json   # cleanup
```

## Scenario I — Agent-facing capability interface (flux serve)

**Terminal 2:**

```bash
flux serve
```

**Terminal 3:**

```bash
curl http://127.0.0.1:8000/capabilities
curl -X POST http://127.0.0.1:8000/capabilities/lookup_member_savings_balance/invoke \
  -H "Content-Type: application/json" \
  -d '{"params": {"member_id": "10003"}, "secrets": {"password": "letmein"}}'
```

Expect `{"kind":"success","outputs":{"savings_balance":"$15230.02"}}`. Try `member_id: 99999` to
see the business-outcome shape instead. Interactive docs at `http://127.0.0.1:8000/docs`.

## Cleanup / resetting state

Sub-account creations (Scenarios G, I) mutate the mock bank's in-memory data. **Restart the Flask
server** (Ctrl+C in Terminal 1, re-run the start command) to reset all seed data — there's no
reset endpoint, since this is a mock, not a real system. Demo artifacts you created along the way
(`demo_run.json`, `demo_broken.json`, `demo_handoff.json`, etc.) are untracked — `git status` will
show them; delete freely, they're not part of the repo.

## Troubleshooting

- **`anthropic-workspace-id is required`** — your API key is identity-linked, not
  workspace-scoped. Create a new key in the Console, explicitly selecting a workspace (e.g.
  "Default"), not a personal key.
- **`credit balance is too low`** — add credits at console.anthropic.com → Plans & Billing.
- **`Page.goto: net::ERR_ABORTED` / a bizarre pytest `INTERNALERROR`** — only relevant if you're
  writing new tests that call `BrowserSurface.launch()`: don't call it from the same thread an
  active pytest-playwright fixture is running on (conflicts with Playwright's sync API); either
  reuse the injected `page` fixture, or call `.launch()` from code that FastAPI/another framework
  dispatches to a different thread. See `tests/integration/conftest.py` and
  `tests/integration/test_capability_api.py` for the two patterns already in use.
- **A second `flux ... --headed` run won't start / CDP port conflict** — only one process can
  bind `--remote-debugging-port=9222` (the default) at a time. Close the previous one first.
