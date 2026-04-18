from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile

from api.errors import FileStorageError, ValidationError
from api.types import CampaignType

router = APIRouter(prefix="/media", tags=["media"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_DIR = PROJECT_ROOT / "web" / "media" / "avatars"
BACKGROUND_DIR = PROJECT_ROOT / "web" / "media" / "backgrounds"


def _file_extension(upload: UploadFile, default: str = ".jpg") -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    return suffix or default


async def _validated_upload_bytes(upload: UploadFile, *, field_name: str) -> bytes:
    if upload.content_type and not upload.content_type.startswith("image/"):
        raise ValidationError(
            f"{field_name} must be an image upload.",
            details={"filename": upload.filename, "content_type": upload.content_type},
        )

    file_bytes = await upload.read()
    if not file_bytes:
        raise ValidationError(
            f"{field_name} upload was empty.",
            details={"filename": upload.filename},
        )
    return file_bytes


@router.post("/upload")
async def upload_media(
    campaign_type: CampaignType = Form(...),
    avatar: UploadFile = File(...),
    background: UploadFile = File(...),
) -> dict[str, str]:
    try:
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FileStorageError("Unable to prepare media upload directories.") from exc

    avatar_ext = _file_extension(avatar)
    background_ext = _file_extension(background)

    avatar_path = AVATAR_DIR / f"{campaign_type}{avatar_ext}"
    background_path = BACKGROUND_DIR / f"{campaign_type}{background_ext}"

    avatar_bytes = await _validated_upload_bytes(avatar, field_name="avatar")
    background_bytes = await _validated_upload_bytes(background, field_name="background")

    try:
        avatar_path.write_bytes(avatar_bytes)
        background_path.write_bytes(background_bytes)
    except OSError as exc:
        raise FileStorageError(
            "Unable to store uploaded media.",
            details={"campaign_type": campaign_type},
        ) from exc

    return {
        "campaign_type": campaign_type,
        "avatar_url": f"/web/media/avatars/{avatar_path.name}",
        "background_url": f"/web/media/backgrounds/{background_path.name}",
    }
