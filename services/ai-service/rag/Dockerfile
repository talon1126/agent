FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /uvx /bin/

RUN addgroup --system rag && adduser --system --ingroup rag rag

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY main.py ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

USER rag

CMD ["python", "main.py"]
