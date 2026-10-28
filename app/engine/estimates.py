"""Faixas de importância segurada pelas regras EST-* (catalog/estimativas.yaml).

Entradas por prioridade: confirmado > observado > derivado > estimado; sem entrada, a tabela da
equipe (`padrao`, natureza estimado). Confiança = Σ fator(natureza das entradas presentes,
tabela da equipe = estimado) ÷ nº de entradas da regra (CONTRATOS_API §9.5).
"""

from dataclasses import dataclass
from typing import Any

from app.domain.decision import Hypothesis, InsuredAmount
from app.domain.fact_keys import get_fact_key
from app.domain.facts import Fact
from app.engine.catalog import Catalog, EstimateRule
from app.engine.rules import usable_values

BASIC_PARTS = {"ambos": ["predio", "conteudo"], "predio": ["predio"], "conteudo": ["conteudo"]}


@dataclass(frozen=True)
class Input:
    value: Any
    status: str
    source: str


@dataclass(frozen=True)
class Estimate:
    amount: InsuredAmount
    confidence: float
    data_used: list[str]
    calc_rule: str
    to_confirm: list[str]


def build_inputs(
    facts: dict[str, Fact], hyps: list[Hypothesis], *, include_estimated: bool
) -> dict[str, Input]:
    usable = usable_values(facts, include_estimated=include_estimated)
    inputs = {k: Input(v, facts[k].status.value, facts[k].source.value) for k, v in usable.items()}
    for h in hyps:
        inputs.setdefault(h.fact_key, Input(h.value, "estimado", "grafo"))
    return inputs


def _basic(rule: EstimateRule, inputs: dict[str, Input], money, strict: bool):
    """Soma das faixas de prédio e/ou conteúdo. strict: sem tabela da equipe (None se faltar)."""
    used, extra_confirm, defaulted = [], [], False
    if "objeto_seguro" in inputs:
        parts = BASIC_PARTS[inputs["objeto_seguro"].value]
    elif "relacao_imovel" in inputs:
        # Premissa: inquilino protege só o conteúdo; proprietário, prédio e conteúdo.
        inquilino = inputs["relacao_imovel"].value == "inquilino"
        parts = ["conteudo"] if inquilino else ["predio", "conteudo"]
        used.append("relacao_imovel")
        extra_confirm.append("objeto_seguro")
    elif strict:
        return None
    else:
        parts = ["predio", "conteudo"]
        extra_confirm.append("objeto_seguro")

    lo = hi = 0
    for part in parts:
        key = f"valor_{part}_faixa"
        if key in inputs:
            rng = money[inputs[key].value]
        elif strict:
            return None
        else:
            rng = money[rule.padrao[key]]
            defaulted = True
        lo, hi = lo + rng[0], hi + rng[1]
    return lo, hi, used, defaulted, extra_confirm


def _compute(rule: EstimateRule, inputs: dict[str, Input], money):
    """Devolve (min, max, entradas extras usadas, usou tabela da equipe?, a confirmar extras)."""
    match rule.tipo:
        case "basica":
            return _basic(rule, inputs, money, strict=False)
        case "proporcional":
            m_lo, m_hi = rule.multiplicador
            for base in rule.base:
                if base.fato in inputs:
                    rng = money[inputs[base.fato].value]
                    return rng[0] * base.fator * m_lo, rng[1] * base.fator * m_hi, [], False, []
        case "tabela":
            item = inputs.get(rule.fato)
            if item is not None and item.value in rule.linhas:
                lo, hi = rule.linhas[item.value]
                return lo, hi, [], False, []
        case "contagem":
            present = [k for k in rule.fatos if k in inputs]
            if present:
                lo, hi = rule.linhas[sum(inputs[k].value is True for k in present)]
                return lo, hi, [], False, []
    lo, hi = rule.padrao
    return lo, hi, [], True, []


def estimate(code: str, inputs: dict[str, Input], catalog: Catalog) -> Estimate | None:
    est = catalog.estimates
    rule = est.rules.get(code)
    if rule is None:
        return None
    lo, hi, extra_used, defaulted, extra_confirm = _compute(rule, inputs, est.money_ranges)

    # Inteiros em reais: min arredondado para baixo, max para cima, dentro dos limites da cobertura.
    step = est.rounding
    cov = catalog.coverages[code]
    lo, hi = max(int(lo) // step * step, step), -(-int(hi) // step) * step
    if cov.is_min is not None:
        lo, hi = max(lo, cov.is_min), max(hi, cov.is_min)
    if cov.is_max is not None:
        lo, hi = min(lo, cov.is_max), min(hi, cov.is_max)

    present = [k for k in rule.entradas if k in inputs]
    score = sum(est.status_factors[inputs[k].status] for k in present)
    if defaulted:
        score += est.status_factors["estimado"]
    confidence = round(min(1.0, score / len(rule.entradas)), 2)

    used = sorted(set(present) | set(extra_used))
    data_used = [f"{k} ({inputs[k].status}, {inputs[k].source})" for k in used]
    if defaulted:
        data_used.append(f"tabela da equipe {rule.id} (estimado)")
    pending = {k for k in rule.entradas if k not in inputs or inputs[k].status == "estimado"}
    to_confirm = [
        f"Confirmar: {get_fact_key(k).label.lower()}?" for k in sorted(pending | set(extra_confirm))
    ]

    return Estimate(
        amount=InsuredAmount(min=lo, max=hi),
        confidence=confidence,
        data_used=data_used,
        calc_rule=rule.id,
        to_confirm=to_confirm,
    )


def basic_midpoint(inputs: dict[str, Input], catalog: Catalog) -> int | None:
    """IS básica estimada (ponto médio, sem teto) para a ELG-008. Só com entradas sabidas:
    sem prédio/conteúdo informados não há derivação — o caso não é redirecionado por suposição."""
    est = catalog.estimates
    rule = next(r for r in est.rules.values() if r.tipo == "basica")
    result = _basic(rule, inputs, est.money_ranges, strict=True)
    return None if result is None else (result[0] + result[1]) // 2
