import hashlib
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel


class TwilioMessageCreateRequest(BaseModel):
    to: str
    body: str
    messaging_service_sid: Optional[str] = None
    from_number: Optional[str] = None
    status_callback: Optional[str] = None


class TwilioMessageResource(BaseModel):
    sid: str
    account_sid: str
    api_version: str = "2010-04-01"
    body: str
    date_created: str
    date_sent: str
    date_updated: str
    direction: str = "outbound-api"
    error_code: str | None = None
    error_message: str | None = None
    from_number: str | None = None
    messaging_service_sid: str | None = None
    num_media: str = "0"
    num_segments: str = "1"
    price: str | None = None
    price_unit: str | None = None
    status: str
    to: str
    uri: str
    provider: str = "twilio"
    channel: str = "sms"
    status_callback: str | None = None


class TwilioStatusCallbackPayload(BaseModel):
    MessageSid: str
    MessageStatus: str
    AccountSid: str
    To: str
    From: str | None = None
    ErrorCode: str | None = None
    SmsSid: str | None = None
    SmsStatus: str | None = None
    RawDlrDoneDate: str | None = None


def _now_rfc2822() -> str:
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


async def send_sms(
    *,
    to_phone_number: str,
    body: str,
    messaging_service_sid: Optional[str] = None,
    from_phone_number: Optional[str] = None,
    status_callback_url: Optional[str] = None,
) -> TwilioMessageResource:
    TwilioMessageCreateRequest(
        to=to_phone_number,
        body=body,
        messaging_service_sid=messaging_service_sid,
        from_number=from_phone_number,
        status_callback=status_callback_url,
    )

    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "AC" + "a" * 32)
    resolved_from = from_phone_number or os.getenv("TWILIO_FROM_NUMBER")
    resolved_service_sid = messaging_service_sid or os.getenv("TWILIO_MESSAGING_SERVICE_SID")
    digest = hashlib.sha256(
        f"{to_phone_number}:{body}:{uuid4().hex}".encode("utf-8")
    ).hexdigest()[:32]
    message_sid = f"SM{digest.upper()}"
    base_path = f"/2010-04-01/Accounts/{account_sid}/Messages/{message_sid}.json"
    timestamp = _now_rfc2822()

    return TwilioMessageResource(
        sid=message_sid,
        account_sid=account_sid,
        body=body,
        date_created=timestamp,
        date_sent=timestamp,
        date_updated=timestamp,
        from_number=resolved_from,
        messaging_service_sid=resolved_service_sid,
        status="queued",
        to=to_phone_number,
        uri=base_path,
        status_callback=status_callback_url,
    )


def build_status_callback_payload(
    *,
    message: TwilioMessageResource,
    status: str,
    error_code: str | None = None,
    raw_dlr_done_date: str | None = None,
) -> TwilioStatusCallbackPayload:
    return TwilioStatusCallbackPayload(
        MessageSid=message.sid,
        MessageStatus=status,
        AccountSid=message.account_sid,
        To=message.to,
        From=message.from_number,
        ErrorCode=error_code,
        SmsSid=message.sid,
        SmsStatus=status,
        RawDlrDoneDate=raw_dlr_done_date,
    )
