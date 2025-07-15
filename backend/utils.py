
# This file contains all the helper functions that do the actual work.
import os
import asyncio, httpx
import tempfile
import logging
import docx
import html
import io
from fastapi import UploadFile, HTTPException
from pypdf import PdfReader
import assemblyai as aai
from datetime import datetime
from typing import List, Optional
import config
import google.generativeai as genai

logger = logging.getLogger(__name__)


async def read_text_from_file(file: UploadFile):
    filename = file.filename.lower()
    try:
        file_content = await file.read()
        file_stream = io.BytesIO(file_content)

        if filename.endswith(".pdf"):
            reader = PdfReader(file_stream)
            text_parts = [page.extract_text() for page in reader.pages if page.extract_text()]
            return "".join(text_parts).strip()
        elif filename.endswith(".docx"):
            doc = docx.Document(file_stream)
            full_text_list = [para.text for para in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        full_text_list.append(cell.text)
            return "\n".join(full_text_list).strip()
        elif filename.endswith(".txt"):
            return file_content.decode("utf-8").strip()
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: '{filename}'. Please upload a PDF, DOCX, or TXT file.",
            )
    except Exception as e:
        logger.error(f"Error reading file {file.filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process file: {e}")


# async def transcribe_to_segments(file: UploadFile, language: Optional[str] = None) -> List[dict]:
#     tmp_path = None
#     try:
#         with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
#             tmp.write(await file.read())
#             tmp_path = tmp.name

#         model = WhisperModel("base", device="cpu", compute_type="int8")
#         lang_code = language.split('-')[0] if language else None
#         segments_generator, _ = model.transcribe(tmp_path, language=lang_code, beam_size=5)
        
#         segments_list = [{"start": s.start, "end": s.end, "text": s.text} for s in segments_generator]
#         logger.info(f"Generated {len(segments_list)} segments from {file.filename}")
#         return segments_list
#     except Exception as e:
#         logger.error(f"Transcription to segments failed for {file.filename}: {e}")
#         raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
#     finally:
#         if tmp_path and os.path.exists(tmp_path):
#             os.remove(tmp_path)

# async def transcribe_audio_file_simple(file: UploadFile, language: Optional[str] = None) -> str:
#     tmp_path = None
#     try:
#         with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
#             tmp.write(await file.read())
#             tmp_path = tmp.name
        
#         model = WhisperModel("base", device="cpu", compute_type="int8")
#         lang_code = language.split('-')[0] if language else None
#         segments, _ = model.transcribe(tmp_path, language=lang_code, beam_size=5)
        
#         transcription = " ".join([segment.text for segment in segments]).strip()
#         logger.info(f"Simple transcription complete for {file.filename}")
#         return transcription
#     except Exception as e:
#         logger.error(f"Simple transcription failed for {file.filename}: {e}")
#         raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
#     finally:
#         if tmp_path and os.path.exists(tmp_path):
#             os.remove(tmp_path)

async def transcribe_with_assemblyai(file: UploadFile) -> str:
    """
    Transcribes an audio file using the AssemblyAI API and formats the output
    with speaker labels if available.
    """
    try:
        # Configure your API key
        aai.settings.api_key = os.getenv("ASSEMBLYAI_API_KEY")
        if not aai.settings.api_key:
            raise HTTPException(status_code=500, detail="AssemblyAI API key not configured.")

        # Configure transcription to identify different speakers
        config = aai.TranscriptionConfig(speaker_labels=True)
        transcriber = aai.Transcriber()

        # The SDK can directly handle the file stream from FastAPI
        transcript = await asyncio.to_thread(transcriber.transcribe, file.file, config)

        if transcript.status == aai.TranscriptStatus.error:
            logger.error(f"AssemblyAI transcription failed: {transcript.error}")
            raise HTTPException(status_code=500, detail=transcript.error)

        # If speaker labels are detected, format the transcript by speaker.
        if transcript.utterances:
            formatted_transcript = "\n".join(
                f"Speaker {utterance.speaker}: {utterance.text}"
                for utterance in transcript.utterances
            )
            return formatted_transcript
        else:
            # Otherwise, return the plain text.
            return transcript.text

    except Exception as e:
        logger.error(f"AssemblyAI transcription failed for {file.filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")



async def generate_gemini_content(prompt: str, model_name: str = "gemini-1.5-flash", is_json: bool = False):
    try:
        model = genai.GenerativeModel(model_name)
        response_type = "application/json" if is_json else "text/plain"
        generation_config = genai.types.GenerationConfig(response_mime_type=response_type)
        response = await model.generate_content_async(prompt, generation_config=generation_config)
        return response.text
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}")
        raise HTTPException(status_code=500, detail=f"AI content generation failed: {e}")


async def send_email_via_brevo(recipient_email, subject, html_content, sender_name="AI Meeting Wizard"):
    brevo_api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("SENDER_EMAIL")
    if not brevo_api_key or not sender_email:
        logger.error("Email service not configured. Missing BREVO_API_KEY or SENDER_EMAIL.")
        return False

    headers = {"api-key": brevo_api_key, "Content-Type": "application/json", "Accept": "application/json"}
    payload = {"sender": {"name": sender_name, "email": sender_email}, "to": [{"email": recipient_email}], "subject": subject, "htmlContent": html_content}
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers, timeout=30.0)
            response.raise_for_status()
            logger.info(f"Email sent successfully to {recipient_email}")
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"Brevo API Error sending to {recipient_email}: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred while sending email to {recipient_email}: {e}")
            return False


async def send_welcome_email(recipient_email, username):
    if not config.welcome_template:
        logger.error("Welcome email template is not loaded. Cannot send welcome email.")
        return

    subject = "Welcome to Ally, your AI Meeting Wizard!"
    app_url = "https://ally-frontend-vw00.onrender.com"
    html_content = config.welcome_template.replace("[USER_NAME]", html.escape(username))
    html_content = html_content.replace("[MY_URL]", app_url)
    html_content = html_content.replace("[CURRENT_YEAR]", str(datetime.now().year))
    
    await send_email_via_brevo(recipient_email, subject, html_content, sender_name="Ally")


async def send_reminder_email(task_data, user_id, task_id, reminder_type):
    if not config.reminder_template:
        logger.error("Reminder email template is not loaded. Cannot send reminder.")
        return

    recipient_email = task_data.get("assigneeEmail")
    if not recipient_email:
        logger.warning(f"Task {task_id} has no assignee email. Skipping reminder.")
        return

    subject, reminder_message = "", ""
    base_url = os.getenv("MY_API", "https://ally-backend-y2pq.onrender.com")
    update_url = f"{base_url}/api/update-task-status"
    in_progress_url = f"{update_url}?userId={user_id}&taskId={task_id}&newStatus=In Progress"
    completed_url = f"{update_url}?userId={user_id}&taskId={task_id}&newStatus=Completed"
    task_title = task_data.get('title', 'N/A')
    start_date_str = task_data.get('startDate', 'N/A')
    deadline_str = task_data.get('deadline', 'N/A')

    if reminder_type == "start_date":
        subject = f"Reminder: Task '{task_title}' is scheduled to start today!"
        reminder_message = f"This is a reminder that your task, <strong>{task_title}</strong>, is scheduled to begin today, {start_date_str}."
    elif reminder_type == "before_deadline":
        subject = f"Reminder: Task '{task_title}' is due tomorrow!"
        reminder_message = f"This is a friendly reminder that your task, <strong>{task_title}</strong>, is due tomorrow, {deadline_str}."
    elif reminder_type == "deadline":
        subject = f"URGENT: Task '{task_title}' is due today!"
        reminder_message = f"This is an urgent reminder that your task, <strong>{task_title}</strong>, is due today, {deadline_str}."
    elif reminder_type == "manual_notification":
        subject = f"Notification: A message about your task '{task_title}'"
        reminder_message = f"This is a notification regarding your task: <strong>{task_title}</strong>. Please review the details and update its status as needed."

    email_body = config.reminder_template
    email_body = email_body.replace("[REMINDER_SUBJECT]", html.escape(subject))
    email_body = email_body.replace("[PREHEADER_TEXT]", html.escape(subject))
    email_body = email_body.replace("[ASSIGNEE_NAME]", html.escape(task_data.get('assignee', 'Team Member')))
    email_body = email_body.replace("[REMINDER_MESSAGE_HTML]", reminder_message)
    email_body = email_body.replace("[TASK_TITLE]", html.escape(task_title))
    email_body = email_body.replace("[TASK_START_DATE]", html.escape(start_date_str))
    email_body = email_body.replace("[TASK_DEADLINE]", html.escape(deadline_str))
    email_body = email_body.replace("[IN_PROGRESS_URL]", in_progress_url)
    email_body = email_body.replace("[COMPLETED_URL]", completed_url)
    email_body = email_body.replace("[CURRENT_YEAR]", str(datetime.now().year))
    
    await send_email_via_brevo(recipient_email, subject, email_body, sender_name="Ally, your AI Meeting Wizard")
