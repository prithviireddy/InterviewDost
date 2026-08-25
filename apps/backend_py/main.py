"""
FastAPI application factory and entry point.

Lifespan: disposes the DB engine on shutdown.
Middleware: CORS, SlowAPI global rate limiting (100 req / 15 min per IP).
Sensitive routes (interviews, ATS, payments) apply a stricter 30 / 15 min limit
via the @limiter.limit decorator — add `request: Request` to those route
signatures to activate it.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from config import get_settings
from database import engine
from routers import auth, interviews, ats, payments, misc
from ws.interview import router as ws_interview_router
from ws.stt import router as ws_stt_router

settings = get_settings()

# ── Rate limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/15minutes"],
)


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)
    logger = logging.getLogger("startup")
    logger.info("=== SETTINGS CHECK ===")
    logger.info("FRONTEND_URL = %r", settings.frontend_url)
    logger.info("BACKEND_URL  = %r", settings.backend_url)
    logger.info("APP_ENV      = %r", settings.app_env)
    logger.info("======================")
    yield
    await engine.dispose()


# ── App factory ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="InterviewDost API",
    description="FastAPI / SQLAlchemy backend for InterviewDost.",
    version="2.0.0",
    lifespan=lifespan,
    # Disable interactive docs in production
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
)

# SlowAPI   
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS
origins = list({
    settings.frontend_url,
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
})

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=600,
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(auth.router,        prefix="/api/v1")
app.include_router(interviews.router,  prefix="/api/v1")
app.include_router(ats.router,         prefix="/api/v1")
app.include_router(payments.router,    prefix="/api/v1")
app.include_router(misc.router,        prefix="/api/v1")

# WebSocket routes (no prefix — paths defined inside each router)
app.include_router(ws_interview_router)
app.include_router(ws_stt_router)


# ── Error handlers ─────────────────────────────────────────────────────────────
@app.exception_handler(404)
async def not_found(_request: Request, _exc: Exception):
    return JSONResponse(status_code=404, content={"error": "Not found"})


@app.exception_handler(Exception)
async def global_error(_request: Request, exc: Exception):
    print(f"[error] Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ── Dev entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=not settings.is_production,
    )
