
# This file contains all the API endpoints for the app.
import os
import json
import base64
import asyncio, httpx
from datetime import date, datetime, timedelta
import logging
import html
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from typing import List
from bs4 import BeautifulSoup
import utils
from pyModels import ResultRequest, AiHelperRequest, ManualEmailRequest, WelcomeEmailRequest
import config


router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/transcribe/", summary="Transcribe Audio/Video File(s)")
async def transcribe_endpoint(
    user_audio: UploadFile = File(None),
    system_audio: UploadFile = File(None),
    file: UploadFile = File(None),
    language: str = Form(None)
):
    if file:
        transcription = await utils.transcribe_audio_file_simple(file, language)
        return {"transcription": transcription}

    if not user_audio and not system_audio:
        raise HTTPException(status_code=400, detail="No audio files provided.")

    tasks = []
    if user_audio: tasks.append(utils.transcribe_to_segments(user_audio, language))
    if system_audio: tasks.append(utils.transcribe_to_segments(system_audio, language))
    
    transcription_results = await asyncio.gather(*tasks)

    all_segments = []
    result_index = 0
    if user_audio:
        for seg in transcription_results[result_index]: seg['source'] = 'Speaker 1'
        all_segments.extend(transcription_results[result_index])
        result_index += 1
    if system_audio:
        for seg in transcription_results[result_index]: seg['source'] = 'Speaker 2'
        all_segments.extend(transcription_results[result_index])

    all_segments.sort(key=lambda x: x['start'])
    raw_transcript = "\n".join([f"{s['source'].capitalize()}: {s['text'].strip()}" for s in all_segments])

    return {"transcription": raw_transcript}

@router.post("/upload-document/", summary="Upload and Process a Document")
async def upload_document_endpoint(file: UploadFile = File(...), language: str = Form(None)):
    logger.info(f"Processing document '{file.filename}'")
    text_content = await utils.read_text_from_file(file)
    return {"text": text_content}

@router.post("/generate-result/", summary="Generate Combined Result")
async def generate_result_endpoint(request: ResultRequest):
    if request.role and request.role != "No Selection":
        perspective_role = request.role
        perspective = f"only focusing on discussions that concern the {perspective_role}"
    else:
        perspective_role = "organization"
        perspective = "providing a general overview"

    summary_prompt = f"""
You are an advanced and professional assistant that summarizes content. You work on meeting transcripts or any other business or communication documents.

Given the following document — whether a meeting transcript or another text source — generate a well-structured and accurate summary {perspective}.

If the selected role ({perspective_role}) is not mentioned or discussed in the document, explicitly state that the role was not part of the conversation. Then provide a general overview of the meeting or document, highlighting only points that might indirectly affect the {perspective_role} or the organization.


If the document appears to be a meeting transcript:
Generate a concise, well-organized meeting summary using these standard headings only if available in the input. Omit any heading that has no relevant information. Do not fabricate or assume missing details. Summarize through the lens of the {perspective_role}:
- Prioritize discussions, risks, actions, or decisions that directly impact or concern the {perspective_role}.
- Minimize or omit unrelated content unless it directly affects the {perspective_role}.

Standard headings:
1. Meeting Details (date, time, platform, facilitator, note taker if provided)
2. Attendees (list participants; include absentees if mentioned)
3. Agenda (pre-set or inferred topics)
4. Discussion Summary / Key Points (prioritize discussion relevant to the {perspective_role})
5. Decisions Made (only include decisions that affect the {perspective_role})
6. Action Items (tasks related to or assigned to the {perspective_role}, include owner and deadlines if present)
7. Next Steps (only unresolved issues or follow-ups involving the {perspective_role})
8. Next Meeting (include schedule and any agenda items affecting the {perspective_role})
9. Additional Notes (context that helps the {perspective_role} plan or react)

If the document is **not** a meeting transcript:
Generate a structured summary using headings appropriate to the content (e.g., "Key Findings," "Main Insights," "Risks for {perspective_role}," or "Recommended Actions"). Always summarize through the lens of the {perspective_role}.

General Rules:
- Never fabricate, assume, or generalize.
- Never reframe non-meeting content as a meeting.
- Use only the language and tone provided.
- Use concise, plain text (no markdown formatting).
- Do not use ** anywhere in the output.
- Structure output clearly and accurately.
- Always reflect the role's perspective if provided.
- Recheck for at least 99% accuracy before replying.


Text to summarize:
---
{request.text}
---
""" 
    email_subject_prompt = f"Based on the following text, generate a very concise and relevant email subject line, no more than 8-10 words. Output ONLY the subject line itself, with no extra text or quotation marks.\n\nText:\n---\n{request.text}"

    summary = await utils.generate_gemini_content(summary_prompt)
    await asyncio.sleep(1)
    email_subject_raw = await utils.generate_gemini_content(email_subject_prompt)
    email_subject = email_subject_raw.strip().replace('"', '')

    final_summary = summary
    if request.target_language and request.target_language != "No Translation":
        translation_prompt = f"Translate the following text into {request.target_language}. Provide only the translated text, without any additional titles or explanations.\n\nText:\n---\n{summary}"
        final_summary = await utils.generate_gemini_content(translation_prompt)

    final_summary_html = html.escape(final_summary).replace('\n', '<br>')

    lines = final_summary_html.split('<br>')
    processed_lines = []
    for line in lines:
        stripped_line = line.strip()
       
        if (stripped_line.endswith(':') and len(stripped_line) < 100) or (stripped_line.isupper() and len(stripped_line) > 1):
             processed_lines.append(f"<h3>{stripped_line}</h3>")
        elif stripped_line:
            processed_lines.append(f"<p>{stripped_line}</p>")

    formatted_result = "".join(processed_lines)

    
    return {"formatted_result": formatted_result, "email_subject": email_subject, "plain_text_summary": final_summary}

@router.post("/ai-helper", summary="Generic AI Helper")
async def ai_helper_endpoint(request: AiHelperRequest):
    prompt = "" 
    
    if request.task_type == "autocomplete":
        prompt = f"""You are an intelligent auto-completion AI. Continue the following text in a natural and helpful way.
        Provide only the continuation, without repeating the original text. The continuation should be a few words or a short phrase.

        TEXT: "{request.context.get('text')}"

        CONTINUATION:"""

    elif request.task_type == "q_and_a":
        prompt = f"""You are a helpful assistant answering questions about a document.
        Use ONLY the information from the provided document to answer the user's question.
        If the answer is not in the document, say so. Keep your answers concise.

        Document:
        {request.context.get('context')}

        Question:
        {request.context.get('question')}

        Answer:"""

    elif request.task_type == "detect_topics":
        prompt = f"""You are an expert at structuring documents. Analyze the following transcript and identify logical sections.
        For each section, provide a concise heading and the character index where it begins.
        The output must be a valid JSON array of objects, each with "topic" and "index" keys.
        Ensure the index is at the start of a paragraph. Recheck for 99% accuracy.

        {request.context.get('text')}"""

    elif request.task_type == "extract_actions":
        prompt = f"""From the following meeting summary, extract all action items.
        Your response MUST be a valid JSON array of objects.
        Each object should have 'task', 'assignee', 'assigneeEmail', 'startDate', and 'deadline' keys.
        **Crucially, any date found for 'startDate' and 'deadline' MUST be formatted as 'yyyy-mm-dd'.**
        If a value is not mentioned, set it to null. If no person is assigned, set assignee to 'Unassigned' and assigneeEmail to null.
        The language of the action items should be the same as the language of the summary.
        But all dates must be in English format (yyyy-mm-dd).
        If no action items are found, return an empty array.
        Do not insinuate a task if it is not explicitly mentioned in the text.
        Do not fabricate or assume details. Recheck for 99% accuracy.

        Summary:
        ---
        {request.context.get('summary')}
        ---"""
    else:
        raise HTTPException(status_code=400, detail="Invalid task_type specified.")
    
    response_text = await utils.generate_gemini_content(prompt, is_json=request.is_json)
    if request.is_json:
        cleaned_json_string = response_text.strip().replace("```json", "").replace("```", "")
        try:
            return json.loads(cleaned_json_string)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="AI returned invalid JSON.")
    else:
        return {"text": response_text}

@router.post("/send-email/", summary="Send Email via Brevo with Attachments")
async def send_email_endpoint(
    recipients: str = Form(...), subject: str = Form(...), html_body: str = Form(...), attachments: List[UploadFile] = File(...)
):
    brevo_api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("SENDER_EMAIL")
    sender_name = os.getenv("SENDER_NAME", "Ally, your AI Meeting Wizard")
    project_name = "Ally"

    logger.info(f"Attempting to send email via Brevo from sender: '{sender_email}' to recipients: '{recipients}'")

    if not brevo_api_key or not sender_email:
        raise HTTPException(
            status_code=500,
            detail="Email service is not configured on the server. Missing BREVO_API_KEY or SENDER_EMAIL in .env file."
        )

    soup = BeautifulSoup(html_body, 'html.parser')
    all_attachments = []
    for attachment in attachments:
        if attachment.filename:
            encoded_content = base64.b64encode(await attachment.read()).decode()
            all_attachments.append({"name": attachment.filename, "content": encoded_content})

    if config.email_template:
        app_url = "http://127.0.0.1:5501/index.html"
        final_html_content = config.email_template.replace("[EMAIL_SUBJECT]", html.escape(subject))

        preheader_text = ' '.join(soup.get_text().split())
        final_html_content = final_html_content.replace("[PREHEADER_TEXT]", html.escape(preheader_text[:150]))
        final_html_content = final_html_content.replace("[PROJECT_NAME]", project_name)
        final_html_content = final_html_content.replace("[MAIN_CONTENT_HTML]", str(soup))
        final_html_content = final_html_content.replace("[MY_URL]", app_url)
        final_html_content = final_html_content.replace("[CURRENT_YEAR]", str(datetime.now().year))
    else:
        final_html_content = str(soup)

    headers = {
        "api-key": brevo_api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    to_list = [{"email": e.strip()} for e in recipients.split(',') if e.strip()]
    if not to_list:
        raise HTTPException(status_code=400, detail="No valid recipient emails provided.")

    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": to_list,
        "subject": subject,
        "htmlContent": final_html_content
    }

    if all_attachments:
        payload["attachment"] = all_attachments
        logger.info(f"Attaching {len(all_attachments)} total file(s) to the email.")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers, timeout=30.0)
            response.raise_for_status()
            logger.info("Email sent successfully via Brevo.")
            return JSONResponse(content={"message": "Email sent successfully!"}, status_code=200)
        except httpx.HTTPStatusError as e:
            error_details = e.response.text
            logger.error(f"Brevo API Error: {error_details}")
           
            try:
                error_json = json.loads(error_details)
                detail_message = error_json.get("message", error_details)
            except json.JSONDecodeError:
                detail_message = error_details
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Failed to send email. API Error: {detail_message}"
            )
        except Exception as e:
            logger.error(f"An unexpected error occurred while sending email: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"An unexpected error occurred: {str(e)}"
            )


@router.post("/send-welcome-email", summary="Send a Welcome Email to a New User")
async def send_welcome_email_endpoint(request: WelcomeEmailRequest):
    await utils.send_welcome_email(request.email, request.username)
    return {"message": "Welcome email sent successfully."}

@router.post("/send-task-reminders", summary="Scheduled Task Reminder Trigger")
async def task_reminder_scheduler():
    if not config.db:
        raise HTTPException(status_code=500, detail="Firestore is not initialized.")
    logger.info("Running daily task reminder check...")
    today = date.today()
    tomorrow = today + timedelta(days=1)
    
    tasks_ref = config.db.collection_group('actionLogs').where('status', '!=', 'Completed')
    tasks_stream = tasks_ref.stream()

    sent_reminders = 0
    for task in tasks_stream:
        task_data = task.to_dict()
        task_id = task.id
        user_id = task.reference.parent.parent.id
        reminder_type = None
        
        try:
            deadline_str = task_data.get('deadline')
            start_date_str = task_data.get('startDate')
            
            if start_date_str:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                if start_date == today:
                    reminder_type = "start_date"

            if not reminder_type and deadline_str:
                deadline_date = datetime.strptime(deadline_str, '%Y-%m-%d').date()
                if deadline_date == tomorrow:
                    reminder_type = "before_deadline"
                elif deadline_date == today:
                    reminder_type = "deadline"

            if reminder_type and task_data.get("assigneeEmail"):
                logger.info(f"Sending {reminder_type} reminder for task {task_id} to {task_data['assigneeEmail']}")
                await utils.send_reminder_email(task_data, user_id, task_id, reminder_type)
                sent_reminders += 1

        except (ValueError, TypeError) as e:
            logger.error(f"Could not parse date for task {task_id}. Error: {e}. Skipping.")
            continue
            
    logger.info(f"Task reminder check complete. Sent {sent_reminders} reminders.")
    return {"message": "Task reminder check complete."}

@router.get("/update-task-status", summary="Update Task Status from Email", response_class=HTMLResponse)
async def update_task_status_from_email(userId: str, taskId: str, newStatus: str):
    if not config.db:
        raise HTTPException(status_code=500, detail="Firestore is not initialized.")
    if not all([userId, taskId, newStatus]):
        return HTMLResponse(content="<h1>Error</h1><p>Missing required parameters.</p>", status_code=400)
    task_ref = config.db.collection('users').document(userId).collection('actionLogs').document(taskId)
    task_ref.update({'status': newStatus})
    logger.info(f"Updated task {taskId} for user {userId} to status '{newStatus}'")
    return HTMLResponse(content=config.success_template)

@router.post("/send-manual-reminder", summary="Send a Manual Task Reminder Email")
async def send_manual_reminder_endpoint(request: ManualEmailRequest):
    await utils.send_reminder_email(request.task, request.userId, request.task.get("id"), "manual_notification")
    return {"message": "Manual reminder email sent."}

@router.get("/firebase-config", summary="Get Firebase Client Configuration")
async def get_firebase_config():
    firebase_config = {
        "apiKey": os.getenv("FIREBASE_API_KEY"), "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
        "projectId": os.getenv("FIREBASE_PROJECT_ID"), "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
        "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID"), "appId": os.getenv("FIREBASE_APP_ID")
    }
    if not all(firebase_config.values()):
        raise HTTPException(status_code=500, detail="Firebase client configuration is not properly set up on the server.")
    return firebase_config
