# Local Setup & Engineering Guide

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.11+
- Docker & Docker Compose (optional, for containerized run)
- Supabase account & project
- OpenAI API Key

### 2. Environment Setup
```bash
# Clone the repository (if not already local)
# cd ai_project/fumadocs_ingest_service

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
Copy the example environment file and fill in your secrets:
```bash
cp .env.example .env
```
**Required Variables**:
- `SUPABASE_URL` & `SUPABASE_KEY`
- `OPENAI_API_KEY`
- `EMBEDDING_PROVIDER` (set to `openai` for production parity or `ollama` for local)

### 4. Running the Application
**Directly via Uvicorn**:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Via Docker Compose**:
```bash
docker compose up -d --build
```

## 🛠 Developer Utilities

### Logging & Tracing
- **Logs**: Located in `/logs`. Structured as JSON.
- **Traces**: Located in `/traces`. Toggled via `DEBUG_TRACE=true` in `.env`.

### Testing
```bash
pytest tests/
```

## 🔍 Troubleshooting
- **ModuleNotFoundError**: Ensure your virtual environment is active and `pip install` completed.
- **Supabase Connection Error**: Verify `SUPABASE_URL` and `SUPABASE_KEY` in `.env`.
- **429 Rate Limit**: The system includes retry logic, but ensure your API quotas are sufficient.
```
