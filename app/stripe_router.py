"""
Kronvex — stripe_router.py  (v2 — fixed)
Handles: Stripe Checkout, webhook plan upgrade, onboarding emails.

ENV VARS required:
  STRIPE_SECRET_KEY      sk_test_...
  STRIPE_WEBHOOK_SECRET  whsec_...
  SUPABASE_URL           https://kkulzoaoqkfbpefponlp.supabase.co
  SUPABASE_SERVICE_KEY   service_role key
  RESEND_API_KEY         re_...
  FRONTEND_URL           https://kronvex.io
"""

import os
import json
import httpx
import stripe


async def _sub_raw(sub_id: str) -> dict:
    """Fetch a Stripe subscription as a plain dict via raw REST API.
    Bypasses Stripe SDK v9+ typed objects which silently drop some fields
    (e.g. current_period_end is not stored in the SDK's internal dict)."""
    sk = os.getenv("STRIPE_SECRET_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.stripe.com/v1/subscriptions/{sub_id}",
                auth=(sk, ""),
            )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}
import httpx
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models import ApiKey, Agent
from app.auth import _generate_key
from app.plans import get_plan, PLANS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["billing"])

# ── Config ────────────────────────────────────────────────────────────────────
stripe.api_key     = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET     = os.getenv("STRIPE_WEBHOOK_SECRET", "")
FRONTEND_URL       = os.getenv("FRONTEND_URL", "https://kronvex.io")
RESEND_API_KEY     = os.getenv("RESEND_API_KEY", "")

# Price IDs — replace placeholders with real Stripe price IDs from your dashboard
# Stripe Dashboard → Products → create product for each plan → copy price ID
PRICE_TO_PLAN = {
    # Monthly
    "price_REPLACE_BUILDER_29":  "builder",   # €29/mo  ⚠ replace with real ID
    "price_REPLACE_STARTUP_99":  "startup",   # €99/mo  ⚠ replace with real ID
    "price_REPLACE_BUSINESS_349": "business", # €349/mo ⚠ replace with real ID
    # Yearly (~20% off) — add entries once created in Stripe Dashboard
    # "price_1XXXXXXXXXXXXXXX": "builder",  # ~€264/yr (€22/mo × 12)
    # "price_1XXXXXXXXXXXXXXX": "startup",  # ~€888/yr (€74/mo × 12)
    # "price_1XXXXXXXXXXXXXXX": "business", # ~€3,144/yr (€262/mo × 12)
}

PLAN_LABELS = {
    "builder":  "Builder",
    "startup":  "Startup",
    "business": "Business",
    "enterprise": "Enterprise",
}


# ── Resend helper ─────────────────────────────────────────────────────────────
async def send_email(to: str, subject: str, html: str):
    if not RESEND_API_KEY:
        logger.warning("[EMAIL] RESEND_API_KEY not set — skipping")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            payload = {
                "from": "Kronvex <hello@kronvex.io>",
                "to": [to],
                "subject": subject,
                "html": html
            }
            logger.info(f"[EMAIL] Sending to {to}: {subject}")
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json=payload,
            )
            if r.status_code not in (200, 201):
                logger.error(f"[EMAIL] FAILED Resend {r.status_code}: {r.text[:500]}")
                logger.error(f"[EMAIL] Payload was: to={to}, subject={subject}")
            else:
                resp_data = r.json()
                logger.info(f"[EMAIL] SUCCESS id={resp_data.get('id')} to={to}")
    except Exception as e:
        logger.error(f"[EMAIL] Exception sending to {to}: {e}", exc_info=True)


# ── Upgrade helper (uses SQLAlchemy, same DB as app) ─────────────────────────
async def upgrade_key_by_email(db: AsyncSession, email: str, plan: str) -> tuple[ApiKey, str, str] | None:
    """Find the API key for this email and upgrade it to the given plan."""
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.contact_email == email)
        .order_by(ApiKey.created_at.desc())
        .limit(1)
    )
    key = result.scalars().first()
    if not key:
        logger.warning(f"[WEBHOOK] No api_key found for email {email}")
        return None

    p = get_plan(plan)
    key.plan         = plan
    key.is_demo      = False
    key.memory_limit = p["memories"]
    key.agent_limit  = p["agents"]
    key.is_active    = True
    # Generate a fresh key so user gets it in the email
    full_key, key_hash, key_prefix = _generate_key()
    key.key_hash     = key_hash
    key.key_prefix   = key_prefix
    await db.commit()
    await db.refresh(key)

    # Auto-create first agent if none exists
    from app.models import Agent as AgentModel
    agents_result = await db.execute(
        select(AgentModel).where(AgentModel.api_key_id == key.id).limit(1)
    )
    existing_agent = agents_result.scalar_one_or_none()
    if not existing_agent:
        agent = AgentModel(
            name="Agent 001",
            description=f"Auto-created on {plan} plan upgrade",
            api_key_id=key.id
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        agent_id = str(agent.id)
        logger.info(f"[WEBHOOK] ✓ Auto-created agent for {email}: {agent_id}")
    else:
        agent_id = str(existing_agent.id)

    logger.info(f"[WEBHOOK] ✓ {email} upgraded to {plan}")
    return key, full_key, agent_id


# ── POST /billing/checkout ────────────────────────────────────────────────────
class CheckoutRequest(BaseModel):
    price_id: str
    customer_email: str = ""
    customer_name: str = ""

@router.post("/checkout")
async def create_checkout(data: CheckoutRequest, request: Request):
    if not stripe.api_key:
        raise HTTPException(500, "Stripe not configured")
    if data.price_id not in PRICE_TO_PLAN:
        raise HTTPException(400, f"Unknown price_id: {data.price_id}")

    plan = PRICE_TO_PLAN[data.price_id]

    # Get email from Supabase JWT if not provided
    customer_email = data.customer_email
    customer_name  = data.customer_name
    if not customer_email:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            from app.auth_router import SUPABASE_URL
            SUPA_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    r = await client.get(
                        f"{SUPABASE_URL}/auth/v1/user",
                        headers={"apikey": SUPA_KEY, "Authorization": auth_header},
                    )
                    if r.status_code == 200:
                        u = r.json()
                        customer_email = u.get("email", "")
                        customer_name  = customer_name or customer_email.split("@")[0]
            except Exception as e:
                logger.warning(f"[CHECKOUT] JWT lookup failed: {e}")

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": data.price_id, "quantity": 1}],
            customer_email=customer_email or None,
            metadata={
                "plan": plan,
                "customer_email": customer_email,
                "customer_name": customer_name,
            },
            success_url=f"{FRONTEND_URL}/dashboard?success=1",
            cancel_url=f"{FRONTEND_URL}/dashboard",
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except stripe.StripeError as e:
        raise HTTPException(400, str(e))


# ── POST /billing/webhook ─────────────────────────────────────────────────────
@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload       = await request.body()
    sig_header    = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except stripe.SignatureVerificationError:
        raise HTTPException(400, "Invalid signature")
    except Exception as e:
        raise HTTPException(400, str(e))

    event_type = event["type"]
    logger.info("stripe.webhook", extra={"event_type": event_type})

    try:
        # ── checkout.session.completed ─────────────────────────────────────
        if event_type == "checkout.session.completed":
            session        = event["data"]["object"]

            # ── Handle payment method update (setup mode) ────────────────────
            if session.get("mode") == "setup":
                try:
                    setup_intent_id = session.get("setup_intent")
                    sub_id = ((session.get("metadata") or {}).get("subscription_id"))
                    if setup_intent_id and sub_id:
                        si = stripe.SetupIntent.retrieve(setup_intent_id)
                        pm_id = getattr(si, "payment_method", None) or si.get("payment_method")
                        if pm_id:
                            stripe.Subscription.modify(sub_id, default_payment_method=pm_id)
                            logger.info(f"[WEBHOOK] payment method updated for sub {sub_id}")
                except Exception as e:
                    logger.error(f"[WEBHOOK] setup mode payment update failed: {e}")

            else:
                # ── Subscription checkout ─────────────────────────────────────
                customer_email = (
                    session.get("customer_details", {}).get("email")
                    or session.get("customer_email")
                    or session["metadata"].get("customer_email", "")
                )

                # 1) Plan depuis les métadonnées (chemin normal)
                plan = session["metadata"].get("plan", "")

                # 2) Fallback : récupérer price_id depuis les line_items Stripe
                if not plan:
                    try:
                        line_items = stripe.checkout.Session.list_line_items(
                            session["id"], limit=1
                        )
                        price_id = (
                            line_items.data[0].price.id if line_items.data else ""
                        )
                        plan = PRICE_TO_PLAN.get(price_id, "")
                        if plan:
                            logger.info(f"[WEBHOOK] plan résolu via line_items: {price_id} → {plan}")
                        else:
                            logger.warning(f"[WEBHOOK] price_id inconnu dans line_items: {price_id}")
                    except Exception as e:
                        logger.warning(f"[WEBHOOK] Impossible de récupérer les line_items: {e}")

                if not plan:
                    logger.error(f"[WEBHOOK] Could not resolve plan for session {session.get('id')} — aborting upgrade")
                    return JSONResponse({"ok": False, "reason": "unknown_plan"}, status_code=200)
                amount_total = session.get("amount_total", 0)
                amount_str   = f"€{amount_total / 100:.0f}"

                logger.info(f"[WEBHOOK] checkout completed: {customer_email} → {plan} ({amount_str})")

                if customer_email:
                    result = await upgrade_key_by_email(db, customer_email, plan)
                    if result:
                        key_obj, full_key, agent_id = result
                        await send_email(
                            to=customer_email,
                            subject=f"✓ Your Kronvex {plan.capitalize()} plan is active",
                            html=_payment_confirmed_email(plan, amount_str, full_key, agent_id),
                        )
                    else:
                        logger.warning(f"[WEBHOOK] No key to upgrade for {customer_email}")
                else:
                    logger.warning("[WEBHOOK] checkout.session.completed: no customer_email")

        # ── customer.subscription.updated ──────────────────────────────────
        elif event_type == "customer.subscription.updated":
            sub      = event["data"]["object"]
            # IMPORTANT: skip cancellations AND upgrades-from-cancel
            is_cancellation = bool(getattr(sub, "cancel_at_period_end", False))
            # Also check previous attributes to detect cancel being SET
            try:
                prev_attrs = getattr(event["data"], "previous_attributes", None) or {}
                prev_cancel = bool(getattr(prev_attrs, "cancel_at_period_end", False))
            except Exception:
                prev_cancel = False
            just_cancelled = is_cancellation and not prev_cancel
            sub_status = getattr(sub, "status", "") or ""
            if just_cancelled or is_cancellation:
                logger.info(f"[WEBHOOK] subscription.updated skipped — cancel_at_period_end={is_cancellation}")
            elif sub_status == "active":
                items_obj = getattr(sub, "items", None)
                items     = getattr(items_obj, "data", []) or []
                price_id  = items[0]["price"]["id"] if items else ""
                new_plan  = PRICE_TO_PLAN.get(price_id)
                if new_plan:
                    cust_id = getattr(sub, "customer", None)
                    try:
                        cust  = stripe.Customer.retrieve(cust_id)
                        email = getattr(cust, "email", "") or ""
                        if email:
                            result = await upgrade_key_by_email(db, email, new_plan)
                            if result:
                                key_obj, full_key, agent_id = result
                                # Schedule purge of excess memories (10-day grace period)
                                new_limit = key_obj.memory_limit
                                if new_limit is not None:
                                    key_obj.data_purge_at = datetime.now(timezone.utc) + timedelta(days=10)
                                    await db.commit()
                                    logger.info(f"[DOWNGRADE] data_purge_at scheduled in 10 days for {email} (new limit: {new_limit})")
                                await send_email(
                                    to=email,
                                    subject=f"✓ Your Kronvex {new_plan.capitalize()} plan is active",
                                    html=_payment_confirmed_email(new_plan, "", full_key, agent_id),
                                )
                                logger.info(f"[WEBHOOK] subscription.updated email sent to {email}")
                    except Exception as e:
                        logger.error(f"[WEBHOOK] subscription.updated error: {e}")

        # ── customer.subscription.deleted ──────────────────────────────────
        elif event_type == "customer.subscription.deleted":
            sub     = event["data"]["object"]
            cust_id = getattr(sub, "customer", None)
            try:
                cust  = stripe.Customer.retrieve(cust_id)
                email = getattr(cust, "email", "") or ""
                if email:
                    result = await db.execute(
                        select(ApiKey)
                        .where(ApiKey.contact_email == email, ApiKey.is_active == True)
                        .order_by(ApiKey.created_at.desc())
                        .limit(1)
                    )
                    key = result.scalars().first()
                    if key:
                        key.plan         = "demo"
                        key.is_demo      = True
                        key.memory_limit = 100
                        key.agent_limit  = 1
                        key.data_purge_at = datetime.now(timezone.utc) + timedelta(days=10)
                        await db.commit()
                        logger.info(f"[WEBHOOK] {email} downgraded to demo, data purge scheduled in 10 days")
            except Exception as e:
                logger.error(f"[WEBHOOK] subscription.deleted error: {e}")

        # ── invoice.payment_succeeded — reset monthly memory quota ──────────
        elif event_type == "invoice.payment_succeeded":
            invoice = event["data"]["object"]
            customer_email = getattr(invoice, "customer_email", None) or getattr(getattr(invoice, "customer_details", None), "email", "") or ""
            if customer_email:
                result = await db.execute(
                    select(ApiKey)
                    .where(ApiKey.contact_email == customer_email, ApiKey.is_active == True)
                    .order_by(ApiKey.created_at.desc())
                    .limit(1)
                )
                key = result.scalars().first()
                if key and key.plan not in ("demo", "free"):
                    key.cycle_memories_used = 0
                    key.cycle_reset_at = datetime.now(timezone.utc)
                    await db.commit()
                    logger.info(f"[WEBHOOK] Monthly memory quota reset for {customer_email} ({key.plan})")

        # ── invoice.payment_failed ──────────────────────────────────────────
        elif event_type == "invoice.payment_failed":
            invoice        = event["data"]["object"]
            customer_email = invoice.get("customer_email")
            if customer_email:
                await send_email(
                    to=customer_email,
                    subject="⚠️ Kronvex — payment failed",
                    html=_payment_failed_email(),
                )

    except Exception as e:
        # Always return 200 so Stripe doesn't retry endlessly
        logger.error(f"[WEBHOOK] Unhandled error in {event_type}: {e}", exc_info=True)

    return JSONResponse({"received": True})


# ── GET /billing/usage ────────────────────────────────────────────────────────
@router.get("/usage")
async def get_usage(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models import ApiCall
    from sqlalchemy import func, cast, Date

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")

    from app.auth_router import SUPABASE_URL
    SUPA_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPA_KEY, "Authorization": auth_header},
            )
        if r.status_code != 200:
            raise HTTPException(401, "Invalid token")
        email = r.json().get("email")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Token verification failed")

    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.contact_email == email, ApiKey.is_active == True)
        .order_by(ApiKey.created_at.desc())
        .limit(1)
    )
    key = result.scalars().first()
    if not key:
        return {"total": 0, "by_endpoint": {}, "last_14_days": [], "avg_latency_ms": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    ep_result = await db.execute(
        select(ApiCall.endpoint, func.count(ApiCall.id).label("cnt"))
        .where(ApiCall.api_key_id == key.id, ApiCall.called_at >= cutoff)
        .group_by(ApiCall.endpoint)
    )
    by_endpoint = {row.endpoint: row.cnt for row in ep_result.all()}

    day_result = await db.execute(
        select(cast(ApiCall.called_at, Date).label("day"), func.count(ApiCall.id).label("cnt"))
        .where(ApiCall.api_key_id == key.id, ApiCall.called_at >= datetime.now(timezone.utc) - timedelta(days=14))
        .group_by("day").order_by("day")
    )
    daily = [{"date": str(r.day), "calls": r.cnt} for r in day_result.all()]

    lat_result = await db.execute(
        select(func.avg(ApiCall.latency_ms))
        .where(ApiCall.api_key_id == key.id, ApiCall.called_at >= cutoff)
    )
    avg_lat = round(float(lat_result.scalar() or 0))

    return {
        "total": sum(by_endpoint.values()),
        "by_endpoint": by_endpoint,
        "last_14_days": daily,
        "avg_latency_ms": avg_lat,
    }


# ── GET /billing/plan-usage ───────────────────────────────────────────────────
@router.get(
    "/plan-usage",
    summary="Current plan usage",
    description=(
        "Returns the caller's current plan, billing period, API call count for the "
        "current month, total memories used vs plan limit, and agent count vs plan limit. "
        "Requires a Supabase JWT in the Authorization header (Bearer token from dashboard login)."
    ),
)
async def get_plan_usage(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models import ApiCall, Memory, Agent as AgentModel
    from sqlalchemy import func

    # ── Verify Supabase JWT ───────────────────────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")

    SUPA_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{os.getenv('SUPABASE_URL', 'https://kkulzoaoqkfbpefponlp.supabase.co')}/auth/v1/user",
                headers={"apikey": SUPA_KEY, "Authorization": auth_header},
            )
        if r.status_code != 200:
            raise HTTPException(401, "Invalid or expired token")
        email = r.json().get("email")
        if not email:
            raise HTTPException(400, "No email in token")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Token verification failed")

    # ── Find API key by email ─────────────────────────────────────────────────
    key_result = await db.execute(
        select(ApiKey)
        .where(ApiKey.contact_email == email, ApiKey.is_active == True)
        .order_by(ApiKey.created_at.desc())
        .limit(1)
    )
    key = key_result.scalars().first()
    if not key:
        raise HTTPException(404, "No API key found for this account")

    # ── Billing period (current calendar month) ───────────────────────────────
    now = datetime.now(timezone.utc)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        period_end = now.replace(year=now.year + 1, month=1, day=1) - timedelta(seconds=1)
    else:
        period_end = now.replace(month=now.month + 1, day=1) - timedelta(seconds=1)

    # ── API calls this month ──────────────────────────────────────────────────
    calls_result = await db.execute(
        select(func.count(ApiCall.id))
        .where(ApiCall.api_key_id == key.id, ApiCall.called_at >= period_start)
    )
    calls_used = calls_result.scalar_one()

    # ── Total memories across all agents ──────────────────────────────────────
    mem_result = await db.execute(
        select(func.count(Memory.id))
        .join(AgentModel, AgentModel.id == Memory.agent_id)
        .where(AgentModel.api_key_id == key.id)
    )
    memories_used = mem_result.scalar_one()

    # ── Agent count ───────────────────────────────────────────────────────────
    agent_result = await db.execute(
        select(func.count(AgentModel.id)).where(AgentModel.api_key_id == key.id)
    )
    agents_used = agent_result.scalar_one()

    # Fetch actual Stripe billing period end + cancel status for paid users
    stripe_period_end = None
    stripe_cancel_at_period_end = False
    if key.plan not in ('demo', 'enterprise'):
        try:
            stripe_customers = stripe.Customer.list(email=email, limit=1)
            if stripe_customers.data:
                stripe_subs = stripe.Subscription.list(
                    customer=stripe_customers.data[0].id, status="active", limit=1
                )
                if stripe_subs.data:
                    sub_id = stripe_subs.data[0].id
                    raw = await _sub_raw(sub_id)
                    stripe_period_end = raw.get('current_period_end') or None
                    if stripe_period_end is not None:
                        stripe_period_end = int(stripe_period_end)
                    stripe_cancel_at_period_end = bool(raw.get('cancel_at_period_end', False))
                    logger.warning(f"[PLAN-USAGE] sub={sub_id} period_end={stripe_period_end} cancel={stripe_cancel_at_period_end}")
        except Exception as e:
            logger.error(f"[PLAN-USAGE] Stripe fetch failed: {e}")

    return {
        "plan": key.plan,
        "billing_period": {
            "start": period_start.date().isoformat(),
            "end": period_end.date().isoformat(),
        },
        "stripe_period_end": stripe_period_end,
        "stripe_cancel_at_period_end": stripe_cancel_at_period_end,
        "api_calls": {
            "used": calls_used,
            "limit": None,
        },
        "memories": {
            "used": memories_used,
            "limit": key.memory_limit,
        },
        "agents": {
            "used": agents_used,
            "limit": key.agent_limit,
        },
    }


def _cancellation_email(plan: str, period_end_ts: int, full_key: str = "") -> str:
    """Email envoyé à l'utilisateur lors de l'annulation de l'abonnement."""
    import datetime
    end_date = datetime.datetime.fromtimestamp(period_end_ts, tz=datetime.timezone.utc)
    end_str = end_date.strftime("%d %B %Y")
    plan_label = PLAN_LABELS.get(plan, plan.capitalize())

    content = f"""
  <h2 style="color:#e8edf8;font-size:22px;font-weight:700;margin:0 0 8px">Abonnement annulé</h2>
  <p style="color:#8a9bb8;font-size:14px;margin:0 0 24px;line-height:1.6">
    Votre abonnement <strong style="color:#e8edf8">{plan_label}</strong> a bien été annulé.
    Vous continuez à bénéficier de toutes ses fonctionnalités jusqu'au :
  </p>

  <div style="background:#0d1628;border:1px solid rgba(74,126,245,0.2);border-radius:10px;padding:20px 24px;margin:0 0 24px;text-align:center">
    <div style="font-size:11px;letter-spacing:2px;color:#5a6b8a;font-family:'Courier New',monospace;margin-bottom:6px">FIN D'ACCÈS</div>
    <div style="font-size:26px;font-weight:700;color:#f5c842">{end_str}</div>
  </div>

  <p style="color:#8a9bb8;font-size:13px;margin:0 0 16px;line-height:1.7">
    Après cette date, votre compte passera automatiquement en plan <strong style="color:#e8edf8">Demo gratuit</strong>
    (100 mémoires, 1 agent). Vos données existantes sont conservées.
  </p>

  <p style="color:#8a9bb8;font-size:13px;margin:0 0 24px;line-height:1.7">
    Vous avez changé d'avis ? Réactivez votre abonnement avant le {end_str} :
  </p>

  <div style="text-align:center;margin:24px 0">
    <a href="{FRONTEND_URL}/dashboard" style="display:inline-block;background:linear-gradient(135deg,#4a7ef5,#6366f1);color:#fff;text-decoration:none;padding:14px 32px;border-radius:8px;font-weight:700;font-size:14px;letter-spacing:.5px">
      Réactiver mon abonnement →
    </a>
  </div>

  <p style="color:#5a6b8a;font-size:12px;margin:0;text-align:center;line-height:1.6">
    Des questions ? <a href="mailto:hello@kronvex.io" style="color:#4a7ef5;text-decoration:none">hello@kronvex.io</a>
  </p>
"""
    return _email_base(content)

# ── POST /billing/portal ──────────────────────────────────────────────────────
@router.post("/portal")
async def billing_portal(request: Request):
    """Return a Stripe Customer Portal URL for managing subscription/invoices/payment methods."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")

    from app.auth_router import SUPABASE_URL
    SUPA_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPA_KEY, "Authorization": auth_header},
            )
        if r.status_code != 200:
            raise HTTPException(401, "Invalid token")
        email = r.json().get("email", "")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Token verification failed")

    if not email:
        raise HTTPException(400, "Could not resolve customer email")

    try:
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            raise HTTPException(404, "No Stripe customer found for this account")
        cust_id = customers.data[0].id
        portal_session = stripe.billing_portal.Session.create(
            customer=cust_id,
            return_url=f"{FRONTEND_URL}/dashboard",
        )
        return {"portal_url": portal_session.url}
    except HTTPException:
        raise
    except stripe.StripeError as e:
        logger.error(f"[PORTAL] Stripe error: {e}")
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"[PORTAL] Unexpected error: {e}")
        raise HTTPException(500, f"Portal failed: {e}")


# ── GET /billing/invoices ─────────────────────────────────────────────────────
@router.get("/invoices")
async def list_invoices(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")

    from app.auth_router import SUPABASE_URL
    SUPA_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPA_KEY, "Authorization": auth_header},
            )
        if r.status_code != 200:
            raise HTTPException(401, "Invalid token")
        email = r.json().get("email", "")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Token verification failed")

    if not email:
        raise HTTPException(400, "Could not resolve customer email")

    try:
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            return {"invoices": []}
        cust_id = customers.data[0].id
        inv_list = stripe.Invoice.list(customer=cust_id, limit=24, status="paid")
        invoices = []
        for inv in inv_list.data:
            try:
                # Get plan name from first line item description
                plan_label = None
                try:
                    lines = getattr(inv, 'lines', None)
                    if lines and hasattr(lines, 'data') and lines.data:
                        plan_label = getattr(lines.data[0], 'description', None)
                except Exception:
                    pass
                invoices.append({
                    "id": inv.id,
                    "number": getattr(inv, "number", None),
                    "date": int(inv.created),
                    "amount": int(inv.total),
                    "currency": inv.currency,
                    "status": inv.status,
                    "pdf_url": getattr(inv, "invoice_pdf", None),
                    "hosted_url": getattr(inv, "hosted_invoice_url", None),
                    "plan_label": plan_label,
                })
            except Exception:
                pass
        return {"invoices": invoices}
    except HTTPException:
        raise
    except stripe.StripeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"[INVOICES] Unexpected error: {e}")
        raise HTTPException(500, f"Invoices failed: {e}")


# ── POST /billing/update-payment ──────────────────────────────────────────────
@router.post("/update-payment")
async def update_payment(request: Request):
    """Create a Stripe Checkout setup session to update payment method (no portal config needed)."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")

    from app.auth_router import SUPABASE_URL
    SUPA_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPA_KEY, "Authorization": auth_header},
            )
        if r.status_code != 200:
            raise HTTPException(401, "Invalid token")
        email = r.json().get("email", "")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Token verification failed")

    if not email:
        raise HTTPException(400, "Could not resolve customer email")

    try:
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            raise HTTPException(404, "No Stripe customer found for this account")
        cust_id = customers.data[0].id

        # Find active subscription to attach setup to
        subs = stripe.Subscription.list(customer=cust_id, status="active", limit=1)
        sub_id = subs.data[0].id if subs.data else None

        session_params = {
            "customer": cust_id,
            "payment_method_types": ["card"],
            "mode": "setup",
            "success_url": f"{FRONTEND_URL}/dashboard?payment_updated=1",
            "cancel_url": f"{FRONTEND_URL}/dashboard",
        }
        if sub_id:
            session_params["setup_intent_data"] = {
                "metadata": {"subscription_id": sub_id}
            }

        session = stripe.checkout.Session.create(**session_params)
        return {"url": session.url}
    except HTTPException:
        raise
    except stripe.StripeError as e:
        logger.error(f"[UPDATE-PAYMENT] Stripe error: {e}")
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"[UPDATE-PAYMENT] Unexpected error: {e}")
        raise HTTPException(500, f"Update payment failed: {e}")


# ── POST /billing/cancel ──────────────────────────────────────────────────────
@router.post("/cancel")
async def cancel_subscription(request: Request, db: AsyncSession = Depends(get_db)):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")

    from app.auth_router import SUPABASE_URL
    SUPA_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPA_KEY, "Authorization": auth_header},
            )
        email = r.json().get("email", "")
    except Exception:
        raise HTTPException(401, "Invalid token")

    if not email:
        raise HTTPException(401, "Could not retrieve email from token")
    try:
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            raise HTTPException(404, "No Stripe customer found for this account")
        cust_id = customers.data[0].id
        subs = stripe.Subscription.list(customer=cust_id, status="active", limit=1)
        if not subs.data:
            raise HTTPException(404, "No active subscription found")
        sub = subs.data[0]
        stripe.Subscription.modify(sub.id, cancel_at_period_end=True)
        raw = await _sub_raw(sub.id)
        period_end = int(raw['current_period_end']) if raw.get('current_period_end') else 0
        logger.warning(f"[CANCEL] period_end={period_end} for sub {sub.id}")

        # Send cancellation confirmation email (best-effort)
        try:
            key_result = await db.execute(
                select(ApiKey).where(ApiKey.contact_email == email, ApiKey.is_active == True)
                .order_by(ApiKey.created_at.desc())
                .limit(1)
            )
            key_obj = key_result.scalars().first()
            plan_name = key_obj.plan if key_obj else "starter"
            await send_email(
                to=email,
                subject=f"Votre abonnement Kronvex {PLAN_LABELS.get(plan_name, plan_name)} a été annulé",
                html=_cancellation_email(plan_name, period_end),
            )
        except Exception as e:
            logger.error(f"[CANCEL] Email error: {e}")

        return {"cancelled": True, "period_end": period_end}
    except HTTPException:
        raise
    except stripe.StripeError as e:
        logger.error(f"[CANCEL] Stripe error: {e}")
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"[CANCEL] Unexpected error: {e}")
        raise HTTPException(500, f"Cancel failed: {e}")


# ── GET /billing/status ───────────────────────────────────────────────────────
@router.get("/status")
async def get_subscription_status(request: Request):
    """Returns cancel_at_period_end + current_period_end for the current user."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")

    from app.auth_router import SUPABASE_URL
    SUPA_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPA_KEY, "Authorization": auth_header},
            )
        email = r.json().get("email", "")
    except Exception:
        raise HTTPException(401, "Invalid token")

    if not email:
        raise HTTPException(401, "Could not retrieve email from token")

    try:
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            return {"status": "no_subscription", "cancel_at_period_end": False, "current_period_end": None}
        subs = stripe.Subscription.list(customer=customers.data[0].id, status="active", limit=1)
        if not subs.data:
            return {"status": "no_subscription", "cancel_at_period_end": False, "current_period_end": None}
        sub_id = subs.data[0].id
        raw = await _sub_raw(sub_id)
        cancel_at = bool(raw.get('cancel_at_period_end', False))
        pe_int = int(raw['current_period_end']) if raw.get('current_period_end') else None
        logger.warning(f"[STATUS] sub={sub_id} cancel_at={cancel_at} period_end={pe_int}")
        return {
            "status": "active",
            "cancel_at_period_end": bool(cancel_at),
            "current_period_end": pe_int,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[STATUS] Error: {e}")
        return {"status": "unknown", "cancel_at_period_end": False, "current_period_end": None}


# ── POST /billing/resume ──────────────────────────────────────────────────────
@router.post("/resume")
async def resume_subscription(request: Request):
    """Re-activates a subscription scheduled for cancellation."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")

    from app.auth_router import SUPABASE_URL
    SUPA_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPA_KEY, "Authorization": auth_header},
            )
        email = r.json().get("email", "")
    except Exception:
        raise HTTPException(401, "Invalid token")

    if not email:
        raise HTTPException(401, "Could not retrieve email from token")

    try:
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            raise HTTPException(404, "No Stripe customer found")
        subs = stripe.Subscription.list(customer=customers.data[0].id, status="active", limit=1)
        if not subs.data:
            raise HTTPException(404, "No active subscription found")
        stripe.Subscription.modify(subs.data[0].id, cancel_at_period_end=False)
        return {"resumed": True}
    except HTTPException:
        raise
    except stripe.StripeError as e:
        logger.error(f"[RESUME] Stripe error: {e}")
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"[RESUME] Unexpected error: {e}")
        raise HTTPException(500, f"Resume failed: {e}")


# ── POST /billing/apply-discount ─────────────────────────────────────────────
RETENTION_COUPON_ID = "RETENTION10"

@router.post("/apply-discount")
async def apply_discount(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")

    from app.auth_router import SUPABASE_URL
    SUPA_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPA_KEY, "Authorization": auth_header},
            )
        email = r.json().get("email", "")
    except Exception:
        raise HTTPException(401, "Invalid token")

    if not email:
        raise HTTPException(401, "Could not retrieve email from token")
    try:
        # Ensure retention coupon exists
        try:
            stripe.Coupon.retrieve(RETENTION_COUPON_ID)
        except Exception:
            stripe.Coupon.create(
                id=RETENTION_COUPON_ID,
                percent_off=10,
                duration="once",
                name="10% retention discount",
            )

        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            raise HTTPException(404, "No Stripe customer found for this account")
        cust_id = customers.data[0].id
        subs = stripe.Subscription.list(customer=cust_id, status="active", limit=1)
        if not subs.data:
            raise HTTPException(404, "No active subscription found")
        sub = subs.data[0]

        # Only apply if not already discounted
        if getattr(sub, 'discount', None):
            return {"applied": False, "reason": "already_discounted"}

        stripe.Subscription.modify(sub.id, coupon=RETENTION_COUPON_ID)
        return {"applied": True}
    except HTTPException:
        raise
    except stripe.StripeError as e:
        logger.error(f"[DISCOUNT] Stripe error: {e}")
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"[DISCOUNT] Unexpected error: {e}")
        raise HTTPException(500, f"Apply discount failed: {e}")


# ── Email templates ───────────────────────────────────────────────────────────
def _email_base(content: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#050810;font-family:'Helvetica Neue',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#050810;padding:40px 20px">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">
<tr><td style="padding:0 0 24px">
  <table cellpadding="0" cellspacing="0"><tr>
    <td valign="middle" style="padding-right:10px">
      <svg width="28" height="34" viewBox="0 0 18 22" fill="none" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="ml-st" x1="9" y1="2.5" x2="9" y2="9" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#f5c842" stop-opacity=".95"/><stop offset="100%" stop-color="#c07820" stop-opacity=".6"/></linearGradient><linearGradient id="ml-sb" x1="9" y1="13" x2="9" y2="19.5" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#c07820" stop-opacity=".55"/><stop offset="100%" stop-color="#f5c842" stop-opacity=".95"/></linearGradient><linearGradient id="ml-cr" x1="9" y1="-1.5" x2="9" y2="1" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#f5c842"/><stop offset="100%" stop-color="#b06010"/></linearGradient><linearGradient id="ml-br" x1="0" y1="0" x2="18" y2="0" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#7a4008" stop-opacity=".5"/><stop offset="50%" stop-color="#e09030"/><stop offset="100%" stop-color="#7a4008" stop-opacity=".5"/></linearGradient><clipPath id="ml-ct"><path d="M1,2.5 L17,2.5 L11,9 L7,9 Z"/></clipPath><clipPath id="ml-cb"><path d="M7,13 L11,13 L17,19.5 L1,19.5 Z"/></clipPath></defs><ellipse cx="8.3" cy="-.1" rx="2.4" ry="1.0" fill="url(#ml-cr)" opacity=".96" transform="rotate(-15 8.3 -.1)"/><ellipse cx="5.6" cy="-.6" rx="2.2" ry=".92" fill="url(#ml-cr)" opacity=".89" transform="rotate(-32 5.6 -.6)"/><ellipse cx="3.0" cy="-.5" rx="2.0" ry=".82" fill="url(#ml-cr)" opacity=".80" transform="rotate(-50 3.0 -.5)"/><ellipse cx="9.7" cy="-.1" rx="2.4" ry="1.0" fill="url(#ml-cr)" opacity=".96" transform="rotate(15 9.7 -.1)"/><ellipse cx="12.4" cy="-.6" rx="2.2" ry=".92" fill="url(#ml-cr)" opacity=".89" transform="rotate(32 12.4 -.6)"/><ellipse cx="15.0" cy="-.5" rx="2.0" ry=".82" fill="url(#ml-cr)" opacity=".80" transform="rotate(50 15.0 -.5)"/><rect x="0" y="1.2" width="18" height="1.1" rx=".55" fill="url(#ml-br)"/><path d="M1,2.5 L17,2.5 L11,9 L7,9 Z" fill="rgba(74,126,245,0.09)" stroke="#4a7ef5" stroke-width=".9" stroke-linejoin="round" stroke-opacity=".65"/><path d="M7,9 Q8.5,11 7,13" fill="none" stroke="#4a7ef5" stroke-width=".7" stroke-opacity=".5"/><path d="M11,9 Q9.5,11 11,13" fill="none" stroke="#4a7ef5" stroke-width=".7" stroke-opacity=".5"/><path d="M7,13 L11,13 L17,19.5 L1,19.5 Z" fill="rgba(74,126,245,0.06)" stroke="#4a7ef5" stroke-width=".9" stroke-linejoin="round" stroke-opacity=".6"/><rect x="0" y="20" width="18" height="1.1" rx=".55" fill="url(#ml-br)"/><rect x="1" y="2.5" width="16" height="6.5" fill="url(#ml-st)" clip-path="url(#ml-ct)"/><rect x="1" y="13" width="16" height="6.5" fill="url(#ml-sb)" clip-path="url(#ml-cb)"/></svg>
    </td>
    <td valign="middle">
      <span style="font-family:'Courier New',monospace;font-size:13px;font-weight:700;letter-spacing:3px;color:#4a7ef5">KRONVEX</span>
    </td>
  </tr></table>
</td></tr>
<tr><td style="background:#0a0f1e;border:1px solid rgba(255,255,255,.07);border-radius:12px;padding:40px">
  {content}
</td></tr>
<tr><td style="padding:24px 0 0;font-size:11px;color:#2a3a55;line-height:1.7">
  
<div style="background:#0a1628;border:1px solid rgba(74,126,245,0.15);border-radius:8px;padding:16px 20px;margin:20px 0">
  <div style="font-size:9px;color:#e09030;letter-spacing:2px;margin-bottom:10px;font-family:'Courier New',monospace">📍 RETROUVER VOS CREDENTIALS</div>
  <p style="margin:0 0 6px;color:#8a9bb8;font-size:12px;line-height:1.6">
    Votre <strong style="color:#c8d4e8">API Key</strong> et votre <strong style="color:#c8d4e8">Agent ID</strong> sont toujours accessibles dans votre Dashboard :
  </p>
  <p style="margin:0;color:#8a9bb8;font-size:12px">
    → <a href="{FRONTEND_URL}/dashboard" style="color:#4a7ef5;text-decoration:none">kronvex.io/dashboard</a>
    &nbsp;·&nbsp; onglet <strong style="color:#e8edf8">API Key</strong>
    &nbsp;·&nbsp; cliquez <strong style="color:#e8edf8">"SHOW"</strong> pour révéler la clé
  </p>
</div>
Questions? <a href="mailto:hello@kronvex.io" style="color:#4a7ef5;text-decoration:none">hello@kronvex.io</a><br>
  <a href="{FRONTEND_URL}" style="color:#2a3a55;text-decoration:none">kronvex.io</a> · Built in Paris 🇫🇷
</td></tr>
</table></td></tr></table>
</body></html>"""


def _payment_confirmed_email(plan: str, amount: str, api_key: str, agent_id: str = "") -> str:
    plan_label = PLAN_LABELS.get(plan, plan.capitalize())
    p = get_plan(plan)
    agents_str  = str(p["agents"]) if p["agents"] else "Unlimited"
    mem_str     = f"{p['memories']:,}" if p["memories"] else "Unlimited"

    features = {
        "dev":     ["3 agents", "5,000 memories", "3 memory types", "<40ms recall"],
        "starter": ["5 agents", "15,000 memories", "3 memory types", "<40ms recall"],
        "pro":     ["10 agents", "75,000 memories", "Session filtering", "Memory explorer", "Audit trail"],
        "growth":  ["30 agents", "300,000 memories", "TTL auto-decay", "Confidence scoring", "Priority infra", "GDPR DPA"],
        "scale":   ["Unlimited agents", "Unlimited memories", "Custom TTL", "GDPR DPA", "SLA", "Dedicated support"],
    }
    feats = "".join(f'<li style="margin:4px 0;color:#c8d4e8">✓ {f}</li>' for f in features.get(plan, []))
    agent_block = ""
    if agent_id:
        agent_block = f"""
<div style="background:#080e1c;border:1px solid rgba(255,255,255,.07);border-left:3px solid #e09030;border-radius:0 8px 8px 0;padding:16px 20px;margin:16px 0">
  <div style="font-size:9px;color:#e09030;letter-spacing:2px;margin-bottom:8px;font-family:'Courier New',monospace">YOUR AGENT ID</div>
  <code style="font-family:'Courier New',monospace;font-size:13px;color:#c8d4e8;word-break:break-all">{agent_id}</code>
</div>"""


    content = f"""
<h1 style="margin:0 0 8px;font-size:26px;font-weight:700;color:#e8edf8">Your {plan.capitalize()} plan is active.</h1>
<p style="margin:0 0 28px;font-size:13px;color:#4a5a70">Plan <strong style="color:#e8edf8">{plan_label}</strong> · Thank you for choosing Kronvex.</p>
<div style="height:1px;background:linear-gradient(90deg,rgba(74,126,245,.4),transparent);margin:0 0 28px"></div>

<p style="color:#8a9bb8;line-height:1.7;margin:0 0 20px">
  Your plan is now active. Save your API key — <strong style="color:#f87171">find it anytime in your <a href='https://kronvex.io/dashboard' style='color:#4a7ef5'>dashboard</a>.</strong>
</p>

<div style="background:#080e1c;border:1px solid rgba(255,255,255,.07);border-left:3px solid #4a7ef5;border-radius:0 8px 8px 0;padding:16px 20px;margin:16px 0">
  <div style="font-size:9px;color:#4a7ef5;letter-spacing:2px;margin-bottom:8px;font-family:'Courier New',monospace">YOUR API KEY</div>
  <code style="font-family:'Courier New',monospace;font-size:13px;color:#c8d4e8;word-break:break-all">{api_key}</code>
</div>

<div style="background:#0d1a2e;border:1px solid rgba(74,126,245,.15);border-radius:8px;padding:20px;margin:20px 0">
  <div style="font-size:10px;color:#4a7ef5;letter-spacing:2px;margin-bottom:12px;font-family:'Courier New',monospace">YOUR PLAN INCLUDES</div>
  <ul style="margin:0;padding-left:4px;list-style:none">{feats}</ul>
</div>

<div style="margin-top:28px">
  <a href="{FRONTEND_URL}/dashboard" style="display:inline-block;background:#1e56d9;color:#fff;text-decoration:none;padding:14px 28px;border-radius:6px;font-weight:700;font-size:12px;letter-spacing:.8px;margin-right:12px">Open Dashboard →</a>
  <a href="{FRONTEND_URL}/docs" style="display:inline-block;background:transparent;color:#4a7ef5;text-decoration:none;padding:13px 28px;border-radius:6px;font-weight:600;font-size:12px;border:1px solid rgba(74,126,245,.35)">Read the Docs</a>
</div>
"""
    return _email_base(content)


def _payment_failed_email() -> str:
    content = f"""
<h1 style="margin:0 0 8px;font-size:24px;font-weight:700;color:#f87171">Payment failed</h1>
<p style="margin:0 0 24px;font-size:13px;color:#4a5a70">We couldn't process your payment. Please update your payment method to keep your plan active.</p>
<a href="{FRONTEND_URL}/dashboard" style="display:inline-block;background:#1e56d9;color:#fff;text-decoration:none;padding:14px 28px;border-radius:6px;font-weight:700;font-size:12px">UPDATE PAYMENT METHOD →</a>
"""
    return _email_base(content)
