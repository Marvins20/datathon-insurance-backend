# Negócio de Pé — Backend

FastAPI + LangGraph + motor determinístico de recomendação de seguros.

## Setup

```bash
uv sync
cp .env.example .env
uv run fastapi dev app/main.py      # http://localhost:8000/docs
```

## Comandos

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
uv run langgraph dev                # LangGraph Studio
uv export --format requirements-txt --no-hashes --no-dev > requirements.txt
```

## Dados externos (desastres e crimes)

```bash
uv run python -m app.etl.externo --data-dir ../xenga-datathon-context --check    # alvo e modo de acesso
uv run python -m app.etl.externo --data-dir ../xenga-datathon-context            # carga idempotente (UPSERT)
```

Detalhes, tabelas e limitações em `docs/DADOS_EXTERNOS.md` (repositório de contexto).
