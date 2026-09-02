# Evidence

Four curated runs, generated against the live mock bank with the scripted fake LLM client
(`tests/integration/test_discovery_loop.py::FakeLLMClient`) that the automated test suite also
uses — see [`REPORT.md`](../REPORT.md) for why that's a legitimate stand-in for a live model here.
Each is a straight copy of a real run directory from `evidence/runs/` (gitignored scratch —
every run lands there; these four were promoted for the submission).

The artifact these were recorded/replayed against is
[`artifacts/lookup_member_savings_balance.json`](../artifacts/lookup_member_savings_balance.json).

| Directory | What it shows |
|---|---|
| `discovery-run/` | The full observe → decide → act loop logging in via a fake operator account, searching for member `10001`, opening their record, and reading the savings balance via `table_row_value` — ending in `goal_complete`. This run is what `lookup_member_savings_balance.json` was recorded from. |
| `replay-success-run/` | The same artifact replayed **deterministically, no LLM**, with `member_id=10002` — a different member than it was recorded against. Confirms the `{{member_id}}` templating is real: outputs `$980.00`, member 10002's actual balance, not 10001's. |
| `replay-error-run/` | Replayed with `member_id=77777` (unseeded). Reported as `business_outcome: member_not_found` — a legitimate, typed result, not a crash. This is the brief's "bad input / not-found result" case. |
| `replay-hard-failure-run/` | A deliberately corrupted artifact (the final extract step's locator replaced with one that can never resolve) replayed against member `10002`. Reports `failure` / `category=action` with the step index, what was expected, and every locator candidate tried — plus, unlike the other three, `failure-step6.png` and `failure-step6.ax.txt`: the richer screenshot/accessibility-tree evidence `flux.observability.evidence` captures specifically on a failure (business outcomes and clean runs don't trigger it — there's nothing to debug). |

Reproduce any of these yourself:

```bash
python -m flask --app mock_bank.app run --port 5055 &
flux replay --artifact lookup_member_savings_balance --param member_id=10002 --secret password=letmein
flux replay --artifact lookup_member_savings_balance --param member_id=77777 --secret password=letmein
```

(The discovery and hard-failure runs used the scripted fake client directly rather than the CLI's
live `AnthropicClient` path — see `docs/ROADMAP.md` §5 Q1 and `REPORT.md` for why, and
`flux discover --goal ... --target ... --name ...` for the live equivalent once
`ANTHROPIC_API_KEY` is set.)
