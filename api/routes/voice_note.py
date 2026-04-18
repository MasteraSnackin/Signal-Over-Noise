from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from api.errors import ExternalServiceError, FileStorageError, NotFoundError, ValidationError
from api.services.speechmatics import (
    SpeechmaticsVoiceConfig,
    build_voice_config,
    transcribe_with_config,
)
from api.services.thymia import RiskBucket, get_risk_level
from api.services.tuner import log_event
from api.types import CampaignType
from db import VoiceNoteRecord, get_db

router = APIRouter(prefix="/voice_note", tags=["voice-note"])
TMP_DIR = Path("/tmp")


class VoiceNoteSubmitResponse(BaseModel):
    voice_note_id: str
    customer_id: str
    campaign_type: CampaignType
    transcript: str
    risk_bucket: RiskBucket
    created_at: datetime


class MockVoiceNoteSubmitRequest(BaseModel):
    customer_id: str = Field(min_length=1)
    campaign_type: CampaignType


class VoiceNoteSummaryResponse(BaseModel):
    total_notes: int
    high_risk_count: int
    risk_counts: dict[str, int]
    campaign_counts: dict[str, int]
    latest_created_at: datetime | None


DEMO_VOICE_NOTE_TARGETS: list[dict[str, str]] = [
    {"customer_id": "demo_elder_001", "campaign_type": "elderly_checkin"},
    {"customer_id": "demo_gp_001", "campaign_type": "primary_care"},
    {"customer_id": "demo_mh_001", "campaign_type": "mental_health"},
]

DEMO_SIGNAL_PROFILES: dict[CampaignType, dict[str, str]] = {
    "elderly_checkin": {
        "transcript": "I am doing alright today, just moving a little slower than usual, but I have eaten breakfast and I am feeling steady.",
        "risk_bucket": "low",
    },
    "primary_care": {
        "transcript": "I am mostly okay, but I have felt more run down this week and I think I should probably speak to someone soon.",
        "risk_bucket": "medium",
    },
    "mental_health": {
        "transcript": "I have been feeling overwhelmed, I have not been sleeping well, and I think I need someone from the care team to check in with me.",
        "risk_bucket": "high",
    },
}


def _speechmatics_config_for_campaign(campaign_type: CampaignType) -> SpeechmaticsVoiceConfig:
    healthcare_vocab = {
        "elderly_checkin": ["mobility", "wellbeing"],
        "primary_care": ["blood pressure", "medication"],
        "mental_health": ["sleep", "wellbeing"],
    }
    return build_voice_config(
        language="en",
        domain="medical",
        output_locale="en-GB",
        max_delay=0.7,
        enable_diarization=False,
        include_partials=False,
        additional_vocab=healthcare_vocab.get(campaign_type, []),
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


def _persist_temp_audio(temp_path: Path, audio_bytes: bytes) -> None:
    try:
        temp_path.write_bytes(audio_bytes)
    except OSError as exc:
        raise FileStorageError(
            "Unable to store the uploaded voice note.",
            details={"path": str(temp_path)},
        ) from exc


def _remove_temp_audio(temp_path: Path) -> None:
    try:
        temp_path.unlink(missing_ok=True)
    except OSError:
        return


async def _create_voice_note_record(
    *,
    customer_id: str,
    campaign_type: CampaignType,
    source_path: str,
    source: str,
    store: dict[str, dict[str, object]],
    audio_filename: str | None = None,
    log_to_tuner: bool = True,
    transcript_override: str | None = None,
    risk_bucket_override: RiskBucket | None = None,
) -> VoiceNoteSubmitResponse:
    voice_note_id = f"vn_{uuid4().hex[:12]}"
    speechmatics_config = _speechmatics_config_for_campaign(campaign_type)
    try:
        speechmatics_result = await transcribe_with_config(
            source_path,
            speechmatics_config,
        )
    except Exception as exc:
        raise ExternalServiceError(
            "Speech transcription could not be completed.",
            service="speechmatics",
            details={"campaign_type": campaign_type},
        ) from exc
    transcript = transcript_override or speechmatics_result.transcript
    try:
        risk_bucket: RiskBucket = risk_bucket_override or await get_risk_level(source_path)
    except Exception as exc:
        raise ExternalServiceError(
            "Risk classification could not be completed.",
            service="thymia",
            details={"campaign_type": campaign_type},
        ) from exc
    created_at = datetime.now(timezone.utc)

    record = VoiceNoteRecord(
        voice_note_id=voice_note_id,
        customer_id=customer_id,
        campaign_type=campaign_type,
        transcript=transcript,
        risk_bucket=risk_bucket,
        created_at=created_at,
    )
    store["voice_notes"][voice_note_id] = record

    if log_to_tuner:
        await log_event(
            event_type="voice_note_submitted",
            customer_id=customer_id,
            campaign_type=campaign_type,
            risk_bucket=risk_bucket,
            transcript=transcript,
            audio_filename=audio_filename,
            source=source,
            speechmatics_language=speechmatics_result.language,
            speechmatics_domain=speechmatics_result.domain,
            speechmatics_output_locale=speechmatics_result.output_locale,
            speechmatics_mode=speechmatics_result.mode,
            speechmatics_diarization=speechmatics_config.enable_diarization,
            speechmatics_include_partials=speechmatics_config.include_partials,
            speechmatics_max_delay=speechmatics_config.max_delay,
        )

    return _serialize_voice_note(record)


async def seed_demo_voice_notes(
    store: dict[str, dict[str, object]],
    *,
    log_to_tuner: bool = True,
) -> list[VoiceNoteSubmitResponse]:
    seeded_notes: list[VoiceNoteSubmitResponse] = []

    for target in DEMO_VOICE_NOTE_TARGETS:
        exists = any(
            record.customer_id == target["customer_id"]
            and record.campaign_type == target["campaign_type"]
            for record in store["voice_notes"].values()
        )
        if exists:
            continue

        mock_path = str(TMP_DIR / f"seed_{target['customer_id']}_{target['campaign_type']}.wav")
        profile = DEMO_SIGNAL_PROFILES[target["campaign_type"]]
        seeded_notes.append(
            await _create_voice_note_record(
                customer_id=target["customer_id"],
                campaign_type=target["campaign_type"],
                source_path=mock_path,
                source="seed_demo",
                store=store,
                audio_filename="seed_demo.wav",
                log_to_tuner=log_to_tuner,
                transcript_override=profile["transcript"],
                risk_bucket_override=profile["risk_bucket"],  # type: ignore[arg-type]
            )
        )

    return seeded_notes


@router.post("/submit", response_model=VoiceNoteSubmitResponse)
async def submit_voice_note(
    customer_id: str = Form(...),
    campaign_type: CampaignType = Form(...),
    audio: UploadFile = File(...),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> VoiceNoteSubmitResponse:
    if not customer_id.strip():
        raise ValidationError("customer_id is required.")

    suffix = Path(audio.filename or "voice_note.wav").suffix or ".wav"
    temp_path = TMP_DIR / f"{customer_id}_{campaign_type}_{uuid4().hex[:12]}{suffix}"

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise ValidationError(
            "The uploaded voice note file was empty.",
            details={"filename": audio.filename},
        )
    _persist_temp_audio(temp_path, audio_bytes)
    try:
        return await _create_voice_note_record(
            customer_id=customer_id,
            campaign_type=campaign_type,
            source_path=str(temp_path),
            source="browser_recording",
            store=store,
            audio_filename=audio.filename,
        )
    finally:
        _remove_temp_audio(temp_path)


@router.post("/mock_submit", response_model=VoiceNoteSubmitResponse)
async def mock_submit_voice_note(
    payload: MockVoiceNoteSubmitRequest,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> VoiceNoteSubmitResponse:
    mock_path = str(TMP_DIR / f"mock_{payload.customer_id}_{payload.campaign_type}.wav")
    profile = DEMO_SIGNAL_PROFILES.get(payload.campaign_type)
    return await _create_voice_note_record(
        customer_id=payload.customer_id,
        campaign_type=payload.campaign_type,
        source_path=mock_path,
        source="demo_stub",
        store=store,
        audio_filename="demo_stub.wav",
        transcript_override=profile["transcript"] if profile else None,
        risk_bucket_override=profile["risk_bucket"] if profile else None,  # type: ignore[arg-type]
    )


@router.get("/recent", response_model=list[VoiceNoteSubmitResponse])
async def recent_voice_notes(
    limit: int = Query(default=5, ge=1, le=20),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> list[VoiceNoteSubmitResponse]:
    recent_records = sorted(
        store["voice_notes"].values(),
        key=lambda record: record.created_at,
        reverse=True,
    )[:limit]
    return [_serialize_voice_note(record) for record in recent_records]


@router.get("/latest", response_model=VoiceNoteSubmitResponse)
async def latest_voice_note(
    customer_id: str = Query(...),
    campaign_type: CampaignType = Query(...),
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> VoiceNoteSubmitResponse:
    matching_records = [
        record
        for record in store["voice_notes"].values()
        if record.customer_id == customer_id and record.campaign_type == campaign_type
    ]
    if not matching_records:
        raise NotFoundError(
            "Voice note not found.",
            details={"customer_id": customer_id, "campaign_type": campaign_type},
        )

    latest_record = max(matching_records, key=lambda record: record.created_at)
    return _serialize_voice_note(latest_record)


@router.post("/seed_demo", response_model=list[VoiceNoteSubmitResponse])
async def seed_demo_signal_notes(
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> list[VoiceNoteSubmitResponse]:
    return await seed_demo_voice_notes(store, log_to_tuner=True)


@router.get("/summary", response_model=VoiceNoteSummaryResponse)
async def voice_note_summary(
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> VoiceNoteSummaryResponse:
    risk_counts = {"low": 0, "medium": 0, "high": 0}
    campaign_counts = {
        "elderly_checkin": 0,
        "primary_care": 0,
        "mental_health": 0,
    }
    latest_created_at: datetime | None = None

    for record in store["voice_notes"].values():
        if record.risk_bucket in risk_counts:
            risk_counts[record.risk_bucket] += 1
        if record.campaign_type in campaign_counts:
            campaign_counts[record.campaign_type] += 1
        if latest_created_at is None or record.created_at > latest_created_at:
            latest_created_at = record.created_at

    return VoiceNoteSummaryResponse(
        total_notes=len(store["voice_notes"]),
        high_risk_count=risk_counts["high"],
        risk_counts=risk_counts,
        campaign_counts=campaign_counts,
        latest_created_at=latest_created_at,
    )
