from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.util import find_spec
from pathlib import Path
from uuid import uuid4

from api.errors import ApplicationError, ValidationError
from api.routes.video import (
    CreateVideoJobRequest,
    SendOutreachRequest,
    _create_and_store_video_job,
    _send_outreach_delivery,
)
from api.types import CampaignType
from db import AutomationRunRecord

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODAL_APP_PATH = PROJECT_ROOT / "modal_app.py"


@dataclass
class BatchOutreachRecipientSpec:
    customer_id: str
    name: str
    campaign_type: CampaignType
    plan: str | None = None
    days_to_expiry: int | None = None
    phone_number: str | None = None
    avatar_image_url: str | None = None
    background_image_url: str | None = None


def modal_python_available() -> bool:
    return find_spec("modal") is not None


def modal_scaffold_present() -> bool:
    return MODAL_APP_PATH.exists()


def modal_serverless_ready() -> bool:
    return modal_scaffold_present() and modal_python_available()


async def execute_batch_outreach_run(
    *,
    store: dict[str, dict[str, object]],
    recipients: list[BatchOutreachRecipientSpec],
    send_sms: bool,
    source: str,
    execution_mode: str = "local",
) -> AutomationRunRecord:
    started_at = datetime.now(timezone.utc)
    run = AutomationRunRecord(
        run_id=f"run_{uuid4().hex[:12]}",
        execution_mode=execution_mode,
        source=source,
        send_sms=send_sms,
        status="running",
        total_recipients=len(recipients),
        processed_recipients=0,
        created_jobs=0,
        created_deliveries=0,
        error_count=0,
        results=[],
        started_at=started_at,
    )
    store["automation_runs"][run.run_id] = run

    for recipient in recipients:
        result = {
            "customer_id": recipient.customer_id,
            "name": recipient.name,
            "campaign_type": recipient.campaign_type,
            "status": "failed",
            "video_job_id": None,
            "delivery_id": None,
            "delivery_status": None,
            "error_message": None,
        }

        try:
            if not recipient.customer_id.strip():
                raise ValidationError("customer_id is required for batch recipients.")
            if not recipient.name.strip():
                raise ValidationError("name is required for batch recipients.")

            create_request = CreateVideoJobRequest(
                customer_id=recipient.customer_id,
                name=recipient.name,
                plan=recipient.plan,
                days_to_expiry=recipient.days_to_expiry,
                campaign_type=recipient.campaign_type,
                avatar_image_url=recipient.avatar_image_url,
                background_image_url=recipient.background_image_url,
            )
            video_job = await _create_and_store_video_job(create_request, store)
            result["video_job_id"] = video_job.job_id
            run.created_jobs += 1

            if send_sms:
                if not recipient.phone_number or not recipient.phone_number.strip():
                    raise ValidationError(
                        "phone_number is required when send_sms is enabled.",
                        details={"customer_id": recipient.customer_id},
                    )

                send_request = SendOutreachRequest(
                    customer_id=recipient.customer_id,
                    campaign_type=recipient.campaign_type,
                    phone_number=recipient.phone_number,
                )
                delivery = await _send_outreach_delivery(payload=send_request, store=store)
                result["delivery_id"] = delivery.delivery_id
                result["delivery_status"] = delivery.status
                run.created_deliveries += 1

            result["status"] = "completed"
            run.processed_recipients += 1
        except ApplicationError as exc:
            run.error_count += 1
            result["error_message"] = exc.message
        except Exception:
            run.error_count += 1
            result["error_message"] = "Unexpected error while processing the batch recipient."

        run.results.append(result)

    run.completed_at = datetime.now(timezone.utc)
    if run.error_count == 0:
        run.status = "completed"
    elif run.processed_recipients == 0:
        run.status = "failed"
    else:
        run.status = "completed_with_errors"

    return run
