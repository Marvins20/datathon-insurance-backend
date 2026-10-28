import pytest

from app.graph.reasoner import cnae_ancestors, hypotheses, probability
from app.graph.store import load_snapshot


@pytest.fixture(scope="module")
def snapshot():
    return load_snapshot()


def test_ontology_layers_are_linked(snapshot) -> None:
    g = snapshot.graph
    assert g.has_edge("CNAE:1091-1/02", "mercadoria_refrigerada", key="TIPICAMENTE_TEM")
    assert g.has_edge("mercadoria_refrigerada", "deterioracao_mercadoria", key="EXPOE_A")
    assert g.has_edge("deterioracao_mercadoria", "conteudo", key="AFETA")


def test_cnae_ancestors() -> None:
    assert cnae_ancestors("1091-1/02") == ["CNAE:1091-1/02", "CNAE:1091-1", "CNAE:109", "CNAE:10"]


def test_probability_uses_specific_level(snapshot) -> None:
    assert probability(snapshot, "1091-1/02", "mercadoria_refrigerada") == pytest.approx(0.8)


def test_probability_backs_off_to_division(snapshot) -> None:
    assert probability(snapshot, "1091-1/02", "estoque_relevante") == pytest.approx(0.6)


def test_unknown_activity_has_no_hypothesis(snapshot) -> None:
    assert probability(snapshot, "4711-3/02", "mercadoria_refrigerada") is None
    assert hypotheses(snapshot, "4711-3/02") == {}


def test_hypotheses_are_deterministic(snapshot) -> None:
    assert hypotheses(snapshot, "1091-1/02") == hypotheses(load_snapshot(), "1091-1/02")
