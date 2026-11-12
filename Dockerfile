# syntax=docker/dockerfile:1

# ---------- Build-Stufe: Abhängigkeiten + Paket in ein venv ----------
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app

# Erst nur Abhängigkeiten (Cache-freundlich) …
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# … dann das Paket selbst (nicht editable, inkl. static/ und templates/)
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# ---------- Laufzeit-Stufe ----------
FROM python:3.13-slim
RUN groupadd --system --gid 1000 avinox \
 && useradd --system --uid 1000 --gid avinox --home-dir /app --shell /usr/sbin/nologin avinox \
 && mkdir -p /data && chown avinox:avinox /data
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    AVINOX_DATA_DIR=/data
WORKDIR /app
USER avinox
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]
CMD ["uvicorn", "avinox.server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
