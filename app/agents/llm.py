"""Único ponto de chamada ao Claude."""

from collections.abc import AsyncIterator
from functools import cache
from typing import Literal

from anthropic import AsyncAnthropic
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.config import get_settings

Effort = Literal["low", "medium"]
REFUSAL = "Desculpe, não consigo ajudar com isso por aqui. Posso seguir com outra pergunta?"


@cache
def get_client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=get_settings().anthropic_api_key, timeout=60.0, max_retries=2)


def to_api_messages(history: list[BaseMessage], context: str | None = None) -> list[dict]:
    """Histórico do grafo → mensagens da API. Texto do usuário vai delimitado (é dado, não ordem);
    o estado volátil do caso entra no fim como mensagem de sistema para não quebrar o cache."""
    messages = []
    for msg in history:
        if msg.type == "human":
            content = f"<mensagem_usuario>\n{msg.content}\n</mensagem_usuario>"
            messages.append({"role": "user", "content": content})
        elif msg.type == "ai":
            messages.append({"role": "assistant", "content": msg.content})
    if context:
        messages.append({"role": "system", "content": context})
    return messages


def _system(prompt: str) -> list[dict]:
    return [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]


async def parse[T: BaseModel](
    *,
    model: str,
    system: str,
    messages: list[dict],
    output_format: type[T],
    effort: Effort = "low",
    max_tokens: int = 2048,
) -> T | None:
    """Saída estruturada. Devolve None em recusa (o chamador segue sem o resultado)."""
    response = await get_client().messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=_system(system),
        messages=messages,
        output_format=output_format,
        output_config={"effort": effort},
    )
    if response.stop_reason == "refusal":
        return None
    return output_format.model_validate(response.parsed_output)


async def stream_text(
    *,
    model: str,
    system: str,
    messages: list[dict],
    effort: Effort = "low",
    max_tokens: int = 1024,
) -> AsyncIterator[str]:
    """Texto exibido ao usuário, em pedaços. Em recusa, emite o texto de template."""
    async with get_client().messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=_system(system),
        messages=messages,
        output_config={"effort": effort},
    ) as stream:
        async for text in stream.text_stream:
            yield text
        final = await stream.get_final_message()
    if final.stop_reason == "refusal":
        yield REFUSAL
    # TODO: fallback do servidor quando o modelo principal for Opus (Haiku não tem).
