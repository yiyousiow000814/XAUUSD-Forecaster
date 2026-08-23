# Documentation Guide

Directory placement is the primary signal of a document's authority. Choose a
type from its purpose, not from how important the subject feels.

## Taxonomy

- **Contract**: non-negotiable correctness, safety, causality, evidence,
  isolation, compatibility, or externally relied-upon boundaries.
- **Specification**: required product, lifecycle, state, output, or presentation
  behavior whose implementation may change.
- **Protocol**: ordered research, evaluation, evidence, or promotion procedure.
- **Design**: current architecture, data flow, tradeoffs, and rationale.
- **Plan**: proposed future work; it is temporary and not a current authority.
- **Runbook**: commands and operational recovery or deployment steps.
- **Audit**: point-in-time inspection findings.
- **Report**: historical results of completed work, repairs, or migrations.
- **Reference**: factual catalogs, inputs, providers, fields, and configuration.
- **ADR**: a durable architecture decision whose alternatives and consequences
  need preservation. Add `decisions/` only when a real ADR exists.

Contracts, specifications, and applicable protocols are normative. Designs and
references explain the current system but do not override normative documents.
Plans propose work. Audits and reports preserve historical evidence and are not
current rules merely because they discovered one.

## Authoritative documents

### Contracts

- [System boundaries](contracts/SYSTEM_BOUNDARIES.md)
- [Forward-only evidence](contracts/FORWARD_ONLY.md)
- [Replay](contracts/REPLAY.md)
- [News evidence](contracts/NEWS_EVIDENCE.md)
- [Evidence lanes](contracts/EVIDENCE_LANES.md)
- [Hosting boundaries](contracts/HOSTING_BOUNDARIES.md)
- [Release control](contracts/RELEASE_CONTROL.md)
- [Preview isolation](contracts/PREVIEW_ISOLATION.md)
- [Assistant state](contracts/ASSISTANT_STATE.md)
- [Assistant orchestration](contracts/ASSISTANT_ORCHESTRATION.md)
- [Assistant security boundaries](contracts/ASSISTANT_SECURITY.md)
- [Daily Brief](contracts/DAILY_BRIEF.md)
- [Operational health](contracts/OPERATIONAL_HEALTH.md)

### Specifications

- [Product](specs/PRODUCT.md)
- [Factor coverage](specs/FACTOR_COVERAGE.md)
- [Learning curves](specs/LEARNING_CURVES.md)
- [Preview behavior](specs/PREVIEW_BEHAVIOR.md)
- [Dashboard presentation](specs/DASHBOARD_PRESENTATION.md)
- [Storylines](specs/STORYLINES.md)
- [Temporal event graph](specs/TEMPORAL_EVENT_GRAPH.md)
- [Assistant behavior](specs/ASSISTANT_BEHAVIOR.md)

### Protocols

- [Execution-model research](protocols/EXECUTION_MODEL_RESEARCH.md)
- [Storyline promotion](protocols/STORYLINE_PROMOTION.md)
- [News candidate retrieval evaluation](protocols/NEWS_CANDIDATE_RETRIEVAL_EVALUATION.md)

### Designs

- [News identity retrieval](design/NEWS_IDENTITY_RETRIEVAL.md)

- [Dynamic AI scheduler](design/AI_PRIORITY_SCHEDULER.md)
- [Cloudflare hosting](design/CLOUDFLARE_HOSTING.md)
- [Paged dashboard history](design/PAGED_DASHBOARD_HISTORY.md)
- [Assistant architecture](design/ASSISTANT_ARCHITECTURE.md)
- [Assistant implementation status](design/ASSISTANT_IMPLEMENTATION_STATUS.md)

### Architecture decisions

- [ADR-001: Forecaster-owned provider-independent Assistant state](decisions/ADR-001-provider-independent-assistant-state.md)
- [ADR-002: Persistent history and incremental active context](decisions/ADR-002-persistent-history-and-incremental-context.md)
- [ADR-003: Shared retrieval and bounded tool loop](decisions/ADR-003-shared-retrieval-and-bounded-tool-loop.md)
- [ADR-004: Private Assistant authentication](decisions/ADR-004-private-assistant-authentication.md)
- [ADR-005: Versioned events and structured content](decisions/ADR-005-versioned-events-and-structured-content.md)

### Current plans

- [Assistant implementation roadmap](plans/ASSISTANT_ROADMAP.md)

### Operations and reference

- [Cloudflare deployment runbook](runbooks/CLOUDFLARE_DEPLOYMENT.md)
- [Control Plane installation runbook](runbooks/CONTROL_PLANE_INSTALLATION.md)
- [Input and provider reference](reference/INPUTS.md)
- [AI provider quota reference](AI_PROVIDER_QUOTAS.md)

### Historical evidence

- Audits: [test suite](audits/TEST_SUITE.md),
  [release-control ownership 2026-08-20](audits/RELEASE_CONTROL_2026_08_20.md),
  [U5 authority](audits/U5_AUTHORITY.md),
  [documentation inventory](audits/DOCUMENTATION_INVENTORY.md),
  [repository reliability 2026-08-17](audits/REPOSITORY_RELIABILITY_2026_08_17.md),
  [legacy news irrelevance recovery](audits/NEWS_IRRELEVANCE_RECOVERY_2026_08_16.md),
  and [news candidate retrieval baseline](audits/NEWS_CANDIDATE_RETRIEVAL_2026_08_17.md)
- Reports: [Phase 2F repair](reports/PHASE2F_REPAIR.md),
  [initial free-data feasibility](reports/FREE_DATA_FEASIBILITY.md),
  [canonical event handover](reports/CANONICAL_EVENT_HANDOVER.md),
  [Core/Broad handover](reports/CORE_BROAD_NEWS_HANDOVER.md), and
  [news metrics migration](reports/NEWS_METRICS_MIGRATION.md)

## Adding documentation

Start from the question the document answers: a hard boundary belongs in
`contracts/`, behavior in `specs/`, an investigation sequence in `protocols/`,
architecture in `design/`, operator steps in `runbooks/`, factual catalogs in
`reference/`, future work in `plans/`, inspection findings in `audits/`, and
completed outcomes in `reports/`. Split materially different roles rather than
combining them under an umbrella title.

When a durable rule already has an authoritative home, link to it instead of
restating it differently. Tests and implementation demonstrate or enforce a
rule; they do not become the source of truth merely because documentation is
missing. A test, code comment, pull request, audit, report, or plan must not be
the only place a current durable rule is defined.
