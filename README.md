# 🤖 Code Review Agent

> Autonomous AI-powered GitHub PR review system — stateful LangGraph agent with tool calling, orchestration & decision logic backed by Claude, FastAPI, PostgreSQL, and Docker.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.116-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.6-1C3C3C?style=flat&logo=langchain&logoColor=white)](https://langchain-ai.github.io/langgraph/)
[![Claude](https://img.shields.io/badge/Claude-3.5_Sonnet-D97757?style=flat)](https://anthropic.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-13-336791?style=flat&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)](https://docker.com)

---

## 🎯 What It Does

Submit any public GitHub PR URL → the agent autonomously:

1. **Fetches** PR metadata and all changed files via the GitHub API
2. **Triages** files using a stateful LangGraph workflow — deciding what to prioritize
3. **Analyzes** each file with Claude 3.5 Sonnet, returning structured issues (type, severity, line, suggestion)
4. **Synthesizes** a scored summary across bugs, style, performance, security, and maintainability
5. **Persists** every task and result to PostgreSQL for full auditability
6. **Posts** results asynchronously via Celery — the API returns instantly with a task ID to poll

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        REST Client                           │
│              POST /api/v1/analyze-pr  →  task_id            │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI  (port 8000)                      │
│   • Validates request  •  Creates DB task  •  Enqueues job  │
└───────────────────────┬──────────────────────────────────────┘
                        │  Redis (Celery broker)
                        ▼
┌──────────────────────────────────────────────────────────────┐
│                   Celery Worker                              │
│                                                              │
│  1. GitHubService  ──► fetch PR + file contents             │
│  2. LangGraphAnalyzer ──► run AI workflow                    │
│  3. Persist results  ──► PostgreSQL                         │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│               LangGraph Stateful Workflow                    │
│                                                              │
│  ┌─────────────┐   ┌──────────────────┐   ┌─────────────┐  │
│  │  triage_pr  │──►│ file_analysis    │──►│  synthesize │  │
│  │             │   │ _loop  (per file)│   │  _report    │  │
│  └─────────────┘   └──────────────────┘   └─────────────┘  │
│         │                   │                     │         │
│         ▼                   ▼                     ▼         │
│   Identify           analyze_code_with_ai    Summary +      │
│   critical files     (Claude 3.5 Sonnet)     severity       │
│                      via instructor          breakdown       │
└──────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│                      PostgreSQL                              │
│   analysis_tasks  •  analysis_results  •  analysis_summaries│
└──────────────────────────────────────────────────────────────┘
```

---

## ✨ Key Features

| Feature | Detail |
|---|---|
| **Stateful LangGraph Agent** | `triage → analysis_loop → synthesize` with conditional edges and per-file decision logic |
| **Tool Calling** | `analyze_code_with_ai` tool wraps Claude; `python_tools` provide static analysis via AST |
| **Structured Output** | `instructor` patches the Anthropic client → Pydantic-validated `AIAnalysisResult` every time |
| **Async End-to-End** | FastAPI + `asyncio` + Celery workers; non-blocking from HTTP request to DB write |
| **Full Auditability** | Every task, file result, and summary persisted in PostgreSQL with timestamps and status |
| **GitHub Integration** | PyGithub with Redis-cached repo objects + graceful rate-limit handling |
| **Alembic Migrations** | Schema versioned migrations — no `create_all()` in production |
| **Docker Compose** | One command spins up Postgres + Redis + API + worker |

---

## 📁 Project Structure

```
code-review-agent/
├── app/
│   ├── agents/
│   │   ├── ai_workflow.py        # LangGraph StateGraph (triage → loop → synthesize)
│   │   ├── analyzer.py           # LangGraphAnalyzer facade
│   │   └── tools/
│   │       ├── ai_tools.py       # analyze_code_with_ai — calls LLMService
│   │       └── python_tools.py   # @tool functions: AST style/bug/perf/best-practice
│   ├── api/v1/
│   │   ├── endpoints/
│   │   │   ├── analyze.py        # POST /analyze-pr, DELETE /tasks/{id}, GET /tasks
│   │   │   └── status.py         # GET /tasks/{id}/status, GET /tasks/{id}/results
│   │   └── router.py
│   ├── config/
│   │   ├── settings.py           # Pydantic config loaded from config.toml + env vars
│   │   └── database.py           # SQLModel engine + session management
│   ├── models/
│   │   ├── database.py           # AnalysisTask, AnalysisResult, AnalysisSummary
│   │   └── schemas.py            # Pydantic request/response schemas
│   ├── services/
│   │   ├── github.py             # GitHubService — PR fetch, file content, caching
│   │   └── llm_service.py        # LLMService — Claude via instructor
│   ├── tasks/
│   │   ├── celery_app.py         # Celery app instance
│   │   └── analyze_tasks.py      # analyze_pr_task (Celery task)
│   ├── utils/
│   │   ├── exceptions.py         # Custom exceptions + FastAPI handlers
│   │   ├── language_detection.py # File extension → language mapping
│   │   ├── logger.py             # Loguru logger
│   │   └── redis_client.py       # Redis client helper
│   └── main.py                   # FastAPI app factory + lifespan
├── migrations/                   # Alembic migration versions
├── tests/                        # Unit + integration tests
├── config.toml                   # App configuration (env vars substituted at runtime)
├── docker-compose.yml            # Postgres + Redis + web + worker
├── DockerFile
├── requirements.txt
└── .env.example
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose
- An [Anthropic API key](https://console.anthropic.com/settings/keys)
- A [GitHub Personal Access Token](https://github.com/settings/tokens) (optional, for higher rate limits / private repos)

### 1 — Clone & configure

```bash
git clone https://github.com/iKatiyar/code-review-agent.git
cd code-review-agent

cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY, GITHUB_TOKEN, SECRET_KEY
```

### 2 — Run with Docker Compose

```bash
docker compose up --build
```

This starts:
- `db` — PostgreSQL on port 5433
- `redis` — Redis on port 6379
- `web` — FastAPI on port 8000
- `worker` — Celery worker

### 3 — Run database migrations

```bash
docker compose exec web uv run alembic upgrade head
```

### 4 — Submit a PR for review

```bash
curl -X POST http://localhost:8000/api/v1/analyze-pr \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/owner/repo",
    "pr_number": 42
  }'
```

Response:
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Analysis queued successfully"
}
```

### 5 — Poll for results

```bash
curl http://localhost:8000/api/v1/tasks/550e8400-e29b-41d4-a716-446655440000/status
curl http://localhost:8000/api/v1/tasks/550e8400-e29b-41d4-a716-446655440000/results
```

Interactive API docs: **http://localhost:8000/docs**

---

## 🔌 API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/analyze-pr` | Queue a PR analysis |
| `GET` | `/api/v1/tasks/{id}/status` | Poll task progress |
| `GET` | `/api/v1/tasks/{id}/results` | Fetch full analysis results |
| `GET` | `/api/v1/tasks` | List tasks with filters |
| `DELETE` | `/api/v1/tasks/{id}` | Cancel a pending task |
| `GET` | `/health` | Service health check |
| `GET` | `/docs` | Swagger UI |

### Example Results Response

```json
{
  "task_id": "550e8400-...",
  "status": "completed",
  "summary": {
    "total_files_analyzed": 3,
    "total_issues": 12,
    "severity_breakdown": { "critical": 1, "high": 3, "medium": 6, "low": 2 },
    "issue_type_breakdown": { "bug": 4, "style": 5, "best_practice": 3 }
  },
  "files": {
    "src/auth.py": {
      "language": "python",
      "issues": [
        {
          "type": "bug",
          "severity": "critical",
          "line": 47,
          "description": "Bare except clause silently swallows all exceptions",
          "suggestion": "Replace with 'except Exception as e:' and log the error"
        }
      ]
    }
  }
}
```

---

## 🧠 LangGraph Workflow

```
START
  │
  ▼
triage_pr_node
  • Reads PR metadata + file list
  • Selects Python files for deep AI analysis
  │
  ▼
file_analysis_loop_node  ◄──────────┐
  • Pops one file from critical_files │
  • Calls analyze_code_with_ai tool   │  (loop while critical_files not empty)
  • Appends issues to analysis_results│
  │                                   │
  ▼  should_continue_analysis?        │
  ├── "continue" ─────────────────────┘
  └── "end"
         │
         ▼
synthesize_report_node
  • Counts total issues
  • Builds severity + type breakdowns
  • Writes final_summary to state
  │
  ▼
END
```

Each node receives and returns the full `AIAnalysisState` TypedDict — making the workflow inspectable, resumable, and easy to extend with new nodes.

---

## 🗃️ Database Schema

```
analysis_tasks
  id (UUID PK) • repo_url • pr_number • status • progress
  created_at • started_at • completed_at • celery_task_id

analysis_results
  id (UUID PK) • task_id (FK) • file_name • file_path
  language • issues (JSON) • analysis_duration

analysis_summaries
  id (UUID PK) • task_id (FK)
  total_files • total_issues • critical/high/medium/low_issues
  style/bug/performance/security/maintainability/best_practice_issues
  code_quality_score • maintainability_score
```

---

## 🛠️ Local Development (without Docker)

```bash
# Install uv (fast Python package manager)
pip install uv

# Create venv + install deps
uv sync

# Start Postgres + Redis only
docker compose up db redis -d

# Run migrations
uv run alembic upgrade head

# Start API
uv run uvicorn app.main:app --reload --port 8000

# Start Celery worker (new terminal)
uv run celery -A app.tasks.celery_app worker --loglevel=info
```

---

## 🧪 Tests

```bash
uv run pytest tests/ -v --cov=app
```

---

## 🌐 Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Claude API key |
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `REDIS_URL` | ✅ | Redis connection string |
| `CELERY_BROKER_URL` | ✅ | Celery broker (same as Redis) |
| `CELERY_RESULT_BACKEND` | ✅ | Celery results store |
| `SECRET_KEY` | ✅ | App secret for signing |
| `GITHUB_TOKEN` | Optional | Higher GitHub API rate limits |
| `ENVIRONMENT` | Optional | `development` / `production` |

---

## 🐳 Deployment (AWS)

The app is Dockerized for simple AWS deployment:

```bash
# Build production image
docker build -t code-review-agent .

# Push to ECR, deploy via ECS / EC2 + RDS (PostgreSQL) + ElastiCache (Redis)
```

Docker Compose `web` and `worker` services reference the same image — scale workers independently based on queue depth.

---

## 🔧 Tech Stack

`Python 3.12` `FastAPI` `LangGraph` `Claude 3.5 Sonnet` `Anthropic API` `instructor` `Celery` `Redis` `PostgreSQL` `SQLModel` `Alembic` `PyGithub` `Docker` `AWS`
