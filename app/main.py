from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.agents.profiling.graph import build_graph
from app.api import cases, offers
from app.api.sse import EventBus
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
    app.state.cases = cases.CaseStore()
    app.state.bus = EventBus()
    # Checkpointer em memória (uma instância); AsyncPostgresSaver entra com a camada de banco.
    app.state.profile_graph = build_graph().compile(checkpointer=InMemorySaver())
    app.state.runner = cases.build_runner(app)
    app.state.limiter = cases.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(cases.router)
    app.include_router(offers.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
