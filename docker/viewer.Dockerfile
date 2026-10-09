# Read-only replay viewer.
# Templates, static files, and published bundles only.
# No runner image, no private benchmark, no raw runs, no model key.
FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    REPAIR_AGENT_PUBLIC_RUNS=/app/public_runs \
    PYTHONPATH=/app/src

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin viewer

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/repair_agent/__init__.py src/repair_agent/py.typed src/repair_agent/schemas.py src/repair_agent/measures.py src/repair_agent/web.py src/repair_agent/
COPY src/repair_agent/templates src/repair_agent/templates
COPY src/repair_agent/static src/repair_agent/static
COPY examples/public_runs /app/public_runs
COPY examples/dev_runs /app/public_runs

RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && chown -R viewer:viewer /app

USER viewer

EXPOSE 8000

CMD ["sh", "-c", "exec python -m uvicorn repair_agent.web:app --host 0.0.0.0 --port ${PORT:-8000}"]
