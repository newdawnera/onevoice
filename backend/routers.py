from __future__ import annotations

import base64
import hmac
import html
import json
import logging
import os
from email_validator import EmailNotValidError, validate_email
from pathlib import Path
from typing import Annotated

import bleach
import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

import config
import utils
from auth import AuthenticatedUser, get_current_user
from config import Settings, get_settings
from pyModels import AiHelperRequest, ManualReminderRequest, ResultRequest
from rate_limit import (
    AI_RATE_LIMIT,
    DOCUMENT_RATE_LIMIT,
    EMAIL_RATE_LIMIT,
    TRANSCRIPTION_RATE_LIMIT,
)


router = APIRouter()
logger = logging.getLogger(__name__)

Authenticated = Annotated[AuthenticatedUser, Depends(get_current_user)]
AppSettings = Annotated[Settings, Depends(get_settings)]

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


@router.post("/generate-result/", summary="Generate Combined Result")
async def generate_result_endpoint(
    request: ResultRequest,
    current_user: Authenticated,
    settings: AppSettings,
    _rate_limit: Annotated[None, Depends(AI_RATE_LIMIT)],
):
    del current_user, _rate_limit
    if len(request.text) > settings.max_ai_text_chars:
        raise HTTPException(status_code=413, detail="The submitted text is too large.")

    summary_prompt = f"""
    Create a neutral, accurate, structured summary of the source text below.
    For meeting transcripts, include applicable meeting details, attendees,
    agenda, discussion, decisions, action items, next steps, and next meeting.
    For other documents, use logical headings appropriate to the material.
    Use only information in the source, do not fabricate details, and return
    plain text without Markdown.

    Source text:
    ---
    {request.text}
    ---
    """
    subject_prompt = (
        "Generate a concise email subject of at most 10 words for the following "
        f"text. Return only the subject.\n\n{request.text}"
    )
    try:
        general_summary = await utils.generate_gemini_content(summary_prompt)
        refined_summary = await utils.role_summary(general_summary, request.role)
        summary = await utils.correct_summary_language(
            request.text, refined_summary
        )
        email_subject = (
            (await utils.generate_gemini_content(subject_prompt))
            .strip()
            .replace('"', "")
        )
        if request.target_language and request.target_language != "No Translation":
            translation_prompt = (
                f"Translate the following text into {request.target_language}. "
                f"Return only the translation.\n\n{summary}"
            )
            summary = await utils.generate_gemini_content(translation_prompt)
    except HTTPException:
        raise HTTPException(status_code=502, detail="AI processing failed.")
    except Exception as exc:
        logger.exception("AI generation failed.")
        raise HTTPException(status_code=502, detail="AI processing failed.") from exc

    formatted_lines = []
    for raw_line in summary.splitlines():
        line = html.escape(raw_line.strip())
        if not line:
            continue
        if (line.endswith(":") and len(line) < 100) or (
            line.isupper() and len(line) > 1
        ):
            formatted_lines.append(f"<h3>{line}</h3>")
        else:
            formatted_lines.append(f"<p>{line}</p>")
    return {
        "formatted_result": "".join(formatted_lines),
        "email_subject": email_subject[: settings.max_email_subject_chars],
        "plain_text_summary": summary,
    }


@router.post("/ai-helper", summary="Generic AI Helper")
async def ai_helper_endpoint(
    request: AiHelperRequest,
    current_user: Authenticated,
    settings: AppSettings,
    _rate_limit: Annotated[None, Depends(AI_RATE_LIMIT)],
):
    del current_user, _rate_limit
    serialized_context = json.dumps(request.context, ensure_ascii=False)
    if len(serialized_context) > settings.max_ai_text_chars:
        raise HTTPException(status_code=413, detail="The submitted text is too large.")

    if request.task_type == "autocomplete":
        prompt = (
            "Continue this text naturally with only a short continuation: "
            f"{request.context.get('text', '')}"
        )
    elif request.task_type == "q_and_a":
        prompt = (
            "Answer the question using only the supplied document. If the answer "
            "is absent, say so.\n\nDocument:\n"
            f"{request.context.get('context', '')}\n\nQuestion:\n"
            f"{request.context.get('question', '')}"
        )
    elif request.task_type == "detect_topics":
        prompt = (
            "Return a JSON array of logical document sections. Each object must "
            "contain a topic string and the character index where it begins.\n\n"
            f"{request.context.get('text', '')}"
        )
    else:
        prompt = (
            "Extract action items from this summary as a JSON array. Each item must "
            "contain task, assignee, assigneeEmail, startDate, and deadline. Dates "
            "must use yyyy-mm-dd; missing values must be null; do not fabricate.\n\n"
            f"{request.context.get('summary', '')}"
        )

    try:
        response_text = await utils.generate_gemini_content(
            prompt, is_json=request.is_json
        )
    except Exception as exc:
        logger.exception("AI helper failed.")
        raise HTTPException(status_code=502, detail="AI processing failed.") from exc

    if not request.is_json:
        return {"text": response_text}
    cleaned = response_text.strip().replace("```json", "").replace("```", "")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="AI processing failed.") from exc


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


def _require_scheduler(request: Request, settings: Settings) -> None:
    if request.headers.get("origin") is not None:
        raise HTTPException(status_code=403, detail="Request not allowed.")
    if not settings.scheduler_secret:
        raise HTTPException(status_code=503, detail="Scheduler route is unavailable.")
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise HTTPException(status_code=401, detail="Unauthorized.")
    parts = values[0].strip().split()
    if (
        len(parts) != 2
        or parts[0].lower() != "bearer"
        or not hmac.compare_digest(parts[1], settings.scheduler_secret)
    ):
        raise HTTPException(status_code=401, detail="Unauthorized.")


@router.get("/send-task-reminders", summary="Internal reminder scheduler")
async def task_reminder_scheduler(request: Request, settings: AppSettings):
    _require_scheduler(request, settings)
    raise HTTPException(
        status_code=503,
        detail="Scheduled reminders are disabled until the reminder data migration.",
    )


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


@router.post("/send-manual-reminder", summary="Manual task reminder")
async def send_manual_reminder_endpoint(
    request: ManualReminderRequest,
    current_user: Authenticated,
):
    del request, current_user
    raise HTTPException(
        status_code=503,
        detail="Manual reminders are disabled until action-item migration is complete.",
    )


@router.get("/firebase-config", summary="Deprecated Firebase configuration")
async def get_firebase_config():
    raise HTTPException(
        status_code=410,
        detail="Firebase Auth configuration is no longer available.",
    )
