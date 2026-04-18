# signal-over-noise/
# ├── api/
# │   ├── __init__.py
# │   ├── main.py
# │   ├── routes/
# │   │   ├── video.py
# │   │   ├── voice_note.py
# │   │   └── media.py
# │   └── services/
# │       ├── seedance.py
# │       ├── speechmatics.py
# │       ├── thymia.py
# │       ├── tuner.py
# │       └── tinyfish.py
# ├── web/
# │   ├── index.html
# │   ├── upload.html
# │   └── js/
# │       └── record.js
# ├── db.py
# ├── .env.example
# ├── .env
# ├── requirements.txt
# └── Dockerfile

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from api.errors import register_exception_handlers
from api.routes.automation import router as automation_router
from api.routes.media import router as media_router
from api.routes.video import DEMO_VIDEO_SCENARIOS
from api.routes.video import public_router as video_public_router
from api.routes.video import router as video_router
from api.routes.video import seed_demo_video_jobs
from api.routes.voice_note import router as voice_note_router
from db import get_db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_ROOT / "web"

load_dotenv(PROJECT_ROOT / ".env")


@asynccontextmanager
async def lifespan(_: FastAPI):
    store = await get_db()
    await seed_demo_video_jobs(store)
    yield


app = FastAPI(title="Signal Over Noise", lifespan=lifespan)
register_exception_handlers(app)
api_router = APIRouter()
api_router.include_router(automation_router)
api_router.include_router(media_router)
api_router.include_router(video_router)
api_router.include_router(voice_note_router)

app.include_router(api_router, prefix="/api/v1")
app.include_router(video_public_router)
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")


@app.get("/")
async def root() -> dict[str, object]:
    return {
        "message": "Welcome to Signal Over Noise.",
        "demo_console_url": "/web/upload.html",
        "seeded_demo_pages": [
            {
                "customer_id": scenario["customer_id"],
                "campaign_type": scenario["campaign_type"],
                "video_page_url": (
                    f"/video_page?customer_id={scenario['customer_id']}"
                    f"&campaign_type={scenario['campaign_type']}"
                ),
            }
            for scenario in DEMO_VIDEO_SCENARIOS
        ],
    }


@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
