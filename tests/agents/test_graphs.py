from datetime import UTC, datetime

from app.agents.profiling.graph import ExtractedFact, Extraction, interviewer_context
from app.agents.profiling.graph import graph as profiling
from app.domain.facts import Fact, FactSource, FactStatus


def _fact(key, value, status=FactStatus.OBSERVADO) -> Fact:
    return Fact(
        key=key,
        value=value,
        status=status,
        source=FactSource.USUARIO_TEXTO,
        confidence=0.8,
        updated_at=datetime(2026, 10, 28, tzinfo=UTC),
        updated_by="teste",
    )


CNAE = _fact("cnae_principal", "1091-1/02")


async def test_profiling_extracts_cnae_and_asks_most_uncertain(fake_llm) -> None:
    fake_llm.parsed[Extraction] = Extraction(
        facts=[
            ExtractedFact(key="cnae_principal", value="1091-1/02", evidence="padaria 1091-1/02"),
            ExtractedFact(key="inventado", value=True, evidence="..."),
            ExtractedFact(key="delivery", value="talvez", evidence="..."),
            ExtractedFact(key="municipio_ibge", value="5300108", evidence="..."),
        ]
    )
    result = await profiling.ainvoke({"case_id": "c1", "messages": [("user", "padaria 1091-1/02")]})

    # Descartados: chave inexistente, valor fora do tipo e chave não extraível.
    assert set(result["facts"]) == {"cnae_principal"}
    cnae = result["facts"]["cnae_principal"]
    assert (cnae.value, cnae.status, cnae.source) == ("1091-1/02", "observado", "usuario_texto")
    assert cnae.evidence == "padaria 1091-1/02"
    assert result["hypotheses"]["mercadoria_refrigerada"] == 0.8
    # Motor: impedimentos ainda em aberto → a pergunta é de elegibilidade.
    assert result["decision"].eligibility.status == "pendente"
    assert result["question"].score >= 1000
    assert result["questions_asked"] == 1
    assert result["messages"][-1].content.strip() == "Pergunta de teste?"
    # O Entrevistador recebe a pergunta escolhida pelo motor no fim, como mensagem de sistema.
    context = fake_llm.calls[-1]["messages"][-1]
    assert context["role"] == "system" and result["question"].prompt in context["content"]


async def test_profiling_asks_activity_first(fake_llm) -> None:
    result = await profiling.ainvoke({"case_id": "c1", "messages": [("user", "oi")]})
    assert result["question"].fact_key == "cnae_principal"


async def test_ineligible_case_gets_no_question(fake_llm) -> None:
    facts = {"cnae_principal": CNAE, "imovel_em_obra": _fact("imovel_em_obra", True)}
    result = await profiling.ainvoke({"case_id": "c1", "facts": facts, "messages": []})
    assert result["question"] is None
    assert result["questions_asked"] == 0
    assert "inelegível" in interviewer_context(result)
