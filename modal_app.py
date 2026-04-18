"""Modal deployment entrypoint for Signal Over Noise."""

from __future__ import annotations

try:
    import modal
except ImportError:  # pragma: no cover - optional dependency for serverless deployment
    modal = None

APP_NAME = "signal-over-noise"

if modal is not None:
    image = modal.Image.debian_slim(python_version="3.11").pip_install_from_requirements(
        "requirements.txt"
    )
    modal_app = modal.App(APP_NAME)

    @modal_app.function(
        image=image,
        timeout=900,
        allow_concurrent_inputs=50,
    )
    @modal.asgi_app()
    def fastapi_asgi():
        from api.main import app

        return app
else:
    modal_app = None

    def fastapi_asgi():
        raise RuntimeError(
            "Modal is not installed. Install it with `pip install modal` and run "
            "`modal deploy modal_app.py` to serve Signal Over Noise on Modal."
        )
