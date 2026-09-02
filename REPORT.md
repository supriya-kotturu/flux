# flux — Design Report

## Architecture

flux is a single Python process with five layers, each depending only on the one below it:
`surface` (Playwright, behind a `Surface` protocol) → `agent` (the LLM discovery loop) →
`artifact` (schema + recorder) → `replay` (the deterministic executor) → `safety` /
`escalation` / `observability` cutting across all of them. No services, no queue — the brief is
explicit that scaling infrastructure isn't rewarded here, and a take-home evaluated on judgment
is the wrong place to build it speculatively.

The one abstraction the whole design leans on is `Surface`: `observe() -> Observation`,
`act(Action) -> ActionResult`, `screenshot() -> bytes`. Discovery, the artifact schema, and
replay only ever see those three types — never Playwright directly. `BrowserSurface` is the only
implementation; a legacy-web or desktop surface would be a new class behind the same contract,
not a rewrite of anything above it (see **Heterogeneity** below).

Discovery perceives via Playwright's accessibility-tree snapshot (`aria_snapshot`), not
screenshots+coordinates. The brief biases toward approaches that survive "no clean DOM," and
accessibility semantics (role, label, accessible name) hold up on table-based legacy markup in a
way raw DOM structure or pixel coordinates don't — it's also literally the same computation an
assistive technology relies on. The model picks one locator strategy per step from a deliberately
narrow menu (role+name, label, a purpose-built `table_row_value` for two-column legacy tables, or
visible text — never coordinates); turning that single choice into a *ranked* fallback list is
the recorder's job, not discovery's, because the extra candidates it adds require the live
resolved element in hand, which only exists during execution.

**Key trade-off:** Playwright over a raw CDP/vision harness. Costs some generality (a truly
DOM-less desktop app needs a different surface), buys a mature accessible-locator API, auto-
waiting, and — critically — a CDP debug port already available for the escalation handoff, for
free.

## Artifact schema

An artifact is a capability contract, not a step recording. Beyond the ordered steps, it carries:
typed `input_schema`/`output_schema` (what a calling agent must supply and gets back); per-step
`locator` as a *ranked candidate list* with a confidence and a `describe()`; `known_outcomes` —
named, declared, detectable business results (e.g. `member_not_found`) using the exact same
detection primitive a locator resolves with; a `checkpoint` (same mechanism, asserts the final
state); `requires_approval`, computed from whether any step was recorded with a confirmed native
dialog; and `required_secrets` — names, never values, for steps whose target field looked like a
credential.

Two decisions were forced by bugs I found while building replay against real recorded artifacts,
not designed in up front — both are in the Phase 5/6 PR descriptions with the failing test that
caught them:

- **Extracting a value by the text discovered during recording breaks replay for any other
  input**, by construction — I did this first, and it worked once and only once. Fixed with
  `table_row_value`, a locator strategy purpose-built for the label/value table layout these
  screens actually use: it finds a value by its row's *label*, never the value.
- **A step whose target field looks like a credential must never have its typed value recorded**,
  literal or templated — an early artifact stored the mock login password in plaintext, because
  "type the password" got templated exactly like any other discovered string. Fixed by recording
  a `{{secret:name}}` reference instead, resolved only from an out-of-band mapping at replay time.
  Not rewritten out of git history (see **Cuts**) — noted, not hidden.

Parameterization itself is a human decision, not inference: the caller states which concrete
value used during a given discovery run maps to which named parameter
(`--param member_id=10001`), and the recorder replaces every literal occurrence. A person decided
what varies per invocation; the recorder doesn't guess from the transcript.

Stored as plain versioned JSON under `artifacts/`, git-tracked — reviewable with `git diff` by a
human and directly loadable by a calling agent. No database, for the same "don't build
infrastructure the brief doesn't reward" reason as above.

## Determinism & error handling

Replay's result is a real three-way type (`ReplaySuccess | ReplayBusinessOutcome | ReplayFailure`),
not a docstring convention layered over exceptions. The ordering that makes it work:

1. Any step whose action doesn't succeed triggers a `known_outcomes` check **before** it's ever
   treated as a failure — a locator failing to resolve is often exactly the signal that "member
   not found" happened, not evidence the artifact is broken.
2. A transient timeout gets a bounded retry (2 attempts, 1s backoff) — the one recoverable
   tactic implemented; an interstitial-dismissal tactic would slot into the same branch.
3. Every step reporting success still isn't trusted: the final checkpoint is independently
   re-probed, with another `known_outcomes` check if it doesn't hold.
4. A candidate other than the top-ranked one winning a step is logged as `locator_drift`, not
   silently accepted — the signal the multi-tenant story (next section) depends on.

`Artifact.requires_approval` is enforced as a real gate: unattended replay of an artifact with any
dialog-confirmed step refuses to run without `approved=True`. Native dialogs default to
**dismiss** unless a specific step explicitly arms `accept` — proven against the mock bank's own
"this cannot be undone" confirm(), which blocks a submit by default and only completes on
explicit opt-in.

## Heterogeneity & multi-tenant

**Surface abstraction.** A legacy web app (framesets, nested tables, no test IDs) needs no new
abstraction — `BrowserSurface` already assumes exactly that (the mock bank's own page chrome is a
nested `<table>` around every page's content, which is what surfaced the `table_row_value` bug).
A desktop app is a new `Surface` implementation backed by OS accessibility APIs (UIA / AX API)
returning the same `Observation`/`ActionResult` shapes; the replay executor, artifact schema, and
safety layer are unchanged because they only ever depend on the protocol. Not built — the seam is
the design answer the brief asks for here.

**Multi-tenant reuse.** `AppTarget.vendor_product` names the underlying app template independent
of any one institution; `tenant_id` is `None` for a base/vendor-template artifact or set for a
tenant override. A tenant-specific patch would override individual locator candidates or text
patterns without re-recording the whole flow. **Drift detection already exists as a side effect
of the replay design**, not as separate machinery: `locator_drift` events (§ above) are exactly
the signal that a tenant's variant has shifted — if replay consistently resolves a step at rank 2
instead of rank 1 for one tenant, that's the trigger to review, patch, or re-record. Not
implemented: the actual override-layer storage and an aggregation step over drift events across
runs.

## Escalation & handoff

Detection (`flux.escalation.detector`) separates *what stopped the run* from *whether it's worth
a person's time*: all four discovery stopping conditions except `goal_complete` qualify; for
replay, only `action`/`checkpoint`/`timeout` failures do — a `policy` failure (missing approval,
missing secret) is a configuration problem fixed by re-running with the right flag, not something
a live browser helps with.

Control transfer is real, not simulated: `BrowserSurface` already launches with
`--remote-debugging-port` (needed for nothing else — added specifically for this). "Take control"
resolves the *live tab's own* DevTools front-end URL from that CDP endpoint and hands it to a
human — the same mechanism `chrome://inspect` uses, opened in any Chromium browser, same session,
not a fresh one. `ControlPlaneStore` tracks who's driving as a small on-disk JSON record under
`evidence/escalations/`, so a second process (an operator in another terminal, `flux operator
resume <id>`) and the waiting automation agree on state without shared memory — verified with two
independent `ControlPlaneStore` instances over the same directory, and manually as two real
separate CLI processes (`flux replay --escalate-on-failure` blocking, `flux operator resume`
unblocking it from a different terminal).

Resuming reuses `replay()` itself: `resume_from_step` skips entry navigation and every step before
the one the human bridged, then continues the remaining recorded steps on the *same* surface and
re-verifies the checkpoint — proven end-to-end with the human's actions simulated by direct
`Surface.act()` calls on the paused session.

## Safety

**Allowlist** is enforced inside `BrowserSurface.act()` itself, not at a call site — so a saved
capability can't act outside its original bounds during replay just because a particular caller
forgot to check. A denied `navigate` never issues the request; a same-page click that happens to
land off-domain is caught on the outcome too. Both CLI commands default the allowlist to the
target/artifact's own host; `--allow-domain` extends it.

**Redaction** is the `RunLogger` default for both the log file and the console (a real bug — the
console echo used pre-redaction data — got fixed while wiring this in). It's targeted, not a
generic recursive walk: a log event's field-identity and typed-content live in sibling keys
(`locator.value` names the field, `text` holds what was typed), so redaction checks the former to
decide whether to scrub the latter.

**Never persisted.** As described under Artifact schema, a credential-looking field's value never
reaches the artifact file at all — a `{{secret:name}}` reference does, resolved only from
`--secret` / `FLUX_SECRET_<NAME>` at replay time.

**Limits.** The field-sensitivity heuristic is a name-hint list (`password`, `ssn`, `token`, …) —
a field labeled something it doesn't recognize would leak. Risk classification (`safe` vs.
`irreversible`) is a single heuristic (dialog-confirmed or not) — a real policy would also weigh
route patterns and button-text verbs. The allowlist covers domains and action kinds; it doesn't
yet restrict *which* fields a `type` action may target or cap how many irreversible actions one
replay can attempt.

## Cuts

- **Discovery doesn't wire up live mid-loop handoff.** Detection fires the same way replay's
  does, but resuming an open-ended LLM decision loop mid-flight is a materially bigger lift than
  resuming a deterministic step sequence, and the brief's own escalation example ("a replay hits
  a condition it can't recover from") points at replay as the primary case. The mechanism
  (`ControlPlaneStore`, the DevTools URL, pause/resume) is identical either way and is real; only
  the "re-enter the LLM loop with prior context" half is missing for discovery.
- **`resume_from_step` doesn't adapt to how far the human actually got.** It's hardcoded to
  "exactly one step past the one that failed," assuming minimal intervention — the human fixes
  only the blocking step and hands back immediately. Found by hand: manually testing the handoff,
  typing the missing input *and* clicking the next button myself (a natural thing to do while
  already in there) left the session one step ahead of what resume expected, and the next
  automated action — clicking a button that page no longer had — failed. The mechanism itself
  (pause, live takeover, resume on the same session) worked correctly in every trial; only the
  assumption about exactly where the human stopped is wrong. Fix: let `flux operator resume`
  accept an explicit `--resume-from-step`, or have replay re-observe the current page and match
  it against each step's checkpoint instead of trusting an index at all.
- **An artifact recorded purely through the CLI has no `known_outcomes`.** `flux discover`
  doesn't expose a flag for them — `recorder.record()` supports the parameter, nothing in the CLI
  calls it with one. So a legitimate business outcome (a search that genuinely finds nothing) on
  such an artifact gets reported as a hard failure instead of a clean `business_outcome`, purely
  because nobody declared what "no results" looks like for that particular capability. Confirmed
  live: replaying a CLI-recorded artifact with a made-up search term correctly detected there
  was nothing to click — and had no way to know that was expected rather than broken. The
  artifacts in `evidence/` that *do* have `known_outcomes` were recorded through the recorder API
  directly, not the CLI, for exactly this reason.
- **One recoverable tactic**, not several. Transient-timeout retry is real and tested;
  interstitial-dismissal would be the same branch with a different trigger, not built.
- **Multi-tenant override storage isn't built** — the drift *signal* is real (§ Heterogeneity),
  the layer that turns it into a stored per-tenant patch isn't.
- **The Phase 5 credential leak wasn't scrubbed from git history.** It was an obviously-fake mock
  app password I invented, not a real secret, and rewriting history on a repo with several merged
  PRs for a take-home isn't worth the disruption — documented transparently in the Phase 6 PR and
  here instead.
- **One stretch goal implemented: the agent-facing capability interface** (`flux serve` — see
  `flux/api/server.py`). `GET /capabilities` lists saved artifacts with their typed
  input/output schemas; `POST /capabilities/{name}/invoke` runs the same `replay()` executor
  `flux replay` does and returns the same three-way result, now as a discriminated-union JSON
  response instead of CLI text. Deliberately the cheapest stretch goal to build well: the
  Pydantic artifact schema already *is* the typed contract this asks for, so the endpoint is a
  thin HTTP wrapper, not a new subsystem — no new execution engine, no job queue, one browser
  launched and closed per call, matching the "don't build infrastructure" guidance the rest of
  the project follows. The other stretch goals (code generation, confidence scoring, assisted
  fallback, canonicalization, multi-run stability) weren't attempted — the brief says pick 1-2,
  and depth on the core over breadth across stretch goals was the better use of time.
