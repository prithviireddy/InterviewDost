# InterviewDost — Python Backend (FastAPI)

A complete rewrite of the original Express/Prisma backend using **FastAPI 0.115**, **SQLAlchemy 2.0 async**, and **Alembic** for migrations.

---

## Stack

| Concern | Library |
|---------|---------|
| Framework | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 (async) |
| DB driver | asyncpg |
| Migrations | Alembic |
| Config | pydantic-settings |
| HTTP client | httpx |
| Rate limiting | SlowAPI |
| WebSocket proxy | websockets |

---

## Project layout

```
backend_py/
├── main.py              # App factory, middleware, router mounts
├── config.py            # pydantic-settings (reads .env)
├── database.py          # Async engine + session factory
├── models.py            # SQLAlchemy ORM models
├── deps.py              # FastAPI dependencies (DB session, auth)
├── routers/
│   ├── auth.py          # GitHub OAuth, /me, /logout
│   ├── interviews.py    # Pre-interview setup, listing, results
│   ├── ats.py           # ATS resume checker
│   ├── payments.py      # Razorpay create-order + verify
│   └── misc.py          # ping, pricing, credits, dashboard, analytics, TTS
├── ws/
│   ├── interview.py     # /api/v1/ws  — authenticated interview WebSocket
│   └── stt.py           # /api/v1/stt — Deepgram STT proxy
├── services/
│   ├── groq.py          # Groq LLM calls
│   ├── github.py        # GitHub repo scraping
│   └── razorpay.py      # Razorpay API + HMAC verification
├── alembic/             # Alembic migration environment
│   └── versions/
│       └── 0001_initial.py
├── alembic.ini
├── requirements.txt
├── Dockerfile
└── .env.example
```

---

## Quick start

### 1. Install dependencies

```bash
cd apps/backend_py
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in DATABASE_URL, GROQ_API_KEY, DEEPGRAM_API_KEY,
# GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
```

### 3. Run database migrations

```bash
alembic upgrade head
```

### 4. Start the development server

```bash
uvicorn main:app --reload --port 8000
```

API docs: http://localhost:8000/docs  
Health check: http://localhost:8000/api/v1/ping

---

## API surface (100 % compatible with the original Node.js backend)

| Method | Path | Auth |
|--------|------|------|
| GET | `/api/v1/auth/github` | — |
| GET | `/api/v1/auth/github/callback` | — |
| POST | `/api/v1/auth/logout` | Bearer |
| GET | `/api/v1/auth/me` | Bearer |
| POST | `/api/v1/pre-interview/github` | Bearer |
| POST | `/api/v1/pre-interview/resume` | Bearer |
| GET | `/api/v1/interviews` | Bearer |
| GET | `/api/v1/result/{id}` | Bearer |
| POST | `/api/v1/ats/check` | Bearer |
| GET | `/api/v1/ats` | Bearer |
| GET | `/api/v1/ats/history` | Bearer |
| GET | `/api/v1/ats/{id}` | Bearer |
| POST | `/api/v1/payments/create-order` | Bearer |
| POST | `/api/v1/payments/verify` | Bearer |
| GET | `/api/v1/credits` | Bearer |
| GET | `/api/v1/dashboard/stats` | Bearer |
| GET | `/api/v1/analytics` | Bearer |
| GET | `/api/v1/pricing` | — |
| POST | `/api/v1/tts` | — |
| GET | `/api/v1/ping` | — |
| WS | `/api/v1/ws?token=&interviewId=` | token |
| WS | `/api/v1/stt` | — |

---

## Running migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Roll back the last migration
alembic downgrade -1

# Auto-generate a new migration after model changes
alembic revision --autogenerate -m "describe your change"
```

---

## Docker

```bash
docker build -t interviewdost-backend-py .
docker run -p 8000:8000 --env-file .env interviewdost-backend-py
```

The container runs `alembic upgrade head` before starting uvicorn, so the DB is always up to date on deploy.
