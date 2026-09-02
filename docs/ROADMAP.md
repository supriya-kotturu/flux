# Flux — Requirements & Build Roadmap

Working doc, not a deliverable. `/README.md` and `/REPORT.md` (the graded artifacts) get written in
Phase 9 once the system exists to describe.

## 1. What's actually being graded

Source: `interface.ai` take-home, "Computer-Use Automation System." Full text mirrored in
[`docs/BRIEF.md`](./BRIEF.md) for reference.

One sentence: **an LLM discovers how to do a task in a legacy bank back-office UI once, records
it as a typed reusable capability, and after that the capability replays deterministically —
no model in the loop — with real error handling and a way to pull a human in when it can't
safely proceed.**

Evaluation order (their weighting, not mine):

1. System design — artifact schema + replay contract are "central"
2. Correctness of the core loop
3. Robustness & error handling (business outcome vs. recoverable vs. hard failure)
4. Human-in-the-loop escalation (real control transfer, not a TODO)
5. Generalization story (heterogeneous surfaces, multi-tenant reuse) — **design only, not built**
6. Safety & data handling
7. Code quality
8. Communication (REPORT.md)

Explicitly **not** rewarded: feature breadth, framework name-dropping, scaling infrastructure
(queues, clusters, multi-tenant plumbing). A small, correct, well-argued system beats a big one.

**Implication for sequencing:** build breadth-first to a thin, real, end-to-end thread (Phases
0–8 below each touch every core requirement in the least amount that's still "real"), then spend
remaining time deepening the artifact schema, error taxonomy, and locator strategy — not adding
new surfaces or scaling plumbing.

## 2. Decisions made so far

| Decision | Choice | Why | Trade-off accepted |
|---|---|---|---|
| Language | Python | Pydantic gives typed/validated artifact schemas almost for free (this is literally the focal point of the eval); Playwright-python is full parity with JS; mature Anthropic SDK; FastAPI available cheaply if we do the capability-API stretch goal | Slower at runtime than Go/Node, irrelevant at this scale; less "showcase my Go" value — accepted, this submission is judged on system design, not language choice |
| LLM / perception | Claude, custom tool-calling over Playwright's **accessibility tree** (not native computer-use screenshot+coordinates) | The brief explicitly biases toward approaches that survive "no clean DOM" — accessibility tree still exists on ugly table/frame-based legacy apps. Tools like `click(locator)`/`type(locator, text)` force the model to pick a *locator*, and that locator (role + accessible name) is exactly what deterministic replay needs — discovery and replay share one locator vocabulary instead of two | Coordinate/vision-based clicking is more literal to "computer use" and is a documented fallback for when accessibility info is missing (see §4 locator strategy, rank 4) |
| Target surface | Custom-built "legacy-hostile" mock bank portal (server-rendered, table layout, no test IDs) | We own the backend, so we can *seed* deterministic error states — member not found, validation error, permission denial, session timeout, slow load — on demand. That's the only way to cleanly demonstrate the error taxonomy, which is a top-3 eval criterion. A public sandbox site gives none of that control and carries ToS/rate-limit risk | Building it is real work (~Phase 1) instead of zero-cost; accepted because it directly buys evidence for the highest-weighted robustness criterion |
| Architecture | Single process, synchronous, single repo | Brief explicitly says scaling infra (queues/clusters) is *not* rewarded and premature infra is a negative signal | None really — this is the least-regret choice for a time-boxed take-home |

## 3. System design

### 3.1 Repo layout

```
flux/
  README.md                 # deliverable: setup + demo commands
  REPORT.md                 # deliverable: ~1-3 page write-up
  docs/
    ROADMAP.md               # this file
    BRIEF.md                 # mirrored assignment text
  pyproject.toml
  flux/
    surface/                 # the perception/action seam (§3.2)
      base.py                 # abstract Surface: observe() / act()
      browser.py               # Playwright implementation
      locator.py                # locator candidate generation + fallback resolution
      accessibility.py          # ax-tree fetch + normalization (frame/table aware)
    agent/                    # discovery: LLM-driven observe->decide->act loop
      loop.py
      tools.py                  # click/type/select/navigate/wait/extract/give_up
      prompts.py
      llm_client.py
    artifact/                 # the capability contract (§3.3 — the focal point)
      schema.py                 # Pydantic: Artifact, Step, Locator, Param, Output, Checkpoint, Outcome
      store.py                   # save/load JSON, versioning
      recorder.py                 # discovery transcript -> Artifact (decoupled, see below)
    replay/                   # deterministic execution (§3.4)
      executor.py
      errors.py                  # BusinessOutcome / RecoverableCondition / HardFailure
      checkpoint.py
    safety/                   # §3.5
      allowlist.py
      risk.py                    # reversible vs irreversible classification
      redaction.py
    escalation/                # §3.6
      detector.py
      handoff.py                  # control-plane: who's driving (agent | human)
      operator.py                  # minimal operator surface (CLI + one HTML page)
    observability/
      logger.py                    # structured JSONL, redaction-filtered
      evidence.py                    # screenshot + ax-tree snapshot on failure
    cli.py                     # `flux discover ...` / `flux replay ...`
  mock_bank/                 # the target surface (Phase 1)
    app.py                    # Flask, server-rendered, tables, seeded error injection
    templates/
  artifacts/                 # saved capability artifacts (JSON, git-tracked, reviewable via diff)
  evidence/                  # discovery + replay run logs, screenshots (deliverable #3)
  tests/
    unit/
    integration/
```

### 3.2 The surface abstraction (the seam §3.7 asks about)

```python
class Observation(BaseModel):
    url: str
    title: str
    ax_tree: AxNode              # normalized accessibility tree (frames flattened, tables walked)
    screenshot_ref: str | None   # evidence only, never the primary decision input

class Action(BaseModel):
    kind: Literal["click", "type", "select", "navigate", "wait_for", "extract"]
    locator: Locator | None
    value: str | None

class Surface(Protocol):
    def observe(self) -> Observation: ...
    def act(self, action: Action) -> ActionResult: ...
```

`BrowserSurface` (Playwright) is the only implementation we build. Nothing above this line —
agent loop, artifact schema, replay executor, safety layer — knows it's a browser. That's the
answer to 3.7's "surface abstraction" question:

- **Legacy web** (framesets, nested tables): same `BrowserSurface`, a different `ax_tree`
  normalization pass (flatten frames, treat table cells as addressable regions). No new
  abstraction needed.
- **Desktop**: a new `DesktopSurface` backed by OS accessibility APIs (UIA on Windows, AX API on
  macOS) implementing the same `observe()`/`act()` contract. Replay executor, artifact schema,
  and safety layer are unchanged. **Not built** — this is the design answer, per the brief's
  explicit "design, not necessarily build" for 3.7.

### 3.3 Artifact schema — the focal point

An artifact is a **capability contract**, not a step recording. Shape:

```python
class Artifact(BaseModel):
    id: str
    version: int
    name: str                      # e.g. "lookup_member_savings_balance"
    description: str
    app_target: AppTarget          # base_url, vendor_product tag, tenant_id (nullable = "base template")
    input_schema: dict[str, ParamSpec]     # typed, e.g. {"member_id": {"type": "string", "required": True}}
    output_schema: dict[str, ParamSpec]    # typed, e.g. {"balance": {"type": "decimal"}}
    steps: list[Step]
    known_outcomes: list[NamedOutcome]     # e.g. "member_not_found" -> detection condition
    checkpoint: Checkpoint                  # final success assertion
    provenance: Provenance                  # discovery_run_id — NOT the raw transcript (decoupled per 3.2)
    created_at: datetime

class Step(BaseModel):
    index: int
    action: Action
    locator_candidates: list[LocatorCandidate]   # ranked, with strategy + confidence, not just the winner
    description: str                              # human-readable "why this step"
    risk_level: Literal["safe", "irreversible"]
    step_checkpoint: Checkpoint | None            # optional per-step assertion

class LocatorCandidate(BaseModel):
    strategy: Literal["role_name", "text", "structural_path", "coordinates"]
    value: str
    confidence: float
```

Why ranked locator candidates instead of one selector per step: replay tries them in
confidence order; if rank 1 fails but rank 2 resolves, that's a **drift signal** worth logging,
not silently swallowing — this is also the mechanism that lets one artifact survive
per-tenant layout variance (§5).

`known_outcomes` living on the artifact (not inferred at replay time) is what makes "no such
member" a *contract* return value instead of something the replay engine has to guess about
from page text.

Storage: plain versioned JSON under `artifacts/`, git-tracked — reviewable via `git diff` by a
human, and directly loadable by a calling agent. No database; premature infra per §1.

### 3.4 Deterministic replay & error taxonomy

`ReplayResult` is a tagged union, not an exception hierarchy for the good paths:

```python
ReplayResult = Success(outputs=...) | BusinessOutcome(name=..., data=...) | Failure(step, expected, observed, evidence_ref)
```

- **BusinessOutcome** — matched one of the artifact's `known_outcomes` (e.g. "not found" page
  text). Returned to the caller as a legitimate result.
- **RecoverableCondition** — a small, fixed, artifact-declared set of tactics (dismiss a known
  interstitial pattern, bounded retry/backoff on a transient load). Attempted automatically,
  logged either way.
- **HardFailure** — locator unresolvable after exhausting the candidate list, checkpoint never
  satisfied, or retries exhausted. Stops and returns step index + expected vs. observed +
  evidence pointer. Never proceeds blindly.

### 3.5 Safety

- **Allowlist** (`safety/allowlist.py`, config file): permitted base domains/routes and
  permitted `Action.kind` values. Enforced inside `Surface.act()` itself — not just at the
  agent-loop layer — so replay is covered too, not only discovery.
- **Risk classification**: each `Step` is tagged `safe` (read/navigate/search — freely
  replayable unattended) or `irreversible` (submits that create/modify records — e.g. "confirm
  new sub-account"). An artifact containing any irreversible step defaults to
  `requires_approval: true` and unattended replay refuses to run it until an operator flips
  that flag — a minimal, real version of draft→approved gating, not the full stretch-goal
  confidence scoring.
- **Redaction**: the structured logger runs every log line and artifact write through a
  redaction filter (known PII/secret patterns — account numbers, SSN-shaped strings, anything
  from a field flagged `sensitive` in the mock bank's own form metadata) before it touches
  disk. Screenshots taken as evidence get sensitive regions masked where the mock app marks
  them.

### 3.6 Escalation & handoff

Detector triggers on: N consecutive failed attempts on the same step, the model itself emitting
a "cannot proceed" tool call, an irreversible step encountered during discovery without
pre-approval, or replay hitting `HardFailure`.

Control-transfer mechanism (real, not mocked):

1. Playwright launches Chromium headed, with a remote-debugging port open.
2. On escalation, the agent stops issuing actions and writes an **intervention request**
   (capability/goal, current step, screenshot, ax-tree snippet, reason) to a tiny control-plane
   record: `{session_id, controller: "agent" | "human", status}`.
3. A minimal operator surface (one HTML page + CLI) lists pending requests and a "take control"
   action, which is literally opening the live CDP debug URL for that same browser context —
   **the same session**, not a fresh one.
4. The human acts; Playwright's own page event listeners keep logging (they fire regardless of
   who's driving), so the evidence trail is continuous across the handoff.
5. Human hits "resume" → control-plane flips back to `agent` → agent re-observes current state
   (never trusts its pre-handoff plan) and continues or finishes.

Mock/thin: the operator UI itself (per the brief's explicit allowance). Real: the session
continuity, the control-plane state, and the resume-and-reobserve logic.

### 3.7 Multi-tenant reuse (design only, §3.7 of brief)

Artifact separates a **vendor-product template** (steps + locator strategy) from a **tenant
binding** (`app_target.tenant_id`, base URL, tenant-specific label/copy overrides). A tenant
override layer patches specific locator candidates or text patterns without re-recording the
flow. The locator-confidence drift signal from §3.3 is the detection mechanism: if a tenant
consistently resolves steps at locator rank 2+ instead of rank 1, that's flagged for review —
either promote the override to a proper tenant patch, or re-record if drift is too large.

## 4. Build phases

Each phase is a thin vertical slice — after Phase 8 every core requirement in brief §3 has a
real (if minimal) implementation. Depth gets added in the second pass, not new phases.

| Phase | Deliverable | Tests |
|---|---|---|
| 0 | Repo scaffolding: pyproject, package skeleton, CLI stub, structured logger stub | smoke: `flux --help` runs |
| 1 | `mock_bank/`: Flask, server-rendered tables, login, member search → detail → balance, open-sub-account multi-step form + confirmation, seeded members incl. special IDs that trigger not-found / validation-error / permission-denied / session-timeout / slow-load | route tests |
| 2 | `Surface` protocol + `BrowserSurface` (Playwright): ax-tree observation, action primitives, locator candidate generation + fallback resolution | unit: locator resolution against mock_bank fixtures |
| 3 | Discovery agent loop: Claude tool-calling over `Surface`, goal+target input, stopping conditions, raw transcript logged separately from any artifact | integration: completes a real goal against mock_bank |
| 4 | Artifact schema (Pydantic) + recorder (transcript → versioned Artifact) + file store | schema validation tests; recorder unit test on a canned transcript |
| 5 | Replay executor: no LLM, full error taxonomy, checkpoint verification, `ReplayResult` | replay happy path; replay hitting seeded not-found; replay hitting seeded validation error; replay hitting an induced hard failure — this set doubles as the `/evidence` error-state demo |
| 6 | Safety layer: allowlist enforced in `Surface.act()`, risk classification, approval-gate for irreversible artifacts, redaction wired into logger + artifact serialization | allowlist blocks disallowed domain/action; redaction test strips seeded fake PII from log output |
| 7 | Observability: JSONL run logs, screenshot + ax-tree capture on failure, `evidence/` writer | evidence files created on induced failure |
| 8 | Escalation & handoff: stuck detector, intervention request, CDP-based control-plane, operator CLI/page, resume-and-reobserve | automated: stuck→request→mock-resume→run continues; manual smoke test for the actual CDP handoff (documented as a manual verification step, not automatable cheaply) |
| 9 | `README.md` + `REPORT.md` + curate `/evidence/` (one discovery run, one clean replay, one error-state replay, optional recording) | — |
| 10 (only if time remains) | One stretch goal — leaning toward **agent-facing capability interface** (small FastAPI surface listing artifacts as callable tools + one invocation demo), since it reinforces the "capability an agent can call" framing the whole brief is built on, and it's cheap on top of Phase 4's Pydantic schemas (they already convert to JSON-schema). Revisit once the core is actually done — no sense picking now. | — |

## 5. Open questions for you

- **Q1:** Anthropic API key available for this session/repo, or should discovery be built
  against a mockable LLM boundary first and wired to a real key later? (Brief explicitly allows
  mocking this boundary if needed.)
- **Q2:** Any preference on how deep the mock bank's "legacy hostility" should go — e.g. is an
  actual `<frameset>` worth the extra Playwright frame-handling complexity, or is table-based
  layout + no test-IDs + inconsistent markup enough to make the point?
- **Q3:** Comfortable with me starting at Phase 0/1 now (scaffolding + mock bank), or do you want
  to review/adjust the artifact schema (§3.3) in more detail first, since that's the piece the
  brief calls out as most central to the evaluation?
