# syntax=docker/dockerfile:1

FROM python:3.12-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --shell /usr/sbin/nologin podex \
    && chown -R podex:podex /app

USER podex

EXPOSE 8000

CMD ["uvicorn", "podex.main:app", "--host", "0.0.0.0", "--port", "8000"]
