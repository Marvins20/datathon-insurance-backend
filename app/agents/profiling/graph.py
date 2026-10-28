"""Sistema 1 — perfilamento: extrai fatos, consulta o context graph e escolhe a próxima pergunta."""

from datetime import UTC, datetime
from functools import cache

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.agents import llm
from app.agents.profiling.state import ProfileState
from app.agents.prompts import load_prompt
from app.config import get_settings
from app.domain.fact_keys import FactKey, fact_keys
from app.domain.facts import Fact, FactSource, FactStatus
from app.engine.catalog import default_catalog
from app.engine.decide import decide
from app.engine.questions import next_question
from app.graph.reasoner import hypotheses
from app.graph.store import GraphSnapshot, load_snapshot


@cache
def _snapshot() -> GraphSnapshot:
    return load_snapshot()


def _describe(spec: FactKey) -> str:
    kind = f"{spec.type}: {', '.join(spec.options)}" if spec.options else spec.type
    pattern = f", formato {spec.pattern}" if spec.pattern else ""
    return f"- {spec.key} ({kind}{pattern}) — {spec.label}"


@cache
def _extractor_system() -> str:
    """Prompt + lista de chaves extraíveis: estável, então fica no system e entra no cache."""
    keys = "\n".join(_describe(s) for s in fact_keys().values() if s.extractable)
    return f"{load_prompt('profiling', 'extractor')}\n\n## Chaves permitidas\n\n{keys}\n"


# Confiança de fato declarado em texto livre (peso de "observado" em CONTRATOS_API §9.5).
TEXT_CONFIDENCE = 0.8


class ExtractedFact(BaseModel):
    key: str
    value: bool | int | float | str | list[str]
    evidence: str


class Extraction(BaseModel):
    facts: list[ExtractedFact]


async def extract(state: ProfileState) -> dict:
    messages = state.get("messages", [])
    if not messages or messages[-1].type != "human":
        return {}
    result = await llm.parse(
        model=get_settings().llm_model_fast,
        system=_extractor_system(),
        messages=llm.to_api_messages(messages),
        output_format=Extraction,
    )
    if result is None:
        return {}
    now = datetime.now(UTC)
    facts = {}
    for f in result.facts:
        spec = fact_keys().get(f.key)
        # Chave inexistente, não extraível ou valor fora do tipo → descartado.
        if spec is None or not spec.extractable:
            continue
        try:
            facts[f.key] = Fact(
                key=f.key,
                value=f.value,
                status=FactStatus.OBSERVADO,
                source=FactSource.USUARIO_TEXTO,
                confidence=TEXT_CONFIDENCE,
                evidence=f.evidence,
                updated_at=now,
                updated_by="agente:extrator",
            )
        except ValueError:
            continue
    return {"facts": facts}


def reason(state: ProfileState) -> dict:
    """Motor determinístico: decisão e próxima pergunta. O LLM nunca decide nada aqui."""
    snapshot, catalog = _snapshot(), default_catalog()
    facts = state.get("facts", {})
    decision = decide(facts, catalog, snapshot)
    question = next_question(
        facts, catalog, snapshot, asked=state.get("questions_asked", 0), decision=decision
    )
    cnae_fact = facts.get("cnae_principal")
    cnae = cnae_fact.value if cnae_fact and cnae_fact.is_known else None
    return {
        "decision": decision,
        "question": question,
        "hypotheses": hypotheses(snapshot, cnae) if cnae else {},
        "kg_version": snapshot.version,
    }


def interviewer_context(state: ProfileState) -> str:
    """Estado volátil para o Entrevistador: o que o motor decidiu que deve ser dito."""
    decision, question = state.get("decision"), state.get("question")
    if decision is not None and decision.eligibility.status == "inelegivel":
        reasons = "; ".join(b.description for b in decision.eligibility.blockers)
        redirect = decision.eligibility.redirect_to
        extra = f" Indique o {redirect}." if redirect else ""
        return f"Caso inelegível: {reasons}. Explique com empatia, sem oferecer produto.{extra}"
    if question is None:
        return "Sem pergunta indicada."
    return (
        f"Pergunta indicada ({question.style}): {question.prompt}\n"
        f"Por que perguntamos: {question.reason}"
    )


async def ask(state: ProfileState) -> dict:
    question = state.get("question")
    context = interviewer_context(state)

    writer = get_stream_writer()
    chunks = []
    async for text in llm.stream_text(
        model=get_settings().llm_model_main,
        system=load_prompt("profiling", "interviewer"),
        messages=llm.to_api_messages(state.get("messages", []), context),
    ):
        chunks.append(text)
        writer({"event": "message.delta", "case_id": state.get("case_id"), "text": text})
    asked = state.get("questions_asked", 0) + (question is not None)
    return {"questions_asked": asked, "messages": [AIMessage("".join(chunks))]}


def build_graph() -> StateGraph:
    builder = StateGraph(ProfileState)
    builder.add_node("extract", extract)
    builder.add_node("reason", reason)
    builder.add_node("ask", ask)
    builder.add_edge(START, "extract")
    builder.add_edge("extract", "reason")
    builder.add_edge("reason", "ask")
    builder.add_edge("ask", END)
    return builder


# Sem checkpointer para o LangGraph Studio; a API compila com AsyncPostgresSaver.
graph = build_graph().compile()
