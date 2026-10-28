"""Textos determinísticos a partir da trilha: título, motivos, diferenças, limitações, explicação,
confiança geral e perguntas de confirmação. Sempre disponível; usado no lote. O LLM pode reescrever
a explicação, mas nunca estes campos."""

from app.domain.decision import (
    Confidence,
    Eligibility,
    Offer,
    Reason,
)
from app.domain.facts import Fact
from app.engine.catalog import Catalog

PLAN_NAMES = {"ESSENCIAL": "Essencial", "PRATICO": "Prático", "TOTAL": "Total"}
TITLES = {
    "melhor_ajuste": "o que recomendamos para o seu negócio",
    "custo_beneficio": "alternativa mais enxuta",
    "protecao_ampliada": "proteção ampliada",
    "personalizada": "sua versão personalizada",
}
LIMITATIONS = [
    "Limites finais, franquias e condições seguem as condições gerais do Seguro Empresarial.",
    "Nenhum preço é calculado aqui: o prêmio sai da cotação oficial.",
]
EXTERNAL_SOURCES = {
    "api_territorio": "IBGE — dados territoriais do município",
    "api_clima": "Open-Meteo — clima do município",
    "api_desastres": "Atlas Digital de Desastres no Brasil — nível município",
    "api_seguranca": "Dados públicos de segurança — nível UF",
    "receita_cnpj": "Cadastro público de CNPJ",
}
# Recomendação preliminar (impedimentos pendentes) pesa menos na confiança geral.
PENDING_FACTOR = 0.8


def brl(value: int) -> str:
    return "R$ " + f"{value:,}".replace(",", ".")


def title(offer: Offer) -> str:
    return f"Plano {PLAN_NAMES[offer.plan_code]} — {TITLES[offer.kind]}"


def reasons(offer: Offer) -> list[Reason]:
    """Uma razão por cobertura trazida por fatos (as fixas vêm do plano, não do perfil)."""
    return [
        Reason(
            fact_keys=c.fact_keys,
            risk=c.risk,
            need=c.need,
            coverage_code=c.code,
            rule_id=c.rule_id,
            text=c.justification,
        )
        for c in offer.coverages
        if c.fact_keys
    ]


def diff_vs_best(offer: Offer, best: Offer) -> list[str]:
    """`+ nome` / `− nome` em relação à melhor. Fixas nunca saem, então nunca aparecem."""
    mine = {c.code: c.name for c in offer.coverages}
    theirs = {c.code: c.name for c in best.coverages}
    added = [f"+ {mine[c]}" for c in sorted(mine.keys() - theirs.keys())]
    removed = [f"− {theirs[c]}" for c in sorted(theirs.keys() - mine.keys())]
    return added + removed


def explanation_md(offer: Offer, best: Offer) -> str:
    lines = [f"**{title(offer)}**", ""]
    if offer.kind != "melhor_ajuste" and (diff := diff_vs_best(offer, best)):
        lines += ["Em relação à recomendação: " + "; ".join(diff) + ".", ""]
    for c in offer.coverages:
        amount = c.insured_amount
        value = f"{brl(amount.min)} a {brl(amount.max)}" if amount else "valor na cotação"
        lines.append(f"- {c.name}: {value}. {c.justification}")
    lines += ["", "Assistências do plano: " + ", ".join(offer.assistance_packages) + "."]
    lines += ["", *LIMITATIONS]
    return "\n".join(lines)


def describe(offer: Offer, best: Offer) -> dict:
    """Campos textuais da oferta, prontos para `offer.model_copy(update=...)`."""
    return {
        "title": title(offer),
        "reasons": reasons(offer),
        "diff_vs_best": diff_vs_best(offer, best) if offer.kind != "melhor_ajuste" else [],
        "limitations": LIMITATIONS,
        "explanation_md": explanation_md(offer, best),
        "explanation_source": "template",
    }


def overall_confidence(best: Offer, eligibility: Eligibility) -> Confidence:
    values = [c.confidence for c in best.coverages if c.confidence is not None]
    overall = sum(values) / len(values) if values else 0.0
    if eligibility.status == "pendente":
        overall *= PENDING_FACTOR
    overall = round(overall, 2)
    level = "alta" if overall >= 0.75 else "media" if overall >= 0.5 else "baixa"
    return Confidence(overall=overall, level=level)


def confirmation_questions(
    eligibility: Eligibility, best: Offer | None, catalog: Catalog
) -> list[str]:
    """Impedimentos pendentes primeiro (com o texto da pergunta), depois o que confirmar."""
    questions = []
    for p in eligibility.pending:
        text = catalog.questions.get(p.fact_key)
        questions.append(text.texto if text else f"{p.why}?")
    if best:
        questions += sorted({q for c in best.coverages for q in c.to_confirm})
    return list(dict.fromkeys(questions))


def external_sources(facts: dict[str, Fact]) -> list[str]:
    used = {f.source.value for f in facts.values()}
    return sorted(EXTERNAL_SOURCES[s] for s in used if s in EXTERNAL_SOURCES)
