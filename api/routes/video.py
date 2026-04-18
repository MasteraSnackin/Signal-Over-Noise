import os
from dataclasses import dataclass
from datetime import datetime, timezone
from heapq import nlargest, nsmallest
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse
from jinja2 import Template
from pydantic import BaseModel, ConfigDict, Field

from api.errors import ExternalServiceError, NotFoundError, TemplateRenderError, ValidationError
from api.services.seedance import generate_video
from api.services.tinyfish import trigger_video_job
from api.services.tuner import EVENT_LOGS, log_event
from api.services.twilio import TwilioStatusCallbackPayload, build_status_callback_payload, send_sms
from api.routes.voice_note import VoiceNoteSubmitResponse, VoiceNoteSummaryResponse
from api.types import CampaignType, ReviewOutcome, TwilioDeliveryStatus
from db import (
    CaseReviewRecord,
    FallbackHandoffRecord,
    OutreachDeliveryRecord,
    VideoJob,
    VoiceNoteRecord,
    get_db,
)

router = APIRouter(prefix="/video", tags=["video"])
public_router = APIRouter(tags=["video-page"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INDEX_TEMPLATE = PROJECT_ROOT / "web" / "index.html"

SCRIPT_TEMPLATES: dict[CampaignType, str] = {
    "elderly_checkin": (
        "Hi {name}, this is your elderly care check-in from Signal Over Noise. "
        "We wanted to see how you're feeling today and invite you to leave a short voice note."
    ),
    "primary_care": (
        "Hi {name}, this is your primary care clinic reaching out with a quick check-in. "
        "Please watch this message and share how you're feeling in a short voice note."
    ),
    "mental_health": (
        "Hi {name}, this is your mental health support check-in. "
        "When you're ready, let us know how things have been feeling for you today."
    ),
}


class CreateVideoJobRequest(BaseModel):
    customer_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    plan: Optional[str] = None
    days_to_expiry: Optional[int] = None
    campaign_type: CampaignType
    avatar_image_url: Optional[str] = None
    background_image_url: Optional[str] = None


class CreateVideoJobResponse(BaseModel):
    job_id: str
    customer_id: str
    video_url: str
    thumbnail_url: str
    campaign_type: CampaignType


class ScriptPreviewResponse(BaseModel):
    campaign_type: CampaignType
    name: str
    plan: Optional[str] = None
    days_to_expiry: Optional[int] = None
    script: str


class DemoVideoJobResponse(BaseModel):
    customer_id: str
    name: str
    campaign_type: CampaignType
    page_url: str
    video_url: str
    thumbnail_url: str


class VideoHistoryItemResponse(BaseModel):
    job_id: str
    customer_id: str
    name: str
    campaign_type: CampaignType
    created_at: datetime
    plan: str | None = None
    days_to_expiry: int | None = None
    visual_mode: str
    video_url: str
    thumbnail_url: str
    script: str


class VideoSummaryResponse(BaseModel):
    total_jobs: int
    campaign_counts: dict[str, int]


class ReviewSummaryResponse(BaseModel):
    total_reviews: int
    outcome_counts: dict[str, int]
    latest_reviewed_at: datetime | None


class TunerEventResponse(BaseModel):
    event_type: str
    customer_id: str
    campaign_type: str
    risk_bucket: str | None = None
    timestamp: str
    source: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ObservabilityResponse(BaseModel):
    total_events: int
    event_counts: dict[str, int]
    recent_events: list[TunerEventResponse]


class DemoResetResponse(BaseModel):
    message: str
    video_jobs_seeded: int
    voice_notes_cleared: int
    outreach_deliveries_cleared: int
    fallback_handoffs_cleared: int
    case_reviews_cleared: int
    automation_runs_cleared: int
    events_cleared: int


class DashboardOverviewResponse(BaseModel):
    demo_jobs: list[DemoVideoJobResponse]
    video_summary: VideoSummaryResponse
    voice_summary: VoiceNoteSummaryResponse
    review_summary: ReviewSummaryResponse
    care_queue: list["CareQueueItemResponse"]
    recent_voice_notes: list[VoiceNoteSubmitResponse]
    observability: ObservabilityResponse
    sponsor_summary: "SponsorSummaryResponse"
    recent_outreach_deliveries: list["OutreachDeliveryResponse"]
    recent_fallback_handoffs: list["FallbackHandoffResponse"]


class CareQueueItemResponse(BaseModel):
    customer_id: str
    name: str
    campaign_type: CampaignType
    page_url: str
    status: str
    risk_bucket: str | None = None
    latest_transcript: str | None = None
    latest_created_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    review_outcome: ReviewOutcome | None = None
    review_note: str | None = None
    review_active: bool = False


class MockTriggerRequest(BaseModel):
    customer_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    campaign_type: CampaignType


class OutreachDeliveryResponse(BaseModel):
    delivery_id: str
    provider_message_id: str
    customer_id: str
    name: str
    campaign_type: CampaignType
    channel: str
    destination: str
    provider: str
    status: str
    page_url: str
    created_at: datetime
    message_body: str
    account_sid: str | None = None
    from_number: str | None = None
    messaging_service_sid: str | None = None
    status_callback_url: str | None = None
    message_uri: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    updated_at: datetime | None = None


class OutreachDeliveryHistoryResponse(BaseModel):
    customer_id: str
    campaign_type: CampaignType
    deliveries: list[OutreachDeliveryResponse]


class FallbackHandoffResponse(BaseModel):
    fallback_id: str
    customer_id: str
    name: str
    campaign_type: CampaignType
    message_sid: str | None = None
    delivery_status: str | None = None
    page_url: str
    absolute_page_url: str
    source: str
    created_at: datetime


class FallbackHandoffHistoryResponse(BaseModel):
    customer_id: str
    campaign_type: CampaignType
    handoffs: list[FallbackHandoffResponse]


class TwilioMessageResourceResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    account_sid: str | None = None
    api_version: str = "2010-04-01"
    body: str
    date_created: str | None = None
    date_sent: str | None = None
    date_updated: str | None = None
    direction: str = "outbound-api"
    error_code: str | None = None
    error_message: str | None = None
    from_number: str | None = Field(default=None, alias="from")
    messaging_service_sid: str | None = None
    num_media: str = "0"
    num_segments: str = "1"
    price: str | None = None
    price_unit: str | None = None
    sid: str
    status: str
    subresource_uris: dict[str, str]
    to: str
    uri: str


class TwilioMessageListResponse(BaseModel):
    messages: list[TwilioMessageResourceResponse]
    page: int = 0
    page_size: int
    uri: str


class TwilioStatusSimulationRequest(BaseModel):
    message_sid: str
    status: TwilioDeliveryStatus
    error_code: str | None = None


class TwilioStatusSimulationResponse(BaseModel):
    delivery: OutreachDeliveryResponse
    simulated_status: TwilioDeliveryStatus


class CaseReviewResponse(BaseModel):
    review_id: str
    customer_id: str
    campaign_type: CampaignType
    reviewed_at: datetime
    reviewed_by: str
    outcome: ReviewOutcome
    note: str | None = None
    source: str | None = None
    active: bool = True


class ReviewStatusResponse(BaseModel):
    customer_id: str
    campaign_type: CampaignType
    status: str
    review: CaseReviewResponse | None = None


class SendOutreachRequest(BaseModel):
    customer_id: str = Field(min_length=1)
    campaign_type: CampaignType
    phone_number: str = Field(min_length=3)
    custom_message: str | None = None


class SendOutreachResponse(BaseModel):
    delivery: OutreachDeliveryResponse


class RetryOutreachRequest(BaseModel):
    message_sid: str = Field(min_length=3)


class RetryOutreachResponse(BaseModel):
    original_delivery: OutreachDeliveryResponse
    retried_delivery: OutreachDeliveryResponse


class PrepareFallbackLinkRequest(BaseModel):
    customer_id: str = Field(min_length=1)
    campaign_type: CampaignType
    message_sid: str | None = None
    source: str = "twilio_failover_control"


class PrepareFallbackLinkResponse(BaseModel):
    customer_id: str
    campaign_type: CampaignType
    name: str
    message_sid: str | None = None
    delivery_status: str | None = None
    page_url: str
    absolute_page_url: str


class MarkReviewedRequest(BaseModel):
    customer_id: str = Field(min_length=1)
    campaign_type: CampaignType
    reviewed_by: str = Field(default="care_ops_demo", min_length=1)
    outcome: ReviewOutcome = "routine_followup"
    note: str | None = None
    source: str = "care_ops_console"


class RenderSponsorResponse(BaseModel):
    deployment_target: str
    ready: bool
    healthcheck_path: str
    render_yaml_present: bool
    dockerfile_present: bool
    notes: list[str]


class TunerSponsorResponse(BaseModel):
    total_events: int
    event_counts: dict[str, int]
    source_counts: dict[str, int]
    twilio_delivery_events: int
    twilio_retry_events: int
    twilio_failover_events: int
    review_outcome_counts: dict[str, int]


class TwilioSponsorResponse(BaseModel):
    total_deliveries: int
    latest_status: str
    latest_destination: str | None = None
    campaign_counts: dict[str, int]
    total_fallback_handoffs: int
    latest_fallback_at: datetime | None = None


class SponsorSummaryResponse(BaseModel):
    render: RenderSponsorResponse
    tuner: TunerSponsorResponse
    twilio: TwilioSponsorResponse


DashboardOverviewResponse.model_rebuild()


@dataclass
class _DashboardDerivedState:
    video_summary: VideoSummaryResponse
    voice_summary: VoiceNoteSummaryResponse
    review_summary: ReviewSummaryResponse
    care_queue: list[CareQueueItemResponse]
    recent_voice_notes: list[VoiceNoteSubmitResponse]
    sponsor_summary: SponsorSummaryResponse
    recent_outreach_deliveries: list[OutreachDeliveryResponse]
    recent_fallback_handoffs: list[FallbackHandoffResponse]


DEMO_VIDEO_SCENARIOS: list[dict[str, Any]] = [
    {
        "customer_id": "demo_elder_001",
        "name": "Margaret Hill",
        "plan": "care-home-weekly",
        "days_to_expiry": 3,
        "campaign_type": "elderly_checkin",
        "avatar_image_url": None,
        "background_image_url": None,
    },
    {
        "customer_id": "demo_gp_001",
        "name": "Daniel Price",
        "plan": "blood-pressure-review",
        "days_to_expiry": 5,
        "campaign_type": "primary_care",
        "avatar_image_url": None,
        "background_image_url": None,
    },
    {
        "customer_id": "demo_mh_001",
        "name": "Sofia Khan",
        "plan": "weekly-wellbeing",
        "days_to_expiry": 1,
        "campaign_type": "mental_health",
        "avatar_image_url": None,
        "background_image_url": None,
    },
]


def _compose_script(
    *,
    name: str,
    campaign_type: CampaignType,
    plan: Optional[str] = None,
    days_to_expiry: Optional[int] = None,
) -> str:
    script = SCRIPT_TEMPLATES[campaign_type].format(name=name)

    if plan:
        script += f" Your current plan is {plan}."

    if days_to_expiry is not None:
        script += f" Your next important date is in {days_to_expiry} days."

    return script


def _build_script(payload: CreateVideoJobRequest) -> str:
    return _compose_script(
        name=payload.name,
        campaign_type=payload.campaign_type,
        plan=payload.plan,
        days_to_expiry=payload.days_to_expiry,
    )


def _find_video_job(
    store: dict[str, dict[str, object]],
    customer_id: str,
    campaign_type: CampaignType,
) -> VideoJob | None:
    for job in reversed(list(store["video_jobs"].values())):
        if (
            isinstance(job, VideoJob)
            and job.customer_id == customer_id
            and job.campaign_type == campaign_type
        ):
            return job
    return None


def _render_video_page(job: VideoJob) -> str:
    if not INDEX_TEMPLATE.exists():
        raise TemplateRenderError("web/index.html is missing.")

    try:
        template = Template(INDEX_TEMPLATE.read_text(encoding="utf-8"))
        return template.render(
            video_url=job.video_url,
            thumbnail_url=job.thumbnail_url,
            customer_id=job.customer_id,
            name=job.name,
            campaign_type=job.campaign_type,
        )
    except OSError as exc:
        raise TemplateRenderError("Unable to load the patient page template.") from exc
    except Exception as exc:
        raise TemplateRenderError(
            "Unable to render the patient page.",
            details={"customer_id": job.customer_id, "campaign_type": job.campaign_type},
        ) from exc


def _page_url(customer_id: str, campaign_type: CampaignType) -> str:
    return f"/video_page?customer_id={customer_id}&campaign_type={campaign_type}"


def _absolute_page_url(page_url: str) -> str:
    return f"https://demo.signal-over-noise.com{page_url}"


def _latest_voice_notes_by_customer_campaign(
    store: dict[str, dict[str, object]],
) -> dict[tuple[str, CampaignType], VoiceNoteRecord]:
    latest_notes: dict[tuple[str, CampaignType], VoiceNoteRecord] = {}

    for record in store["voice_notes"].values():
        if not isinstance(record, VoiceNoteRecord):
            continue

        key = (record.customer_id, record.campaign_type)
        current = latest_notes.get(key)
        if current is None or record.created_at > current.created_at:
            latest_notes[key] = record

    return latest_notes


def _latest_reviews_by_customer_campaign(
    store: dict[str, dict[str, object]],
) -> dict[tuple[str, CampaignType], CaseReviewRecord]:
    latest_reviews: dict[tuple[str, CampaignType], CaseReviewRecord] = {}

    for record in store["case_reviews"].values():
        if not isinstance(record, CaseReviewRecord):
            continue

        key = (record.customer_id, record.campaign_type)
        current = latest_reviews.get(key)
        if current is None or record.reviewed_at > current.reviewed_at:
            latest_reviews[key] = record

    return latest_reviews


def _latest_jobs_by_customer_campaign(
    store: dict[str, dict[str, object]],
) -> dict[tuple[str, CampaignType], VideoJob]:
    latest_jobs: dict[tuple[str, CampaignType], VideoJob] = {}

    for job in store["video_jobs"].values():
        if not isinstance(job, VideoJob):
            continue

        key = (job.customer_id, job.campaign_type)
        current = latest_jobs.get(key)
        if current is None or job.created_at > current.created_at:
            latest_jobs[key] = job

    return latest_jobs


def _top_n_by_datetime(
    records: list[Any],
    *,
    limit: int,
    attr_name: str = "created_at",
) -> list[Any]:
    if limit <= 0 or not records:
        return []
    return nlargest(limit, records, key=lambda record: getattr(record, attr_name))


def _active_review_for_case(
    review: CaseReviewRecord | None,
    note: VoiceNoteRecord | None,
) -> CaseReviewRecord | None:
    if review is None:
        return None
    if note is not None and review.reviewed_at < note.created_at:
        return None
    return review


def _find_outreach_delivery_by_message_sid(
    store: dict[str, dict[str, object]],
    message_sid: str,
) -> OutreachDeliveryRecord | None:
    for record in store["outreach_deliveries"].values():
        if isinstance(record, OutreachDeliveryRecord) and record.provider_message_id == message_sid:
            return record
    return None


async def _apply_twilio_status_update(
    *,
    callback_payload: TwilioStatusCallbackPayload,
    store: dict[str, dict[str, object]],
    source: str,
) -> OutreachDeliveryRecord | None:
    matched_delivery = _find_outreach_delivery_by_message_sid(store, callback_payload.MessageSid)
    if matched_delivery is None:
        return None

    matched_delivery.status = callback_payload.MessageStatus
    if callback_payload.AccountSid:
        matched_delivery.account_sid = callback_payload.AccountSid
    if callback_payload.To:
        matched_delivery.destination = callback_payload.To
    if callback_payload.From:
        matched_delivery.from_number = callback_payload.From
    matched_delivery.error_code = callback_payload.ErrorCode
    matched_delivery.error_message = (
        f"Twilio callback reported error {callback_payload.ErrorCode}."
        if callback_payload.ErrorCode
        else None
    )
    matched_delivery.updated_at = datetime.now(timezone.utc)

    await log_event(
        event_type="twilio_delivery_updated",
        customer_id=matched_delivery.customer_id,
        campaign_type=matched_delivery.campaign_type,
        source=source,
        channel=matched_delivery.channel,
        destination=matched_delivery.destination,
        provider_message_id=matched_delivery.provider_message_id,
        delivery_status=matched_delivery.status,
        twilio_account_sid=matched_delivery.account_sid,
        twilio_from=matched_delivery.from_number,
        twilio_to=matched_delivery.destination,
        twilio_error_code=matched_delivery.error_code,
        twilio_sms_sid=callback_payload.SmsSid,
        twilio_sms_status=callback_payload.SmsStatus,
        twilio_raw_dlr_done_date=callback_payload.RawDlrDoneDate,
    )
    return matched_delivery


def _derived_case_status(
    note: VoiceNoteRecord | None,
    review: CaseReviewRecord | None,
) -> str:
    active_review = _active_review_for_case(review, note)
    if active_review is not None:
        return "reviewed"

    status_by_risk = {
        "high": "priority_followup",
        "medium": "review_soon",
        "low": "monitor",
        None: "awaiting_response",
    }
    return status_by_risk[note.risk_bucket if note else None]


def _serialize_event(event: dict[str, Any]) -> TunerEventResponse:
    extra = event.get("extra", {}) or {}
    return TunerEventResponse(
        event_type=event["event_type"],
        customer_id=event["customer_id"],
        campaign_type=event["campaign_type"],
        risk_bucket=event.get("risk_bucket"),
        timestamp=event["timestamp"],
        source=extra.get("source"),
        extra=extra,
    )


def _serialize_delivery(record: OutreachDeliveryRecord) -> OutreachDeliveryResponse:
    return OutreachDeliveryResponse(
        delivery_id=record.delivery_id,
        provider_message_id=record.provider_message_id,
        customer_id=record.customer_id,
        name=record.name,
        campaign_type=record.campaign_type,
        channel=record.channel,
        destination=record.destination,
        provider=record.provider,
        status=record.status,
        page_url=record.page_url,
        created_at=record.created_at,
        message_body=record.message_body,
        account_sid=record.account_sid,
        from_number=record.from_number,
        messaging_service_sid=record.messaging_service_sid,
        status_callback_url=record.status_callback_url,
        message_uri=record.message_uri,
        error_code=record.error_code,
        error_message=record.error_message,
        updated_at=record.updated_at,
    )


def _serialize_fallback_handoff(record: FallbackHandoffRecord) -> FallbackHandoffResponse:
    return FallbackHandoffResponse(
        fallback_id=record.fallback_id,
        customer_id=record.customer_id,
        name=record.name,
        campaign_type=record.campaign_type,
        message_sid=record.message_sid,
        delivery_status=record.delivery_status,
        page_url=record.page_url,
        absolute_page_url=record.absolute_page_url,
        source=record.source,
        created_at=record.created_at,
    )


def _format_twilio_http_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _serialize_twilio_message_resource(
    record: OutreachDeliveryRecord,
) -> TwilioMessageResourceResponse:
    uri = record.message_uri or (
        f"/2010-04-01/Accounts/{record.account_sid or 'AC' + 'a' * 32}/"
        f"Messages/{record.provider_message_id}.json"
    )
    return TwilioMessageResourceResponse(
        account_sid=record.account_sid,
        body=record.message_body,
        date_created=_format_twilio_http_date(record.created_at),
        date_sent=_format_twilio_http_date(record.created_at),
        date_updated=_format_twilio_http_date(record.updated_at or record.created_at),
        error_code=record.error_code,
        error_message=record.error_message,
        from_number=record.from_number,
        messaging_service_sid=record.messaging_service_sid,
        sid=record.provider_message_id,
        status=record.status,
        subresource_uris={"media": uri.replace(".json", "/Media.json")},
        to=record.destination,
        uri=uri,
    )


def _build_twilio_message_resources(
    store: dict[str, dict[str, object]],
    *,
    customer_id: str | None = None,
    campaign_type: CampaignType | None = None,
    limit: int = 6,
) -> list[TwilioMessageResourceResponse]:
    deliveries = [
        record
        for record in store["outreach_deliveries"].values()
        if isinstance(record, OutreachDeliveryRecord)
        and (customer_id is None or record.customer_id == customer_id)
        and (campaign_type is None or record.campaign_type == campaign_type)
    ]
    deliveries.sort(key=lambda record: record.created_at, reverse=True)
    return [_serialize_twilio_message_resource(record) for record in deliveries[:limit]]


def _serialize_review(
    record: CaseReviewRecord,
    *,
    active: bool = True,
) -> CaseReviewResponse:
    return CaseReviewResponse(
        review_id=record.review_id,
        customer_id=record.customer_id,
        campaign_type=record.campaign_type,
        reviewed_at=record.reviewed_at,
        reviewed_by=record.reviewed_by,
        outcome=record.outcome,
        note=record.note,
        source=record.source,
        active=active,
    )


def _serialize_voice_note(record: VoiceNoteRecord) -> VoiceNoteSubmitResponse:
    return VoiceNoteSubmitResponse(
        voice_note_id=record.voice_note_id,
        customer_id=record.customer_id,
        campaign_type=record.campaign_type,
        transcript=record.transcript,
        risk_bucket=record.risk_bucket,
        created_at=record.created_at,
    )


def _build_video_summary_from_jobs(jobs: list[VideoJob]) -> VideoSummaryResponse:
    campaign_counts = {
        "elderly_checkin": 0,
        "primary_care": 0,
        "mental_health": 0,
    }

    for job in jobs:
        if job.campaign_type in campaign_counts:
            campaign_counts[job.campaign_type] += 1

    return VideoSummaryResponse(
        total_jobs=len(jobs),
        campaign_counts=campaign_counts,
    )


def _build_video_summary(store: dict[str, dict[str, object]]) -> VideoSummaryResponse:
    jobs = [job for job in store["video_jobs"].values() if isinstance(job, VideoJob)]
    return _build_video_summary_from_jobs(jobs)


def _build_voice_summary_from_records(
    records: list[VoiceNoteRecord],
) -> VoiceNoteSummaryResponse:
    risk_counts = {"low": 0, "medium": 0, "high": 0}
    campaign_counts = {
        "elderly_checkin": 0,
        "primary_care": 0,
        "mental_health": 0,
    }
    latest_created_at: datetime | None = None

    for record in records:
        if record.risk_bucket in risk_counts:
            risk_counts[record.risk_bucket] += 1
        if record.campaign_type in campaign_counts:
            campaign_counts[record.campaign_type] += 1
        if latest_created_at is None or record.created_at > latest_created_at:
            latest_created_at = record.created_at

    return VoiceNoteSummaryResponse(
        total_notes=len(records),
        high_risk_count=risk_counts["high"],
        risk_counts=risk_counts,
        campaign_counts=campaign_counts,
        latest_created_at=latest_created_at,
    )


def _build_voice_summary(store: dict[str, dict[str, object]]) -> VoiceNoteSummaryResponse:
    records = [
        record
        for record in store["voice_notes"].values()
        if isinstance(record, VoiceNoteRecord)
    ]
    return _build_voice_summary_from_records(records)


def _build_review_summary_from_records(
    records: list[CaseReviewRecord],
) -> ReviewSummaryResponse:
    outcome_counts = {
        "escalated": 0,
        "routine_followup": 0,
        "closed": 0,
    }
    latest_reviewed_at: datetime | None = None

    for record in records:
        if record.outcome in outcome_counts:
            outcome_counts[record.outcome] += 1
        if latest_reviewed_at is None or record.reviewed_at > latest_reviewed_at:
            latest_reviewed_at = record.reviewed_at

    return ReviewSummaryResponse(
        total_reviews=len(records),
        outcome_counts=outcome_counts,
        latest_reviewed_at=latest_reviewed_at,
    )


def _build_review_summary(store: dict[str, dict[str, object]]) -> ReviewSummaryResponse:
    latest_notes = _latest_voice_notes_by_customer_campaign(store)
    latest_reviews = _latest_reviews_by_customer_campaign(store)
    records = [
        review
        for key, review in latest_reviews.items()
        if _active_review_for_case(review, latest_notes.get(key)) is not None
    ]
    return _build_review_summary_from_records(records)


def _build_recent_voice_notes(
    store: dict[str, dict[str, object]],
    *,
    limit: int = 6,
) -> list[VoiceNoteSubmitResponse]:
    recent_records = _top_n_by_datetime(
        [
            record
            for record in store["voice_notes"].values()
            if isinstance(record, VoiceNoteRecord)
        ],
        limit=limit,
    )
    return [_serialize_voice_note(record) for record in recent_records]


def _build_observability(*, limit: int = 10) -> ObservabilityResponse:
    event_counts: dict[str, int] = {}
    for event in EVENT_LOGS:
        event_type = str(event.get("event_type", "unknown"))
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

    recent_events = [_serialize_event(event) for event in reversed(EVENT_LOGS[-limit:])]
    return ObservabilityResponse(
        total_events=len(EVENT_LOGS),
        event_counts=event_counts,
        recent_events=recent_events,
    )


def _build_recent_outreach_deliveries(
    store: dict[str, dict[str, object]],
    *,
    limit: int = 6,
) -> list[OutreachDeliveryResponse]:
    recent_deliveries = _top_n_by_datetime(
        [
            record
            for record in store["outreach_deliveries"].values()
            if isinstance(record, OutreachDeliveryRecord)
        ],
        limit=limit,
    )
    return [_serialize_delivery(record) for record in recent_deliveries]


def _build_recent_fallback_handoffs(
    store: dict[str, dict[str, object]],
    *,
    limit: int = 6,
) -> list[FallbackHandoffResponse]:
    recent_handoffs = _top_n_by_datetime(
        [
            record
            for record in store["fallback_handoffs"].values()
            if isinstance(record, FallbackHandoffRecord)
        ],
        limit=limit,
    )
    return [_serialize_fallback_handoff(record) for record in recent_handoffs]


def _build_outreach_delivery_history(
    store: dict[str, dict[str, object]],
    *,
    customer_id: str,
    campaign_type: CampaignType,
    limit: int = 6,
) -> list[OutreachDeliveryResponse]:
    matching_deliveries = _top_n_by_datetime(
        [
            record
            for record in store["outreach_deliveries"].values()
            if isinstance(record, OutreachDeliveryRecord)
            and record.customer_id == customer_id
            and record.campaign_type == campaign_type
        ],
        limit=limit,
    )
    return [_serialize_delivery(record) for record in matching_deliveries]


def _build_fallback_handoff_history(
    store: dict[str, dict[str, object]],
    *,
    customer_id: str,
    campaign_type: CampaignType,
    limit: int = 6,
) -> list[FallbackHandoffResponse]:
    matching_handoffs = _top_n_by_datetime(
        [
            record
            for record in store["fallback_handoffs"].values()
            if isinstance(record, FallbackHandoffRecord)
            and record.customer_id == customer_id
            and record.campaign_type == campaign_type
        ],
        limit=limit,
    )
    return [_serialize_fallback_handoff(record) for record in matching_handoffs]


def _build_demo_jobs(store: dict[str, dict[str, object]]) -> list[DemoVideoJobResponse]:
    demo_jobs: list[DemoVideoJobResponse] = []
    for scenario in DEMO_VIDEO_SCENARIOS:
        job = _find_video_job(store, scenario["customer_id"], scenario["campaign_type"])
        if job is None:
            continue
        demo_jobs.append(
            DemoVideoJobResponse(
                customer_id=job.customer_id,
                name=job.name,
                campaign_type=job.campaign_type,
                page_url=_page_url(job.customer_id, job.campaign_type),
                video_url=job.video_url,
                thumbnail_url=job.thumbnail_url,
            )
        )
    return demo_jobs


def _serialize_video_history_item(job: VideoJob) -> VideoHistoryItemResponse:
    visual_mode = "uploaded" if (job.avatar_image_url or job.background_image_url) else "demo"
    return VideoHistoryItemResponse(
        job_id=job.job_id,
        customer_id=job.customer_id,
        name=job.name,
        campaign_type=job.campaign_type,
        created_at=job.created_at,
        plan=job.plan,
        days_to_expiry=job.days_to_expiry,
        visual_mode=visual_mode,
        video_url=job.video_url,
        thumbnail_url=job.thumbnail_url,
        script=job.script,
    )


def _build_video_history(
    store: dict[str, dict[str, object]],
    *,
    customer_id: str,
    campaign_type: CampaignType,
    limit: int = 6,
) -> list[VideoHistoryItemResponse]:
    matching_jobs = _top_n_by_datetime(
        [
        job
        for job in store["video_jobs"].values()
        if isinstance(job, VideoJob)
        and job.customer_id == customer_id
        and job.campaign_type == campaign_type
        ],
        limit=limit,
    )
    return [_serialize_video_history_item(job) for job in matching_jobs]


def _build_care_queue(
    store: dict[str, dict[str, object]],
    *,
    limit: int = 10,
) -> list[CareQueueItemResponse]:
    latest_notes = _latest_voice_notes_by_customer_campaign(store)
    latest_reviews = _latest_reviews_by_customer_campaign(store)
    latest_jobs = _latest_jobs_by_customer_campaign(store)
    return _build_care_queue_from_latest(
        latest_jobs=latest_jobs,
        latest_notes=latest_notes,
        latest_reviews=latest_reviews,
        limit=limit,
    )


def _build_care_queue_from_latest(
    *,
    latest_jobs: dict[tuple[str, CampaignType], VideoJob],
    latest_notes: dict[tuple[str, CampaignType], VoiceNoteRecord],
    latest_reviews: dict[tuple[str, CampaignType], CaseReviewRecord],
    limit: int,
) -> list[CareQueueItemResponse]:

    status_rank = {
        "priority_followup": 0,
        "review_soon": 1,
        "monitor": 2,
        "awaiting_response": 3,
        "reviewed": 4,
    }

    ranked_candidates = []
    for (customer_id, campaign_type), job in latest_jobs.items():
        note = latest_notes.get((customer_id, campaign_type))
        review = latest_reviews.get((customer_id, campaign_type))
        active_review = _active_review_for_case(review, note)
        status = _derived_case_status(note, review)
        ranked_candidates.append(
            (
                status_rank.get(status, 5),
                -(note.created_at.timestamp() if note else 0),
                job.name,
                job,
                note,
                review,
                active_review is not None,
                status,
            )
        )

    top_candidates = nsmallest(
        limit,
        ranked_candidates,
        key=lambda item: (item[0], item[1], item[2]),
    )
    return [
        CareQueueItemResponse(
            customer_id=job.customer_id,
            name=job.name,
            campaign_type=job.campaign_type,
            page_url=_page_url(job.customer_id, job.campaign_type),
            status=status,
            risk_bucket=note.risk_bucket if note else None,
            latest_transcript=note.transcript if note else None,
            latest_created_at=note.created_at if note else None,
            reviewed_at=review.reviewed_at if review else None,
            reviewed_by=review.reviewed_by if review else None,
            review_outcome=review.outcome if review else None,
            review_note=review.note if review else None,
            review_active=review_active,
        )
        for _, _, _, job, note, review, review_active, status in top_candidates
    ]


def _build_default_outreach_message(
    *,
    job: VideoJob,
    page_url: str,
) -> str:
    return (
        f"Hi {job.name}, this is Signal Over Noise on behalf of your care team. "
        f"Your {job.campaign_type.replace('_', ' ')} check-in is ready here: {page_url}"
    )


def _build_render_sponsor_summary() -> RenderSponsorResponse:
    project_root = Path(__file__).resolve().parents[2]
    render_yaml_present = (project_root / "render.yaml").exists()
    dockerfile_present = (project_root / "Dockerfile").exists()
    ready = render_yaml_present and dockerfile_present
    notes = [
        "FastAPI app exposes /healthz for Render health checks.",
        "render.yaml and Dockerfile are present for web-service deployment.",
    ]
    return RenderSponsorResponse(
        deployment_target="render_web_service",
        ready=ready,
        healthcheck_path="/healthz",
        render_yaml_present=render_yaml_present,
        dockerfile_present=dockerfile_present,
        notes=notes,
    )


def _build_tuner_sponsor_summary() -> TunerSponsorResponse:
    event_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    twilio_delivery_events = 0
    twilio_retry_events = 0
    twilio_failover_events = 0
    review_outcome_counts = {
        "escalated": 0,
        "routine_followup": 0,
        "closed": 0,
    }

    for event in EVENT_LOGS:
        event_type = str(event.get("event_type", "unknown"))
        extra = event.get("extra") or {}
        source = str(extra.get("source", "app"))
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
        if event_type == "twilio_outreach_sent":
            twilio_delivery_events += 1
        if event_type == "twilio_outreach_retried":
            twilio_retry_events += 1
        if event_type == "twilio_fallback_link_prepared":
            twilio_failover_events += 1
        if event_type == "case_marked_reviewed":
            outcome = str(extra.get("review_outcome", ""))
            if outcome in review_outcome_counts:
                review_outcome_counts[outcome] += 1

    return TunerSponsorResponse(
        total_events=len(EVENT_LOGS),
        event_counts=event_counts,
        source_counts=source_counts,
        twilio_delivery_events=twilio_delivery_events,
        twilio_retry_events=twilio_retry_events,
        twilio_failover_events=twilio_failover_events,
        review_outcome_counts=review_outcome_counts,
    )


def _build_twilio_sponsor_summary(
    store: dict[str, dict[str, object]],
) -> TwilioSponsorResponse:
    delivery_records = [
        record
        for record in store["outreach_deliveries"].values()
        if isinstance(record, OutreachDeliveryRecord)
    ]
    fallback_records = [
        record
        for record in store["fallback_handoffs"].values()
        if isinstance(record, FallbackHandoffRecord)
    ]
    return _build_twilio_sponsor_summary_from_records(delivery_records, fallback_records)


def _build_twilio_sponsor_summary_from_records(
    delivery_records: list[OutreachDeliveryRecord],
    fallback_records: list[FallbackHandoffRecord],
) -> TwilioSponsorResponse:
    campaign_counts = {
        "elderly_checkin": 0,
        "primary_care": 0,
        "mental_health": 0,
    }
    latest_delivery: OutreachDeliveryRecord | None = None
    latest_fallback: FallbackHandoffRecord | None = None

    for record in delivery_records:
        if record.campaign_type in campaign_counts:
            campaign_counts[record.campaign_type] += 1
        if latest_delivery is None or record.created_at > latest_delivery.created_at:
            latest_delivery = record

    for record in fallback_records:
        if latest_fallback is None or record.created_at > latest_fallback.created_at:
            latest_fallback = record

    return TwilioSponsorResponse(
        total_deliveries=len(delivery_records),
        latest_status=latest_delivery.status if latest_delivery else "none_sent",
        latest_destination=latest_delivery.destination if latest_delivery else None,
        campaign_counts=campaign_counts,
        total_fallback_handoffs=len(fallback_records),
        latest_fallback_at=latest_fallback.created_at if latest_fallback else None,
    )


def _build_sponsor_summary(
    store: dict[str, dict[str, object]],
) -> SponsorSummaryResponse:
    return SponsorSummaryResponse(
        render=_build_render_sponsor_summary(),
        tuner=_build_tuner_sponsor_summary(),
        twilio=_build_twilio_sponsor_summary(store),
    )


def _build_dashboard_derived_state(
    store: dict[str, dict[str, object]],
    *,
    queue_limit: int = 8,
    recent_limit: int = 6,
) -> _DashboardDerivedState:
    jobs = [job for job in store["video_jobs"].values() if isinstance(job, VideoJob)]
    voice_records = [
        record
        for record in store["voice_notes"].values()
        if isinstance(record, VoiceNoteRecord)
    ]
    review_records = [
        record
        for record in store["case_reviews"].values()
        if isinstance(record, CaseReviewRecord)
    ]
    delivery_records = [
        record
        for record in store["outreach_deliveries"].values()
        if isinstance(record, OutreachDeliveryRecord)
    ]
    fallback_records = [
        record
        for record in store["fallback_handoffs"].values()
        if isinstance(record, FallbackHandoffRecord)
    ]

    latest_jobs: dict[tuple[str, CampaignType], VideoJob] = {}
    for job in jobs:
        key = (job.customer_id, job.campaign_type)
        current = latest_jobs.get(key)
        if current is None or job.created_at > current.created_at:
            latest_jobs[key] = job

    latest_notes: dict[tuple[str, CampaignType], VoiceNoteRecord] = {}
    for record in voice_records:
        key = (record.customer_id, record.campaign_type)
        current = latest_notes.get(key)
        if current is None or record.created_at > current.created_at:
            latest_notes[key] = record

    latest_reviews: dict[tuple[str, CampaignType], CaseReviewRecord] = {}
    for record in review_records:
        key = (record.customer_id, record.campaign_type)
        current = latest_reviews.get(key)
        if current is None or record.reviewed_at > current.reviewed_at:
            latest_reviews[key] = record

    active_review_records = [
        review
        for key, review in latest_reviews.items()
        if _active_review_for_case(review, latest_notes.get(key)) is not None
    ]

    return _DashboardDerivedState(
        video_summary=_build_video_summary_from_jobs(jobs),
        voice_summary=_build_voice_summary_from_records(voice_records),
        review_summary=_build_review_summary_from_records(active_review_records),
        care_queue=_build_care_queue_from_latest(
            latest_jobs=latest_jobs,
            latest_notes=latest_notes,
            latest_reviews=latest_reviews,
            limit=queue_limit,
        ),
        recent_voice_notes=[
            _serialize_voice_note(record)
            for record in _top_n_by_datetime(voice_records, limit=recent_limit)
        ],
        sponsor_summary=SponsorSummaryResponse(
            render=_build_render_sponsor_summary(),
            tuner=_build_tuner_sponsor_summary(),
            twilio=_build_twilio_sponsor_summary_from_records(delivery_records, fallback_records),
        ),
        recent_outreach_deliveries=[
            _serialize_delivery(record)
            for record in _top_n_by_datetime(delivery_records, limit=recent_limit)
        ],
        recent_fallback_handoffs=[
            _serialize_fallback_handoff(record)
            for record in _top_n_by_datetime(fallback_records, limit=recent_limit)
        ],
    )


async def _create_and_store_video_job(
    payload: CreateVideoJobRequest,
    store: dict[str, dict[str, object]],
    *,
    log_to_tuner: bool = True,
) -> VideoJob:
    script = _build_script(payload)
    try:
        seedance_job = await generate_video(
            script=script,
            voice_tone="calm",
            avatar_style="neutral",
            background_style="simple",
            avatar_image_url=payload.avatar_image_url,
            background_image_url=payload.background_image_url,
        )
    except Exception as exc:
        raise ExternalServiceError(
            "Video generation could not be completed.",
            service="seedance",
            details={"customer_id": payload.customer_id, "campaign_type": payload.campaign_type},
        ) from exc

    video_job = VideoJob(
        job_id=seedance_job.job_id,
        customer_id=payload.customer_id,
        name=payload.name,
        campaign_type=payload.campaign_type,
        script=script,
        video_url=seedance_job.video_url,
        thumbnail_url=seedance_job.thumbnail_url,
        avatar_image_url=payload.avatar_image_url,
        background_image_url=payload.background_image_url,
        plan=payload.plan,
        days_to_expiry=payload.days_to_expiry,
    )
    store["video_jobs"][video_job.job_id] = video_job

    if log_to_tuner:
        await log_event(
            event_type="video_job_created",
            customer_id=payload.customer_id,
            campaign_type=payload.campaign_type,
            avatar_image_url=payload.avatar_image_url,
            background_image_url=payload.background_image_url,
            seedance_job_id=seedance_job.job_id,
            source="seedance_stub",
        )

    return video_job


async def seed_demo_video_jobs(store: dict[str, dict[str, object]]) -> None:
    for scenario in DEMO_VIDEO_SCENARIOS:
        existing_job = _find_video_job(store, scenario["customer_id"], scenario["campaign_type"])
        if existing_job is not None:
            continue
        payload = CreateVideoJobRequest.model_validate(scenario)
        await _create_and_store_video_job(payload, store, log_to_tuner=False)


async def _send_outreach_delivery(
    *,
    payload: SendOutreachRequest,
    store: dict[str, dict[str, object]],
) -> OutreachDeliveryRecord:
    job = _find_video_job(store, payload.customer_id, payload.campaign_type)
    if job is None:
        raise NotFoundError(
            "Video job not found for outreach delivery.",
            details={"customer_id": payload.customer_id, "campaign_type": payload.campaign_type},
        )

    if not payload.phone_number.strip():
        raise ValidationError("phone_number is required.")

    page_url = _page_url(job.customer_id, job.campaign_type)
    absolute_page_url = _absolute_page_url(page_url)
    message_body = payload.custom_message or _build_default_outreach_message(
        job=job,
        page_url=absolute_page_url,
    )

    try:
        twilio_response = await send_sms(
            to_phone_number=payload.phone_number,
            body=message_body,
            messaging_service_sid=os.getenv("TWILIO_MESSAGING_SERVICE_SID"),
            from_phone_number=os.getenv("TWILIO_FROM_NUMBER"),
            status_callback_url=os.getenv("TWILIO_STATUS_CALLBACK_URL"),
        )
    except Exception as exc:
        raise ExternalServiceError(
            "SMS outreach could not be queued.",
            service="twilio",
            details={"customer_id": payload.customer_id, "campaign_type": payload.campaign_type},
        ) from exc

    delivery = OutreachDeliveryRecord(
        delivery_id=f"out_{uuid4().hex[:12]}",
        provider_message_id=twilio_response.sid,
        customer_id=job.customer_id,
        name=job.name,
        campaign_type=job.campaign_type,
        channel=twilio_response.channel,
        destination=payload.phone_number,
        message_body=twilio_response.body,
        provider=twilio_response.provider,
        status=twilio_response.status,
        page_url=page_url,
        created_at=datetime.now(timezone.utc),
        account_sid=twilio_response.account_sid,
        from_number=twilio_response.from_number,
        messaging_service_sid=twilio_response.messaging_service_sid,
        status_callback_url=twilio_response.status_callback,
        message_uri=twilio_response.uri,
        error_code=twilio_response.error_code,
        error_message=twilio_response.error_message,
        updated_at=datetime.now(timezone.utc),
    )
    store["outreach_deliveries"][delivery.delivery_id] = delivery

    await log_event(
        event_type="twilio_outreach_sent",
        customer_id=job.customer_id,
        campaign_type=job.campaign_type,
        source="twilio_stub",
        channel=delivery.channel,
        destination=delivery.destination,
        provider_message_id=delivery.provider_message_id,
        delivery_status=delivery.status,
        twilio_account_sid=delivery.account_sid,
        twilio_from=delivery.from_number,
        twilio_messaging_service_sid=delivery.messaging_service_sid,
        twilio_message_uri=delivery.message_uri,
    )
    return delivery


async def _retry_outreach_delivery(
    *,
    message_sid: str,
    store: dict[str, dict[str, object]],
) -> tuple[OutreachDeliveryRecord, OutreachDeliveryRecord]:
    original_delivery = _find_outreach_delivery_by_message_sid(store, message_sid)
    if original_delivery is None:
        raise NotFoundError(
            "Twilio message resource not found for retry.",
            details={"message_sid": message_sid},
        )

    if original_delivery.status not in {"failed", "undelivered"}:
        raise ValidationError(
            "Only failed or undelivered Twilio demo messages can be retried.",
            details={"message_sid": message_sid, "status": original_delivery.status},
        )

    retry_payload = SendOutreachRequest(
        customer_id=original_delivery.customer_id,
        campaign_type=original_delivery.campaign_type,
        phone_number=original_delivery.destination,
        custom_message=original_delivery.message_body,
    )
    retried_delivery = await _send_outreach_delivery(payload=retry_payload, store=store)

    await log_event(
        event_type="twilio_outreach_retried",
        customer_id=retried_delivery.customer_id,
        campaign_type=retried_delivery.campaign_type,
        source="twilio_retry_control",
        previous_message_sid=original_delivery.provider_message_id,
        retried_message_sid=retried_delivery.provider_message_id,
        destination=retried_delivery.destination,
    )
    return original_delivery, retried_delivery


async def _prepare_fallback_link(
    *,
    payload: PrepareFallbackLinkRequest,
    store: dict[str, dict[str, object]],
) -> PrepareFallbackLinkResponse:
    job = _find_video_job(store, payload.customer_id, payload.campaign_type)
    if job is None:
        raise NotFoundError(
            "Video job not found for fallback handoff.",
            details={"customer_id": payload.customer_id, "campaign_type": payload.campaign_type},
        )

    matched_delivery: OutreachDeliveryRecord | None = None
    if payload.message_sid:
        matched_delivery = _find_outreach_delivery_by_message_sid(store, payload.message_sid)
        if matched_delivery is None:
            raise NotFoundError(
                "Twilio message resource not found for fallback handoff.",
                details={"message_sid": payload.message_sid},
            )
        if (
            matched_delivery.customer_id != payload.customer_id
            or matched_delivery.campaign_type != payload.campaign_type
        ):
            raise ValidationError(
                "Twilio message resource does not match the selected patient journey.",
                details={"message_sid": payload.message_sid},
            )
        if matched_delivery.status not in {"failed", "undelivered"}:
            raise ValidationError(
                "Only failed or undelivered Twilio demo messages can trigger a secure-link fallback.",
                details={"message_sid": payload.message_sid, "status": matched_delivery.status},
            )

    page_url = _page_url(job.customer_id, job.campaign_type)
    absolute_page_url = _absolute_page_url(page_url)
    fallback_record = FallbackHandoffRecord(
        fallback_id=f"fb_{uuid4().hex[:12]}",
        customer_id=job.customer_id,
        name=job.name,
        campaign_type=job.campaign_type,
        message_sid=matched_delivery.provider_message_id if matched_delivery else None,
        delivery_status=matched_delivery.status if matched_delivery else None,
        page_url=page_url,
        absolute_page_url=absolute_page_url,
        source=payload.source,
        created_at=datetime.now(timezone.utc),
    )
    store["fallback_handoffs"][fallback_record.fallback_id] = fallback_record

    await log_event(
        event_type="twilio_fallback_link_prepared",
        customer_id=job.customer_id,
        campaign_type=job.campaign_type,
        source=payload.source,
        previous_message_sid=matched_delivery.provider_message_id if matched_delivery else None,
        previous_delivery_status=matched_delivery.status if matched_delivery else None,
        fallback_page_url=page_url,
        fallback_absolute_page_url=absolute_page_url,
    )
    return PrepareFallbackLinkResponse(
        customer_id=job.customer_id,
        campaign_type=job.campaign_type,
        name=job.name,
        message_sid=fallback_record.message_sid,
        delivery_status=fallback_record.delivery_status,
        page_url=fallback_record.page_url,
        absolute_page_url=fallback_record.absolute_page_url,
    )


async def _mark_case_reviewed(
    *,
    payload: MarkReviewedRequest,
    store: dict[str, dict[str, object]],
) -> CaseReviewRecord:
    job = _find_video_job(store, payload.customer_id, payload.campaign_type)
    if job is None:
        raise NotFoundError(
            "Video job not found for case review.",
            details={"customer_id": payload.customer_id, "campaign_type": payload.campaign_type},
        )

    note = _latest_voice_notes_by_customer_campaign(store).get((payload.customer_id, payload.campaign_type))
    if note is None:
        raise ValidationError(
            "A voice note must be submitted before this case can be marked reviewed.",
            details={
                "customer_id": payload.customer_id,
                "campaign_type": payload.campaign_type,
                "status": "awaiting_response",
            },
        )

    latest_review = _latest_reviews_by_customer_campaign(store).get((payload.customer_id, payload.campaign_type))
    if _active_review_for_case(latest_review, note) is not None:
        raise ValidationError(
            "This case is already marked reviewed for the latest voice note.",
            details={
                "customer_id": payload.customer_id,
                "campaign_type": payload.campaign_type,
                "status": "reviewed",
            },
        )

    review = CaseReviewRecord(
        review_id=f"rev_{uuid4().hex[:12]}",
        customer_id=payload.customer_id,
        campaign_type=payload.campaign_type,
        reviewed_at=datetime.now(timezone.utc),
        reviewed_by=payload.reviewed_by,
        outcome=payload.outcome,
        note=payload.note,
        source=payload.source,
    )
    store["case_reviews"][review.review_id] = review

    await log_event(
        event_type="case_marked_reviewed",
        customer_id=payload.customer_id,
        campaign_type=payload.campaign_type,
        risk_bucket=note.risk_bucket,
        source=payload.source,
        reviewed_by=payload.reviewed_by,
        review_outcome=payload.outcome,
        review_note=payload.note,
    )
    return review


@router.post("/create_job", response_model=CreateVideoJobResponse)
async def create_video_job(
    payload: CreateVideoJobRequest,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> CreateVideoJobResponse:
    video_job = await _create_and_store_video_job(payload, store)

    return CreateVideoJobResponse(
        job_id=video_job.job_id,
        customer_id=video_job.customer_id,
        video_url=video_job.video_url,
        thumbnail_url=video_job.thumbnail_url,
        campaign_type=payload.campaign_type,
    )


@router.post("/send_outreach", response_model=SendOutreachResponse)
async def send_outreach(
    payload: SendOutreachRequest,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> SendOutreachResponse:
    delivery = await _send_outreach_delivery(payload=payload, store=store)
    return SendOutreachResponse(delivery=_serialize_delivery(delivery))


@router.post("/retry_outreach", response_model=RetryOutreachResponse)
async def retry_outreach(
    payload: RetryOutreachRequest,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> RetryOutreachResponse:
    original_delivery, retried_delivery = await _retry_outreach_delivery(
        message_sid=payload.message_sid,
        store=store,
    )
    return RetryOutreachResponse(
        original_delivery=_serialize_delivery(original_delivery),
        retried_delivery=_serialize_delivery(retried_delivery),
    )


@router.post("/prepare_fallback_link", response_model=PrepareFallbackLinkResponse)
async def prepare_fallback_link(
    payload: PrepareFallbackLinkRequest,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> PrepareFallbackLinkResponse:
    return await _prepare_fallback_link(payload=payload, store=store)


@router.post("/mark_reviewed", response_model=CaseReviewResponse)
async def mark_reviewed(
    payload: MarkReviewedRequest,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> CaseReviewResponse:
    review = await _mark_case_reviewed(payload=payload, store=store)
    return _serialize_review(review, active=True)


@router.get("/review_status", response_model=ReviewStatusResponse)
async def review_status(
    customer_id: str = Query(...),
    campaign_type: CampaignType = Query(...),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> ReviewStatusResponse:
    latest_note = _latest_voice_notes_by_customer_campaign(store).get((customer_id, campaign_type))
    latest_review = _latest_reviews_by_customer_campaign(store).get((customer_id, campaign_type))
    active_review = _active_review_for_case(latest_review, latest_note)
    return ReviewStatusResponse(
        customer_id=customer_id,
        campaign_type=campaign_type,
        status=_derived_case_status(latest_note, latest_review),
        review=_serialize_review(latest_review, active=active_review is not None) if latest_review else None,
    )


@router.get("/outreach_deliveries", response_model=OutreachDeliveryHistoryResponse)
async def outreach_delivery_history(
    customer_id: str = Query(...),
    campaign_type: CampaignType = Query(...),
    limit: int = Query(default=6, ge=1, le=20),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> OutreachDeliveryHistoryResponse:
    return OutreachDeliveryHistoryResponse(
        customer_id=customer_id,
        campaign_type=campaign_type,
        deliveries=_build_outreach_delivery_history(
            store,
            customer_id=customer_id,
            campaign_type=campaign_type,
            limit=limit,
        ),
    )


@router.get("/fallback_handoffs", response_model=FallbackHandoffHistoryResponse)
async def fallback_handoff_history(
    customer_id: str = Query(...),
    campaign_type: CampaignType = Query(...),
    limit: int = Query(default=6, ge=1, le=20),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> FallbackHandoffHistoryResponse:
    return FallbackHandoffHistoryResponse(
        customer_id=customer_id,
        campaign_type=campaign_type,
        handoffs=_build_fallback_handoff_history(
            store,
            customer_id=customer_id,
            campaign_type=campaign_type,
            limit=limit,
        ),
    )


@router.get("/twilio_message", response_model=TwilioMessageResourceResponse)
async def twilio_message_resource(
    message_sid: str = Query(..., min_length=3),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> TwilioMessageResourceResponse:
    delivery = _find_outreach_delivery_by_message_sid(store, message_sid)
    if delivery is None:
        raise NotFoundError(
            "Twilio message resource not found.",
            details={"message_sid": message_sid},
        )

    return _serialize_twilio_message_resource(delivery)


@router.get("/twilio_messages", response_model=TwilioMessageListResponse)
async def twilio_message_list(
    customer_id: str | None = Query(default=None),
    campaign_type: CampaignType | None = Query(default=None),
    limit: int = Query(default=6, ge=1, le=20),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> TwilioMessageListResponse:
    if (customer_id is None) != (campaign_type is None):
        raise ValidationError(
            "Provide both customer_id and campaign_type together for journey-scoped message lists.",
            details={"customer_id": customer_id, "campaign_type": campaign_type},
        )

    params: dict[str, str | int] = {"limit": limit}
    if customer_id is not None and campaign_type is not None:
        params["customer_id"] = customer_id
        params["campaign_type"] = campaign_type

    return TwilioMessageListResponse(
        messages=_build_twilio_message_resources(
            store,
            customer_id=customer_id,
            campaign_type=campaign_type,
            limit=limit,
        ),
        page_size=limit,
        uri=f"/api/v1/video/twilio_messages?{urlencode(params)}",
    )


@router.post("/twilio_status")
async def twilio_status_callback(
    MessageSid: str = Form(...),
    MessageStatus: str = Form(...),
    AccountSid: str | None = Form(default=None),
    To: str | None = Form(default=None),
    From: str | None = Form(default=None),
    ErrorCode: str | None = Form(default=None),
    SmsSid: str | None = Form(default=None),
    SmsStatus: str | None = Form(default=None),
    RawDlrDoneDate: str | None = Form(default=None),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> dict[str, str]:
    callback_payload = TwilioStatusCallbackPayload(
        MessageSid=MessageSid,
        MessageStatus=MessageStatus,
        AccountSid=AccountSid or "",
        To=To or "",
        From=From,
        ErrorCode=ErrorCode,
        SmsSid=SmsSid,
        SmsStatus=SmsStatus,
        RawDlrDoneDate=RawDlrDoneDate,
    )
    await _apply_twilio_status_update(
        callback_payload=callback_payload,
        store=store,
        source="twilio_status_callback",
    )

    return {"status": "ok", "message_sid": MessageSid}


@router.post("/twilio_simulate_status", response_model=TwilioStatusSimulationResponse)
async def twilio_simulate_status(
    payload: TwilioStatusSimulationRequest,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> TwilioStatusSimulationResponse:
    delivery = _find_outreach_delivery_by_message_sid(store, payload.message_sid)
    if delivery is None:
        raise NotFoundError(
            "Twilio message resource not found for simulation.",
            details={"message_sid": payload.message_sid},
        )

    message_resource = _serialize_twilio_message_resource(delivery)
    callback_payload = build_status_callback_payload(
        message=message_resource,
        status=payload.status,
        error_code=payload.error_code
        or ("30003" if payload.status in {"undelivered", "failed"} else None),
        raw_dlr_done_date=datetime.now(timezone.utc).strftime("%y%m%d%H%M"),
    )
    updated_delivery = await _apply_twilio_status_update(
        callback_payload=callback_payload,
        store=store,
        source="twilio_status_simulator",
    )
    if updated_delivery is None:
        raise NotFoundError(
            "Twilio message resource not found for simulation.",
            details={"message_sid": payload.message_sid},
        )

    return TwilioStatusSimulationResponse(
        delivery=_serialize_delivery(updated_delivery),
        simulated_status=payload.status,
    )


@router.get("/script_preview", response_model=ScriptPreviewResponse)
async def script_preview(
    name: str = Query(..., min_length=1),
    campaign_type: CampaignType = Query(...),
    plan: str | None = Query(default=None),
    days_to_expiry: int | None = Query(default=None, ge=0),
) -> ScriptPreviewResponse:
    script = _compose_script(
        name=name,
        campaign_type=campaign_type,
        plan=plan,
        days_to_expiry=days_to_expiry,
    )
    return ScriptPreviewResponse(
        campaign_type=campaign_type,
        name=name,
        plan=plan,
        days_to_expiry=days_to_expiry,
        script=script,
    )


@router.get("/summary", response_model=VideoSummaryResponse)
async def video_summary(
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> VideoSummaryResponse:
    return _build_video_summary(store)


@router.get("/history", response_model=list[VideoHistoryItemResponse])
async def video_history(
    customer_id: str = Query(...),
    campaign_type: CampaignType = Query(...),
    limit: int = Query(default=6, ge=1, le=20),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> list[VideoHistoryItemResponse]:
    return _build_video_history(
        store,
        customer_id=customer_id,
        campaign_type=campaign_type,
        limit=limit,
    )


@router.get("/observability", response_model=ObservabilityResponse)
async def observability_feed(
    limit: int = Query(default=10, ge=1, le=30),
) -> ObservabilityResponse:
    return _build_observability(limit=limit)


@router.post("/reset_demo", response_model=DemoResetResponse)
async def reset_demo_state(
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> DemoResetResponse:
    cleared_voice_notes = len(store["voice_notes"])
    cleared_outreach_deliveries = len(store["outreach_deliveries"])
    cleared_fallback_handoffs = len(store["fallback_handoffs"])
    cleared_case_reviews = len(store["case_reviews"])
    cleared_automation_runs = len(store["automation_runs"])
    cleared_events = len(EVENT_LOGS)

    store["video_jobs"].clear()
    store["voice_notes"].clear()
    store["outreach_deliveries"].clear()
    store["fallback_handoffs"].clear()
    store["case_reviews"].clear()
    store["automation_runs"].clear()
    EVENT_LOGS.clear()
    await seed_demo_video_jobs(store)

    return DemoResetResponse(
        message="Demo state reset to seeded outreach journeys.",
        video_jobs_seeded=len(store["video_jobs"]),
        voice_notes_cleared=cleared_voice_notes,
        outreach_deliveries_cleared=cleared_outreach_deliveries,
        fallback_handoffs_cleared=cleared_fallback_handoffs,
        case_reviews_cleared=cleared_case_reviews,
        automation_runs_cleared=cleared_automation_runs,
        events_cleared=cleared_events,
    )


@router.get("/dashboard_overview", response_model=DashboardOverviewResponse)
async def dashboard_overview(
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> DashboardOverviewResponse:
    await seed_demo_video_jobs(store)
    derived_state = _build_dashboard_derived_state(store, queue_limit=8, recent_limit=6)
    return DashboardOverviewResponse(
        demo_jobs=_build_demo_jobs(store),
        video_summary=derived_state.video_summary,
        voice_summary=derived_state.voice_summary,
        review_summary=derived_state.review_summary,
        care_queue=derived_state.care_queue,
        recent_voice_notes=derived_state.recent_voice_notes,
        observability=_build_observability(limit=8),
        sponsor_summary=derived_state.sponsor_summary,
        recent_outreach_deliveries=derived_state.recent_outreach_deliveries,
        recent_fallback_handoffs=derived_state.recent_fallback_handoffs,
    )


@router.get("/care_queue", response_model=list[CareQueueItemResponse])
async def care_queue(
    limit: int = Query(default=10, ge=1, le=30),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> list[CareQueueItemResponse]:
    return _build_care_queue(store, limit=limit)


@router.get("/demo_jobs", response_model=list[DemoVideoJobResponse])
async def get_demo_video_jobs(
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> list[DemoVideoJobResponse]:
    await seed_demo_video_jobs(store)
    return _build_demo_jobs(store)


@router.post("/mock_trigger", response_model=CreateVideoJobResponse)
async def mock_trigger_create_video_job(
    payload: MockTriggerRequest,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> CreateVideoJobResponse:
    trigger_payload = await trigger_video_job(
        customer_id=payload.customer_id,
        name=payload.name,
        campaign_type=payload.campaign_type,
    )
    request_payload = CreateVideoJobRequest.model_validate(trigger_payload)
    return await create_video_job(request_payload, store)


@router.get("/page", response_class=HTMLResponse)
async def api_video_page(
    customer_id: str = Query(...),
    campaign_type: CampaignType = Query(...),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> HTMLResponse:
    job = _find_video_job(store, customer_id, campaign_type)
    if job is None:
        raise NotFoundError(
            "Video job not found.",
            details={"customer_id": customer_id, "campaign_type": campaign_type},
        )

    return HTMLResponse(content=_render_video_page(job))


@public_router.get("/video_page", response_class=HTMLResponse, include_in_schema=False)
async def public_video_page(
    customer_id: str = Query(...),
    campaign_type: CampaignType = Query(...),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> HTMLResponse:
    job = _find_video_job(store, customer_id, campaign_type)
    if job is None:
        raise NotFoundError(
            "Video job not found.",
            details={"customer_id": customer_id, "campaign_type": campaign_type},
        )

    return HTMLResponse(content=_render_video_page(job))
