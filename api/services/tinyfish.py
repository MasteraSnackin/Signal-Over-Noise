async def trigger_video_job(
    customer_id: str,
    name: str,
    campaign_type: str,
) -> dict[str, str | int | None]:
    return {
        "customer_id": customer_id,
        "name": name,
        "plan": "demo_follow_up",
        "days_to_expiry": 7,
        "campaign_type": campaign_type,
        "avatar_image_url": None,
        "background_image_url": None,
    }
