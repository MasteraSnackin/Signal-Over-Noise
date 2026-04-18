import logging
from datetime import datetime, timezone
from typing import Any, Optional

from api.services.thymia import RiskBucket

logger = logging.getLogger("signal_over_noise.tuner")
EVENT_LOGS: list[dict[str, Any]] = []


async def log_event(
    event_type: str,
    customer_id: str,
    campaign_type: str,
    risk_bucket: Optional[RiskBucket] = None,
    **extra: Any,
) -> None:
    event = {
        "event_type": event_type,
        "customer_id": customer_id,
        "campaign_type": campaign_type,
        "risk_bucket": risk_bucket,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "extra": extra,
    }
    EVENT_LOGS.append(event)
    logger.info("Tuner stub event: %s", event)
