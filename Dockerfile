FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY scripts/ scripts/
RUN mkdir -p data results && uv sync --locked --no-dev

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

# Regenerate the order history, then the short run: three peak days per window, three plans.
CMD ["sh", "-c", "python scripts/generate_data.py && peak-capacity --quick --out results"]
