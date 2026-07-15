# --- Stage 1: build the React SPA ---
FROM node:22-slim AS frontend
RUN corepack enable
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build   # -> /frontend/dist

# --- Stage 2: Python runtime ---
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# ffmpeg + spotDL power the optional local download mirror.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# spotDL as an isolated tool so its old pins never constrain the app's web stack
# (see downloads._spotdl_cmd, which finds it on PATH).
RUN uv tool install spotdl
ENV PATH="/root/.local/bin:${PATH}"

COPY main.py ./
COPY songmirror ./songmirror
COPY --from=frontend /frontend/dist ./frontend/dist

# SONGMIRROR_ENV_FILE points the engine at SettingsStore's managed env so wizard-saved
# credentials win over any stale .env.
ENV PYTHONUNBUFFERED=1 SONGMIRROR_DATA_DIR=/data SONGMIRROR_ENV_FILE=/data/app.env
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "uvicorn", "songmirror.web:app", "--host", "0.0.0.0", "--port", "8080"]
