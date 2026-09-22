from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import time
from email_validator import EmailNotValidError, validate_email
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import bleach
import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import config
import utils
from auth import AuthenticatedUser, get_current_user
from config import Settings, get_settings
from qstash_auth import (
    QStashRequestVerifier,
    QStashVerificationRejected,
    QStashVerificationUnavailable,
    get_qstash_verifier,
)
from rate_limit import (
    DOCUMENT_RATE_LIMIT,
    EMAIL_RATE_LIMIT,
    REMINDER_RATE_LIMIT,
    TRANSCRIPTION_RATE_LIMIT,
)
from reminders import (
    ReminderInfrastructureError,
    ReminderService,
    StatusPostRateLimiter,
    get_reminder_service,
    get_status_post_limiter,
)


router = APIRouter()
logger = logging.getLogger(__name__)

Authenticated = Annotated[AuthenticatedUser, Depends(get_current_user)]
AppSettings = Annotated[Settings, Depends(get_settings)]
ReminderWorkflow = Annotated[ReminderService, Depends(get_reminder_service)]
QStashVerifier = Annotated[QStashRequestVerifier, Depends(get_qstash_verifier)]
StatusLimiter = Annotated[StatusPostRateLimiter, Depends(get_status_post_limiter)]

AUDIO_EXTENSIONS = {".mp3", ".wav", ".mp4", ".m4a", ".webm", ".ogg"}
AUDIO_MIME_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/mp4",
    "audio/x-m4a",
    "audio/webm",
    "audio/ogg",
    "video/mp4",
    "video/webm",
}
DOCUMENT_MIME_TYPES = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    },
    ".txt": {"text/plain"},
}
EMAIL_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strong",
    "u",
    "ul",
}
EMAIL_ALLOWED_ATTRIBUTES = {"a": ["href", "title"]}
MAX_EMAIL_ATTACHMENTS = 5
STATUS_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
MAX_STATUS_POST_BYTES = 2048


def _megabytes(value: int) -> int:
    return value * 1024 * 1024


def _safe_filename(upload: UploadFile) -> tuple[str, str]:
    raw = (upload.filename or "").strip()
    if not raw or "\x00" in raw or len(raw) > 255:
        raise HTTPException(status_code=400, detail="The uploaded filename is invalid.")
    normalized = raw.replace("\\", "/")
    filename = normalized.rsplit("/", 1)[-1]
    if filename in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="The uploaded filename is invalid.")
    return filename, Path(filename).suffix.lower()


def _upload_size(upload: UploadFile) -> int:
    stream = upload.file
    current = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0 if current != 0 else current)
    return size


def _validate_upload(
    upload: UploadFile,
    *,
    max_bytes: int,
    allowed_extensions: set[str],
    allowed_mime_types: set[str],
) -> str:
    filename, extension = _safe_filename(upload)
    content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if extension not in allowed_extensions or content_type not in allowed_mime_types:
        raise HTTPException(status_code=415, detail="The uploaded file type is not supported.")
    size = _upload_size(upload)
    if size <= 0:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if size > max_bytes:
        raise HTTPException(status_code=413, detail="The uploaded file is too large.")
    return filename


def _validate_document(upload: UploadFile, max_bytes: int) -> str:
    filename, extension = _safe_filename(upload)
    content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    allowed_mimes = DOCUMENT_MIME_TYPES.get(extension)
    if not allowed_mimes or content_type not in allowed_mimes:
        raise HTTPException(status_code=415, detail="The uploaded file type is not supported.")
    size = _upload_size(upload)
    if size <= 0:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if size > max_bytes:
        raise HTTPException(status_code=413, detail="The uploaded file is too large.")
    return filename


@router.post("/transcribe/", summary="Transcribe Audio/Video File")
async def transcribe_endpoint(
    current_user: Authenticated,
    settings: AppSettings,
    _rate_limit: Annotated[None, Depends(TRANSCRIPTION_RATE_LIMIT)],
    file: UploadFile = File(...),
    language: str = Form("auto"),
):
    del current_user, _rate_limit
    _validate_upload(
        file,
        max_bytes=_megabytes(settings.max_audio_upload_mb),
        allowed_extensions=AUDIO_EXTENSIONS,
        allowed_mime_types=AUDIO_MIME_TYPES,
    )
    try:
        transcription = await utils.transcribe_with_assemblyai(file, language)
    except Exception as exc:
        logger.warning("Transcription provider failed.")
        raise HTTPException(status_code=502, detail="Transcription failed.") from exc
    return {"transcription": transcription}


@router.post("/upload-document/", summary="Upload and Process a Document")
async def upload_document_endpoint(
    current_user: Authenticated,
    settings: AppSettings,
    _rate_limit: Annotated[None, Depends(DOCUMENT_RATE_LIMIT)],
    file: UploadFile = File(...),
):
    del current_user, _rate_limit
    _validate_document(file, _megabytes(settings.max_document_upload_mb))
    try:
        text_content = await utils.read_text_from_file(file)
    except Exception as exc:
        logger.warning("Document processing failed.")
        raise HTTPException(status_code=500, detail="Document processing failed.") from exc
    return {"text": text_content}


def _parse_recipients(raw: str, settings: Settings) -> list[dict[str, str]]:
    if len(raw) > settings.max_email_recipients * 321:
        raise HTTPException(status_code=400, detail="The recipient list is invalid.")
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if not entries or len(entries) > settings.max_email_recipients:
        raise HTTPException(status_code=400, detail="The recipient list is invalid.")

    recipients = []
    for entry in entries:
        if "\r" in entry or "\n" in entry or len(entry) > 320:
            raise HTTPException(status_code=400, detail="The recipient list is invalid.")
        try:
            normalized = validate_email(
                entry, check_deliverability=False
            ).normalized
        except EmailNotValidError as exc:
            raise HTTPException(
                status_code=400, detail="The recipient list is invalid."
            ) from exc
        recipients.append({"email": normalized})
    return recipients


@router.post("/send-email/", summary="Send Email via Brevo with Attachments")
async def send_email_endpoint(
    current_user: Authenticated,
    settings: AppSettings,
    _rate_limit: Annotated[None, Depends(EMAIL_RATE_LIMIT)],
    recipients: str = Form(...),
    subject: str = Form(...),
    html_body: str = Form(...),
    attachments: list[UploadFile] = File(default=[]),
):
    del current_user, _rate_limit
    subject = subject.strip()
    if (
        not subject
        or len(subject) > settings.max_email_subject_chars
        or "\r" in subject
        or "\n" in subject
    ):
        raise HTTPException(status_code=400, detail="The email subject is invalid.")
    if not html_body or len(html_body) > settings.max_email_html_chars:
        raise HTTPException(status_code=413, detail="The email body is too large.")

    to_list = _parse_recipients(recipients, settings)
    named_attachments = [item for item in attachments if item.filename]
    if len(named_attachments) > MAX_EMAIL_ATTACHMENTS:
        raise HTTPException(status_code=400, detail="Too many attachments.")

    encoded_attachments = []
    total_size = 0
    for attachment in named_attachments:
        filename, _extension = _safe_filename(attachment)
        size = _upload_size(attachment)
        if size <= 0:
            raise HTTPException(status_code=400, detail="An attachment is empty.")
        if size > _megabytes(settings.max_email_attachment_mb):
            raise HTTPException(status_code=413, detail="An attachment is too large.")
        total_size += size
        if total_size > _megabytes(settings.max_email_total_attachment_mb):
            raise HTTPException(
                status_code=413, detail="The combined attachments are too large."
            )
        await attachment.seek(0)
        encoded_attachments.append(
            {
                "name": filename,
                "content": base64.b64encode(await attachment.read()).decode("ascii"),
            }
        )

    sanitized_body = bleach.clean(
        html_body,
        tags=EMAIL_ALLOWED_TAGS,
        attributes=EMAIL_ALLOWED_ATTRIBUTES,
        protocols={"https", "mailto"},
        strip=True,
        strip_comments=True,
    )
    if not BeautifulSoup(sanitized_body, "html.parser").get_text(strip=True):
        raise HTTPException(status_code=400, detail="The email body is empty.")

    brevo_api_key = os.getenv("BREVO_API_KEY", "").strip()
    sender_email = os.getenv("SENDER_EMAIL", "").strip()
    sender_name = os.getenv("SENDER_NAME", "Ally, your AI Meeting Wizard").strip()
    if not brevo_api_key or not sender_email:
        raise HTTPException(status_code=503, detail="Email service is unavailable.")

    if config.email_template:
        text_preview = " ".join(
            BeautifulSoup(sanitized_body, "html.parser").get_text(" ").split()
        )
        final_html = config.email_template.replace(
            "[EMAIL_SUBJECT]", html.escape(subject)
        )
        final_html = final_html.replace(
            "[PREHEADER_TEXT]", html.escape(text_preview[:150])
        )
        final_html = final_html.replace("[PROJECT_NAME]", "Ally")
        final_html = final_html.replace("[MAIN_CONTENT_HTML]", sanitized_body)
        final_html = final_html.replace(
            "[MY_URL]", "https://ally-vimd.onrender.com"
        )
    else:
        final_html = sanitized_body

    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": to_list,
        "subject": subject,
        "htmlContent": final_html,
    }
    if encoded_attachments:
        payload["attachment"] = encoded_attachments

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                json=payload,
                headers={
                    "api-key": brevo_api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Brevo email request failed.")
        raise HTTPException(status_code=502, detail="Email delivery failed.") from exc
    return JSONResponse(content={"message": "Email sent successfully."})


@router.post("/send-welcome-email", summary="Deprecated welcome-email route")
async def send_welcome_email_endpoint(current_user: Authenticated):
    del current_user
    raise HTTPException(
        status_code=410,
        detail="Welcome emails are disabled until the verified-email flow is complete.",
    )


@router.post("/internal/reminders/run", summary="Signed internal reminder worker")
async def task_reminder_scheduler(
    request: Request,
    verifier: QStashVerifier,
    workflow: ReminderWorkflow,
):
    if request.headers.get("origin") is not None:
        raise HTTPException(status_code=403, detail="Request not allowed.")
    signatures = request.headers.getlist("upstash-signature")
    if len(signatures) != 1:
        raise HTTPException(status_code=401, detail="Unauthorized.")
    raw_body = await request.body()
    try:
        verifier.verify(raw_body=raw_body, signature=signatures[0])
    except QStashVerificationUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Reminder worker is unavailable."
        ) from exc
    except QStashVerificationRejected as exc:
        raise HTTPException(status_code=401, detail="Unauthorized.") from exc

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid worker request.") from exc
    if not isinstance(payload, dict) or set(payload) - {"version"}:
        raise HTTPException(status_code=400, detail="Invalid worker request.")
    if payload.get("version", 1) != 1:
        raise HTTPException(status_code=400, detail="Invalid worker request.")

    try:
        counts = await workflow.run_scheduled()
    except ReminderInfrastructureError as exc:
        raise HTTPException(
            status_code=503, detail="Reminder processing is temporarily unavailable."
        ) from exc
    return {"status": "processed", **counts}


@router.get("/send-task-reminders", summary="Removed legacy reminder scheduler")
async def legacy_task_reminder_scheduler():
    raise HTTPException(status_code=410, detail="This scheduler route was removed.")


@router.api_route(
    "/update-task-status",
    methods=["GET", "POST"],
    summary="Disabled legacy task-status mutation",
)
async def update_task_status_disabled():
    raise HTTPException(
        status_code=410,
        detail="This legacy status-update link has been disabled.",
    )


@router.post(
    "/action-items/{action_item_id}/reminders",
    summary="Request a server-owned action reminder",
)
async def send_manual_reminder_endpoint(
    request: Request,
    action_item_id: UUID,
    current_user: Authenticated,
    workflow: ReminderWorkflow,
    _rate_limit: Annotated[None, Depends(REMINDER_RATE_LIMIT)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    del _rate_limit
    body = await request.body()
    if body.strip():
        raise HTTPException(
            status_code=400,
            detail="Reminder requests must not contain action or recipient data.",
        )
    try:
        parsed_key = UUID(idempotency_key or "")
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=400, detail="A valid Idempotency-Key UUID is required."
        ) from exc

    try:
        result = await workflow.request_manual(
            user_id=current_user.id,
            action_item_id=action_item_id,
            idempotency_key=parsed_key,
        )
    except ReminderInfrastructureError as exc:
        raise HTTPException(
            status_code=503, detail="Reminder processing is temporarily unavailable."
        ) from exc

    if result["outcome"] == "not_found":
        raise HTTPException(status_code=404, detail="Action item not found.")
    if result["outcome"] == "incompatible":
        raise HTTPException(
            status_code=409,
            detail="This Idempotency-Key was already used for another request.",
        )
    if result["outcome"] == "ineligible":
        raise HTTPException(
            status_code=409, detail="A reminder cannot be sent for this action item."
        )

    public_result = {
        "delivery_id": result.get("delivery_id"),
        "status": result.get("status"),
        "attempt_count": result.get("attempt_count"),
        "next_attempt_at": result.get("next_attempt_at"),
    }
    status_code = 200 if result.get("status") == "sent" else 202
    return JSONResponse(status_code=status_code, content=public_result)


@router.post("/send-manual-reminder", summary="Removed legacy manual reminder")
async def legacy_manual_reminder_endpoint(current_user: Authenticated):
    del current_user
    raise HTTPException(status_code=410, detail="This reminder route was removed.")


def _status_headers(response: HTMLResponse) -> HTMLResponse:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    return response


def _status_page(title: str, message: str, form: str = "") -> HTMLResponse:
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font-family:system-ui,sans-serif;background:#f8fafc;color:#172033;margin:0;display:grid;min-height:100vh;place-items:center}}
main{{background:#fff;border:1px solid #dbe3ec;border-radius:12px;max-width:32rem;padding:2rem;margin:1rem;box-shadow:0 8px 24px rgba(15,23,42,.08)}}
button{{background:#2563eb;border:0;border-radius:8px;color:#fff;font-weight:700;padding:.75rem 1rem;cursor:pointer}}
</style></head><body><main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>{form}</main></body></html>"""
    return _status_headers(HTMLResponse(document))


def _valid_status_token(value: str) -> bool:
    return bool(STATUS_TOKEN_PATTERN.fullmatch(value))


@router.get("/action-status", response_class=HTMLResponse, summary="Confirm an action status")
async def confirm_action_status(workflow: ReminderWorkflow, token: str = ""):
    if not _valid_status_token(token):
        return _status_page(
            "Link unavailable",
            "This status link is invalid, expired, revoked, or already used.",
        )
    try:
        inspected = await workflow.inspect_status_token(token)
    except ReminderInfrastructureError:
        response = _status_page(
            "Temporarily unavailable", "Please try this link again later."
        )
        response.status_code = 503
        return response
    if not inspected.get("valid"):
        return _status_page(
            "Link unavailable",
            "This status link is invalid, expired, revoked, or already used.",
        )
    target = inspected.get("target_status")
    label = "Completed" if target == "completed" else "In Progress"
    form = (
        '<form action="/action-status" method="post">'
        f'<input type="hidden" name="token" value="{html.escape(token, quote=True)}">'
        f'<button type="submit">Confirm {html.escape(label)}</button></form>'
    )
    return _status_page(
        "Confirm status change",
        f"Confirm that this action should be marked {label}.",
        form,
    )


def _same_origin(value: str, expected_origin: str) -> bool:
    try:
        parsed = urlparse(value)
        actual = f"{parsed.scheme}://{parsed.netloc}"
    except ValueError:
        return False
    return actual == expected_origin


@router.post("/action-status", summary="Consume an action-status token")
async def consume_action_status(
    request: Request,
    settings: AppSettings,
    workflow: ReminderWorkflow,
    limiter: StatusLimiter,
):
    client_identity = request.client.host if request.client else "unknown"
    if not await limiter.allow(client_identity, time.monotonic()):
        raise HTTPException(status_code=429, detail="Too many requests.")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_STATUS_POST_BYTES:
                raise HTTPException(status_code=413, detail="Request too large.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid request.") from exc

    expected = urlparse(settings.api_public_url)
    expected_origin = f"{expected.scheme}://{expected.netloc}" if expected.netloc else ""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Request not allowed.")
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if expected_origin and origin and not _same_origin(origin, expected_origin):
        raise HTTPException(status_code=403, detail="Request not allowed.")
    if expected_origin and referer and not _same_origin(referer, expected_origin):
        raise HTTPException(status_code=403, detail="Request not allowed.")

    raw_body = await request.body()
    if len(raw_body) > MAX_STATUS_POST_BYTES:
        raise HTTPException(status_code=413, detail="Request too large.")
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        if media_type == "application/x-www-form-urlencoded":
            values = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
            if set(values) != {"token"} or len(values["token"]) != 1:
                raise ValueError
            token = values["token"][0]
        elif media_type == "application/json":
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"token"}:
                raise ValueError
            token = payload["token"]
        else:
            raise HTTPException(status_code=415, detail="Unsupported content type.")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid request.") from exc
    if not isinstance(token, str) or not _valid_status_token(token):
        return RedirectResponse("/action-status/result?outcome=unavailable", status_code=303)
    try:
        result = await workflow.consume_status_token(token)
    except ReminderInfrastructureError as exc:
        raise HTTPException(
            status_code=503, detail="Status updates are temporarily unavailable."
        ) from exc
    outcome = "updated" if result.get("updated") else "unavailable"
    return RedirectResponse(f"/action-status/result?outcome={outcome}", status_code=303)


@router.get("/action-status/result", response_class=HTMLResponse, summary="Action-status result")
async def action_status_result(outcome: str = "unavailable"):
    if outcome == "updated":
        return _status_page(
            "Status updated", "The action status was updated successfully."
        )
    return _status_page(
        "Link unavailable",
        "This status link is invalid, expired, revoked, or already used.",
    )
