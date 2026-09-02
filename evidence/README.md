# Evidence

## Live runs (real Claude, real API call)

`discovery-run-live/` and `replay-success-run-live/` are a genuine `flux discover` /
`flux replay` pair against the real Anthropic API — not the scripted fake. The artifact is
[`artifacts/lookup_member_savings_balance_live.json`](../artifacts/lookup_member_savings_balance_live.json).

Claude logged in, searched for member `10001`, opened the record, and chose `table_row_value` to
read the balance on its own — its own reasoning is in each step's `description` and in the log
(`model_decided` events), and its own free-text `checkpoint` in `goal_complete` is what the
recorder used as the artifact's checkpoint. The password never appears in the discovery log
(`text: "[REDACTED]"`) or in the artifact (`value_template: "{{secret:password}}"}`) — the
redaction and secret-reference mechanism (`REPORT.md` → Safety) working exactly the same way it
does against the scripted client, because neither depends on which one produced the run.
`replay-success-run-live/` then replays that same Claude-recorded artifact **deterministically,
no LLM**, against `member_id=10002` — a member Claude never saw — and gets `$980.00`, that
member's real balance, back.

Reproduce this yourself (needs `ANTHROPIC_API_KEY` in `.env`/`.env.local`):

```bash
flux discover --goal "Log in with username 'operator' and password 'letmein'. Then look up member 10001 and read their savings balance." \
  --target http://127.0.0.1:5055/login --name lookup_member_savings_balance_live --param member_id=10001
flux replay --artifact lookup_member_savings_balance_live --param member_id=10002 --secret password=letmein
```

## Scripted-fake runs (deterministic, no API key needed)

Four further curated runs, generated against the live mock bank with the scripted fake LLM
client (`tests/integration/test_discovery_loop.py::FakeLLMClient`) that the automated test suite
also uses — see `REPORT.md` for why that's a legitimate stand-in for a live model. Each is a
straight copy of a real run directory from `evidence/runs/` (gitignored scratch — every run lands
there; these were promoted for the submission). The artifact is
[`artifacts/lookup_member_savings_balance.json`](../artifacts/lookup_member_savings_balance.json)
— same capability, recorded before the live run above, kept as the deterministic baseline
everything else (tests included) is built against.

| Directory | What it shows |
|---|---|
| `discovery-run/` | The same flow as the live run above, scripted. This run is what `lookup_member_savings_balance.json` was recorded from. |
| `replay-success-run/` | Replayed with `member_id=10002` — a different member than it was recorded against. Outputs `$980.00`. |
| `replay-error-run/` | Replayed with `member_id=77777` (unseeded). Reported as `business_outcome: member_not_found` — a legitimate, typed result, not a crash. This is the brief's "bad input / not-found result" case. |
| `replay-hard-failure-run/` | A deliberately corrupted artifact (the final extract step's locator replaced with one that can never resolve) replayed against member `10002`. Reports `failure` / `category=action` with the step index, what was expected, and every locator candidate tried — plus, unlike every other run here, `failure-step6.png` and `failure-step6.ax.txt`: the richer screenshot/accessibility-tree evidence `flux.observability.evidence` captures specifically on a failure (business outcomes and clean runs don't trigger it — there's nothing to debug). |

```bash
python -m flask --app mock_bank.app run --port 5055 &
flux replay --artifact lookup_member_savings_balance --param member_id=10002 --secret password=letmein
flux replay --artifact lookup_member_savings_balance --param member_id=77777 --secret password=letmein
```

## Agent-facing capability interface (stretch goal)

`api-invoke-success/` and `api-invoke-business-outcome/` are the same capability invoked over
HTTP instead of the CLI — `flux serve`, brief §8's stretch goal
(`flux/api/server.py`). Same executor, same artifact, same three-way result — now JSON.

```bash
flux serve &
curl http://127.0.0.1:8000/capabilities
curl -X POST http://127.0.0.1:8000/capabilities/lookup_member_savings_balance/invoke \
  -H "Content-Type: application/json" \
  -d '{"params": {"member_id": "10003"}, "secrets": {"password": "letmein"}}'
# {"kind":"success","outputs":{"savings_balance":"$15230.02"}}

curl -X POST http://127.0.0.1:8000/capabilities/lookup_member_savings_balance/invoke \
  -H "Content-Type: application/json" \
  -d '{"params": {"member_id": "99999"}, "secrets": {"password": "letmein"}}'
# {"kind":"business_outcome","name":"member_not_found","description":"No member matches the given ID.","step_index":5}
```

Auto-generated interactive docs at `http://127.0.0.1:8000/docs` once `flux serve` is running.
