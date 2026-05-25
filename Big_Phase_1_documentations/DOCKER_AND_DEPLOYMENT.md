# Docker and Deployment

## Docker Architecture

Multi-stage build: `builder` stage installs dependencies, `runtime` stage is minimal.

```dockerfile
# Stage 1: Builder
FROM python:3.11-slim as builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim
# Security: run as non-root user
RUN groupadd -r appgroup && useradd -r -g appgroup -u 1001 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Key security decisions:**
- `USER appuser` (UID 1001) — service runs as non-root; limits blast radius of container escape
- `python:3.11-slim` — minimal base image, reduced attack surface
- Multi-stage build — build tools (pip, gcc) not present in runtime image
- No `--privileged` flag, no host network mode

## Docker Compose (Root Level)

The root `docker-compose.yml` is the production configuration:

```yaml
version: "3.9"

services:
  kwikid-ingest:
    build:
      context: ./kwikid-ai-ingest/ai_project/fumadocs_ingest_service
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    env_file:
      - ./kwikid-ai-ingest/ai_project/fumadocs_ingest_service/.env
    volumes:
      # Persistent storage — survives container restart
      - ./kwikid-ai-ingest/ai_project/fumadocs_ingest_service/logs:/app/logs
      - ./kwikid-ai-ingest/ai_project/fumadocs_ingest_service/traces:/app/traces
      - ./kwikid-ai-ingest/ai_project/fumadocs_ingest_service/data/reports:/app/data/reports
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    # No host port exposure — internal only
    expose:
      - "6379"
```

**Why persistent volumes?** Without volumes:
- Ingestion run reports (JSON + Markdown) are lost on restart
- Debug traces are lost on restart
- Log files are lost on restart

All three are gitignored (runtime artifacts) but should be persisted across container restarts for operational visibility.

## Starting the Service

### Local Development

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Copy and configure env
cp .env.example .env
# Edit .env with your secrets

# Run service
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Or use the dev scripts:
```bash
./scripts_dev/run_local.sh   # Linux/Mac
./scripts_dev/run_local.ps1  # Windows
```

### Docker

```bash
# From repo root
docker compose up --build

# Background
docker compose up --build -d

# Logs
docker compose logs -f kwikid-ingest

# Restart
docker compose restart kwikid-ingest
```

### Rebuilding Container

```powershell
# scripts_dev/recreate-container.ps1
docker compose down
docker compose build --no-cache
docker compose up -d
```

## Environment Injection

Never store secrets in the Docker image. Inject them via:

1. **Bind-mounted `.env` file** (dev/staging):
   ```yaml
   env_file: ./kwikid-ai-ingest/ai_project/fumadocs_ingest_service/.env
   ```

2. **Docker secrets** (production):
   ```yaml
   secrets:
     - supabase_key
   environment:
     SUPABASE_KEY_FILE: /run/secrets/supabase_key
   ```

3. **CI/CD environment injection** (GitHub Actions, Railway, Render):
   ```yaml
   env:
     SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
   ```

## Production Deployment Considerations

### Linux Volume Permissions

The container runs as UID 1001. On Linux, bind-mounted directories may be owned by root:

```bash
# Run before starting container on Linux production host
chown -R 1001:1001 kwikid-ai-ingest/ai_project/fumadocs_ingest_service/logs/
chown -R 1001:1001 kwikid-ai-ingest/ai_project/fumadocs_ingest_service/traces/
chown -R 1001:1001 kwikid-ai-ingest/ai_project/fumadocs_ingest_service/data/reports/
```

### Health Check Endpoints

| Endpoint | Purpose | Auth |
|----------|---------|------|
| `GET /health` | Liveness probe | None |
| `GET /ready` | Readiness probe | None |

Configure Kubernetes or Docker health checks to use `/health` for liveness and `/ready` for readiness.

### Horizontal Scaling

When running multiple replicas:
- Enable Redis rate limiting (`REDIS_RATE_LIMIT_ENABLED=true`) for shared per-IP rate limits
- Ensure all replicas share the same `ACTIVE_INDEX_VERSION`
- Chat history (Supabase) is inherently shared — no additional configuration needed

### Uvicorn Workers

For production, run with multiple workers:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Note: With `--workers > 1`, in-process rate limiting state is NOT shared between workers. Use Redis rate limiting for accurate per-IP limits across workers.

## .dockerignore

Key patterns to ensure secrets and local artifacts are not included in the Docker build context:

```
.env
.env.*
.venv/
__pycache__/
*.pyc
logs/
traces/
data/reports/
data/raw/
data/backups/
.git/
.github/
tests/
docs/
scripts_dev/
*.md
```

## Dependency Security

All packages in `requirements.txt` are pinned to exact versions (`==`). This ensures:
- Reproducible builds
- No silent breaking upgrades
- Auditable supply chain

To update a dependency:
1. Test the new version locally
2. Update the specific package version in `requirements.txt`
3. Update all packages affected by the upgrade
4. Run full test suite

To check for vulnerabilities:
```bash
pip install safety
safety check -r requirements.txt
```
