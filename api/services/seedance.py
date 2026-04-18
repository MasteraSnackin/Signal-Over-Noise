import hashlib
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel


class SeedanceRequest(BaseModel):
    script: str
    voice_tone: str = "calm"
    avatar_style: str = "neutral"
    background_style: str = "simple"
    avatar_image_url: Optional[str] = None
    background_image_url: Optional[str] = None


class SeedanceResponse(BaseModel):
    job_id: str
    video_url: str
    thumbnail_url: str


async def generate_video(
    script: str,
    voice_tone: str = "calm",
    avatar_style: str = "neutral",
    background_style: str = "simple",
    avatar_image_url: Optional[str] = None,
    background_image_url: Optional[str] = None,
) -> SeedanceResponse:
    SeedanceRequest(
        script=script,
        voice_tone=voice_tone,
        avatar_style=avatar_style,
        background_style=background_style,
        avatar_image_url=avatar_image_url,
        background_image_url=background_image_url,
    )

    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()[:12]
    job_id = f"seed_{digest}_{uuid4().hex[:6]}"
    base_url = "https://demo.signal-over-noise.com/videos"

    return SeedanceResponse(
        job_id=job_id,
        video_url=f"{base_url}/{job_id}.mp4",
        thumbnail_url=f"{base_url}/{job_id}.jpg",
    )
