# PLAN

Status: Verified & Polished. Engine endpoints expanded.

Last audit: 2026-04-18
Scope: Visual and functional quality gate for the FastAPI + plain HTML/JS demo flow.

Latest builder update: 2026-04-18
- Added serverless-ready automation endpoints for batch outreach orchestration.
- Added Modal deployment entrypoint scaffolding in `modal_app.py`.
- Added in-memory automation run tracking for batch execution status and replayable results.
- Added `GET /api/v1/automation/runs` so tracked automation runs can be listed without already knowing a run ID.
- Fixed automation run accounting so `processed_recipients` reflects every attempted recipient and mixed-success batches report coherent totals.
- Added regression coverage for all-failed automation batches and made test resets clear in-memory event logs for hermetic workflow tests.
- Hardened batch outreach validation so invalid SMS recipients fail before any video-job or delivery side effects are created.
- Rejected duplicate patient journeys inside a single batch request so one care-ops action cannot create duplicate jobs or outreach for the same case.
- Cleared stale review metadata from reopened care-queue items so open cases no longer carry inactive review outcome details.
- Made Twilio retry recovery idempotent per failed message so the same original delivery cannot be retried repeatedly from one failure state.
- Made secure-link fallback preparation idempotent per failed Twilio message so repeated recovery clicks reuse the same handoff.
- Hardened case-review state transitions so review requires a submitted voice note and cannot be duplicated for the same unchanged signal.
- Aligned dashboard review outcome summaries with the latest active review per patient journey so reopened cases do not inflate aggregate reporting.
- Reset demo state now clears persisted automation runs as well as queue-visible records, so stale run IDs do not survive a reset.
- Added backend regression coverage in `tests/test_review_workflow.py` for review preconditions, duplicate-review blocking, review reopening after a newer signal, review-summary aggregation, reset behavior, and automation run listing.
