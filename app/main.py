"""
app/main.py
─────────────────────────────────────────────────────────────────
FastAPI application: routes, middleware, anonymous-session
management, CSRF protection, and template rendering.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_factory, create_tables, dispose_engine, get_session
from app.models import (
    Base,
    Delivery,
    Message,
    MessageStatus,
    ModerationEvent,
    User,
)
from app.services import moderation, rate_limit
from app.services.delivery import (
    DEFAULT_FALLBACK_TEXT,
    get_or_assign_daily_message,
)
from app.settings import settings

import os

# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── App ─────────────────────────────────────────────────────────
app = FastAPI(title="Uplift", docs_url=None, redoc_url=None)

# Static files & templates
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Signed-cookie serialiser
_signer = URLSafeSerializer(settings.secret_key, salt="uplift-user")
_csrf_signer = URLSafeSerializer(settings.secret_key, salt="uplift-csrf")

USER_COOKIE = "uplift_uid"
CSRF_COOKIE = "uplift_csrf"


# ── Lifecycle ───────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup() -> None:
    await create_tables()
    logger.info("Uplift started – tables ready")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await dispose_engine()


# ── Helpers ─────────────────────────────────────────────────────
def _get_user_id(request: Request) -> Optional[str]:
    """Decode the signed anonymous user cookie."""
    raw = request.cookies.get(USER_COOKIE)
    if not raw:
        return None
    try:
        return _signer.loads(raw)
    except BadSignature:
        return None


def _set_user_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        USER_COOKIE,
        _signer.dumps(user_id),
        max_age=60 * 60 * 24 * 365,  # 1 year
        httponly=True,
        samesite="lax",
    )


def _generate_csrf_token() -> str:
    token = secrets.token_urlsafe(32)
    return token


def _set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        _csrf_signer.dumps(token),
        max_age=60 * 60 * 2,  # 2 hours
        httponly=True,
        samesite="strict",
    )


def _verify_csrf(request: Request, form_token: str) -> bool:
    raw = request.cookies.get(CSRF_COOKIE)
    if not raw:
        return False
    try:
        cookie_token = _csrf_signer.loads(raw)
    except BadSignature:
        return False
    return secrets.compare_digest(cookie_token, form_token)


async def _ensure_user(
    request: Request, response: Response, session: AsyncSession
) -> str:
    """Return the user_id, creating one if needed."""
    user_id = _get_user_id(request)

    if user_id:
        # Update last_seen
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.last_seen_at = datetime.now(timezone.utc)
            await session.commit()
            return user_id
        # Cookie exists but user row doesn't — recreate
        user_id = None

    if not user_id:
        user_id = str(uuid.uuid4())
        new_user = User(id=user_id)
        session.add(new_user)
        await session.commit()
        _set_user_cookie(response, user_id)

    return user_id


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Routes ──────────────────────────────────────────────────────

# ---- GET / ---- Home page: today's daily message ---------------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, session: AsyncSession = Depends(get_session)):
    response = Response()
    user_id = await _ensure_user(request, response, session)

    message = await get_or_assign_daily_message(session, user_id)

    message_text = message.text if message else DEFAULT_FALLBACK_TEXT
    is_fallback = message is None

    html = templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "message_text": message_text,
            "is_fallback": is_fallback,
        },
    )

    # Copy cookies from our helper response into the template response
    for key, morsel in response.raw_headers:
        if key.lower() == b"set-cookie":
            html.raw_headers.append((key, morsel))

    return html


# ---- GET /write ---- Write-a-message form ----------------------
@app.get("/write", response_class=HTMLResponse)
async def write_form(request: Request, session: AsyncSession = Depends(get_session)):
    response = Response()
    user_id = await _ensure_user(request, response, session)

    csrf_token = _generate_csrf_token()

    html = templates.TemplateResponse(
        "write.html",
        {
            "request": request,
            "csrf_token": csrf_token,
            "max_length": settings.max_message_length,
            "error": None,
        },
    )

    _set_csrf_cookie(html, csrf_token)

    for key, morsel in response.raw_headers:
        if key.lower() == b"set-cookie":
            html.raw_headers.append((key, morsel))

    return html


# ---- POST /write ---- Submit a message -------------------------
@app.post("/write", response_class=HTMLResponse)
async def write_submit(
    request: Request,
    text: str = Form(...),
    csrf_token: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    response = Response()
    user_id = await _ensure_user(request, response, session)

    # CSRF check
    if not _verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    ip = _client_ip(request)

    # Rate limit
    rl_reason = rate_limit.check_rate_limit(user_id, ip)
    if rl_reason:
        new_csrf = _generate_csrf_token()
        html = templates.TemplateResponse(
            "write.html",
            {
                "request": request,
                "csrf_token": new_csrf,
                "max_length": settings.max_message_length,
                "error": rl_reason,
            },
        )
        _set_csrf_cookie(html, new_csrf)
        return html

    # Moderate
    result = moderation.moderate(text)

    # Store message
    msg = Message(
        author_user_id=user_id,
        text=text.strip(),
        status=result.status,
        rejection_reason=result.reason,
        approved_at=datetime.now(timezone.utc) if result.status == "approved" else None,
    )
    session.add(msg)
    await session.flush()  # get msg.id

    # Store moderation events
    for evt in result.events:
        session.add(
            ModerationEvent(
                message_id=msg.id,
                event_type=evt["event_type"],
                details_json=evt.get("details_json"),
            )
        )

    await session.commit()

    # Record in rate-limiter
    rate_limit.record_submission(user_id, ip)

    if result.status == "rejected":
        new_csrf = _generate_csrf_token()
        html = templates.TemplateResponse(
            "write.html",
            {
                "request": request,
                "csrf_token": new_csrf,
                "max_length": settings.max_message_length,
                "error": (
                    "Your message couldn't be posted. "
                    "Please keep it kind and positive. 💛"
                ),
            },
        )
        _set_csrf_cookie(html, new_csrf)
        for key, morsel in response.raw_headers:
            if key.lower() == b"set-cookie":
                html.raw_headers.append((key, morsel))
        return html

    # approved or pending → redirect to /sent
    redirect = RedirectResponse("/sent", status_code=303)
    for key, morsel in response.raw_headers:
        if key.lower() == b"set-cookie":
            redirect.raw_headers.append((key, morsel))
    return redirect


# ---- GET /sent ---- Confirmation page ---------------------------
@app.get("/sent", response_class=HTMLResponse)
async def sent(request: Request):
    return templates.TemplateResponse("sent.html", {"request": request})


# ---- GET /admin ---- Admin dashboard ----------------------------
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    password: str = "",
    session: AsyncSession = Depends(get_session),
):
    if password != settings.admin_password:
        return templates.TemplateResponse(
            "admin.html",
            {
                "request": request,
                "authenticated": False,
                "pending_messages": [],
                "recent_events": [],
                "stats": {},
                "csrf_token": "",
            },
        )

    # Pending messages
    pending_stmt = (
        select(Message)
        .where(Message.status == MessageStatus.pending)
        .order_by(Message.created_at.desc())
        .limit(50)
    )
    pending_result = await session.execute(pending_stmt)
    pending_messages = pending_result.scalars().all()

    # Recent moderation events
    events_stmt = (
        select(ModerationEvent)
        .order_by(ModerationEvent.created_at.desc())
        .limit(50)
    )
    events_result = await session.execute(events_stmt)
    recent_events = events_result.scalars().all()

    # Stats
    total_stmt = select(func.count()).select_from(Message)
    total_result = await session.execute(total_stmt)
    total_messages = total_result.scalar() or 0

    approved_stmt = select(func.count()).select_from(Message).where(Message.status == MessageStatus.approved)
    approved_result = await session.execute(approved_stmt)
    approved_count = approved_result.scalar() or 0

    rejected_stmt = select(func.count()).select_from(Message).where(Message.status == MessageStatus.rejected)
    rejected_result = await session.execute(rejected_stmt)
    rejected_count = rejected_result.scalar() or 0

    pending_count = len(pending_messages)

    csrf_token = _generate_csrf_token()

    html = templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "authenticated": True,
            "pending_messages": pending_messages,
            "recent_events": recent_events,
            "stats": {
                "total": total_messages,
                "approved": approved_count,
                "rejected": rejected_count,
                "pending": pending_count,
            },
            "csrf_token": csrf_token,
            "password": password,
        },
    )
    _set_csrf_cookie(html, csrf_token)
    return html


# ---- POST /admin/review ---- Approve / reject -------------------
@app.post("/admin/review")
async def admin_review(
    request: Request,
    message_id: str = Form(...),
    action: str = Form(...),
    reason: str = Form(""),
    csrf_token: str = Form(...),
    password: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    if password != settings.admin_password:
        raise HTTPException(status_code=403, detail="Unauthorized")
    if not _verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    stmt = select(Message).where(Message.id == message_id)
    result = await session.execute(stmt)
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    if action == "approve":
        msg.status = MessageStatus.approved
        msg.approved_at = datetime.now(timezone.utc)
        msg.rejection_reason = None
        event_type = "manual_approve"
    elif action == "reject":
        msg.status = MessageStatus.rejected
        msg.rejection_reason = reason or "Rejected by admin"
        event_type = "manual_reject"
    else:
        raise HTTPException(status_code=400, detail="Invalid action")

    session.add(
        ModerationEvent(
            message_id=msg.id,
            event_type=event_type,
            details_json=f'{{"reason": "{reason}"}}',
        )
    )

    await session.commit()

    return RedirectResponse(f"/admin?password={password}", status_code=303)


# ---- GET /health ---- Health check ------------------------------
@app.get("/health")
async def health():
    return {"status": "ok"}
