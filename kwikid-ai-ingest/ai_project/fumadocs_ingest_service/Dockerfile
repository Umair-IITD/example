# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Installs all Python dependencies in an isolated layer.
# Only the installed packages are copied to the runtime stage —
# no build tools, pip, or wheel caches leak into the final image.
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root application user — runs the service without root privileges.
# UID/GID 1001 is arbitrary; avoids conflicts with common host UIDs.
RUN addgroup --system --gid 1001 appgroup \
 && adduser  --system --uid 1001 --ingroup appgroup --no-create-home appuser

WORKDIR /app

# Copy installed packages from the builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages \
                    /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source — owned by appuser so the process can write traces/logs
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 8000

# Health check: polls the lightweight /health endpoint.
# --start-period gives uvicorn time to fully start before checks begin.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c \
      "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2"]
