import os
from contextlib import asynccontextmanager

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.rate_limit import ip_rate_limit

from app.config import settings
from app.database import init_db
from app.logging_config import setup_logging
from app.router import router, _admin_router
from app.auth_router import router as auth_router
from app.stripe_router import router as stripe_router
from app.onboarding_router import router as onboarding_router
from app.reviews_router import router as reviews_router
from app.admin_router import router as admin_router

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=0.1,
        environment=os.getenv("ENVIRONMENT", "production"),
        send_default_pii=False,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    # Start background tasks
    from app.tasks import ttl_decay_loop, usage_alert_loop
    import asyncio
    ttl_task     = asyncio.create_task(ttl_decay_loop(interval_seconds=3600))
    alerts_task  = asyncio.create_task(usage_alert_loop(interval_seconds=21600))
    yield
    ttl_task.cancel()
    alerts_task.cancel()


_API_VERSION = "0.4.0"

app = FastAPI(
    title="Kronvex",
    description="""
## Long-term memory for AI agents. 🧠

**Kronvex** gives your B2B AI agents persistent memory across sessions.
Three endpoints. One API key. Your agent goes from amnesiac to contextually aware in minutes.

---

### Quick start

**1. Get a free demo key**
```
POST /auth/demo  →  {"name": "...", "email": "...", "usecase": "..."}
```

**2. Add your key to every request**
```
X-API-Key: kv-xxxxxxxx
```

**3. Give your agent memory**
```
POST /api/v1/agents/{id}/remember       → store a memory
POST /api/v1/agents/{id}/inject-context → get context block ✨
```
    """,
    version=_API_VERSION,
    lifespan=lifespan,
)

_raw_origins = os.getenv("ALLOWED_ORIGINS", "https://kronvex.io,https://www.kronvex.io")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_ratelimit_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = "1000"
    response.headers["X-RateLimit-Window"] = "3600"
    response.headers["X-Kronvex-Version"] = _API_VERSION
    return response

# Global exception handler — ensures CORS headers are present even on 500
# Without this, browser shows "failed to fetch" instead of the real error
import logging as _logging
from fastapi import Request as _Request
from fastapi.responses import JSONResponse as _JSONResponse

_exc_logger = _logging.getLogger("kronvex.exceptions")

@app.exception_handler(Exception)
async def global_exception_handler(request: _Request, exc: Exception):
    _exc_logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    return _JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again or contact support."},
        headers={
            "Access-Control-Allow-Origin": _allowed_origins[0] if _allowed_origins else "https://kronvex.io",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        },
    )

app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(router, prefix="/api/v1")
app.include_router(stripe_router, prefix="/billing", tags=["Billing"])
app.include_router(onboarding_router, prefix="/auth", tags=["Onboarding"])
app.include_router(_admin_router, prefix="")
app.include_router(reviews_router)
app.include_router(admin_router)


@app.get("/health", tags=["System"])
async def health():
    from app.database import AsyncSessionLocal
    from sqlalchemy import text as _text
    try:
        async with AsyncSessionLocal() as _db:
            await _db.execute(_text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok", "service": "kronvex", "version": _API_VERSION}


@app.get("/version", tags=["System"])
async def version():
    return {"version": _API_VERSION, "api": "v1", "env": os.getenv("RAILWAY_ENVIRONMENT", "production")}


@app.get("/ping", include_in_schema=False)
async def ping():
    return "pong"


# ── /contact ─────────────────────────────────────────────────────────────────
from pydantic import BaseModel as _BaseModel

class ContactRequest(_BaseModel):
    name: str
    email: str
    message: str
    company: str = ""
    subject: str = ""

@app.post("/contact", tags=["Contact"], summary="Send a contact message",
          dependencies=[Depends(ip_rate_limit(5, 3600))])
async def contact(data: ContactRequest):
    import httpx, os
    from html import escape as _esc
    resend_key = os.getenv("RESEND_API_KEY", "")
    if not resend_key:
        return {"sent": False, "detail": "Email not configured"}
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;padding:20px">
      <h2 style="color:#4a7ef5">New contact from kronvex.io</h2>
      <p><strong>Name:</strong> {_esc(data.name)}</p>
      <p><strong>Email:</strong> {_esc(data.email)}</p>
      {("<p><strong>Company:</strong> "+_esc(data.company)+"</p>") if data.company else ""}
      {("<p><strong>Subject:</strong> "+_esc(data.subject)+"</p>") if data.subject else ""}
      <p><strong>Message:</strong></p>
      <div style="background:#f5f5f5;padding:16px;border-radius:8px;white-space:pre-wrap">{_esc(data.message)}</div>
    </div>"""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.resend.com/emails",
                json={
                    "from": "Kronvex Contact <hello@kronvex.io>",
                    "to": ["baptiste@kronvex.io"],
                    "reply_to": data.email,
                    "subject": f"[Contact] {data.name} — {data.subject or 'kronvex.io'}",
                    "html": html,
                },
                headers={"Authorization": f"Bearer {resend_key}"},
                timeout=10,
            )
        return {"sent": r.status_code in (200, 201)}
    except Exception as e:
        return {"sent": False, "detail": str(e)}


# ── /newsletter ───────────────────────────────────────────────────────────────
@app.post("/newsletter", tags=["Newsletter"], summary="Subscribe to the Kronvex newsletter",
          dependencies=[Depends(ip_rate_limit(3, 3600))])
async def newsletter_signup(request: _Request):
    import httpx, os
    body = await request.json()
    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        return {"ok": False, "error": "invalid email"}
    resend_key = os.getenv("RESEND_API_KEY", "")
    if resend_key:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_key}"},
                    json={
                        "from": "hello@kronvex.io",
                        "to": [email],
                        "subject": "You're on the Kronvex newsletter ✓",
                        "html": (
                            "<div style='font-family:Arial,sans-serif;max-width:600px;"
                            "background:#0d1117;color:#e8edf8;padding:32px;border-radius:8px'>"
                            "<p style='font-size:16px;margin-bottom:16px'>"
                            "Thanks for subscribing to the Kronvex newsletter.</p>"
                            "<p style='color:#7a8fa8;font-size:14px;line-height:1.7'>"
                            "We'll send you monthly insights on AI agent memory, new articles, "
                            "and product updates. No spam — unsubscribe anytime.</p>"
                            "<p style='margin-top:24px;color:#7a8fa8;font-size:13px'>"
                            "— Baptiste, Kronvex</p></div>"
                        ),
                    },
                    timeout=10,
                )
        except Exception:
            pass
    return {"ok": True}
