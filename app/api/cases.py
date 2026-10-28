"""Perfilamento (CONTRATOS_API §6.1): caso anônimo com `X-Case-Token`, comandos 202 e SSE.

Os casos ficam em memória (uma instância) até a camada de banco existir; o estado do grafo fica no
checkpointer do LangGraph (`thread_id = profile:{case_id}`).
"""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from langchain_core.messages import AIMessage
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from app.agents.profiling.runner import TurnRunner, thread_config
from app.api.sse import HEARTBEAT_S, EventBus
from app.domain.case import CaseSnapshot, ChatMessage, Progress, Recommendation
from app.domain.decision import Decision
from app.domain.facts import Fact, FactSource, FactStatus
from app.engine.catalog import Catalog
from app.engine.explain_graph import trail_graph
from app.engine.profile_cards import profile_cards
from app.engine.questions import build_question

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])
limiter = Limiter(key_func=get_remote_address)
GREETING = (
    "Oi! Eu sou a Lia, assistente virtual do Negócio de Pé. Vou te ajudar a entender qual seguro "
    "faz sentido para o seu negócio, com poucas perguntas."
)


@dataclass
class CaseRecord:
    token_hash: str
    created_at: datetime
    recommendation: Recommendation | None = None
    events: list[dict] = field(default_factory=list)  # pedidos de revisão e afins (auditoria)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _error(code: int, kind: str, message: str) -> HTTPException:
    return HTTPException(
        code, detail={"error": {"code": kind, "message": message, "retryable": False}}
    )


class CaseStore:
    def __init__(self) -> None:
        self._cases: dict[str, CaseRecord] = {}

    def create(self) -> tuple[str, str]:
        case_id, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
        self._cases[case_id] = CaseRecord(_hash(token), datetime.now(UTC))
        return case_id, token

    def get(self, case_id: str, token: str | None) -> CaseRecord:
        case = self._cases.get(case_id)
        if case is None or not hmac.compare_digest(case.token_hash, _hash(token or "")):
            raise _error(404, "case_not_found", "Caso não encontrado.")
        return case

    def record(self, case_id: str) -> CaseRecord:
        return self._cases[case_id]


def build_runner(app) -> TurnRunner:
    """Liga o executor de turnos ao EventBus e aos repositórios da aplicação."""

    def on_decision(case_id: str, decision: Decision, facts: dict[str, Fact]) -> None:
        case = app.state.cases.record(case_id)
        rec = Recommendation.from_decision(
            f"rec_{uuid.uuid4().hex[:12]}", case_id, decision, datetime.now(UTC)
        )
        case.recommendation = rec
        app.state.offers.save_hashed(case_id, case.token_hash, decision.offers, facts)
        app.state.bus.publish(case_id, "recommendation.ready", rec.model_dump(mode="json"))

    return TurnRunner(
        app.state.profile_graph, app.state.catalog, app.state.bus.publish, on_decision
    )


def get_store(request: Request) -> CaseStore:
    return request.app.state.cases


def get_runner(request: Request) -> TurnRunner:
    return request.app.state.runner


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus


def get_catalog(request: Request) -> Catalog:
    return request.app.state.catalog


CaseToken = Annotated[str | None, Header(alias="X-Case-Token")]
Store = Annotated[CaseStore, Depends(get_store)]
Runner = Annotated[TurnRunner, Depends(get_runner)]
Bus = Annotated[EventBus, Depends(get_bus)]
CatalogDep = Annotated[Catalog, Depends(get_catalog)]


class CreateCase(BaseModel):
    consent: bool
    channel: str = "web"


class CreatedCase(BaseModel):
    case_id: str
    case_token: str


class UserText(BaseModel):
    text: str


class FactValue(BaseModel):
    value: Any


class ReviewRequest(BaseModel):
    reason: str


class Accepted(BaseModel):
    turn_id: str


def _turn_id() -> str:
    return f"turn_{uuid.uuid4().hex[:12]}"


def _user_fact(key: str, value: Any, status_: FactStatus) -> Fact:
    """Resposta direta, edição ou confirmação de card: origem `usuario_resposta`."""
    try:
        return Fact(
            key=key,
            value=value,
            status=status_,
            source=FactSource.USUARIO_RESPOSTA,
            confidence=1.0,
            updated_at=datetime.now(UTC),
            updated_by="usuario",
        )
    except ValueError as error:
        raise _error(422, "invalid_fact", str(error)) from None


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreatedCase)
@limiter.limit("30/minute")
async def create_case(
    request: Request, body: CreateCase, store: Store, bus: Bus, catalog: CatalogDep
):
    if not body.consent:
        raise _error(422, "consent_required", "É preciso consentir com o uso dos dados.")
    case_id, token = store.create()
    question = build_question("cnae_principal", 0.5, None, 0.0, catalog)
    await request.app.state.profile_graph.aupdate_state(
        thread_config(case_id),
        {"case_id": case_id, "messages": [AIMessage(GREETING)], "question": question},
        as_node="ask",
    )
    bus.publish(
        case_id,
        "message.completed",
        {"message_id": "msg_greeting", "role": "assistant", "text": GREETING},
    )
    bus.publish(case_id, "question", question.model_dump(mode="json"))
    bus.publish(case_id, "avatar.state", {"state": "ouvindo"})
    return CreatedCase(case_id=case_id, case_token=token)


@router.get("/{case_id}", response_model=CaseSnapshot)
async def get_case(
    case_id: str, store: Store, runner: Runner, catalog: CatalogDep, x_case_token: CaseToken = None
) -> CaseSnapshot:
    case = store.get(case_id, x_case_token)
    state = await runner.state(case_id)
    decision: Decision | None = state.get("decision")
    question = state.get("question")
    messages = [
        ChatMessage(
            message_id=f"m{i}", role="user" if m.type == "human" else "assistant", text=m.content
        )
        for i, m in enumerate(state.get("messages", []))
        if m.type in ("human", "ai")
    ]
    if decision is not None and decision.eligibility.status == "inelegivel":
        case_status, avatar = "inelegivel", "preocupado"
    elif case.recommendation is not None and question is None:
        case_status, avatar = "recomendado", "explicando"
    else:
        case_status, avatar = "em_perfilamento", "ouvindo"
    pending = len(decision.eligibility.pending) if decision else 1
    return CaseSnapshot(
        case_id=case_id,
        status=case_status,
        cards=profile_cards(state.get("facts", {}), catalog),
        question=question,
        messages=messages,
        eligibility=decision.eligibility if decision else None,
        recommendation=case.recommendation,
        avatar_state=avatar,
        progress=Progress(
            answered=state.get("questions_asked", 0),
            estimated_remaining=0 if question is None else max(1, pending),
        ),
    )


@router.get("/{case_id}/events")
async def case_events(
    case_id: str,
    store: Store,
    bus: Bus,
    x_case_token: CaseToken = None,
    last_event_id: Annotated[int, Header(alias="Last-Event-ID")] = 0,
    follow: bool = True,
) -> EventSourceResponse:
    """`text/event-stream`. Retoma a partir de `Last-Event-ID`. `follow=false` devolve só o
    histórico e fecha (diagnóstico e testes)."""
    store.get(case_id, x_case_token)

    async def stream():
        if not follow:
            for item in bus.history(case_id, last_event_id):
                yield item.as_sse()
            return
        async for item in bus.subscribe(case_id, last_event_id):
            yield item.as_sse()

    return EventSourceResponse(stream(), ping=HEARTBEAT_S)


@router.post("/{case_id}/messages", status_code=status.HTTP_202_ACCEPTED, response_model=Accepted)
@limiter.limit("60/minute")
async def post_message(
    request: Request,
    case_id: str,
    body: UserText,
    background: BackgroundTasks,
    store: Store,
    runner: Runner,
    x_case_token: CaseToken = None,
) -> Accepted:
    store.get(case_id, x_case_token)
    turn_id = _turn_id()
    background.add_task(runner.run, case_id, turn_id, {"messages": [("user", body.text)]})
    return Accepted(turn_id=turn_id)


def _fact_command(
    case_id: str, fact: Fact, background: BackgroundTasks, runner: TurnRunner
) -> Accepted:
    turn_id = _turn_id()
    background.add_task(runner.run, case_id, turn_id, {"facts": {fact.key: fact}})
    return Accepted(turn_id=turn_id)


@router.put("/{case_id}/facts/{key}", status_code=status.HTTP_202_ACCEPTED, response_model=Accepted)
async def put_fact(
    case_id: str,
    key: str,
    body: FactValue,
    background: BackgroundTasks,
    store: Store,
    runner: Runner,
    x_case_token: CaseToken = None,
) -> Accepted:
    """Resposta direta ou edição de card: estado `confirmado`, origem `usuario_resposta`."""
    store.get(case_id, x_case_token)
    return _fact_command(
        case_id, _user_fact(key, body.value, FactStatus.CONFIRMADO), background, runner
    )


@router.post(
    "/{case_id}/facts/{key}/confirm", status_code=status.HTTP_202_ACCEPTED, response_model=Accepted
)
async def confirm_fact(
    case_id: str,
    key: str,
    background: BackgroundTasks,
    store: Store,
    runner: Runner,
    x_case_token: CaseToken = None,
) -> Accepted:
    """Confirma o card `observado`/`estimado` com o valor atual."""
    store.get(case_id, x_case_token)
    current = (await runner.state(case_id)).get("facts", {}).get(key)
    if current is None or current.value is None:
        raise _error(404, "fact_not_found", "Não há valor para confirmar.")
    return _fact_command(
        case_id, _user_fact(key, current.value, FactStatus.CONFIRMADO), background, runner
    )


@router.delete(
    "/{case_id}/facts/{key}", status_code=status.HTTP_202_ACCEPTED, response_model=Accepted
)
async def reject_fact(
    case_id: str,
    key: str,
    background: BackgroundTasks,
    store: Store,
    runner: Runner,
    x_case_token: CaseToken = None,
) -> Accepted:
    """Usuário remove o card: estado `rejeitado`; o grafo não volta a sugerir o valor."""
    store.get(case_id, x_case_token)
    return _fact_command(case_id, _user_fact(key, None, FactStatus.REJEITADO), background, runner)


@router.get("/{case_id}/recommendation", response_model=Recommendation)
async def get_recommendation(
    case_id: str, store: Store, x_case_token: CaseToken = None
) -> Recommendation:
    case = store.get(case_id, x_case_token)
    if case.recommendation is None:
        raise _error(404, "recommendation_not_ready", "A recomendação ainda não está pronta.")
    return case.recommendation


@router.get("/{case_id}/explain-graph")
async def explain_graph(
    case_id: str, store: Store, offer_id: str = "of_1", x_case_token: CaseToken = None
) -> dict:
    case = store.get(case_id, x_case_token)
    offers = {o.offer_id: o for o in (case.recommendation.offers if case.recommendation else [])}
    if offer_id not in offers:
        raise _error(404, "offer_not_found", "Oferta não encontrada.")
    return trail_graph(offers[offer_id])


@router.post("/{case_id}/review-request", status_code=status.HTTP_202_ACCEPTED)
async def review_request(
    case_id: str, body: ReviewRequest, store: Store, x_case_token: CaseToken = None
) -> dict:
    """Pedido de revisão humana da decisão automatizada (LGPD art. 20), registrado na auditoria."""
    case = store.get(case_id, x_case_token)
    case.events.append(
        {"type": "review_requested", "reason": body.reason, "ts": datetime.now(UTC).isoformat()}
    )
    return {"accepted": True}
