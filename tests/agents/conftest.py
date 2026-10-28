import pytest

from app.agents import llm


class FakeLLM:
    """Substitui llm.parse / llm.stream_text: devolve respostas prontas e registra as chamadas."""

    def __init__(self) -> None:
        self.parsed: dict[type, object] = {}
        self.text = "Pergunta de teste?"
        self.calls: list[dict] = []

    async def parse(self, *, output_format, **kwargs):
        self.calls.append({"output_format": output_format, **kwargs})
        return self.parsed.get(output_format)

    async def stream_text(self, **kwargs):
        self.calls.append(kwargs)
        for word in self.text.split(" "):
            yield word + " "


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(llm, "parse", fake.parse)
    monkeypatch.setattr(llm, "stream_text", fake.stream_text)
    return fake
