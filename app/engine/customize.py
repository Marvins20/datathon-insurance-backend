"""Personalizar: aplica as mudanças do cliente sobre uma oferta e devolve a oferta `personalizada`
ou as violações. Nenhum preço é calculado."""

from app.domain.decision import (
    CustomizeRequest,
    CustomizeResult,
    Offer,
    OfferCoverage,
    Violation,
)
from app.domain.facts import Fact
from app.engine.catalog import Catalog
from app.engine.decide import case_context
from app.engine.explain_template import describe
from app.engine.prefill import quote_prefill
from app.engine.recommender import offer_violations, size_coverage
from app.graph.store import GraphSnapshot

CUSTOMIZE_RULE_ID = "PERSONALIZAR"


def customize(
    base: Offer,
    request: CustomizeRequest,
    facts: dict[str, Fact],
    catalog: Catalog,
    graph: GraphSnapshot,
) -> CustomizeResult:
    """`base`: a oferta de origem (de `decide`). `reset` devolve a base (Oferta padrão)."""
    if request.reset:
        return CustomizeResult(offer=base)

    _, inputs, _ = case_context(facts, graph)
    plan = catalog.plans[base.plan_code]
    coverages = {c.code: c for c in base.coverages}
    chosen_is: dict[str, int] = {}
    violations: list[Violation] = []

    def reject(code: str | None, message: str) -> None:
        violations.append(Violation(code=code, message=message))

    for change in request.coverages:
        cov = catalog.coverages.get(change.code)
        if cov is None:
            reject(change.code, "Cobertura inexistente no catálogo.")
            continue
        if not change.active:
            if cov.grupo == "fixa":
                reject(cov.codigo, "Cobertura fixa: não pode ser desligada.")
            else:
                coverages.pop(cov.codigo, None)
            continue

        if cov.grupo == "especifica_atividade" and not plan.permite_especificas:
            reject(cov.codigo, "Coberturas específicas da atividade só no Prático ou no Total.")
            continue
        if cov.grupo == "padrao_plano" and cov.codigo not in plan.padrao:
            reject(cov.codigo, f"Não faz parte do plano {base.plan_code}; escolha um plano acima.")
            continue
        if cov.codigo not in coverages:
            item = OfferCoverage(
                code=cov.codigo,
                name=cov.nome,
                group=cov.grupo,
                rule_id=CUSTOMIZE_RULE_ID,
                fact_keys=[],
                risk=None,
                need=cov.dimensao,
                justification="Incluída por você em Personalizar.",
            )
            coverages[cov.codigo] = size_coverage(item, inputs, catalog)

        if change.insured_amount is not None:
            low = cov.is_min if cov.is_min is not None else 1
            if change.insured_amount < low or (
                cov.is_max is not None and change.insured_amount > cov.is_max
            ):
                upper = f" e {cov.is_max}" if cov.is_max is not None else ""
                reject(cov.codigo, f"Importância segurada fora dos limites ({low}{upper}).")
            else:
                chosen_is[cov.codigo] = change.insured_amount

    term = catalog.term
    if request.vigencia_anos is not None and not term.min <= request.vigencia_anos <= term.max:
        reject(None, f"Vigência deve ficar entre {term.min} e {term.max} anos.")

    premium = base.premium_benefit
    if request.premium_benefit is not None:
        if premium is None:
            reject(None, "Benefício Premium só existe no plano Total.")
        elif request.premium_benefit not in premium["options"]:
            reject(None, "Benefício Premium inválido.")

    if violations:
        return CustomizeResult(offer=None, violations=violations)

    if premium is not None:
        premium = {**premium, "selected": request.premium_benefit or premium.get("selected")}
    offer = base.model_copy(
        update={
            "offer_id": f"{base.offer_id}_p",
            "kind": "personalizada",
            "coverages": sorted(coverages.values(), key=lambda c: c.code),
            "premium_benefit": premium,
        }
    )
    # Mudança válida para o cliente mas inválida no catálogo é bug de regra.
    problems = offer_violations(offer, catalog)
    if problems:
        return CustomizeResult(
            offer=None, violations=[Violation(code=None, message=p) for p in problems]
        )

    prefill = quote_prefill(
        offer, inputs, catalog, vigencia_anos=request.vigencia_anos, chosen_is=chosen_is
    )
    return CustomizeResult(
        offer=offer.model_copy(update={**describe(offer, base), "quote_prefill": prefill})
    )
