"""Kronvex private admin API — CMO + monitoring. Protected by X-Admin-Token."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ApiKey, ApiCall, Memory, CmoLead, CmoEmail

router = APIRouter(prefix="/admin", tags=["Admin"])

# ── Auth ───────────────────────────────────────────────────────────────────

def require_admin(x_admin_token: str = Header(None)):
    token = os.getenv("ADMIN_TOKEN", "")
    if not token or x_admin_token != token:
        raise HTTPException(401, "Unauthorized")


# ── Overview metrics ───────────────────────────────────────────────────────

@router.get("/metrics", dependencies=[Depends(require_admin)])
async def get_metrics(db: AsyncSession = Depends(get_db)):
    total_keys = (await db.execute(select(func.count(ApiKey.id)))).scalar_one()
    active_keys = (await db.execute(
        select(func.count(ApiKey.id)).where(ApiKey.is_active == True, ApiKey.is_demo == False)
    )).scalar_one()
    demo_keys = (await db.execute(
        select(func.count(ApiKey.id)).where(ApiKey.is_demo == True)
    )).scalar_one()
    total_memories = (await db.execute(select(func.count(Memory.id)))).scalar_one()
    api_calls_today = (await db.execute(
        select(func.count(ApiCall.id)).where(
            func.date(ApiCall.called_at) == func.current_date()
        )
    )).scalar_one()

    # CMO stats
    cmo_leads = (await db.execute(select(func.count(CmoLead.id)))).scalar_one()
    cmo_contacted = (await db.execute(
        select(func.count(CmoLead.id)).where(CmoLead.status.in_(["contacted", "replied"]))
    )).scalar_one()
    cmo_emails_sent = (await db.execute(
        select(func.count(CmoEmail.id)).where(CmoEmail.status == "sent")
    )).scalar_one()

    return {
        "total_keys": total_keys,
        "active_keys": active_keys,
        "demo_keys": demo_keys,
        "total_memories": total_memories,
        "api_calls_today": api_calls_today,
        "cmo_leads": cmo_leads,
        "cmo_contacted": cmo_contacted,
        "cmo_emails_sent": cmo_emails_sent,
    }


# ── Costs ──────────────────────────────────────────────────────────────────

@router.get("/costs", dependencies=[Depends(require_admin)])
async def get_costs(db: AsyncSession = Depends(get_db)):
    # API calls per endpoint (last 30 days)
    usage_rows = (await db.execute(
        select(ApiCall.endpoint, func.count(ApiCall.id).label("n"))
        .where(ApiCall.called_at >= text("NOW() - INTERVAL '30 days'"))
        .group_by(ApiCall.endpoint)
    )).all()
    usage = {r.endpoint: r.n for r in usage_rows}

    # Total embed calls = remember + inject (each does 1 embed)
    embed_calls = usage.get("remember", 0) + usage.get("inject_context", 0)
    # text-embedding-3-small: $0.02 / 1M tokens, ~200 tokens avg per call
    embed_cost = round(embed_calls * 200 / 1_000_000 * 0.02, 4)

    # CMO email generation (gpt-4o-mini: ~$0.15/1M input + $0.60/1M output)
    cmo_gen = (await db.execute(
        select(func.count(CmoEmail.id))
    )).scalar_one()
    cmo_cost = round(cmo_gen * 500 / 1_000_000 * 0.75, 4)  # ~500 tokens avg, blended rate

    # Resend emails sent
    emails_sent = (await db.execute(
        select(func.count(CmoEmail.id)).where(CmoEmail.status == "sent")
    )).scalar_one()

    # Stripe MRR (live from Stripe API)
    mrr = 0.0
    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if stripe_key:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://api.stripe.com/v1/subscriptions",
                    params={"status": "active", "limit": 100},
                    auth=(stripe_key, ""),
                    timeout=8,
                )
                subs = r.json().get("data", [])
                for s in subs:
                    for item in s.get("items", {}).get("data", []):
                        price = item.get("price", {})
                        amount = price.get("unit_amount", 0) or 0
                        interval = price.get("recurring", {}).get("interval", "month")
                        qty = item.get("quantity", 1) or 1
                        monthly = (amount * qty / 100) if interval == "month" else (amount * qty / 100 / 12)
                        mrr += monthly
        except Exception:
            pass

    return {
        "mrr_eur": round(mrr, 2),
        "api_usage_30d": usage,
        "embed_calls_30d": embed_calls,
        "embed_cost_usd": embed_cost,
        "cmo_emails_generated": cmo_gen,
        "cmo_email_cost_usd": cmo_cost,
        "resend_emails_sent": emails_sent,
        "total_ai_cost_usd": round(embed_cost + cmo_cost, 4),
    }


# ── Users ──────────────────────────────────────────────────────────────────

@router.get("/users", dependencies=[Depends(require_admin)])
async def list_users(db: AsyncSession = Depends(get_db)):
    keys = (await db.execute(
        select(ApiKey).order_by(ApiKey.created_at.desc()).limit(200)
    )).scalars().all()
    return [
        {
            "id": str(k.id),
            "name": k.name,
            "email": k.contact_email,
            "plan": k.plan,
            "is_demo": k.is_demo,
            "is_active": k.is_active,
            "memories_used": k.memories_used,
            "memory_limit": k.memory_limit,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        }
        for k in keys
    ]


# ── User stats (per-key detail) ───────────────────────────────────────────

@router.get("/users/{key_id}/stats", dependencies=[Depends(require_admin)])
async def user_stats(key_id: str, db: AsyncSession = Depends(get_db)):
    import uuid as _uuid
    try:
        kid = _uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(400, "Invalid key ID")

    key = (await db.execute(select(ApiKey).where(ApiKey.id == kid))).scalar_one_or_none()
    if not key:
        raise HTTPException(404, "Key not found")

    from app.models import Agent, Memory

    # Calls by day — last 30 days (fill missing days on frontend)
    day_rows = (await db.execute(
        select(func.date(ApiCall.called_at).label("day"), func.count(ApiCall.id).label("n"))
        .where(ApiCall.api_key_id == kid, ApiCall.called_at >= text("NOW() - INTERVAL '30 days'"))
        .group_by(func.date(ApiCall.called_at))
        .order_by(func.date(ApiCall.called_at))
    )).all()
    calls_by_day = [{"date": str(r.day), "count": r.n} for r in day_rows]

    # Calls by endpoint — last 30 days
    ep_rows = (await db.execute(
        select(ApiCall.endpoint, func.count(ApiCall.id).label("n"))
        .where(ApiCall.api_key_id == kid, ApiCall.called_at >= text("NOW() - INTERVAL '30 days'"))
        .group_by(ApiCall.endpoint)
    )).all()
    calls_by_endpoint = {r.endpoint: r.n for r in ep_rows}

    # Total calls all time
    total_calls = (await db.execute(
        select(func.count(ApiCall.id)).where(ApiCall.api_key_id == kid)
    )).scalar_one()

    # Agents + memory counts
    ag_rows = (await db.execute(
        select(Agent.id, Agent.name, Agent.created_at, func.count(Memory.id).label("mem_count"))
        .outerjoin(Memory, (Memory.agent_id == Agent.id) & (Memory.deleted_at.is_(None)))
        .where(Agent.api_key_id == kid)
        .group_by(Agent.id, Agent.name, Agent.created_at)
        .order_by(func.count(Memory.id).desc())
    )).all()
    agents = [{"id": str(r.id), "name": r.name, "memories": r.mem_count,
               "created_at": r.created_at.isoformat() if r.created_at else None} for r in ag_rows]

    return {
        "calls_by_day": calls_by_day,
        "calls_by_endpoint": calls_by_endpoint,
        "total_calls_all_time": total_calls,
        "total_calls_30d": sum(r.n for r in ep_rows),
        "agents": agents,
    }


# ── CMO: Leads ─────────────────────────────────────────────────────────────

class LeadIn(BaseModel):
    name: str
    company: str
    email: str = ""
    role: str = ""
    use_case: str = ""
    signal: str = ""
    linkedin_url: str = ""


class StatusIn(BaseModel):
    status: str


@router.get("/cmo/leads", dependencies=[Depends(require_admin)])
async def list_leads(status: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    q = select(CmoLead).order_by(CmoLead.created_at.desc())
    if status:
        q = q.where(CmoLead.status == status)
    rows = (await db.execute(q)).scalars().all()
    return [_lead_dict(l) for l in rows]


@router.post("/cmo/leads", status_code=201, dependencies=[Depends(require_admin)])
async def create_lead(body: LeadIn, db: AsyncSession = Depends(get_db)):
    # Dedup by email
    if body.email:
        exists = (await db.execute(
            select(CmoLead).where(CmoLead.email == body.email)
        )).scalar_one_or_none()
        if exists:
            raise HTTPException(400, "Email already exists")
    lead = CmoLead(**body.model_dump())
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return _lead_dict(lead)


@router.patch("/cmo/leads/{lead_id}/status", dependencies=[Depends(require_admin)])
async def update_lead_status(lead_id: int, body: StatusIn, db: AsyncSession = Depends(get_db)):
    lead = await _get_lead(lead_id, db)
    lead.status = body.status
    await db.commit()
    return {"ok": True}


@router.delete("/cmo/leads/{lead_id}", dependencies=[Depends(require_admin)])
async def delete_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    lead = await _get_lead(lead_id, db)
    await db.delete(lead)
    await db.commit()
    return {"ok": True}


@router.get("/cmo/leads/{lead_id}/emails", dependencies=[Depends(require_admin)])
async def lead_emails(lead_id: int, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(CmoEmail).where(CmoEmail.lead_id == lead_id).order_by(CmoEmail.sequence_n)
    )).scalars().all()
    return [_email_dict(e) for e in rows]


# ── CMO: Email generation ──────────────────────────────────────────────────

# AI generates only the personalized hook + subject + language detection.
# Fixed templates ensure consistent brand voice across all sequences.
_HOOK_PROMPT = """\
You are Baptiste, founder of Kronvex. Generate the personalized opening hook for a cold outreach.

Output ONLY:
- hook: 1-2 sentences, plain text, specific to their product/role/signal. No greeting.
- subject: 2-4 word lowercase email subject line
- lang: "fr" if lead is French-speaking (French name, French company, France signals), else "en"

Do NOT write the full email. Do NOT pitch Kronvex. Just hook, subject, lang.
Respond with JSON only: {"hook": "...", "subject": "...", "lang": "fr"}"""

_LI_HOOK_PROMPT = """\
You are Baptiste, founder of Kronvex. Generate the personalized opening for a LinkedIn DM.

Output ONLY:
- hook: 1 sentence, plain text, specific to their product/role/signal
- lang: "fr" if French-speaking, else "en"

Do NOT write the full message. Do NOT pitch Kronvex. Just hook and lang.
Respond with JSON only: {"hook": "...", "lang": "fr"}"""

# Fixed email body templates — {hook} and {lang}-appropriate CTA get injected
_EMAIL_BODIES = {
    1: {
        "fr": "{hook}\n\nOn construit chez Kronvex une API de mémoire persistante pour les agents IA — les agents retiennent le contexte entre sessions via /remember, /recall et /inject-context. EU-hosted, GDPR-native.\n\nPertinent pour toi ?\n\nBaptiste\nkronvex.io",
        "en": "{hook}\n\nAt Kronvex we've built a persistent memory API for AI agents — agents retain context across sessions via /remember, /recall and /inject-context. EU-hosted, GDPR-native.\n\nWorth exploring?\n\nBaptiste\nkronvex.io",
    },
    2: {
        "fr": "Je me permets de revenir. {hook}\n\nKronvex s'intègre directement dans LangChain, n8n ou CrewAI en quelques lignes.\n\nÇa t'intéresse ?\n\nBaptiste",
        "en": "Following up briefly. {hook}\n\nKronvex plugs into LangChain, n8n or CrewAI in a few lines.\n\nStill relevant?\n\nBaptiste",
    },
    3: {
        "fr": "Dernier message de ma part — si le timing n'est pas bon, pas de problème. La porte reste ouverte.\n\nBaptiste\nkronvex.io",
        "en": "Last message from me — if the timing isn't right, no worries. Door's always open.\n\nBaptiste\nkronvex.io",
    },
}

_LI_BODIES = {
    "fr": "{hook}\n\nJe travaille sur Kronvex — mémoire persistante pour agents IA (/remember, /recall, /inject-context). EU-hosted, GDPR-native.\n\nÇa t'intéresse ?",
    "en": "{hook}\n\nI'm building Kronvex — persistent memory for AI agents (/remember, /recall, /inject-context). EU-hosted, GDPR-native.\n\nWorth a chat?",
}


async def _generate_one(name: str, company: str, role: str = "", signal: str = "", seq: int = 1) -> dict:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(500, "OPENAI_API_KEY not set")

    ctx = [f"Lead: {name}, {role or 'founder/CTO'} at {company}."]
    if signal:
        ctx.append(f"Signal: {signal}.")
    if seq == 3:
        ctx.append("Breakup sequence — just detect lang, leave hook empty.")

    client = AsyncOpenAI(api_key=api_key)
    r = await client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=200,
        messages=[
            {"role": "system", "content": _HOOK_PROMPT},
            {"role": "user", "content": " ".join(ctx)},
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(r.choices[0].message.content)
    hook = data.get("hook", "")
    subject = data.get("subject", f"kronvex — {company.lower()}")
    lang = data.get("lang", "en") if data.get("lang") in ("fr", "en") else "en"

    bodies = _EMAIL_BODIES.get(seq, _EMAIL_BODIES[1])
    body = bodies.get(lang, bodies["en"]).format(hook=hook).strip()

    return {"subject": subject, "body": body}


class LinkedInMsgIn(BaseModel):
    name: str
    company: str
    role: str = ""
    signal: str = ""


@router.post("/cmo/linkedin-msg", dependencies=[Depends(require_admin)])
async def generate_linkedin_msg(body: LinkedInMsgIn):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(500, "OPENAI_API_KEY not set")

    ctx = [f"Lead: {body.name}, {body.role or 'founder/CTO'} at {body.company}."]
    if body.signal:
        ctx.append(f"Signal: {body.signal}.")

    client = AsyncOpenAI(api_key=api_key)
    r = await client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=128,
        messages=[
            {"role": "system", "content": _LI_HOOK_PROMPT},
            {"role": "user", "content": " ".join(ctx)},
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(r.choices[0].message.content)
    hook = data.get("hook", "")
    lang = data.get("lang", "en") if data.get("lang") in ("fr", "en") else "en"
    message = _LI_BODIES.get(lang, _LI_BODIES["en"]).format(hook=hook).strip()
    return {"body": message, "lang": lang}


class GenerateSingleIn(BaseModel):
    lead_id: int
    sequence_n: int = 1


class SaveDraftIn(BaseModel):
    lead_id: int
    subject: str
    body: str
    sequence_n: int = 1


@router.post("/cmo/generate-single", dependencies=[Depends(require_admin)])
async def generate_single(body: GenerateSingleIn, db: AsyncSession = Depends(get_db)):
    lead = await _get_lead(body.lead_id, db)
    data = await _generate_one(lead.name, lead.company, lead.role or "", lead.signal or "", body.sequence_n)
    return data


@router.post("/cmo/draft", dependencies=[Depends(require_admin)])
async def save_draft(body: SaveDraftIn, db: AsyncSession = Depends(get_db)):
    await _get_lead(body.lead_id, db)
    em = CmoEmail(lead_id=body.lead_id, subject=body.subject, body=body.body, sequence_n=body.sequence_n)
    db.add(em)
    await db.commit()
    await db.refresh(em)
    return {"email_id": em.id}


@router.get("/cmo/drafts", dependencies=[Depends(require_admin)])
async def list_drafts(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(CmoEmail, CmoLead)
        .join(CmoLead, CmoEmail.lead_id == CmoLead.id)
        .where(CmoEmail.status == "draft", CmoLead.email != "")
        .order_by(CmoEmail.created_at)
    )).all()
    return [
        {
            "email_id": e.id, "lead_id": l.id,
            "name": l.name, "company": l.company, "to_email": l.email,
            "subject": e.subject, "body": e.body, "sequence_n": e.sequence_n,
        }
        for e, l in rows
    ]


# ── CMO: Quick-add ─────────────────────────────────────────────────────────

class QuickAddIn(BaseModel):
    name: str
    company: str
    email: str = ""
    role: str = ""
    signal: str = ""
    linkedin_url: str = ""


@router.post("/cmo/quick-add", dependencies=[Depends(require_admin)])
async def quick_add(body: QuickAddIn, db: AsyncSession = Depends(get_db)):
    # Check duplicate
    if body.email:
        exists = (await db.execute(
            select(CmoLead).where(CmoLead.email == body.email)
        )).scalar_one_or_none()
        if exists:
            raise HTTPException(400, "Déjà existant")

    lead = CmoLead(name=body.name, company=body.company, email=body.email,
                   role=body.role, signal=body.signal, linkedin_url=body.linkedin_url)
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    data = await _generate_one(lead.name, lead.company, lead.role or "", lead.signal or "", 1)
    em = CmoEmail(lead_id=lead.id, subject=data["subject"], body=data["body"], sequence_n=1)
    db.add(em)
    await db.commit()
    await db.refresh(em)

    return {"lead_id": lead.id, "email_id": em.id, "subject": data["subject"], "body": data["body"]}


# ── CMO: Send ──────────────────────────────────────────────────────────────

class SendIn(BaseModel):
    email_ids: list[int]


@router.post("/cmo/send", dependencies=[Depends(require_admin)])
async def send_emails(body: SendIn, db: AsyncSession = Depends(get_db)):
    resend_key = os.getenv("RESEND_API_KEY", "")
    if not resend_key:
        raise HTTPException(500, "RESEND_API_KEY not set")

    rows = (await db.execute(
        select(CmoEmail, CmoLead)
        .join(CmoLead, CmoEmail.lead_id == CmoLead.id)
        .where(CmoEmail.id.in_(body.email_ids), CmoEmail.status == "draft")
    )).all()

    sent = failed = 0
    errors = []

    async with httpx.AsyncClient() as client:
        for em, lead in rows[:25]:
            try:
                html = em.body.replace("\n", "<br>")
                r = await client.post(
                    "https://api.resend.com/emails",
                    json={
                        "from": "Baptiste <baptiste@kronvex.io>",
                        "to": [lead.email],
                        "subject": em.subject,
                        "text": em.body,
                        "html": f"<p style='font-family:Georgia,serif;font-size:15px;line-height:1.7;max-width:560px'>{html}</p>",
                    },
                    headers={"Authorization": f"Bearer {resend_key}"},
                    timeout=10,
                )
                if r.status_code in (200, 201):
                    em.status = "sent"
                    em.sent_at = datetime.now(timezone.utc)
                    lead.status = "contacted"
                    sent += 1
                else:
                    failed += 1
                    errors.append({"email": lead.email, "error": r.text})
            except Exception as exc:
                failed += 1
                errors.append({"email": lead.email, "error": str(exc)})

    await db.commit()
    return {"sent": sent, "failed": failed, "errors": errors}


# ── CMO: Search (DDG + LinkedIn) ───────────────────────────────────────────

class SearchIn(BaseModel):
    keywords: str = ""
    role: str = ""
    location: str = "France"
    industry: str = ""
    max_results: int = 20


class EnrichIn(BaseModel):
    name: str
    company: str


_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
_GENERIC = {'info', 'contact', 'hello', 'support', 'team', 'noreply',
            'no-reply', 'admin', 'sales', 'press', 'legal', 'careers'}


def _parse_linkedin(r: dict) -> dict | None:
    url   = r.get("href", "")
    title = r.get("title", "")
    body  = r.get("body", "")
    if "linkedin.com/in/" not in url:
        return None
    name = company = role = ""
    title_clean = re.sub(r"\s*[|\u2014\u2013]\s*LinkedIn.*$", "", title, flags=re.I).strip()
    if " - " in title_clean:
        parts = title_clean.split(" - ", 1)
        name = parts[0].strip()
        m = re.search(r"^(.+?)\s+(?:at|chez|@|·)\s+(.+)$", parts[1], re.I)
        if m:
            role, company = m.group(1).strip(), m.group(2).strip()
        else:
            role = parts[1].strip()
    elif title_clean:
        name = title_clean
    if not role and body:
        dots = [p.strip() for p in re.split(r"[·•|]", body) if p.strip()]
        if len(dots) >= 3:
            name = name or dots[0]
            role = role or dots[1]
            company = company or dots[2]
    if not name:
        return None
    company = re.sub(r"\s*[,·]\s*(Paris|Lyon|France|Bordeaux|Nantes|Toulouse).*$", "", company, flags=re.I).strip()
    return {"name": name, "role": role, "company": company, "email": "",
            "linkedin_url": url, "snippet": body[:200], "already_added": False}


@router.post("/cmo/search", dependencies=[Depends(require_admin)])
async def search_leads(body: SearchIn, db: AsyncSession = Depends(get_db)):
    try:
        from ddgs import DDGS
    except ImportError:
        raise HTTPException(500, "ddgs package not installed")

    parts = ["site:linkedin.com/in"]
    if body.role:
        parts.append(f'"{body.role}"')
    if body.keywords:
        parts.append(body.keywords)
    if body.industry:
        parts.append(body.industry)
    if body.location:
        parts.append(body.location)

    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(" ".join(parts), max_results=body.max_results, region="fr-fr"):
                parsed = _parse_linkedin(r)
                if parsed:
                    results.append(parsed)
                time.sleep(0.1)
    except Exception as e:
        return [{"error": str(e)}]

    # Mark already-added
    existing_emails = set((await db.execute(
        select(CmoLead.email).where(CmoLead.email != "")
    )).scalars().all())
    existing_urls = set((await db.execute(
        select(CmoLead.linkedin_url).where(CmoLead.linkedin_url != "")
    )).scalars().all())

    for r in results:
        r["already_added"] = (r["email"] in existing_emails or r["linkedin_url"] in existing_urls)

    return results


@router.post("/cmo/enrich", dependencies=[Depends(require_admin)])
async def enrich_lead(body: EnrichIn):
    # DDG email search
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            for r in ddgs.text(f'"{body.name}" "{body.company}"', max_results=8):
                text_blob = r.get("body", "") + " " + r.get("title", "")
                for email in _EMAIL_RE.findall(text_blob):
                    local = email.split("@")[0].lower()
                    if local not in _GENERIC and not email.endswith((".png", ".jpg")):
                        return {"email": email, "confidence": "found"}
    except Exception:
        pass

    # Fallback: guess from company name
    slug = re.sub(r"[^a-z0-9]", "", body.company.lower())
    parts = body.name.strip().split()
    if slug and parts:
        first = parts[0].lower()
        last  = parts[-1].lower() if len(parts) > 1 else ""
        guessed = f"{first}.{last}@{slug}.com" if last else f"{first}@{slug}.com"
        return {"email": guessed, "confidence": "guessed"}

    return {"email": "", "confidence": ""}


# ── Helpers ────────────────────────────────────────────────────────────────

async def _get_lead(lead_id: int, db: AsyncSession) -> CmoLead:
    lead = (await db.execute(select(CmoLead).where(CmoLead.id == lead_id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


def _lead_dict(l: CmoLead) -> dict:
    return {
        "id": l.id, "name": l.name, "company": l.company, "email": l.email,
        "role": l.role, "use_case": l.use_case, "signal": l.signal,
        "linkedin_url": l.linkedin_url, "status": l.status,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    }


def _email_dict(e: CmoEmail) -> dict:
    return {
        "id": e.id, "lead_id": e.lead_id, "subject": e.subject, "body": e.body,
        "sequence_n": e.sequence_n, "status": e.status,
        "sent_at": e.sent_at.isoformat() if e.sent_at else None,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
