# LAO vNext Strict Orchestration Plan (60 Days)

## Objective
Evolve LAO from a stateful pipeline into a strict contract-driven orchestration platform with deterministic task completion, strict phase scoping, strict schema enforcement, reliable validation observability, and strict closure criteria.

## Baseline
- Reliability for long plans: 7/10
- State/resume robustness: 8/10
- Cross-stack quality assurance: 5/10
- Traceability/adherence rigor: 6/10
- Determinism/auditability: 6.5/10
- Platform-agnostic architecture quality: 8/10
- Overall: 6.8/10

## Confirmed Gaps
1. Phase filtering parity is incomplete (reviewer path not fully phase-scoped).
2. Validation runs are mixed with findings instead of being a clear execution ledger.
3. Deliverable lifecycle and closure rules are under-modeled for strict adherence.
4. Consistency and schema checks are still heuristic-heavy.
5. Architect output schema acceptance is too lenient.

## Contract-Driven Target Model
1. Plan Contract: deliverables and acceptance checks are explicit and typed.
2. Execution Contract: phase scoping and dependencies are enforced at queue boundaries.
3. Validation Contract: each validation execution is recorded as a first-class run.
4. Closure Contract: plan completion requires satisfied deliverables or explicit deferred/blocked statuses with reasons.

## 60-Day Priorities

### P0 (Immediate)
- Phase filter parity in coder/reviewer selection paths.
- Validation execution runs as first-class start/end lifecycle records.
- Strict architect JSON schema validation before task insertion.
- Strict adherence closure mode (`strict_adherence=true`) for deliverable-gated completion.

### P1 (Next)
- Pluggable analyzer interface with confidence scoring (heuristic/AST/compiler-backed).
- Deterministic repair prompt builder from structured findings.
- Contract-level retry policy with cap, cooldown, and escalation reasons.

### P2 (Then)
- `quality_report.json` schema versioning and migration policy.
- Benchmark suite for large/malformed plans and symbol leakage scenarios.
- Operator dashboards for blocked deliverables, retry loops, and failure classes.

## Weekly KPIs
- Plan Success Rate (strict closure without manual intervention).
- First-Pass Validation Success.
- Rework Convergence (median retries per completed task).
- Contract Fail Escape Rate.
- Traceability Coverage.
- Cross-File Defect Leakage.
- Token Efficiency per validated deliverable.

## Phase A Implementation Scope (Start)
1. Reviewer phase-filter parity.
2. Strict architect task schema validator with hard rejection.
3. Strict adherence closure gate in plan completion.
4. Validation execution ledger upgrades (start/end and command-level records).

