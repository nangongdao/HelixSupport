"""CSAT satisfaction survey endpoint (backlog item).

A one-time, unauthenticated rating link issued when a conversation is
resolved. The token is random and single-use; submitting a rating reflows
into the feedback system (and thus the supervisor quality dashboard).

The link points at ``GET /api/csat/{token}``, which renders a minimal
self-contained HTML form the customer can rate from a browser; the form
POSTs the rating back to ``POST /api/csat/{token}``. Both paths share the
same atomic single-use write, so a token can never accept two ratings.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.main import get_services

router = APIRouter(prefix="/api/csat", tags=["csat"])

_PAGE_CSS = """
:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; max-width: 30rem; margin: 4rem auto;
       padding: 0 1rem; line-height: 1.6; }
.small { color: var(--text-secondary, #666); font-size: 0.85rem; }
"""


@router.get("/{token}", response_class=HTMLResponse, include_in_schema=False)
def csat_landing_page(request: Request, token: str) -> HTMLResponse:
    """Render a minimal customer survey form (no API key required).

    The page validates the token first so an expired, answered, or unknown
    link shows an honest "no longer valid" message instead of a form that
    would silently reject the submission.
    """
    survey = get_services(request).database.get_csat_survey(token)
    if survey is None:
        return HTMLResponse(
            content=f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>满意度评价</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<h2>链接已失效</h2>
<p>这个评价链接无效、已过期或已被使用。如果您仍在等待帮助，请重新发起一次会话。</p>
</body>
</html>"""
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>满意度评价</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<h2>请为本次服务评分</h2>
<p>您的反馈会直接帮助我们改进服务质量，感谢您的时间。</p>
<form method="post" action="/api/csat/{token}">
  <select name="rating" aria-label="评分" required>
    <option value="" disabled selected>请选择 1–5 星</option>
    <option value="5">5 星 – 非常满意</option>
    <option value="4">4 星 – 满意</option>
    <option value="3">3 星 – 一般</option>
    <option value="2">2 星 – 不满意</option>
    <option value="1">1 星 – 非常不满意</option>
  </select>
  <br><br>
  <button type="submit">提交评价</button>
</form>
<p class="small">评分链接只可使用一次，7 天内有效。</p>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.post("/{token}", status_code=200, response_model=None)
async def submit_csat_rating(request: Request, token: str) -> dict[str, Any] | HTMLResponse:
    """Record a one-time CSAT rating for a resolved conversation.

    Accepts either a JSON body (``{"rating": 1-5}``, the API contract) or a
    browser ``application/x-www-form-urlencoded`` submission from the survey
    form. Both are routed through the same atomic single-use write, and a
    browser submission receives a thank-you page back.
    """
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or not content_type:
        rating = _parse_rating(await request.body(), form=True)
        is_form = True
    else:
        rating = _parse_rating(await request.body(), form=False)
        is_form = False
    if rating is None:
        raise HTTPException(status_code=422, detail="rating is required (1-5)")
    services = get_services(request)
    survey = services.database.submit_csat_rating(token, rating)
    if survey is None:
        raise HTTPException(status_code=404, detail="Survey link invalid, expired, or already used")
    _reflow_into_feedback(request, survey, rating)
    if is_form:
        return _thank_you_page(rating)
    return {
        "conversation_id": survey["conversation_id"],
        "rating": rating,
        "thank_you": True,
    }


def _parse_rating(raw: bytes, *, form: bool) -> int | None:
    """Parse ``rating`` from a JSON or form body; returns None when absent."""
    try:
        if form:
            pairs = urllib.parse.parse_qsl(raw.decode("utf-8", "replace"))
            value = dict(pairs).get("rating", "")
        else:
            value = json.loads(raw.decode("utf-8") or "{}").get("rating")
    except (ValueError, TypeError):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= 5 else None


def _reflow_into_feedback(request: Request, survey: dict[str, Any], rating: int) -> None:
    """Best-effort: mirror the CSAT rating into the feedback system.

    Skipped silently when the conversation has no assistant message to attach
    the rating to, so a survey on a never-answered conversation does not break
    the resolution path.
    """
    services = get_services(request)
    database = services.database
    try:
        conversation = database.get_conversation(survey["tenant_id"], survey["conversation_id"])
        if not conversation:
            return
        with database.connect() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE tenant_id=? AND conversation_id=? "
                "AND role='assistant' ORDER BY created_at DESC, seq DESC LIMIT 1",
                (survey["tenant_id"], survey["conversation_id"]),
            ).fetchone()
        if not row:
            return
        database.record_feedback(
            survey["tenant_id"],
            survey["conversation_id"],
            str(row["id"]),
            "csat",
            1 if rating >= 4 else -1,
            f"CSAT {rating}/5",
        )
    except Exception:
        pass


def _thank_you_page(rating: int) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>感谢评价</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<h2>感谢您的评价！</h2>
<p>您已提交 {rating}/5 星评分。您的反馈已记录，感谢您帮助我们改进。</p>
<p class="small">现在可以关闭此页面。</p>
</body>
</html>"""
    return HTMLResponse(content=html)
