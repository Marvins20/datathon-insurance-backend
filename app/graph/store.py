"""Context graph: ontologia YAML → snapshot NetworkX (ARQUITETURA §4)."""

from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import yaml

from app.domain.fact_keys import get_fact_key

ONTOLOGY_DIR = Path(__file__).parent / "ontology"


@dataclass(frozen=True)
class GraphSnapshot:
    version: int
    graph: nx.MultiDiGraph


def _read(directory: Path, name: str) -> list[dict]:
    with (directory / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def load_snapshot(directory: Path = ONTOLOGY_DIR, version: int = 1) -> GraphSnapshot:
    g = nx.MultiDiGraph()

    for r in _read(directory, "riscos"):
        g.add_node(r["id"], type="risco", label=r["label"])
        for dim in r["afeta"]:
            g.add_node(dim, type="dimensao", label=dim)
            g.add_edge(r["id"], dim, key="AFETA")

    for c in _read(directory, "caracteristicas"):
        get_fact_key(c["fact_key"])  # erro claro se a chave não existir no registro
        g.add_node(c["id"], type="caracteristica", label=c["label"], fact_key=c["fact_key"])
        for exp in c["expoe_a"]:
            if g.nodes.get(exp["risco"], {}).get("type") != "risco":
                raise ValueError(f"{c['id']}: risco inexistente {exp['risco']!r}")
            g.add_edge(c["id"], exp["risco"], key="EXPOE_A", weight=exp["peso"])

    for p in _read(directory, "priors"):
        if g.nodes.get(p["caracteristica"], {}).get("type") != "caracteristica":
            raise ValueError(f"prior: característica inexistente {p['caracteristica']!r}")
        g.add_node(p["atividade"], type="atividade", label=p["atividade"])
        g.add_edge(
            p["atividade"],
            p["caracteristica"],
            key="TIPICAMENTE_TEM",
            alpha=float(p["alpha"]),
            beta=float(p["beta"]),
        )

    return GraphSnapshot(version=version, graph=g)
