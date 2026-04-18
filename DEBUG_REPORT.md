# Debug Report

## Summary

- Issue: the exported demo brief in the care-ops console reported medium-risk and low-risk case counts as `0`, even when the dashboard showed non-zero values.
- Scope: consistent, frontend-only reporting bug in the generated text brief.
- Impact: demo operators could download an inaccurate summary artifact, which weakened trust in the dashboard-to-brief handoff.

## Expected vs Actual

- Expected: the downloaded demo brief should mirror the live dashboard risk distribution.
- Actual: `High-risk cases` was correct, but `Medium-risk cases` and `Low-risk cases` were always rendered as `0`.

## Root Cause

`buildDemoBrief()` in `web/upload.html` was reading two fields that do not exist in the backend payload:

- `voiceSummary.medium_risk_count`
- `voiceSummary.low_risk_count`

The backend exposes risk totals as:

- `voice_summary.high_risk_count`
- `voice_summary.risk_counts.low`
- `voice_summary.risk_counts.medium`
- `voice_summary.risk_counts.high`

Because the frontend used `?? 0` fallbacks, the mismatch did not throw an error. It silently produced incorrect counts in the exported brief.

## Hypotheses Considered

1. Frontend field-name mismatch in `buildDemoBrief()` causing silent fallback to zero.
2. Backend `dashboard_overview` returning incomplete `voice_summary` data.
3. Demo seeding flow not populating medium/low risk cases correctly.

Hypothesis 1 was confirmed.

## Fix Applied

Updated `web/upload.html` so `buildDemoBrief()` reads medium and low counts from `voiceSummary.risk_counts` instead of nonexistent top-level fields.

## Validation

- `node --check /tmp/upload-inline-check.js` passed after extracting the inline script from `web/upload.html`.
- FastAPI `TestClient` check passed:
  - `POST /api/v1/video/reset_demo`
  - `POST /api/v1/voice_note/seed_demo`
  - `GET /api/v1/video/dashboard_overview`
- Verified backend payload shape:
  - `total_notes: 3`
  - `high_risk_count: 1`
  - `risk_counts.low: 1`
  - `risk_counts.medium: 1`
  - `risk_counts.high: 1`

## Areas to Monitor

- Any other frontend export or reporting helpers that flatten nested API payloads.
- Future backend response-shape changes to `voice_summary`, `review_summary`, or sponsor summary sections.
- Demo brief and case brief generation paths, since they are human-facing artifacts and can hide schema mismatches behind default values.

## Constraints Encountered

- No browser automation runtime was available in this environment, so verification was source-level and API-level rather than screenshot-based.
- Git history could not be inspected because this workspace is not a Git repository.
