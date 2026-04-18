# PLAN

Status: Verified & Polished. Engine endpoints expanded.

Last audit: 2026-04-18
Scope: Visual and functional quality gate for the FastAPI + plain HTML/JS demo flow.

Latest builder update: 2026-04-18
- Added serverless-ready automation endpoints for batch outreach orchestration.
- Added Modal deployment entrypoint scaffolding in `modal_app.py`.
- Added in-memory automation run tracking for batch execution status and replayable results.
- Hardened case-review state transitions so review requires a submitted voice note and cannot be duplicated for the same unchanged signal.
- Aligned dashboard review outcome summaries with the latest active review per patient journey so reopened cases do not inflate aggregate reporting.
- Reset demo state now clears persisted automation runs as well as queue-visible records, so stale run IDs do not survive a reset.
- Added backend regression coverage in `tests/test_review_workflow.py` for review preconditions, duplicate-review blocking, review reopening after a newer signal, review-summary aggregation, and reset behavior.
