from functools import cache

from app.graph.store import GraphSnapshot, load_snapshot


@cache
def snapshot() -> GraphSnapshot:
    return load_snapshot()
