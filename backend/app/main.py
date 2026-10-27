"""FastAPI entry point for the Bugsy development scaffold."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.analyses import router as analyses_router
from app.api.routes.requirements import router as requirements_router
from app.api.routes.verifications import router as verifications_router
from app.config import Settings, settings


def create_app(config: Settings = settings) -> FastAPI:
    app = FastAPI(title="Bugsy API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type"],
    )

    app.include_router(requirements_router, prefix="/api/v1")
    app.include_router(analyses_router, prefix="/api/v1")
    app.include_router(verifications_router, prefix="/api/v1")

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Report API process health only; this does not probe ConTree."""
        return {"status": "ok", "service": "bugsy-api"}

    return app


app = create_app()
