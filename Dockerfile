# syntax=docker/dockerfile:1.7

# ---------- Stage 1: build ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

# Build deps for psycopg, presidio, spacy wheels (libpq, gcc).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml requirements.txt /build/

# Install runtime deps into a target so we can copy the site-packages cleanly.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip wheel \
 && /opt/venv/bin/pip install -r requirements.txt \
 # Presidio's NlpEngine needs a real spaCy model. en_core_web_lg gives the best PHI recall;
 # downgrade to en_core_web_sm in resource-constrained environments by setting
 # PRESIDIO_SPACY_MODEL=en_core_web_sm and rebuilding.
 && /opt/venv/bin/python -m spacy download en_core_web_lg

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# Runtime libs only (libpq for psycopg, ca-certs for outbound TLS).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin --uid 10001 app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app app /app/app
COPY --chown=app:app main.py /app/main.py
COPY --chown=app:app pyproject.toml /app/pyproject.toml

USER app

EXPOSE 8000

# uvicorn is bound by default; override CMD to use gunicorn+uvicorn workers in prod if desired.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
