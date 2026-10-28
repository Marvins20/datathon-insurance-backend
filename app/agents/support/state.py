from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

Intent = Literal["duvida", "contratacao", "sinistro", "humano"]


class SupportState(TypedDict, total=False):
    """CONTRATOS_API §4 — campos serão completados conforme o atendimento for implementado."""

    conversation_id: str
    phone_e164: str
    messages: Annotated[list, add_messages]
    intent: Intent | None
