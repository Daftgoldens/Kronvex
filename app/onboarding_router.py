"""
Kronvex Onboarding Email Sequence
Triggered by: POST /auth/onboarding/schedule  (called right after demo key creation)
Sends: J+1 (first memory nudge), J+3 (use cases), J+7 (upgrade pitch)

In production, use a proper task queue (Celery, ARQ, Railway cron).
Here we use asyncio background tasks via FastAPI BackgroundTasks.
"""
import os
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

router = APIRouter(tags=["Onboarding"])

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FRONTEND_URL   = os.getenv("FRONTEND_URL", "https://kronvex.io")
API_BASE       = os.getenv("API_BASE_URL", "https://api.kronvex.io")


# ── SCHEMA ────────────────────────────────────────────────────────────────────

class OnboardingScheduleRequest(BaseModel):
    email: str
    name: str
    api_key: str        # full key — pre-filled in email CTAs
    agent_id: str


# ── ENDPOINT ──────────────────────────────────────────────────────────────────

@router.post("/onboarding/schedule", summary="Schedule onboarding email sequence")
async def schedule_onboarding(data: OnboardingScheduleRequest, bg: BackgroundTasks):
    """Schedule the 3-email onboarding sequence in the background."""
    bg.add_task(_run_sequence, data)
    return {"scheduled": True, "emails": ["J+1", "J+3", "J+7"]}


# ── SEQUENCE RUNNER ───────────────────────────────────────────────────────────

async def _run_sequence(data: OnboardingScheduleRequest):
    """Schedule all 3 emails via Resend scheduled_at — restart-safe, no sleep."""
    now = datetime.now(timezone.utc)
    await _send_j1(data, send_at=now + timedelta(hours=24))
    await _send_j3(data, send_at=now + timedelta(days=3))
    await _send_j7(data, send_at=now + timedelta(days=7))


# ── EMAIL J+1 — First memory nudge ────────────────────────────────────────────

async def _send_j1(data: OnboardingScheduleRequest, send_at=None):
    name_short = data.name.split()[0] if data.name else "there"
    body = f"""
    <p style="color:#8892a4;line-height:1.85;margin:0 0 8px;font-size:16px;font-weight:600;font-family:Arial,Helvetica,sans-serif">
      Greetings, {name_short}.
    </p>
    <p style="color:#8892a4;line-height:1.85;margin:0 0 28px;font-size:14px;font-family:Arial,Helvetica,sans-serif">
      Your demo key is live and your agent stands ready — an oracle awaiting its first memory.
      The inscription takes <strong style="color:#e8edf8">under 30 seconds</strong>.
      Three rites, in order:
    </p>

    <div style="margin:0 0 6px">
      <span style="display:inline-block;background:rgba(74,126,245,0.12);color:#4a7ef5;font-size:10px;font-weight:700;letter-spacing:2px;padding:4px 12px;border-radius:4px;font-family:Arial,Helvetica,sans-serif">&#9670; RITE I &mdash; ENGRAVE A MEMORY</span>
    </div>
    {_code_block("curl", f"""curl -X POST {API_BASE}/api/v1/agents/{data.agent_id}/remember \\
  -H "X-API-Key: {data.api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{{"content": "User prefers concise answers and uses Python", "memory_type": "semantic"}}'""", "#4a7ef5")}

    <div style="margin:24px 0 6px">
      <span style="display:inline-block;background:rgba(224,144,48,0.12);color:#e09030;font-size:10px;font-weight:700;letter-spacing:2px;padding:4px 12px;border-radius:4px;font-family:Arial,Helvetica,sans-serif">&#9670; RITE II &mdash; SUMMON IT BACK</span>
    </div>
    {_code_block("curl", f"""curl -X POST {API_BASE}/api/v1/agents/{data.agent_id}/recall \\
  -H "X-API-Key: {data.api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{{"query": "user coding preferences", "top_k": 5}}'""", "#e09030")}

    <div style="margin:24px 0 6px">
      <span style="display:inline-block;background:rgba(74,126,245,0.08);color:#7aa8ff;font-size:10px;font-weight:700;letter-spacing:2px;padding:4px 12px;border-radius:4px;font-family:Arial,Helvetica,sans-serif">&#9670; RITE III &mdash; WEAVE INTO CONTEXT</span>
    </div>
    {_code_block("curl", f"""curl -X POST {API_BASE}/api/v1/agents/{data.agent_id}/inject-context \\
  -H "X-API-Key: {data.api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{{"message": "user coding preferences", "top_k": 5}}'""", "#7aa8ff")}

    <div style="border-top:1px solid #1a2235;margin:28px 0 0;padding-top:20px">
      <p style="color:#4a5570;font-size:13px;margin:0;line-height:1.7;font-family:Arial,Helvetica,sans-serif">
        Full API reference &rarr; <a href="{FRONTEND_URL}/docs" style="color:#4a7ef5;text-decoration:none">{FRONTEND_URL}/docs</a>
      </p>
    </div>
    """
    await _send(
        to=data.email,
        subject="Your Kronvex demo key is ready — here's how to use it",
        preheader="3-step quick start: store, recall, inject context — under 30 seconds.",
        title="Your first memory is one curl away.",
        subtitle="Demo key active &nbsp;·&nbsp; Agent ready &nbsp;·&nbsp; 100 memories available",
        body=body,
        cta_label="Open Dashboard &rarr;",
        cta_url=f"{FRONTEND_URL}/dashboard",
        cta2_label="Read the Docs",
        cta2_url=f"{FRONTEND_URL}/docs",
        send_at=send_at,
    )


# ── EMAIL J+3 — Use cases + social proof ─────────────────────────────────────

async def _send_j3(data: OnboardingScheduleRequest, send_at=None):
    name_short = data.name.split()[0] if data.name else "there"
    body = f"""
    <p style="color:#8892a4;line-height:1.85;margin:0 0 8px;font-size:16px;font-weight:600;font-family:Arial,Helvetica,sans-serif">
      {name_short},
    </p>
    <p style="color:#8892a4;line-height:1.85;margin:0 0 28px;font-size:14px;font-family:Arial,Helvetica,sans-serif">
      Your demo key has been active for a few days. Below are three archetypes teams are forging
      with Kronvex memory right now &mdash; does one resemble your work?
    </p>

    {_use_case_block(
        "&#127981; Customer support oracles",
        "Engrave ticket history, frustration level, and past resolutions. Before each interaction, inject the last 5 relevant memories &mdash; your agent greets returning users as if it has known them for years.",
        f'curl -X POST {API_BASE}/api/v1/agents/{data.agent_id}/remember \\\n  -H "X-API-Key: {data.api_key}" \\\n  -d \'{{"content": "User reported billing issue on 2024-03, resolved with \u20ac10 credit", "memory_type": "episodic"}}\'',
        "#4a7ef5"
    )}

    {_use_case_block(
        "&#128187; Coding companions",
        "Remember the stack, conventions, and past decisions for each project. Your coding agent stops asking the same questions and starts making consistent, informed suggestions.",
        f'curl -X POST {API_BASE}/api/v1/agents/{data.agent_id}/remember \\\n  -H "X-API-Key: {data.api_key}" \\\n  -d \'{{"content": "Project uses FastAPI + asyncpg, no ORM, snake_case everywhere", "memory_type": "semantic"}}\'',
        "#e09030"
    )}

    {_use_case_block(
        "&#128200; Sales &amp; outreach envoys",
        "Chronicle every touchpoint: demo attended, objections raised, budget revealed. Recall before the next call so your agent &mdash; or your rep &mdash; is never caught off guard.",
        f'curl -X POST {API_BASE}/api/v1/agents/{data.agent_id}/remember \\\n  -H "X-API-Key: {data.api_key}" \\\n  -d \'{{"content": "Lead mentioned \u20ac500/mo budget cap, interested in annual deal", "memory_type": "episodic"}}\'',
        "#7aa8ff"
    )}

    <div style="border-top:1px solid #1a2235;margin:28px 0 0;padding-top:20px">
      <p style="color:#4a5570;font-size:13px;margin:0;line-height:1.7;font-family:Arial,Helvetica,sans-serif">
        The same three sacred endpoints serve all patterns &mdash;
        <code style="color:#7aa8ff;font-family:'Courier New',monospace;background:rgba(74,126,245,0.08);padding:1px 5px;border-radius:3px">/remember</code>,
        <code style="color:#7aa8ff;font-family:'Courier New',monospace;background:rgba(74,126,245,0.08);padding:1px 5px;border-radius:3px">/recall</code>,
        <code style="color:#7aa8ff;font-family:'Courier New',monospace;background:rgba(74,126,245,0.08);padding:1px 5px;border-radius:3px">/inject-context</code>.
        No SDK required.
      </p>
    </div>
    """
    await _send(
        to=data.email,
        subject="3 ways teams are using Kronvex memory",
        preheader="Support bots, coding assistants, sales agents — all with the same 3 endpoints.",
        title="What are teams forging with Kronvex?",
        subtitle="Three patterns you can ship today with your demo key.",
        body=body,
        cta_label="Try the live demo &rarr;",
        cta_url=f"{FRONTEND_URL}#demo",
        cta2_label="See use cases",
        cta2_url=f"{FRONTEND_URL}/use-cases",
        send_at=send_at,
    )


# ── EMAIL J+7 — Upgrade pitch ─────────────────────────────────────────────────

async def _send_j7(data: OnboardingScheduleRequest, send_at=None):
    name_short = data.name.split()[0] if data.name else "there"
    body = f"""
    <p style="color:#8892a4;line-height:1.85;margin:0 0 8px;font-size:16px;font-weight:600;font-family:Arial,Helvetica,sans-serif">
      {name_short},
    </p>
    <p style="color:#8892a4;line-height:1.85;margin:0 0 24px;font-size:14px;font-family:Arial,Helvetica,sans-serif">
      Seven days have passed since your agent first opened its eyes. How much of your
      100-memory scroll have you filled?
    </p>

    <a href="{FRONTEND_URL}/dashboard" style="display:block;background:rgba(74,126,245,0.07);border:1px solid rgba(74,126,245,0.22);border-radius:10px;padding:18px 22px;margin-bottom:28px;text-decoration:none">
      <span style="font-size:10px;color:#4a7ef5;letter-spacing:2px;font-family:Arial,Helvetica,sans-serif;font-weight:700">&#9670; CONSULT YOUR SCROLL</span><br>
      <span style="font-size:13px;color:#4a5570;margin-top:6px;display:inline-block;font-family:Arial,Helvetica,sans-serif">Dashboard &rarr; Memory &amp; API usage &rarr;</span>
    </a>

    <p style="color:#8892a4;line-height:1.85;margin:0 0 20px;font-size:14px;font-family:Arial,Helvetica,sans-serif">
      If you are approaching the limit &mdash; or forging something real &mdash; here is what lies beyond the demo:
    </p>

    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-bottom:24px;border-radius:8px;overflow:hidden">
      <tr style="background:#1a2235">
        <td style="padding:10px 14px;font-size:10px;color:#4a5570;font-family:Arial,Helvetica,sans-serif;font-weight:700;letter-spacing:1.5px">PLAN</td>
        <td style="padding:10px 14px;font-size:10px;color:#4a5570;font-family:Arial,Helvetica,sans-serif;font-weight:700;letter-spacing:1.5px">PRICE</td>
        <td style="padding:10px 14px;font-size:10px;color:#4a5570;font-family:Arial,Helvetica,sans-serif;font-weight:700;letter-spacing:1.5px">MEMORIES</td>
        <td style="padding:10px 14px;font-size:10px;color:#4a5570;font-family:Arial,Helvetica,sans-serif;font-weight:700;letter-spacing:1.5px">AGENTS</td>
      </tr>
      <tr style="background:#141b26">
        <td style="padding:10px 14px;font-size:13px;color:#4a5570;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">Demo</td>
        <td style="padding:10px 14px;font-size:13px;color:#4a5570;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">Free</td>
        <td style="padding:10px 14px;font-size:13px;color:#4a5570;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">100</td>
        <td style="padding:10px 14px;font-size:13px;color:#4a5570;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">1</td>
      </tr>
      <tr style="background:rgba(74,126,245,0.09)">
        <td style="padding:11px 14px;font-size:13px;color:#e8edf8;font-weight:700;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">&#9670; Dev</td>
        <td style="padding:11px 14px;font-size:13px;color:#4a7ef5;font-weight:700;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">&euro;19/mo</td>
        <td style="padding:11px 14px;font-size:13px;color:#e8edf8;font-weight:700;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">5,000</td>
        <td style="padding:11px 14px;font-size:13px;color:#e8edf8;font-weight:700;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">3</td>
      </tr>
      <tr style="background:#141b26">
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">Starter</td>
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">&euro;49/mo</td>
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">15,000</td>
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">5</td>
      </tr>
      <tr style="background:#141b26">
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">Pro</td>
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">&euro;249/mo</td>
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">75,000</td>
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">10</td>
      </tr>
      <tr style="background:#141b26">
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">Growth</td>
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">&euro;599/mo</td>
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">300,000</td>
        <td style="padding:10px 14px;font-size:13px;color:#8892a4;border-top:1px solid #1a2235;font-family:Arial,Helvetica,sans-serif">30</td>
      </tr>
    </table>

    <div style="background:#0d1117;border:1px solid #1a2235;border-left:3px solid #e09030;border-radius:0 10px 10px 0;padding:20px 24px;margin-bottom:24px">
      <div style="font-size:10px;color:#e09030;letter-spacing:2px;margin-bottom:16px;font-family:Arial,Helvetica,sans-serif;font-weight:700">&#9670; WHAT DEV UNLOCKS</div>
      {''.join(_feature_line(f) for f in [
        "5,000 memories &mdash; 50&times; more than demo",
        "3 agents instead of 1",
        "Memory TTL &amp; decay configuration",
        "Confidence scoring on every recall",
        "Full API call history in dashboard",
      ])}
    </div>

    <div style="border-top:1px solid #1a2235;padding-top:20px">
      <p style="color:#4a5570;font-size:13px;line-height:1.7;margin:0;font-family:Arial,Helvetica,sans-serif">
        Questions before you ascend? Just reply &mdash; I read every message.
      </p>
    </div>
    """
    await _send(
        to=data.email,
        subject="How much memory is your agent using?",
        preheader="Check your usage — and see what Dev unlocks at €19/mo.",
        title="Ready to go beyond the demo?",
        subtitle="Dev &nbsp;·&nbsp; &euro;19/mo &nbsp;·&nbsp; No commitment &nbsp;·&nbsp; Cancel anytime",
        body=body,
        cta_label="Upgrade to Dev &rarr;",
        cta_url=f"{FRONTEND_URL}/dashboard",
        cta2_label="See all plans",
        cta2_url=f"{FRONTEND_URL}/pricing",
        send_at=send_at,
    )


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _code_block(label: str, code: str, color: str = "#4a7ef5") -> str:
    return f"""<div style="background:#0d1117;border:1px solid #1a2235;border-left:3px solid {color};border-radius:0 8px 8px 0;padding:18px 20px;margin:16px 0;overflow-x:auto">
  <div style="font-size:9px;color:{color};letter-spacing:2px;margin-bottom:10px;font-family:'Courier New',monospace;font-weight:700;opacity:0.9">{label}</div>
  <pre style="font-family:'Courier New',monospace;font-size:11px;color:#8892a4;line-height:1.75;margin:0;white-space:pre-wrap;word-break:break-all">{code}</pre>
</div>"""


def _use_case_block(title: str, description: str, code: str, accent: str = "#4a7ef5") -> str:
    return f"""<div style="margin-bottom:20px;border:1px solid #1a2235;border-left:3px solid {accent};border-radius:0 10px 10px 0;overflow:hidden">
  <div style="padding:18px 22px 14px;background:#141b26">
    <div style="font-size:13px;font-weight:700;color:#e8edf8;margin-bottom:6px;font-family:Arial,Helvetica,sans-serif">{title}</div>
    <div style="font-size:13px;color:#8892a4;line-height:1.7;font-family:Arial,Helvetica,sans-serif">{description}</div>
  </div>
  <div style="background:#0d1117;padding:14px 22px;border-top:1px solid #1a2235">
    <pre style="font-family:'Courier New',monospace;font-size:11px;color:#4a5570;line-height:1.65;margin:0;white-space:pre-wrap;word-break:break-all">{code}</pre>
  </div>
</div>"""


def _feature_line(text: str) -> str:
    return f"""<div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:10px">
  <span style="color:#e09030;font-size:13px;flex-shrink:0;margin-top:1px">&#9670;</span>
  <span style="font-size:13px;color:#8892a4;line-height:1.6;font-family:Arial,Helvetica,sans-serif">{text}</span>
</div>"""


async def _send(to: str, subject: str, preheader: str, title: str, subtitle: str,
                body: str, cta_label: str, cta_url: str,
                cta2_label: str = "", cta2_url: str = "", send_at=None):
    """Send email via Resend."""
    if not RESEND_API_KEY:
        print(f"[ONBOARDING] RESEND_API_KEY not set — skipping email to {to}")
        return

    cta2_block = ""
    if cta2_label and cta2_url:
        cta2_block = f"""<a href="{cta2_url}" style="display:inline-block;background:transparent;color:#4a7ef5;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;font-size:12px;letter-spacing:0.8px;border:1px solid rgba(74,126,245,0.35);font-family:Arial,Helvetica,sans-serif">{cta2_label}</a>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:#0d1117;font-family:Arial,Helvetica,sans-serif">
<span style="display:none;font-size:1px;color:#0d1117;max-height:0;overflow:hidden;mso-hide:all">{preheader}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;</span>

<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#0d1117;min-width:100%">
<tr><td align="center" style="padding:40px 20px">
<table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%">

  <!-- HEADER -->
  <tr><td style="padding:0 0 24px 0">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td>
          <span style="font-size:15px;font-weight:800;letter-spacing:5px;color:#e09030;font-family:Arial,Helvetica,sans-serif">KRONVEX</span>
        </td>
        <td align="right">
          <span style="font-size:10px;color:#1a2235;letter-spacing:2px;font-family:Arial,Helvetica,sans-serif;font-weight:600">MEMORY FOR AI AGENTS</span>
        </td>
      </tr>
    </table>
    <!-- Meander decoration -->
    <div style="text-align:center;color:#1a2235;font-size:13px;letter-spacing:6px;margin-top:14px;font-family:Arial,Helvetica,sans-serif;user-select:none">
      &#9672;&nbsp;&middot;&nbsp;&middot;&nbsp;&middot;&nbsp;&#9633;&nbsp;&#9633;&nbsp;&#9633;&nbsp;&middot;&nbsp;&middot;&nbsp;&middot;&nbsp;&#9672;&nbsp;&middot;&nbsp;&middot;&nbsp;&middot;&nbsp;&#9633;&nbsp;&#9633;&nbsp;&#9633;&nbsp;&middot;&nbsp;&middot;&nbsp;&middot;&nbsp;&#9672;
    </div>
  </td></tr>

  <!-- CARD -->
  <tr><td style="background-color:#141b26;border:1px solid #1a2235;border-radius:12px;padding:36px 36px 32px">

    <!-- Card header bar -->
    <div style="border-left:3px solid #4a7ef5;padding-left:14px;margin-bottom:24px">
      <h1 style="margin:0 0 6px;font-size:22px;font-weight:700;color:#e8edf8;letter-spacing:-0.2px;line-height:1.3;font-family:Arial,Helvetica,sans-serif">{title}</h1>
      <p style="margin:0;font-size:11px;color:#4a5570;line-height:1.6;font-family:Arial,Helvetica,sans-serif;letter-spacing:0.8px">{subtitle}</p>
    </div>

    <div style="height:1px;background-color:#1a2235;margin:0 0 28px"></div>

    {body}

    <!-- CTAs -->
    <table cellpadding="0" cellspacing="0" border="0" style="margin-top:32px"><tr>
      <td style="padding-right:12px">
        <a href="{cta_url}" style="display:inline-block;background-color:#4a7ef5;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:600;font-size:13px;letter-spacing:0.5px;font-family:Arial,Helvetica,sans-serif">{cta_label}</a>
      </td>
      {'<td>' + cta2_block + '</td>' if cta2_block else ''}
    </tr></table>

  </td></tr>

  <!-- FOOTER -->
  <tr><td style="padding:28px 4px 0">
    <!-- Meander decoration -->
    <div style="text-align:center;color:#1a2235;font-size:11px;letter-spacing:5px;margin-bottom:16px;font-family:Arial,Helvetica,sans-serif;user-select:none">
      &middot;&nbsp;&#9633;&nbsp;&middot;&nbsp;&#9633;&nbsp;&middot;&nbsp;&#9633;&nbsp;&middot;
    </div>
    <p style="font-size:11px;color:#1a2235;line-height:1.9;margin:0;text-align:center;font-family:Arial,Helvetica,sans-serif">
      <strong style="color:#1a2235;letter-spacing:1px">KRONVEX</strong> &mdash; Memory for AI Agents<br>
      <a href="mailto:hello@kronvex.io" style="color:#1a2235;text-decoration:none">hello@kronvex.io</a>
      &nbsp;&middot;&nbsp;
      <a href="{FRONTEND_URL}" style="color:#1a2235;text-decoration:none">kronvex.io</a>
      &nbsp;&middot;&nbsp;
      Made in France &#127464;&#127479;<br>
      <span style="font-size:10px;color:#1a2235;letter-spacing:0.3px">
        Like Kronos, we hold time &mdash; your agent&apos;s memory persists across every session.
      </span><br>
      <a href="{{{{unsubscribe_url}}}}" style="color:#1a2235;text-decoration:none;font-size:10px">Unsubscribe</a>
    </p>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                json={
                    "from": "Baptiste at Kronvex <hello@kronvex.io>",
                    "to": [to],
                    "subject": subject,
                    "html": html,
                    **({"scheduled_at": send_at.strftime("%Y-%m-%dT%H:%M:%SZ")} if send_at else {}),
                },
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                timeout=12,
            )
            print(f"[ONBOARDING] {subject[:40]} → {to} : {resp.status_code}")
            if resp.status_code not in (200, 201):
                print(f"[ONBOARDING ERROR] {resp.text[:200]}")
    except Exception as e:
        print(f"[ONBOARDING ERROR] {e}")
