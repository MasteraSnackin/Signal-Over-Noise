from typing import Literal


CampaignType = Literal["elderly_checkin", "primary_care", "mental_health"]
ReviewOutcome = Literal["escalated", "routine_followup", "closed"]
TwilioDeliveryStatus = Literal["queued", "sent", "delivered", "undelivered", "failed"]
