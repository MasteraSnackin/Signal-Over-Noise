from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.errors import ExternalServiceError, NotFoundError
from api.routes.video import DEMO_VIDEO_SCENARIOS
from api.services.automation import (
    BatchOutreachRecipientSpec,
    execute_batch_outreach_run,
    modal_python_available,
    modal_scaffold_present,
    modal_serverless_ready,
)
from api.types import CampaignType
from db import AutomationRunRecord, get_db

ExecutionMode = Literal["local", "modal"]

router = APIRouter(prefix="/automation", tags=["automation"])


class BatchOutreachRecipientRequest(BaseModel):
    customer_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    campaign_type: CampaignType
    plan: str | None = None
    days_to_expiry: int | None = Field(default=None, ge=0)
    phone_number: str | None = None
    avatar_image_url: str | None = None
    background_image_url: str | None = None


class BatchOutreachRecipientResultResponse(BaseModel):
    customer_id: str
    name: str
    campaign_type: CampaignType
    status: str
    video_job_id: str | None = None
    delivery_id: str | None = None
    delivery_status: str | None = None
    error_message: str | None = None


class BatchOutreachRunRequest(BaseModel):
    execution_mode: ExecutionMode = "local"
    send_sms: bool = False
    source: str = Field(default="care_ops_batch", min_length=1)
    recipients: list[BatchOutreachRecipientRequest] = Field(min_length=1, max_length=25)


class DemoBatchOutreachRequest(BaseModel):
    execution_mode: ExecutionMode = "local"
    send_sms: bool = False
    source: str = Field(default="demo_daily_batch", min_length=1)
    phone_number: str | None = None


class AutomationRunResponse(BaseModel):
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
    results: list[BatchOutreachRecipientResultResponse]
    started_at: datetime
    completed_at: datetime | None = None


class AutomationCapabilitiesResponse(BaseModel):
    default_execution_mode: ExecutionMode
    supported_execution_modes: list[ExecutionMode]
    modal_scaffold_present: bool
    modal_python_available: bool
    modal_serverless_ready: bool
    live_endpoints: list[str]
    next_feature: str


def _serialize_automation_run(record: AutomationRunRecord) -> AutomationRunResponse:
    return AutomationRunResponse(
        run_id=record.run_id,
        execution_mode=record.execution_mode,
        source=record.source,
        send_sms=record.send_sms,
        status=record.status,
        total_recipients=record.total_recipients,
        processed_recipients=record.processed_recipients,
        created_jobs=record.created_jobs,
        created_deliveries=record.created_deliveries,
        error_count=record.error_count,
        results=[
            BatchOutreachRecipientResultResponse.model_validate(result)
            for result in record.results
        ],
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


def _ensure_execution_mode_available(execution_mode: ExecutionMode) -> None:
    if execution_mode == "modal" and not modal_serverless_ready():
        raise ExternalServiceError(
            "Modal serverless execution is not configured in this environment.",
            service="modal",
            details={
                "modal_scaffold_present": modal_scaffold_present(),
                "modal_python_available": modal_python_available(),
            },
        )


def _demo_recipients(payload: DemoBatchOutreachRequest) -> list[BatchOutreachRecipientSpec]:
    return [
        BatchOutreachRecipientSpec(
            customer_id=scenario["customer_id"],
            name=scenario["name"],
            campaign_type=scenario["campaign_type"],
            plan=scenario.get("plan"),
            days_to_expiry=scenario.get("days_to_expiry"),
            phone_number=payload.phone_number,
            avatar_image_url=scenario.get("avatar_image_url"),
            background_image_url=scenario.get("background_image_url"),
        )
        for scenario in DEMO_VIDEO_SCENARIOS
    ]


@router.get("/capabilities", response_model=AutomationCapabilitiesResponse)
async def automation_capabilities() -> AutomationCapabilitiesResponse:
    supported_execution_modes: list[ExecutionMode] = ["local"]
    if modal_serverless_ready():
        supported_execution_modes.append("modal")

    return AutomationCapabilitiesResponse(
        default_execution_mode="local",
        supported_execution_modes=supported_execution_modes,
        modal_scaffold_present=modal_scaffold_present(),
        modal_python_available=modal_python_available(),
        modal_serverless_ready=modal_serverless_ready(),
        live_endpoints=[
            "/api/v1/automation/capabilities",
            "/api/v1/automation/batch_outreach",
            "/api/v1/automation/demo_batch_outreach",
            "/api/v1/automation/runs/{run_id}",
        ],
        next_feature="batch_outreach_serverless_orchestration",
    )


@router.post("/batch_outreach", response_model=AutomationRunResponse)
async def batch_outreach(
    payload: BatchOutreachRunRequest,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> AutomationRunResponse:
    _ensure_execution_mode_available(payload.execution_mode)
    run = await execute_batch_outreach_run(
        store=store,
        recipients=[
            BatchOutreachRecipientSpec(
                customer_id=recipient.customer_id,
                name=recipient.name,
                campaign_type=recipient.campaign_type,
                plan=recipient.plan,
                days_to_expiry=recipient.days_to_expiry,
                phone_number=recipient.phone_number,
                avatar_image_url=recipient.avatar_image_url,
                background_image_url=recipient.background_image_url,
            )
            for recipient in payload.recipients
        ],
        send_sms=payload.send_sms,
        source=payload.source,
        execution_mode=payload.execution_mode,
    )
    return _serialize_automation_run(run)


@router.post("/demo_batch_outreach", response_model=AutomationRunResponse)
async def demo_batch_outreach(
    payload: DemoBatchOutreachRequest,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> AutomationRunResponse:
    _ensure_execution_mode_available(payload.execution_mode)
    run = await execute_batch_outreach_run(
        store=store,
        recipients=_demo_recipients(payload),
        send_sms=payload.send_sms,
        source=payload.source,
        execution_mode=payload.execution_mode,
    )
    return _serialize_automation_run(run)


@router.get("/runs/{run_id}", response_model=AutomationRunResponse)
async def automation_run_status(
    run_id: str,
    store: dict[str, dict[str, object]] = Depends(get_db),
) -> AutomationRunResponse:
    record = store["automation_runs"].get(run_id)
    if not isinstance(record, AutomationRunRecord):
        raise NotFoundError(
            "Automation run not found.",
            details={"run_id": run_id},
        )
    return _serialize_automation_run(record)
