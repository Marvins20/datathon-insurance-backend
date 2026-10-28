from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import offers
from app.config import get_settings
from app.engine.catalog import default_catalog
from app.graph.store import load_snapshot


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Negócio de Pé API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.catalog = default_catalog()
    app.state.graph = load_snapshot()
    app.state.offers = offers.OfferRepository()
    app.include_router(offers.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
