from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class VideoJob:
    job_id: str
    customer_id: str
    name: str
    campaign_type: str
    script: str
    video_url: str
    thumbnail_url: str
    avatar_image_url: str | None = None
    background_image_url: str | None = None
    plan: str | None = None
    days_to_expiry: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class VoiceNoteRecord:
    voice_note_id: str
    customer_id: str
    campaign_type: str
    transcript: str
    risk_bucket: str
    created_at: datetime


@dataclass
class OutreachDeliveryRecord:
    delivery_id: str
    provider_message_id: str
    customer_id: str
    name: str
    campaign_type: str
    channel: str
    destination: str
    message_body: str
    provider: str
    status: str
    page_url: str
    created_at: datetime
    account_sid: str | None = None
    from_number: str | None = None
    messaging_service_sid: str | None = None
    status_callback_url: str | None = None
    message_uri: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    updated_at: datetime | None = None


@dataclass
class FallbackHandoffRecord:
    fallback_id: str
    customer_id: str
    name: str
    campaign_type: str
    message_sid: str | None
    delivery_status: str | None
    page_url: str
    absolute_page_url: str
    source: str
    created_at: datetime


@dataclass
class CaseReviewRecord:
    review_id: str
    customer_id: str
    campaign_type: str
    reviewed_at: datetime
    reviewed_by: str
    outcome: str = "routine_followup"
    note: str | None = None
    source: str | None = None


@dataclass
class AutomationRunRecord:
    run_id: str
    execution_mode: str
    source: str
    send_sms: bool
    status: str
    total_recipients: int
    processed_recipients: int
    created_jobs: int
    created_deliveries: int
    error_count: int
    results: list[dict[str, Any]]
    started_at: datetime
    completed_at: datetime | None = None


video_jobs: dict[str, VideoJob] = {}
voice_notes: dict[str, VoiceNoteRecord] = {}
outreach_deliveries: dict[str, OutreachDeliveryRecord] = {}
fallback_handoffs: dict[str, FallbackHandoffRecord] = {}
case_reviews: dict[str, CaseReviewRecord] = {}
automation_runs: dict[str, AutomationRunRecord] = {}

db: dict[str, dict[str, Any]] = {
    "video_jobs": video_jobs,
    "voice_notes": voice_notes,
    "outreach_deliveries": outreach_deliveries,
    "fallback_handoffs": fallback_handoffs,
    "case_reviews": case_reviews,
    "automation_runs": automation_runs,
}


async def get_db() -> dict[str, dict[str, Any]]:
    return db
