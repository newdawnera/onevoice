
# This file contains all my helper functions that do the actual work.
import os
import asyncio, httpx
import logging
import docx
import html
import io
from fastapi import UploadFile, HTTPException
from pypdf import PdfReader
import assemblyai as aai
from datetime import datetime
import config

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
        logger.error("File processing failed")
        raise HTTPException(status_code=500, detail=f"Failed to process file: {e}")



# this transcribes an audio file using the AssemblyAI API and formats the output with speaker labels if available.
async def transcribe_with_assemblyai(file: UploadFile, language: str):
    
    try:
        
        aai.settings.api_key = os.getenv("ASSEMBLYAI_API_KEY")
        if not aai.settings.api_key:
            raise HTTPException(status_code=500, detail="AssemblyAI API key not configured.")
        
        speaker_labels_lang = ['auto','en_us','en_uk','es','fr','de','it','ja','pt','ru','zh']

        config_params = {}
        if language in speaker_labels_lang:
            config_params["speaker_labels"] = True

        if language == "auto":
            config_params["language_detection"] = True
            logger.info("AssemblyAI transcription running with automatic language detection.")
        else:
            
            
            config_params["language_code"] = language
            logger.info("AssemblyAI transcription started with an explicit language")

        config = aai.TranscriptionConfig(**config_params)
        transcriber = aai.Transcriber()

        
        transcript = await asyncio.to_thread(transcriber.transcribe, file.file, config)

        if transcript.status == aai.TranscriptStatus.error:
            logger.error("AssemblyAI transcription failed")
            raise HTTPException(status_code=500, detail=transcript.error)

 
        if config.speaker_labels and transcript.utterances:
            formatted_transcript = "\n".join(
                f"Speaker {utterance.speaker}: {utterance.text}"
                for utterance in transcript.utterances
            )
            return formatted_transcript
        else:
      
            return transcript.text

    except Exception as e:
        logger.error("AssemblyAI transcription failed")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")



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
            logger.info("Email provider accepted the message")
            return True
        except httpx.HTTPStatusError:
            logger.error("Email provider rejected the message")
            return False
        except Exception:
            logger.error("Email delivery failed")
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
