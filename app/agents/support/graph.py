"""Sistema 2 — atendimento (WhatsApp): triagem e roteamento para especialistas."""

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.agents import llm
from app.agents.prompts import load_prompt
from app.agents.support.state import Intent, SupportState
from app.config import get_settings


class Triage(BaseModel):
    intent: Intent


async def triage(state: SupportState) -> dict:
    result = await llm.parse(
        model=get_settings().llm_model_fast,
        system=load_prompt("support", "triage"),
        messages=llm.to_api_messages(state.get("messages", [])),
        output_format=Triage,
    )
    # Recusa na triagem → atendente humano.
    return {"intent": result.intent if result else "humano"}


async def answer(state: SupportState) -> dict:
    writer = get_stream_writer()
    chunks = []
    async for text in llm.stream_text(
        model=get_settings().llm_model_main,
        system=load_prompt("support", "answer"),
        messages=llm.to_api_messages(
            state.get("messages", []), f"Intenção classificada: {state.get('intent')}."
        ),
    ):
        chunks.append(text)
        writer(
            {
                "event": "message.delta",
                "conversation_id": state.get("conversation_id"),
                "text": text,
            }
        )
    return {"messages": [AIMessage("".join(chunks))]}


async def handoff(state: SupportState) -> dict:
    # TODO: escalonamento humano com interrupt().
    return {}


def route(state: SupportState) -> str:
    return "handoff" if state.get("intent") == "humano" else "answer"


def build_graph() -> StateGraph:
    builder = StateGraph(SupportState)
    builder.add_node("triage", triage)
    builder.add_node("answer", answer)
    builder.add_node("handoff", handoff)
    builder.add_edge(START, "triage")
    builder.add_conditional_edges("triage", route, ["answer", "handoff"])
    builder.add_edge("answer", END)
    builder.add_edge("handoff", END)
    return builder


graph = build_graph().compile()
