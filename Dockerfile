FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

# Speed up & quiet down pip/uv inside the container.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# shared/sql holds the migration files; the worker resolves them via
# Path(__file__).parents[3] / "shared" / "sql" so the layout must mirror
# the repo (/app/shared, /app/worker).
COPY shared ./shared

WORKDIR /app/worker

# Install deps first for cache friendliness, then the project itself.
COPY worker/pyproject.toml worker/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY worker/src ./src
# YAML configs (e.g., curated Twitter handles) are loaded at runtime from
# worker/config/, so they must be copied into the image.
COPY worker/config ./config
RUN uv sync --frozen --no-dev

# Apply migrations on boot, then drop into the orchestrator loop.
CMD ["sh", "-c", "uv run migrate && uv run worker-loop"]
