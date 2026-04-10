from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx, os

from app.database import get_db
from app.models import Review
from app.schemas import ReviewCreate, ReviewPublic
from app.rate_limit import ip_rate_limit
from app.config import settings

router = APIRouter(prefix="/reviews", tags=["Reviews"])


def _check_admin(token: str):
    if not settings.admin_token or token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Invalid token")


@router.post("", response_model=ReviewPublic, status_code=201,
             dependencies=[Depends(ip_rate_limit(3, 3600))])
async def submit_review(data: ReviewCreate, db: AsyncSession = Depends(get_db)):
    review = Review(
        name=data.name,
        role=data.role,
        stars=data.stars,
        message=data.message,
        review_type=data.review_type,
        approved=False,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    resend_key = os.getenv("RESEND_API_KEY", "")
    if resend_key:
        stars_str = f" — {'★' * (data.stars or 0)}" if data.stars else ""
        base = os.getenv("FRONTEND_URL", "https://api.kronvex.io")
        approve_url = f"{base}/reviews/{review.id}/approve?token={settings.admin_token}"
        delete_url  = f"{base}/reviews/{review.id}/delete?token={settings.admin_token}"
        html = f"""
        <div style="font-family:sans-serif;max-width:600px;padding:20px">
          <h2 style="color:#4a7ef5">New {data.review_type} on Kronvex{stars_str}</h2>
          <p><strong>From:</strong> {data.name}{' · ' + data.role if data.role else ''}</p>
          <p><strong>Message:</strong></p>
          <div style="background:#f5f5f5;padding:16px;border-radius:8px;white-space:pre-wrap">{data.message}</div>
          <div style="margin-top:24px;display:flex;gap:12px">
            <a href="{approve_url}" style="background:#4a7ef5;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold">✓ Approve</a>
            <a href="{delete_url}" style="background:#ef4444;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold">✗ Delete</a>
          </div>
        </div>"""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    "https://api.resend.com/emails",
                    json={
                        "from": "Kronvex Reviews <hello@kronvex.io>",
                        "to": ["baptiste@kronvex.io"],
                        "subject": f"[{data.review_type.upper()}] {data.name}{stars_str}",
                        "html": html,
                    },
                    headers={"Authorization": f"Bearer {resend_key}"},
                    timeout=8,
                )
        except Exception:
            pass

    return review


@router.get("", response_model=list[ReviewPublic])
async def get_reviews(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Review)
        .where(Review.approved == True)
        .order_by(Review.created_at.desc())
        .limit(50)
    )
    return result.scalars().all()


@router.get("/admin", response_class=HTMLResponse)
async def admin_reviews(token: str, db: AsyncSession = Depends(get_db)):
    _check_admin(token)
    result = await db.execute(select(Review).order_by(Review.created_at.desc()))
    reviews = result.scalars().all()
    base = os.getenv("FRONTEND_URL", "https://api.kronvex.io")

    def row(rv):
        status = "<span style='color:#22c55e;font-weight:700'>✓ LIVE</span>" if rv.approved else "<span style='color:#f59e0b;font-weight:700'>⏳ PENDING</span>"
        stars = "★" * (rv.stars or 0) if rv.stars else "—"
        approve_btn = "" if rv.approved else f"<a href='{base}/reviews/{rv.id}/approve?token={token}' style='background:#4a7ef5;color:#fff;padding:6px 14px;border-radius:4px;text-decoration:none;font-size:12px;margin-right:6px'>Approve</a>"
        delete_btn = f"<a href='{base}/reviews/{rv.id}/delete?token={token}' style='background:#ef4444;color:#fff;padding:6px 14px;border-radius:4px;text-decoration:none;font-size:12px' onclick=\"return confirm('Delete this review?')\">Delete</a>"
        return f"""<tr style='border-bottom:1px solid #1e2d45'>
          <td style='padding:12px 8px'>{status}</td>
          <td style='padding:12px 8px'><strong style='color:#e8edf8'>{rv.name}</strong><br><span style='color:#4a5568;font-size:12px'>{rv.role or '—'}</span></td>
          <td style='padding:12px 8px;color:#e09030'>{stars}</td>
          <td style='padding:12px 8px;max-width:320px;font-size:13px;color:#8292b4'>{rv.message[:200]}{'…' if len(rv.message)>200 else ''}</td>
          <td style='padding:12px 8px;font-size:12px;color:#4a5568;white-space:nowrap'>{rv.created_at.strftime('%d %b %Y')}</td>
          <td style='padding:12px 8px;white-space:nowrap'>{approve_btn}{delete_btn}</td>
        </tr>"""

    rows = "\n".join(row(rv) for rv in reviews) if reviews else "<tr><td colspan='6' style='text-align:center;padding:32px;color:#4a5568'>No reviews yet.</td></tr>"
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>Reviews Admin · Kronvex</title>
    <style>*{{box-sizing:border-box}}body{{font-family:'JetBrains Mono',monospace,sans-serif;padding:32px;background:#0d1117;color:#e8edf8;margin:0}}h1{{color:#4a7ef5;font-size:20px;margin-bottom:4px}}table{{width:100%;border-collapse:collapse;background:#141b26;border:1px solid #1e2d45;border-radius:8px;overflow:hidden}}th{{background:#1a2235;padding:10px 8px;text-align:left;font-size:11px;color:#4a7ef5;text-transform:uppercase;letter-spacing:.8px;border-bottom:1px solid #1e2d45}}td{{vertical-align:top}}a{{color:#4a7ef5}}</style>
    </head><body>
    <h1>Kronvex · Reviews Admin</h1>
    <p style='color:#4a5568;font-size:12px;margin-bottom:24px'>{len(reviews)} review(s) · <a href='https://kronvex.io/reviews' style='color:#4a7ef5'>View live page ↗</a></p>
    <table><thead><tr><th>Status</th><th>Author</th><th>Stars</th><th>Message</th><th>Date</th><th>Actions</th></tr></thead>
    <tbody>{rows}</tbody></table></body></html>""")


@router.get("/{review_id}/approve", response_class=HTMLResponse)
async def approve_review(review_id: str, token: str, db: AsyncSession = Depends(get_db)):
    _check_admin(token)
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    review.approved = True
    await db.commit()
    return HTMLResponse("<html><body style='font-family:sans-serif;text-align:center;padding:60px'><h2 style='color:#4a7ef5'>✓ Review approved</h2><p>It is now visible on <a href='https://kronvex.io/reviews'>kronvex.io/reviews</a>.</p></body></html>")


@router.get("/{review_id}/delete", response_class=HTMLResponse)
async def delete_review(review_id: str, token: str, db: AsyncSession = Depends(get_db)):
    _check_admin(token)
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    await db.delete(review)
    await db.commit()
    return HTMLResponse("<html><body style='font-family:sans-serif;text-align:center;padding:60px'><h2 style='color:#ef4444'>✗ Review deleted</h2><p>The review has been permanently removed.</p></body></html>")
