# Tablekeeper Factory

## How to stand up the factory

Start Band Desktop with the five distinct seat identities from `mandates/`: Architect, Builder, Red Team QA, Verifier, and Commander. Each mandate specifies `Harness: Codex` and `Model: gpt-6`. Supply the same kickoff repository, track specification, result workspace and acceptance constraints to the Architect, and make the workspace/Docker runner accessible to the assigned agents. The Architect delegates a bounded stage task to Builder; Builder writes the stage implementation and tests, records an exact SHA-256 manifest and hands off to Red Team QA. Red Team QA independently reproduces failures and runs the stage and inherited checks. Only a QA PASS permits the Architect to hand the **same frozen manifest** to Verifier, who independently rebuilds and validates. Keep each agent's evidence and failed reports; do not self-approve or rewrite the room history.

For reusable mandates on another problem, substitute its specification and workspace. Do not put problem-specific URLs, selectors, fields or expected codes into any seat mandate.

## Responsibilities and collaboration

Architect owns scope, acceptance routing, isolated candidate workspaces and frozen manifests. Builder alone implements each candidate; Red Team QA independently attempts regressions and adversarial cases. Verifier checks immutable artifacts independently after QA approval; Commander provides orchestration without replacing the specialist owner. Five seats were configured. Exact reciprocal exchanges and human interventions must be verified from the genuine original room export, not inferred from this document.

## Product architecture and tradeoffs

- Python 3.12 single-process threaded HTTP service with re-entrant state locking, keeping occupancy, receipts, migration, series and replan writes atomic.
- In-memory state is ephemeral by design and requires explicit fixture/reset or import on fresh startup. The tradeoff is simple deterministic testing with no database/network dependency, not persistence or production onboarding.
- Portable JSON export/import validates identities and relationships before atomic replacement.
- Bundled IANA timezone data handles local DST gaps/folds and UTC-duration arithmetic without external network requirements.
- Bounded concurrent `scrypt` handles credentials; request idempotency receipts prevent duplicate writes during retries.
- Static same-origin browser assets require no frontend service or runtime package fetch. The polished UI supports reservations listing, confirmation, cancellation, mobile and desktop.

## Real failure detection and recovery

The Builder → independent QA → independent Verifier sequence caught and resolved fixture-precedence and key collision errors, hidden empty availability grids, stale booking context, cancellation dialog issues, import inconsistencies, recurrence overflow, history/replan edge cases, forged input, and a cross-tab stale-session problem. A separate final release audit discovered the packaged UI was still reference-lookup-first: missing automatic My Reservations. The room-directed repair integrated the independently accepted polished HTML/CSS/JS as a compatible group plus a bounded navigation fallback. QA and Verifier tested the **actual package image**, not just an unbundled demo.

Rejected candidates and exact reproduction commands remain separate, and frozen accepted stage outputs are not silently overwritten. The evidence directory on the development machine includes command logs, hashes and browser screenshots; the submitted room log and Git history should demonstrate the corresponding handoffs. Do not present later user-provided debugging instructions as fully autonomous stage execution.

## Measured evidence: limits and costs

Observed final packaged result (October 4, 2026): all four shipped official stage suites passed (Stage 1 120/120, Stage 2 25/25, Stage 3 7/7, Stage 4 6/6), with four inherited unit suites 8/8 each; polished browser runner 20/20, session lifecycle 6/6. The independent read-only audit compared all 2,630 submission paths with zero drift, all 660 runtime image files against package content, and clean Docker builds for all four stages.

Measured image readiness during polished-package Builder verification: **928 ms**; accepted isolated image runs within **2 CPUs / 2 GiB** and passes health and offline acceptance. These are engineering measurements, not total development time.

The Band room UI displayed **approximately $174** as its accumulated cost indicator on October 4 (screenshot observation). It is **not confirmed to be the real amount billed** and must not be represented as cash expense. Provider token-level usage, real API invoice and aggregate agent-active minutes are not available in the inspected evidence; record these as **unverified/unavailable**, not zero. The recorded work and final approvals occurred over multiple days ending October 4, but calendar span should not be mislabeled as measured active build time. Detailed per-run command times and latency evidence were retained under `D:\HACKATHON\band-work\checks\` and in the BAND room; those external working files are not automatically part of this submission.

## Reproducibility and release evidence

Each `stage-N/RUN.md` provides an independent Docker build/start command. `README.md` documents the intentionally empty fresh state and how to seed **only a disposable demo instance** with the shipped Stage 2 browser fixture. A genuinely unmodified whole-session `room.json` is required at the repository root for the seat/mandate, reciprocal-message and provenance gates. The original BAND room and Git commits—not this document alone—are the authoritative teamwork evidence. The full isolated harness run, public Git clone test, privacy scan and media submission remain separate release gates.

The factory model and its mandates are reusable; an empty state and a manual fixture import are acknowledged tradeoffs, not hidden runtime dependencies.
