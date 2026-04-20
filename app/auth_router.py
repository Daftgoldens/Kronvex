import uuid
import secrets
import os
import logging
import httpx
from datetime import datetime, timezone
from app.rate_limit import ip_rate_limit

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ApiKey, Agent
from app.schemas import ApiKeyCreate, ApiKeyResponse, ApiKeyCreatedResponse, ApiKeyDemoCreate, DemoKeyCreatedResponse
from app.auth import create_api_key, create_demo_key, get_api_key

router = APIRouter(tags=["Authentication"])

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")


async def create_supabase_user(email: str, password: str) -> dict | None:
    """Create a Supabase user via Admin API. Returns user dict or None if exists."""
    if not SUPABASE_SERVICE_KEY:
        return None
    async with httpx.AsyncClient() as client:
        # Try to create user
        r = await client.post(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "email": email,
                "password": password,
                "email_confirm": True,  # auto-confirm so they can login immediately
            },
            timeout=10,
        )
        if r.status_code in (200, 201):
            return r.json()
        # User already exists — fetch them
        if r.status_code == 422:
            r2 = await client.get(
                f"{SUPABASE_URL}/auth/v1/admin/users",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                },
                params={"email": email},
                timeout=10,
            )
            if r2.status_code == 200:
                users = r2.json().get("users", [])
                return users[0] if users else None
        return None


@router.post("/keys", response_model=ApiKeyCreatedResponse, status_code=201,
             summary="Create an API key (admin)")
async def create_key(data: ApiKeyCreate, request: Request, db: AsyncSession = Depends(get_db)):
    _admin_secret = os.getenv("WEBHOOK_SECRET", "")
    if not _admin_secret or request.headers.get("x-webhook-secret") != _admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")
    api_key, full_key = await create_api_key(db, data.name)
    return ApiKeyCreatedResponse(
        id=api_key.id, name=api_key.name, key_prefix=api_key.key_prefix,
        is_active=api_key.is_active, is_demo=api_key.is_demo,
        memory_limit=api_key.memory_limit, created_at=api_key.created_at,
        last_used_at=api_key.last_used_at, full_key=full_key,
    )


@router.post(
    "/demo",
    response_model=DemoKeyCreatedResponse,
    status_code=201,
    summary="Get demo API key",
    description=(
        "Creates a free demo API key scoped to 1 agent and 100 memories. "
        "One key per email address. Also creates a Supabase account and sends a welcome email "
        "with credentials. Use this key to explore the API before upgrading to a paid plan."
    ),
    dependencies=[Depends(ip_rate_limit(10, 3600))],
)
async def create_demo(data: ApiKeyDemoCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    # Block if email already has a demo key
    existing = await db.execute(
        select(ApiKey).where(
            ApiKey.contact_email == data.email,
            ApiKey.is_demo == True,
            ApiKey.is_active == True,
        )
    )
    existing_key = existing.scalar_one_or_none()

    if existing_key:
        raise HTTPException(
            status_code=409,
            detail="A demo key already exists for this email. Check your inbox or contact us to upgrade."
        )

    # Create API key
    api_key, full_key = await create_demo_key(db, data.name, data.email, data.usecase)
    logger.info("demo_key.created", extra={"email": data.email, "contact_name": data.name})

    await db.commit()

    # Create first agent automatically
    agent = Agent(
        name=f"{data.name}'s agent",
        description="Auto-created demo agent",
        api_key_id=api_key.id,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    # Create Supabase account with random password so user can access dashboard
    temp_password = secrets.token_urlsafe(16)
    try:
        supabase_user = await create_supabase_user(data.email, temp_password)
    except Exception as e:
        logger.warning(f"Supabase user creation failed: {e}")
        supabase_user = None

    # Send welcome email with credentials (always, even if Supabase failed)
    if RESEND_API_KEY:
        try:
            pw_for_email = temp_password if supabase_user else None
            async with httpx.AsyncClient() as client:
                await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "from": "Kronvex <hello@kronvex.io>",
                        "to": [data.email],
                        "subject": "Welcome to Kronvex — your credentials ✓",
                        "html": _welcome_email_html(data.email, pw_for_email, full_key, str(agent.id)),
                    },
                    timeout=10,
                )
        except Exception as e:
            logger.warning(f"Welcome email failed: {e}")

    # Schedule onboarding email sequence J+1, J+3, J+7
    try:
        from app.onboarding_router import _run_sequence, OnboardingScheduleRequest
        background_tasks.add_task(_run_sequence, OnboardingScheduleRequest(
            email=data.email,
            name=data.name,
            api_key=full_key,
            agent_id=str(agent.id),
        ))
    except Exception as e:
        logger.warning(f"Onboarding sequence scheduling failed: {e}")

    return DemoKeyCreatedResponse(
        full_key=full_key,
        agent_id=str(agent.id),
        memory_limit=100,
        message="Ready! Your API key and first agent are set up.",
        temp_password=temp_password if supabase_user else None,
        email=data.email,
    )



@router.get("/keys", response_model=list[ApiKeyResponse], summary="List your API keys")
async def list_keys(api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ApiKey).where(ApiKey.id == api_key.id))
    return result.scalars().all()


@router.delete("/keys/{key_id}", status_code=204, summary="Revoke an API key")
async def revoke_key(key_id: uuid.UUID, api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.id == api_key.id))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    key.is_active = False
    await db.commit()

# ── ROTATE KEY ────────────────────────────────────────────────────────────────
@router.post("/rotate-key", summary="Rotate API key for current Supabase user")
async def rotate_key(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Generates a new API key for the authenticated user, invalidating the old one.
    Agents and memories are preserved (same record, new key material).
    """
    import hashlib as _hashlib

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = auth_header[7:]
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SUPABASE_SERVICE_KEY or "anon",
                "Authorization": f"Bearer {token}",
            },
            timeout=8,
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    email = r.json().get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email in token")

    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.contact_email == email, ApiKey.is_active == True)
        .order_by(ApiKey.created_at.desc())
    )
    all_keys = result.scalars().all()
    key = next((k for k in all_keys if k.plan != 'demo'), None) or (all_keys[0] if all_keys else None)
    if not key:
        raise HTTPException(status_code=404, detail="No API key found for this account")

    raw = secrets.token_urlsafe(32)
    full_key = f"kv-{raw}"
    key.key_hash = _hashlib.sha256(full_key.encode()).hexdigest()
    key.key_prefix = full_key[:12] + "..."
    key.rotated = True
    key.rotated_at = datetime.now(timezone.utc)
    # Preserve sbuid tag so get_my_key lookup still works after rotation
    old = key.contact_usecase or ""
    sbuid_seg = next((s for s in old.split('|') if s.startswith('sbuid:')), "")
    key.contact_usecase = sbuid_seg if sbuid_seg else (key.contact_usecase or "")
    await db.commit()

    return {"full_key": full_key, "key_prefix": key.key_prefix, "rotated_at": key.rotated_at.isoformat()}


# ── MY KEY (used by dashboard) ────────────────────────────────────────────────
@router.get(
    "/my-key",
    summary="Get current API key info",
    description=(
        "Verifies the Supabase JWT from the `Authorization: Bearer <token>` header, "
        "finds the API key linked to the authenticated user's email, and returns its details "
        "including plan, memory limit, and key prefix. Used by the dashboard on login."
    ),
)
async def get_my_key(request: Request, db: AsyncSession = Depends(get_db)):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = auth_header[7:]
    # Verify token with Supabase
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SUPABASE_SERVICE_KEY or "anon",
                "Authorization": f"Bearer {token}",
            },
            timeout=8,
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_data = r.json()
    email = user_data.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email in token")

    # Extract supabase user_id from token response
    supabase_user_id = user_data.get("id", "")

    # Find API key by supabase_user_id stored in contact_usecase (prefix "sbuid:")
    # Fall back to email-based lookup for keys created before this fix
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.is_active == True)
        .where(
            (ApiKey.contact_usecase.like(f"sbuid:{supabase_user_id}%")) |
            (ApiKey.contact_email == email)
        )
        .order_by(ApiKey.created_at.desc())
    )
    all_keys = result.scalars().all()

    # Always prefer paid plan over demo, then prefer sbuid-tagged, then newest
    paid = [k for k in all_keys if k.plan not in ("demo", "free")]
    demo = [k for k in all_keys if k.plan in ("demo", "free")]

    def has_sbuid(k):
        return k.contact_usecase and f"sbuid:{supabase_user_id}" in k.contact_usecase

    # 1. Paid key tagged with this sbuid
    key = next((k for k in paid if has_sbuid(k)), None)
    # 2. Any paid key matching email (sbuid may not be set yet)
    if key is None:
        key = next((k for k in paid if k.contact_email == email), None)
    # 3. Demo key tagged with this sbuid
    if key is None:
        key = next((k for k in demo if has_sbuid(k)), None)
    # 4. Any demo key matching email
    if key is None:
        key = next((k for k in demo if k.contact_email == email), None)
    # 5. Absolute fallback
    if key is None and all_keys:
        key = all_keys[0]

    if not key:
        raise HTTPException(status_code=404, detail="No API key found for this account")

    # Tag this key with the supabase_user_id for future lookups
    if supabase_user_id and key.contact_usecase and not f"sbuid:{supabase_user_id}" in key.contact_usecase:
        existing_usecase = key.contact_usecase or ""
        if not existing_usecase.startswith("sbuid:"):
            key.contact_usecase = f"sbuid:{supabase_user_id}|" + existing_usecase
        await db.commit()
    elif supabase_user_id and not key.contact_usecase:
        key.contact_usecase = f"sbuid:{supabase_user_id}"
        await db.commit()

    # Full key is not stored in DB — only the hash and prefix are kept.
    full_key = None

    # Also fetch agents for this key — avoids needing full_key in dashboard
    from app.models import Agent as AgentModel
    agents_result = await db.execute(
        select(AgentModel).where(AgentModel.api_key_id == key.id)
    )
    agents_list = agents_result.scalars().all()

    return {
        "id": str(key.id),
        "key_prefix": key.key_prefix,
        "full_key": full_key,  # None for old keys, present for new ones
        "plan": key.plan,
        "is_demo": key.is_demo,
        "memory_limit": key.memory_limit,
        "agent_limit": key.agent_limit,
        "cycle_memories_used": key.cycle_memories_used or 0,
        "cycle_reset_at": key.cycle_reset_at.isoformat() if key.cycle_reset_at else None,
        "deleted_memories_count": key.deleted_memories_count or 0,
        "created_at": key.created_at.isoformat(),
        "agents": [
            {"id": str(a.id), "name": a.name, "description": a.description, "created_at": a.created_at.isoformat()}
            for a in agents_list
        ],
    }


# ── SUPABASE AUTH WEBHOOK ─────────────────────────────────────────────────────
# Supabase → Database → Webhooks → Table: auth.users → Event: INSERT
# URL: https://api.kronvex.io/auth/confirmed
# Header: x-webhook-secret: <WEBHOOK_SECRET>

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://kronvex.io")

def _email_code_block(label: str, value: str, accent: str = "#4a7ef5") -> str:
    return f"""
    <div style="background:#080e1c;border:1px solid rgba(255,255,255,0.07);border-left:3px solid {accent};
                border-radius:0 8px 8px 0;padding:16px 20px;margin:16px 0;word-break:break-all">
      <div style="font-size:9px;color:{accent};letter-spacing:2px;margin-bottom:10px;
                  font-family:'Courier New',monospace;opacity:0.8">{label}</div>
      <code style="font-family:'Courier New',monospace;font-size:13px;color:#c8d4e8;
                   letter-spacing:0.3px">{value}</code>
    </div>"""


def _build_email_html(preheader, title, subtitle, body_html, cta_label, cta_url,
                      cta2_label="", cta2_url="", frontend_url="https://kronvex.io") -> str:
    cta2_block = ""
    if cta2_label and cta2_url:
        cta2_block = f"""<a href="{cta2_url}" style="display:inline-block;background:transparent;color:#4a7ef5;text-decoration:none;padding:13px 28px;border-radius:6px;font-weight:600;font-size:12px;letter-spacing:0.8px;border:1px solid rgba(74,126,245,0.35);font-family:'Helvetica Neue',Arial,sans-serif;vertical-align:middle">{cta2_label}</a>"""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>{title}</title></head>
<body style="margin:0;padding:0;background:#050810;font-family:'Helvetica Neue',Arial,sans-serif">
<span style="display:none;font-size:1px;color:#050810;max-height:0;overflow:hidden">{preheader}</span>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#050810;padding:40px 20px"><tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">
<tr><td style="padding:0 0 32px"><table width="100%" cellpadding="0" cellspacing="0"><tr>
  <td valign="middle" style="padding-right:10px"><svg width="28" height="34" viewBox="0 0 18 22" fill="none" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="al-st" x1="9" y1="2.5" x2="9" y2="9" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#f5c842" stop-opacity=".95"/><stop offset="100%" stop-color="#c07820" stop-opacity=".6"/></linearGradient><linearGradient id="al-sb" x1="9" y1="13" x2="9" y2="19.5" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#c07820" stop-opacity=".55"/><stop offset="100%" stop-color="#f5c842" stop-opacity=".95"/></linearGradient><linearGradient id="al-cr" x1="9" y1="-1.5" x2="9" y2="1" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#f5c842"/><stop offset="100%" stop-color="#b06010"/></linearGradient><linearGradient id="al-br" x1="0" y1="0" x2="18" y2="0" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#7a4008" stop-opacity=".5"/><stop offset="50%" stop-color="#e09030"/><stop offset="100%" stop-color="#7a4008" stop-opacity=".5"/></linearGradient><clipPath id="al-ct"><path d="M1,2.5 L17,2.5 L11,9 L7,9 Z"/></clipPath><clipPath id="al-cb"><path d="M7,13 L11,13 L17,19.5 L1,19.5 Z"/></clipPath></defs><ellipse cx="8.3" cy="-.1" rx="2.4" ry="1.0" fill="url(#al-cr)" opacity=".96" transform="rotate(-15 8.3 -.1)"/><ellipse cx="5.6" cy="-.6" rx="2.2" ry=".92" fill="url(#al-cr)" opacity=".89" transform="rotate(-32 5.6 -.6)"/><ellipse cx="3.0" cy="-.5" rx="2.0" ry=".82" fill="url(#al-cr)" opacity=".80" transform="rotate(-50 3.0 -.5)"/><ellipse cx="9.7" cy="-.1" rx="2.4" ry="1.0" fill="url(#al-cr)" opacity=".96" transform="rotate(15 9.7 -.1)"/><ellipse cx="12.4" cy="-.6" rx="2.2" ry=".92" fill="url(#al-cr)" opacity=".89" transform="rotate(32 12.4 -.6)"/><ellipse cx="15.0" cy="-.5" rx="2.0" ry=".82" fill="url(#al-cr)" opacity=".80" transform="rotate(50 15.0 -.5)"/><rect x="0" y="1.2" width="18" height="1.1" rx=".55" fill="url(#al-br)"/><path d="M1,2.5 L17,2.5 L11,9 L7,9 Z" fill="rgba(74,126,245,0.09)" stroke="#4a7ef5" stroke-width=".9" stroke-linejoin="round" stroke-opacity=".65"/><path d="M7,9 Q8.5,11 7,13" fill="none" stroke="#4a7ef5" stroke-width=".7" stroke-opacity=".5"/><path d="M11,9 Q9.5,11 11,13" fill="none" stroke="#4a7ef5" stroke-width=".7" stroke-opacity=".5"/><path d="M7,13 L11,13 L17,19.5 L1,19.5 Z" fill="rgba(74,126,245,0.06)" stroke="#4a7ef5" stroke-width=".9" stroke-linejoin="round" stroke-opacity=".6"/><rect x="0" y="20" width="18" height="1.1" rx=".55" fill="url(#al-br)"/><rect x="1" y="2.5" width="16" height="6.5" fill="url(#al-st)" clip-path="url(#al-ct)"/><rect x="1" y="13" width="16" height="6.5" fill="url(#al-sb)" clip-path="url(#al-cb)"/></svg></td><td valign="middle"><span style="font-family:'Courier New',monospace;font-size:13px;font-weight:700;letter-spacing:3px;color:#4a7ef5">KRONVEX</span></td>
  <td align="right"><span style="font-family:'Courier New',monospace;font-size:10px;color:#2a3a55;letter-spacing:1px">PERSISTENT MEMORY LAYER</span></td>
</tr></table></td></tr>
<tr><td style="background:#0a0f1e;border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:40px">
  <h1 style="margin:0 0 8px;font-size:28px;font-weight:700;color:#e8edf8;letter-spacing:-0.5px;line-height:1.2">{title}</h1>
  <p style="margin:0 0 28px;font-size:13px;color:#4a5a70;line-height:1.6">{subtitle}</p>
  <div style="height:1px;background:linear-gradient(90deg,rgba(74,126,245,0.4),transparent);margin:0 0 28px"></div>
  {body_html}
  <div style="margin-top:32px">
    <a href="{cta_url}" style="display:inline-block;background:#1e56d9;color:#ffffff;text-decoration:none;padding:14px 28px;border-radius:6px;font-weight:700;font-size:12px;letter-spacing:0.8px;font-family:'Helvetica Neue',Arial,sans-serif;vertical-align:middle;margin-right:12px">{cta_label}</a>
    {cta2_block}
  </div>
</td></tr>
<tr><td style="padding:28px 0 0"><table width="100%" cellpadding="0" cellspacing="0"><tr>
  <td style="font-size:11px;color:#2a3a55;line-height:1.7">
    Questions? <a href="mailto:hello@kronvex.io" style="color:#4a7ef5;text-decoration:none">hello@kronvex.io</a><br>
    <a href="{frontend_url}" style="color:#2a3a55;text-decoration:none">kronvex.io</a> · Built in Paris 🇫🇷
  </td>
</tr></table></td></tr>
</table></td></tr></table></body></html>"""


def _welcome_email_html(email: str, temp_password: str | None = None, api_key: str = "", agent_id: str = "") -> str:
    pw_block = ""
    if temp_password:
        import urllib.parse
        encoded_email = urllib.parse.quote(email)
        pw_block = f"""
        {_email_code_block("EMAIL", email, accent="#4a7ef5")}
        {_email_code_block("TEMPORARY PASSWORD", temp_password, accent="#f0a840")}
        <p style="color:#5a7090;font-size:12px;margin:0 0 24px;line-height:1.6">
          ⚠️ This is a temporary password.
          <a href="{FRONTEND_URL}/login?email={encoded_email}" style="color:#4a7ef5">Sign in to Dashboard →</a> &mdash; paste the temporary password above.
        </p>"""

    key_block = ""
    if api_key:
        key_block = _email_code_block("YOUR API KEY", api_key, accent="#4a7ef5")

    agent_block = ""
    if agent_id:
        agent_block = _email_code_block("YOUR AGENT ID", agent_id, accent="#22c55e")

    body = f"""
    <p style="color:#8a9bb8;line-height:1.7;margin:0 0 24px">
      Welcome! Your free demo account is ready —
      <strong style="color:#e8edf8">100 memories</strong>, 1 agent, all three endpoints unlocked.
    </p>
    {pw_block}
    {key_block}
    {agent_block}
    <div style="background:#0d1220;border:1px solid rgba(74,126,245,0.15);border-radius:8px;padding:20px;margin:24px 0">
      <div style="font-size:10px;color:#e09030;letter-spacing:2px;margin-bottom:14px;font-family:'Courier New',monospace">📍 OÙ RETROUVER VOS CREDENTIALS</div>
      <p style="margin:0 0 10px;color:#c8d4e8;font-size:13px">
        Votre <strong style="color:#4a7ef5">API Key</strong> et votre <strong style="color:#22c55e">Agent ID</strong>
        sont toujours disponibles dans votre Dashboard :
      </p>
      <p style="margin:0 0 6px;color:#8a9bb8;font-size:12px">
        → <a href="{FRONTEND_URL}/dashboard" style="color:#4a7ef5;text-decoration:none"><strong>kronvex.io/dashboard</strong></a>
        &nbsp;·&nbsp; onglet <strong style="color:#e8edf8">API Key</strong>
      </p>
      <p style="margin:0 0 20px;color:#8a9bb8;font-size:12px">
        Cliquez sur <strong style="color:#e8edf8">"SHOW"</strong> pour révéler la clé complète,
        et copiez l'Agent ID depuis la même page.
      </p>
      <div style="height:1px;background:rgba(255,255,255,0.05);margin:0 0 16px"></div>
      <div style="font-size:10px;color:#4a7ef5;letter-spacing:2px;margin-bottom:14px;font-family:'Courier New',monospace">GET STARTED</div>
      <p style="margin:0 0 8px;color:#c8d4e8;font-size:13px">
        <span style="color:#4a7ef5;font-family:'Courier New',monospace">01</span> &nbsp;
        Connectez-vous à votre <a href="{FRONTEND_URL}/dashboard" style="color:#4a7ef5;text-decoration:none"><strong>Dashboard</strong></a>
        avec l'email et le mot de passe temporaire ci-dessus
      </p>
      <p style="margin:0 0 8px;color:#c8d4e8;font-size:13px">
        <span style="color:#22c55e;font-family:'Courier New',monospace">02</span> &nbsp;
        Votre agent est déjà créé — utilisez l'Agent ID ci-dessus dans vos appels API
      </p>
      <p style="margin:0;color:#c8d4e8;font-size:13px">
        <span style="color:#22c55e;font-family:'Courier New',monospace">03</span> &nbsp;
        <code style="background:#080e1c;padding:2px 8px;border-radius:4px;color:#22c55e">POST /agents/{'{{agent_id}}'}/remember</code> — stockez votre première mémoire
      </p>
    </div>
    """
    # Build login URL with email pre-filled so user just pastes temp password
    from urllib.parse import quote as _quote
    login_url = f"{FRONTEND_URL}/login?email={_quote(email)}" if email else f"{FRONTEND_URL}/login"

    return _build_email_html(
        preheader="Your Kronvex demo account is ready — 100 free memories, 1 agent, all endpoints unlocked.",
        title="Welcome — your account is ready.",
        subtitle="Your free demo includes 100 memories, 1 agent, and all three endpoints.",
        body_html=body,
        cta_label="Sign in to Dashboard →",
        cta_url=login_url,
        cta2_label="Read the Docs",
        cta2_url=f"{FRONTEND_URL}/docs",
        frontend_url=FRONTEND_URL,
    )

# ── POST /auth/reset-key ──────────────────────────────────────────────────────
@router.post("/reset-key", summary="Reset API key and send new one by email")
async def reset_api_key(request: Request, db: AsyncSession = Depends(get_db)):
    """Generates a new API key for the authenticated user and sends it by email."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth token")
    token = auth_header[7:]

    # Get user from Supabase
    supabase_url = os.getenv("SUPABASE_URL", "https://kkulzoaoqkfbpefponlp.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{supabase_url}/auth/v1/user",
            headers={"apikey": supabase_key, "Authorization": f"Bearer {token}"},
            timeout=8
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid token")

    email = r.json().get("email", "")
    if not email:
        raise HTTPException(status_code=400, detail="No email found")

    # Find API key by email (prefer paid plans, then most recent)
    result = await db.execute(
        select(ApiKey).where(ApiKey.contact_email == email, ApiKey.is_active == True)
        .order_by(ApiKey.created_at.desc())
        .limit(1)
    )
    key = result.scalars().first()
    if not key:
        raise HTTPException(status_code=404, detail="No API key found for this account")

    # Generate new key
    from app.auth import _generate_key
    full_key, key_hash, key_prefix = _generate_key()
    key.key_hash = key_hash
    key.key_prefix = key_prefix
    await db.commit()

    # Send email with new key
    resend_key = os.getenv("RESEND_API_KEY", "")
    frontend_url = os.getenv("FRONTEND_URL", "https://kronvex.io")
    if resend_key:
        html = f"""<!DOCTYPE html><html><body style="background:#050810;font-family:sans-serif;padding:40px 20px">
        <div style="max-width:600px;margin:0 auto">
          <table cellpadding="0" cellspacing="0"><tr><td valign="middle" style="padding-right:10px"><svg width="28" height="34" viewBox="0 0 18 22" fill="none" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="al-st" x1="9" y1="2.5" x2="9" y2="9" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#f5c842" stop-opacity=".95"/><stop offset="100%" stop-color="#c07820" stop-opacity=".6"/></linearGradient><linearGradient id="al-sb" x1="9" y1="13" x2="9" y2="19.5" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#c07820" stop-opacity=".55"/><stop offset="100%" stop-color="#f5c842" stop-opacity=".95"/></linearGradient><linearGradient id="al-cr" x1="9" y1="-1.5" x2="9" y2="1" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#f5c842"/><stop offset="100%" stop-color="#b06010"/></linearGradient><linearGradient id="al-br" x1="0" y1="0" x2="18" y2="0" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#7a4008" stop-opacity=".5"/><stop offset="50%" stop-color="#e09030"/><stop offset="100%" stop-color="#7a4008" stop-opacity=".5"/></linearGradient><clipPath id="al-ct"><path d="M1,2.5 L17,2.5 L11,9 L7,9 Z"/></clipPath><clipPath id="al-cb"><path d="M7,13 L11,13 L17,19.5 L1,19.5 Z"/></clipPath></defs><ellipse cx="8.3" cy="-.1" rx="2.4" ry="1.0" fill="url(#al-cr)" opacity=".96" transform="rotate(-15 8.3 -.1)"/><ellipse cx="5.6" cy="-.6" rx="2.2" ry=".92" fill="url(#al-cr)" opacity=".89" transform="rotate(-32 5.6 -.6)"/><ellipse cx="3.0" cy="-.5" rx="2.0" ry=".82" fill="url(#al-cr)" opacity=".80" transform="rotate(-50 3.0 -.5)"/><ellipse cx="9.7" cy="-.1" rx="2.4" ry="1.0" fill="url(#al-cr)" opacity=".96" transform="rotate(15 9.7 -.1)"/><ellipse cx="12.4" cy="-.6" rx="2.2" ry=".92" fill="url(#al-cr)" opacity=".89" transform="rotate(32 12.4 -.6)"/><ellipse cx="15.0" cy="-.5" rx="2.0" ry=".82" fill="url(#al-cr)" opacity=".80" transform="rotate(50 15.0 -.5)"/><rect x="0" y="1.2" width="18" height="1.1" rx=".55" fill="url(#al-br)"/><path d="M1,2.5 L17,2.5 L11,9 L7,9 Z" fill="rgba(74,126,245,0.09)" stroke="#4a7ef5" stroke-width=".9" stroke-linejoin="round" stroke-opacity=".65"/><path d="M7,9 Q8.5,11 7,13" fill="none" stroke="#4a7ef5" stroke-width=".7" stroke-opacity=".5"/><path d="M11,9 Q9.5,11 11,13" fill="none" stroke="#4a7ef5" stroke-width=".7" stroke-opacity=".5"/><path d="M7,13 L11,13 L17,19.5 L1,19.5 Z" fill="rgba(74,126,245,0.06)" stroke="#4a7ef5" stroke-width=".9" stroke-linejoin="round" stroke-opacity=".6"/><rect x="0" y="20" width="18" height="1.1" rx=".55" fill="url(#al-br)"/><rect x="1" y="2.5" width="16" height="6.5" fill="url(#al-st)" clip-path="url(#al-ct)"/><rect x="1" y="13" width="16" height="6.5" fill="url(#al-sb)" clip-path="url(#al-cb)"/></svg></td><td valign="middle"><span style="font-family:'Courier New',monospace;font-size:13px;font-weight:700;letter-spacing:3px;color:#4a7ef5">KRONVEX</span></td></tr></table>
          <h3 style="color:#e8edf8">Your new API key</h3>
          <p style="color:#7a8fa8">Your API key has been reset. Use this new key in your agents.</p>
          <div style="background:#080e1c;border:1px solid rgba(255,255,255,.07);border-left:3px solid #4a7ef5;border-radius:0 8px 8px 0;padding:16px 20px;margin:20px 0">
            <div style="font-size:9px;color:#4a7ef5;letter-spacing:2px;margin-bottom:8px;font-family:monospace">YOUR NEW API KEY</div>
            <code style="font-family:monospace;font-size:13px;color:#c8d4e8;word-break:break-all">{full_key}</code>
          </div>
          <p style="color:#f87171;font-size:12px">⚠️ Save this key — find it anytime in your <a href='https://kronvex.io/dashboard' style='color:#4a7ef5'>dashboard</a>.</p>
          <p style="color:#4a5a70;font-size:12px">
            <a href="{frontend_url}/dashboard" style="color:#4a7ef5">Go to Dashboard</a> · 
            Questions? <a href="mailto:hello@kronvex.io" style="color:#4a7ef5">hello@kronvex.io</a>
          </p>
        </div></body></html>"""
        try:
            async with httpx.AsyncClient() as client:
                await client.post("https://api.resend.com/emails",
                    json={"from": "Kronvex <hello@kronvex.io>", "to": [email],
                          "subject": "Your new Kronvex API key", "html": html},
                    headers={"Authorization": f"Bearer {resend_key}"}, timeout=10)
        except Exception:
            pass

    return {"full_key": full_key, "key_prefix": key_prefix}


@router.delete("/delete-account", status_code=200, summary="Delete account and all associated data")
async def delete_account(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Permanently deletes the account: cancels Stripe subscription, deletes all agents/memories,
    deactivates API keys, then removes the Supabase user.
    """
    import stripe as _stripe
    from sqlalchemy import delete as sql_delete
    from app.models import Memory

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth_header[7:]

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"apikey": SUPABASE_SERVICE_KEY or "anon", "Authorization": f"Bearer {token}"},
            timeout=8,
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_data = r.json()
    email = user_data.get("email")
    supabase_user_id = user_data.get("id")
    if not email:
        raise HTTPException(status_code=400, detail="No email in token")

    # Find all API keys for this user
    result = await db.execute(
        select(ApiKey).where(
            (ApiKey.contact_email == email) |
            (ApiKey.contact_usecase.like(f"%sbuid:{supabase_user_id}%"))
        )
    )
    keys = result.scalars().all()

    for key in keys:
        # Cancel Stripe subscription if on paid plan
        _stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
        if key.plan not in ("demo", "free") and _stripe_key:
            try:
                _stripe.api_key = _stripe_key
                customers = _stripe.Customer.search(query=f"email:'{email}'", limit=1)
                for cust in customers.data:
                    subs = _stripe.Subscription.list(customer=cust.id, status="active", limit=5)
                    for sub in subs.data:
                        _stripe.Subscription.cancel(sub.id)
                        logger.info(f"[DELETE_ACCOUNT] Cancelled Stripe sub {sub.id} for {email}")
            except Exception as e:
                logger.warning(f"[DELETE_ACCOUNT] Stripe cancellation failed for {email}: {e}")

        # Delete memories for all agents of this key (CASCADE will handle it but explicit is safer)
        agents_result = await db.execute(select(Agent).where(Agent.api_key_id == key.id))
        agents = agents_result.scalars().all()
        for agent in agents:
            await db.execute(sql_delete(Memory).where(Memory.agent_id == agent.id))

        # Delete agents (cascade handles memories too, but we already deleted above)
        await db.execute(sql_delete(Agent).where(Agent.api_key_id == key.id))

        # Hard-delete API key — removes contact_email and cascades to webhook_configs and api_calls
        await db.delete(key)

    await db.commit()
    logger.info(f"[DELETE_ACCOUNT] Account data deleted for {email}")

    # Delete Supabase user via Admin API
    if supabase_user_id and SUPABASE_SERVICE_KEY:
        try:
            async with httpx.AsyncClient() as client:
                await client.delete(
                    f"{SUPABASE_URL}/auth/v1/admin/users/{supabase_user_id}",
                    headers={
                        "apikey": SUPABASE_SERVICE_KEY,
                        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    },
                    timeout=10,
                )
            logger.info(f"[DELETE_ACCOUNT] Supabase user deleted: {supabase_user_id}")
        except Exception as e:
            logger.warning(f"[DELETE_ACCOUNT] Supabase user deletion failed: {e}")

    return {"deleted": True, "email": email}


@router.post("/confirmed")
async def auth_confirmed(request: Request):
    """Supabase webhook — called on new user INSERT. Sends welcome email."""
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    if webhook_secret:
        provided = request.headers.get("x-webhook-secret", "")
        if provided != webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    record = body.get("record", {})
    email = record.get("email")
    if not email:
        return {"ok": False, "reason": "no email"}

    if not RESEND_API_KEY:
        return {"ok": False, "reason": "RESEND_API_KEY not set"}

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={
                    "from": "Kronvex <hello@kronvex.io>",
                    "to": [email],
                    "subject": "Welcome to Kronvex — your account is ready ✓",
                    "html": _welcome_email_html(email),
                },
                timeout=10,
            )
        return {"ok": r.status_code in (200, 201), "status": r.status_code}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── AGENT MANAGEMENT VIA SUPABASE JWT ────────────────────────────────────────
# Used when the full API key is not available in plaintext (old accounts)

import app.service as svc


async def _get_key_from_bearer(request: Request, db: AsyncSession) -> ApiKey:
    """Resolve Supabase Bearer JWT → ApiKey row."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[7:]
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"apikey": SUPABASE_SERVICE_KEY or "anon", "Authorization": f"Bearer {token}"},
            timeout=8,
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_data = r.json()
    email = user_data.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email in token")
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.contact_email == email, ApiKey.is_active == True)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    key = next((k for k in keys if k.plan != "demo"), None) or (keys[0] if keys else None)
    if not key:
        raise HTTPException(status_code=404, detail="No API key for this account")
    return key


@router.get("/agents", summary="List agents with memory_count (JWT)")
async def list_agents_jwt(request: Request, db: AsyncSession = Depends(get_db)):
    key = await _get_key_from_bearer(request, db)
    return await svc.list_agents(db, api_key_id=key.id)


@router.post("/agents", summary="Create agent (JWT)", status_code=201)
async def create_agent_jwt(request: Request, db: AsyncSession = Depends(get_db)):
    key = await _get_key_from_bearer(request, db)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    from app.schemas import AgentCreate
    return await svc.create_agent(db, AgentCreate(name=name, description=body.get("description", "")), api_key=key)


@router.get("/agents/{agent_id}/memories", summary="List agent memories (JWT)")
async def list_agent_memories_jwt(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    per_page: int = 25,
    page: int = 1,
    sort: str = "recent",
    memory_type: str | None = None,
    search: str | None = None,
):
    import uuid as _uuid
    from app.models import Memory, Agent as AgentModel
    from datetime import datetime, timezone
    key = await _get_key_from_bearer(request, db)
    try:
        aid = _uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid agent_id")
    # Verify agent belongs to this key
    agent_res = await db.execute(
        select(AgentModel).where(AgentModel.id == aid, AgentModel.api_key_id == key.id)
    )
    if not agent_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Agent not found")

    now = datetime.now(timezone.utc)
    base_where = [
        Memory.agent_id == aid,
        Memory.deleted_at.is_(None),
        (Memory.expires_at.is_(None) | (Memory.expires_at > now) | Memory.pinned),
    ]
    if memory_type:
        base_where.append(Memory.memory_type == memory_type)
    if search and search.strip():
        base_where.append(Memory.content.ilike(f"%{search.strip()}%"))

    if sort == "oldest":
        order = Memory.created_at.asc()
    elif sort == "access_count":
        order = Memory.access_count.desc()
    else:
        order = Memory.created_at.desc()

    per_page = max(1, min(per_page, 100))
    total = (await db.execute(select(func.count()).select_from(Memory).where(*base_where))).scalar() or 0
    offset = (page - 1) * per_page
    result = await db.execute(select(Memory).where(*base_where).order_by(order).limit(per_page).offset(offset))
    memories = result.scalars().all()
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    from app import service as svc
    return {
        "memories": [svc._memory_to_schema(m) for m in memories],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.delete("/agents/{agent_id}", summary="Delete agent (JWT)", status_code=204)
async def delete_agent_jwt(agent_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    key = await _get_key_from_bearer(request, db)
    import uuid as _uuid
    from sqlalchemy import delete as sql_delete
    from app.models import Agent as AgentModel, Memory
    try:
        aid = _uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid agent_id")
    result = await db.execute(
        select(AgentModel).where(AgentModel.id == aid, AgentModel.api_key_id == key.id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    await db.execute(sql_delete(Memory).where(Memory.agent_id == aid))
    await db.delete(agent)
    await db.commit()


@router.patch("/agents/{agent_id}", summary="Rename agent (JWT)")
async def rename_agent_jwt(agent_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    key = await _get_key_from_bearer(request, db)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    import uuid as _uuid
    from app.models import Agent as AgentModel
    result = await db.execute(
        select(AgentModel).where(AgentModel.id == _uuid.UUID(agent_id), AgentModel.api_key_id == key.id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.name = name
    await db.commit()
    await db.refresh(agent)
    result_count = await svc.get_agent_with_count(db, agent.id, key.id)
    return result_count or {"id": str(agent.id), "name": agent.name, "memory_count": 0}


# ── WEBHOOK CONFIG ───────────────────────────────────────────────────────────

@router.get("/webhook", summary="Get webhook config")
async def get_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    key = await _get_key_from_bearer(request, db)
    return {
        "webhook_url": getattr(key, 'webhook_url', None),
        "webhook_threshold": getattr(key, 'webhook_threshold', None) or 80,
    }


@router.post("/webhook", summary="Set webhook URL and threshold")
async def set_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    key = await _get_key_from_bearer(request, db)
    body = await request.json()
    url = body.get("webhook_url", "").strip() or None
    threshold = int(body.get("threshold", 80))
    threshold = max(50, min(100, threshold))  # clamp 50-100

    # Validate URL if provided
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="webhook_url must start with http:// or https://")

    key.webhook_url = url
    key.webhook_threshold = threshold
    await db.commit()

    return {
        "ok": True,
        "webhook_url": key.webhook_url,
        "webhook_threshold": threshold,
    }


@router.post("/webhook/test", summary="Send a test webhook")
async def test_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    key = await _get_key_from_bearer(request, db)
    if not getattr(key, 'webhook_url', None):
        raise HTTPException(status_code=400, detail="No webhook URL configured")
    from app.auth import _fire_webhook
    await _fire_webhook(getattr(key, 'webhook_url', ''), {
        "event": "webhook.test",
        "api_key_prefix": key.key_prefix,
        "plan": key.plan,
        "message": "This is a test webhook from Kronvex.",
        "timestamp": __import__('datetime').datetime.utcnow().isoformat() + "Z",
    })
    return {"ok": True, "fired_to": key.webhook_url}


# ── /auth/webhooks — multi-webhook manager (wraps single-webhook model) ────────

@router.get("/webhooks", summary="List webhooks (X-API-Key)")
async def list_webhooks(api_key: ApiKey = Depends(get_api_key)):
    if not api_key.webhook_url:
        return []
    return [{
        "id": "default",
        "url": api_key.webhook_url,
        "events": ["memory.stored", "memory.limit_warning", "memory.limit_reached"],
        "threshold": api_key.webhook_threshold or 80,
    }]


@router.post("/webhooks", summary="Add webhook (X-API-Key)", status_code=201)
async def add_webhook(request: Request, api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="url must start with http:// or https://")
    threshold = int(body.get("threshold", 80))
    threshold = max(50, min(100, threshold))
    api_key.webhook_url = url
    api_key.webhook_threshold = threshold
    await db.commit()
    return {
        "id": "default",
        "url": url,
        "events": body.get("events", ["memory.stored"]),
        "threshold": threshold,
        "secret": "see-dashboard",
    }


@router.delete("/webhooks/{webhook_id}", status_code=204, summary="Delete webhook (X-API-Key)")
async def delete_webhook(_webhook_id: str, api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    api_key.webhook_url = None
    api_key.webhook_threshold = None
    await db.commit()
