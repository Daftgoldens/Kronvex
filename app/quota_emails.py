"""
Quota alert emails — sent via Resend when a user's memory quota crosses 80% or 100%.

Functions:
  send_quota_warning_email(email, plan, used, limit)  → 80% alert
  send_quota_reached_email(email, plan, used, limit)  → 100% alert

Both are fire-and-forget: call with asyncio.create_task() from auth.py.
Never raises — errors are logged only.
"""
import os
import httpx

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FRONTEND_URL   = os.getenv("FRONTEND_URL", "https://kronvex.io")


# ── PUBLIC API ─────────────────────────────────────────────────────────────────

async def send_quota_warning_email(email: str, plan: str, used: int, limit: int) -> None:
    """Send an 80% memory quota warning email."""
    pct = round(used / limit * 100) if limit else 0
    body = _quota_body(used=used, limit=limit, pct=pct, is_full=False)
    await _send(
        to=email,
        subject="Your Kronvex memory quota is 80% full",
        preheader=f"You've used {used:,} of {limit:,} memories ({pct}%). Time to plan your next move.",
        title="You're at 80% of your memory quota.",
        subtitle=f"{plan.capitalize()} plan · {used:,} / {limit:,} memories used",
        body=body,
        cta_label="Upgrade plan →",
        cta_url=f"{FRONTEND_URL}/pricing",
        cta2_label="Open Dashboard",
        cta2_url=f"{FRONTEND_URL}/dashboard",
    )


async def send_quota_reached_email(email: str, plan: str, used: int, limit: int) -> None:
    """Send a 100% memory quota reached email."""
    pct = 100
    body = _quota_body(used=used, limit=limit, pct=pct, is_full=True)
    await _send(
        to=email,
        subject="Your Kronvex memory quota is full",
        preheader=f"You've hit your {limit:,}-memory limit on the {plan.capitalize()} plan. New memories are blocked.",
        title="Your memory quota is full.",
        subtitle=f"{plan.capitalize()} plan · {limit:,} / {limit:,} memories used",
        body=body,
        cta_label="Upgrade now →",
        cta_url=f"{FRONTEND_URL}/pricing",
        cta2_label="Open Dashboard",
        cta2_url=f"{FRONTEND_URL}/dashboard",
    )


# ── EMAIL BODY ─────────────────────────────────────────────────────────────────

def _quota_body(used: int, limit: int, pct: int, is_full: bool) -> str:
    bar_color   = "#e53e3e" if is_full else "#e09030"
    bar_width   = min(pct, 100)
    status_text = (
        "New memories are <strong style=\"color:#e53e3e\">blocked</strong> until you upgrade."
        if is_full else
        "You have <strong style=\"color:#e8edf8\">{:,} memories remaining</strong> before new storage is blocked.".format(limit - used)
    )
    alert_color  = "rgba(229,62,62,0.1)"  if is_full else "rgba(224,144,48,0.1)"
    alert_border = "rgba(229,62,62,0.35)" if is_full else "rgba(224,144,48,0.35)"
    icon         = "⛔" if is_full else "⚠️"

    return f"""
    <!-- ALERT BANNER -->
    <div style="background:{alert_color};border:1px solid {alert_border};border-radius:8px;padding:16px 20px;margin-bottom:28px">
      <div style="font-size:13px;color:#e8edf8;font-weight:700;margin-bottom:4px">{icon}&nbsp; Memory quota {"full" if is_full else "warning"}</div>
      <div style="font-size:13px;color:#8a9bb8;line-height:1.6">{status_text}</div>
    </div>

    <!-- USAGE BAR -->
    <div style="margin-bottom:28px">
      <div style="display:flex;justify-content:space-between;margin-bottom:8px">
        <span style="font-size:11px;color:#4a5a70;font-family:Arial,Helvetica,sans-serif;letter-spacing:1px;font-weight:700">MEMORY USAGE</span>
        <span style="font-size:11px;color:{bar_color};font-family:Arial,Helvetica,sans-serif;font-weight:700">{pct}%</span>
      </div>
      <!-- outer track -->
      <div style="background:#1a2235;border-radius:4px;height:10px;overflow:hidden">
        <div style="background:{bar_color};width:{bar_width}%;height:10px;border-radius:4px"></div>
      </div>
      <div style="display:flex;justify-content:space-between;margin-top:6px">
        <span style="font-size:11px;color:#4a5a70">{used:,} used</span>
        <span style="font-size:11px;color:#4a5a70">{limit:,} total</span>
      </div>
    </div>

    <!-- PLAN TABLE -->
    <p style="color:#8a9bb8;line-height:1.7;margin:0 0 16px;font-size:14px">
      Here's what upgrading looks like:
    </p>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-bottom:28px">
      <tr style="background:rgba(255,255,255,0.04)">
        <td style="padding:9px 14px;font-size:10px;color:#4a5a70;font-family:Arial,Helvetica,sans-serif;font-weight:700;letter-spacing:1px;border-bottom:1px solid rgba(255,255,255,0.05)">PLAN</td>
        <td style="padding:9px 14px;font-size:10px;color:#4a5a70;font-family:Arial,Helvetica,sans-serif;font-weight:700;letter-spacing:1px;border-bottom:1px solid rgba(255,255,255,0.05)">PRICE</td>
        <td style="padding:9px 14px;font-size:10px;color:#4a5a70;font-family:Arial,Helvetica,sans-serif;font-weight:700;letter-spacing:1px;border-bottom:1px solid rgba(255,255,255,0.05)">MEMORIES</td>
        <td style="padding:9px 14px;font-size:10px;color:#4a5a70;font-family:Arial,Helvetica,sans-serif;font-weight:700;letter-spacing:1px;border-bottom:1px solid rgba(255,255,255,0.05)">AGENTS</td>
      </tr>
      <tr style="background:rgba(74,126,245,0.07)">
        <td style="padding:9px 14px;font-size:13px;color:#e8edf8;font-weight:700;border-bottom:1px solid rgba(255,255,255,0.04)">Starter</td>
        <td style="padding:9px 14px;font-size:13px;color:#4a7ef5;font-weight:700;border-bottom:1px solid rgba(255,255,255,0.04)">€49/mo</td>
        <td style="padding:9px 14px;font-size:13px;color:#e8edf8;font-weight:700;border-bottom:1px solid rgba(255,255,255,0.04)">10,000</td>
        <td style="padding:9px 14px;font-size:13px;color:#e8edf8;font-weight:700;border-bottom:1px solid rgba(255,255,255,0.04)">1</td>
      </tr>
      <tr>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8;border-bottom:1px solid rgba(255,255,255,0.04)">Pro</td>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8;border-bottom:1px solid rgba(255,255,255,0.04)">€149/mo</td>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8;border-bottom:1px solid rgba(255,255,255,0.04)">25,000</td>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8;border-bottom:1px solid rgba(255,255,255,0.04)">3</td>
      </tr>
      <tr>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8;border-bottom:1px solid rgba(255,255,255,0.04)">Growth</td>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8;border-bottom:1px solid rgba(255,255,255,0.04)">€349/mo</td>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8;border-bottom:1px solid rgba(255,255,255,0.04)">100,000</td>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8;border-bottom:1px solid rgba(255,255,255,0.04)">5</td>
      </tr>
      <tr>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8">Scale</td>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8">€999/mo</td>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8">Unlimited</td>
        <td style="padding:9px 14px;font-size:13px;color:#c8d4e8">20</td>
      </tr>
    </table>

    <p style="color:#4a5a70;font-size:13px;line-height:1.7;margin:0 0 0;border-top:1px solid rgba(255,255,255,0.05);padding-top:20px">
      Questions? Just reply — I read every message.
    </p>
    """


# ── SEND HELPER ────────────────────────────────────────────────────────────────

async def _send(
    to: str,
    subject: str,
    preheader: str,
    title: str,
    subtitle: str,
    body: str,
    cta_label: str,
    cta_url: str,
    cta2_label: str = "",
    cta2_url: str = "",
) -> None:
    """Send an email via Resend. Never raises — errors are logged only."""
    if not RESEND_API_KEY:
        print(f"[QUOTA EMAIL] RESEND_API_KEY not set — skipping email to {to}")
        return

    cta2_block = ""
    if cta2_label and cta2_url:
        cta2_block = f"""<a href="{cta2_url}" style="display:inline-block;background:transparent;color:#4a7ef5;text-decoration:none;padding:12px 24px;border-radius:4px;font-weight:600;font-size:12px;letter-spacing:0.8px;border:1px solid rgba(74,126,245,0.35);font-family:Arial,Helvetica,sans-serif">{cta2_label}</a>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:#080d14;font-family:Arial,Helvetica,sans-serif">
<span style="display:none;font-size:1px;color:#080d14;max-height:0;overflow:hidden;mso-hide:all">{preheader}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;</span>
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#080d14;min-width:100%">
<tr><td align="center" style="padding:40px 20px">
<table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%">

  <!-- HEADER -->
  <tr><td style="padding:0 0 28px 0">
    <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
      <td>
        <span style="font-size:13px;font-weight:800;letter-spacing:4px;color:#e8edf8;font-family:Arial,Helvetica,sans-serif">KRONVEX</span>
      </td>
      <td align="right">
        <span style="font-size:10px;color:#1e2e45;letter-spacing:1.5px;font-family:Arial,Helvetica,sans-serif">PERSISTENT MEMORY API</span>
      </td>
    </tr></table>
  </td></tr>

  <!-- CARD -->
  <tr><td style="background-color:#0d1117;border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:36px 36px 32px">
    <h1 style="margin:0 0 6px;font-size:24px;font-weight:700;color:#e8edf8;letter-spacing:-0.3px;line-height:1.25;font-family:Arial,Helvetica,sans-serif">{title}</h1>
    <p style="margin:0 0 24px;font-size:12px;color:#2a3a55;line-height:1.6;font-family:Arial,Helvetica,sans-serif;letter-spacing:0.5px">{subtitle}</p>
    <div style="height:1px;background-color:#1a2640;margin:0 0 28px"></div>
    {body}
    <!-- CTAs -->
    <table cellpadding="0" cellspacing="0" border="0" style="margin-top:28px"><tr>
      <td style="padding-right:10px">
        <a href="{cta_url}" style="display:inline-block;background-color:#4a7ef5;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:4px;font-weight:700;font-size:12px;letter-spacing:0.8px;font-family:Arial,Helvetica,sans-serif">{cta_label}</a>
      </td>
      {'<td>' + cta2_block + '</td>' if cta2_block else ''}
    </tr></table>
  </td></tr>

  <!-- FOOTER -->
  <tr><td style="padding:24px 4px 0">
    <p style="font-size:11px;color:#1e2e45;line-height:1.8;margin:0;text-align:center;font-family:Arial,Helvetica,sans-serif">
      Kronvex &middot; <a href="mailto:hello@kronvex.io" style="color:#1e2e45;text-decoration:none">hello@kronvex.io</a> &middot; Made in France &#127464;&#127479;<br>
      <a href="{FRONTEND_URL}" style="color:#1e2e45;text-decoration:none">kronvex.io</a>
      &nbsp;&middot;&nbsp;
      <a href="{{{{unsubscribe_url}}}}" style="color:#1e2e45;text-decoration:none">Unsubscribe</a>
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
                    "from": "Kronvex <hello@kronvex.io>",
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                timeout=12,
            )
            print(f"[QUOTA EMAIL] {subject[:50]} → {to} : {resp.status_code}")
            if resp.status_code not in (200, 201):
                print(f"[QUOTA EMAIL ERROR] {resp.text[:200]}")
    except Exception as e:
        print(f"[QUOTA EMAIL ERROR] {e}")
