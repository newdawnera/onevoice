

import os
import json
import base64
import asyncio, httpx
from datetime import date, datetime, timedelta
import logging
import html
from fastapi import APIRouter, Header, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from typing import List
from bs4 import BeautifulSoup
import utils
from pyModels import ResultRequest, AiHelperRequest, ManualEmailRequest, WelcomeEmailRequest
import config


router = APIRouter()
logger = logging.getLogger(__name__)



@router.post("/transcribe/", summary="Transcribe Audio/Video File")
async def transcribe_endpoint(
    file: UploadFile = File(None)):
    if not file:
        raise HTTPException(status_code=400, detail="No audio file provided.")

    # The new util function handles everything, including speaker separation.
    transcription = await utils.transcribe_with_assemblyai(file)
    
    return {"transcription": transcription}



@router.post("/upload-document/", summary="Upload and Process a Document")
async def upload_document_endpoint(file: UploadFile = File(...)):
    logger.info(f"Processing document '{file.filename}'")
    text_content = await utils.read_text_from_file(file)
    return {"text": text_content}

@router.post("/generate-result/", summary="Generate Combined Result")
async def generate_result_endpoint(request: ResultRequest):
    if request.role and request.role != "No Selection":
        perspective_role = request.role
    else:
        perspective_role = "general overview"
        

    summary_prompt = f"""
You are an advanced and professional assistant that summarizes content. You work on meeting transcripts as well as academic, technical, instructional, or communication documents.

Your primary task is to generate a well-structured and accurate summary of the text provided below from a {perspective_role} perspective.

CRITICAL LANGUAGE RULE: Your entire response, including all headings and content, MUST be in the same language as the original text provided. Do not translate any part of the document, including names, terms, or headings.

---
If the document appears to be a meeting transcript:

Generate a concise, well-organized meeting summary. Summarize through the lens of the {perspective_role} only focusing on discussions that concern the {perspective_role}.
- Use appropriate headings based on the content (e.g., "Agenda," "Discussion Summary," "Decisions Made," and "Action Items"). Ensure the headings you create are also in the original language.
- Omit any heading that has no relevant information.
- Prioritize discussions, risks, actions, or decisions that directly impact or concern the {perspective_role}.
- Minimize or omit unrelated content unless it directly affects the {perspective_role}.

---
If the document is NOT a meeting transcript:

Generate a structured summary using headings appropriate to the content (e.g., "Key Findings," "Main Insights," or "Recommended Actions").
- Always summarize through the lens of a {perspective_role}.
- Tailor the structure to match the document's format, purpose, and type (e.g., academic report, technical documentation, guideline, business memo, etc.).

---
Language Handling Rule:

- Do not translate any part of the original text.
- Preserve all headings, names, and content in the original language as provided (e.g., Arabic, French, etc.).
- Summarize in the same language as the source unless explicitly instructed to translate.

---
General Rules:

- Role Handling: If the selected role ({perspective_role}) is not mentioned or discussed in the document, explicitly state that the role was not part of the conversation (in the source language). Then provide a general overview, highlighting only points that might indirectly affect the {perspective_role}.
- Accuracy: Never fabricate, assume, or generalize information not present in the text. Recheck your output for at least 99% accuracy before replying.
- Tone and Language: Use only the language and tone provided in the original document.
- Formatting: Use concise, plain text. Do not use markdown formatting like ``.
- Structure: Structure the output clearly and accurately. Do not reframe non-meeting content as a meeting.



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
    else:
    
        final_summary = summary

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
        If the year is not mentioned, use the current year.
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
        app_url = "https://ally-frontend-vw00.onrender.com"
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

@router.get("/send-task-reminders", summary="Scheduled Task Reminder Trigger")
async def task_reminder_scheduler(authorization: str = Header(None)):

    # this is to verify the secret token from Upstash
    expected_token = f"Bearer {os.getenv('QSTASH_TOKEN')}"
    if authorization != expected_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

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

                if reminder_type == "start_date" and not start_date_str:
                    logger.warning(f"Skipping start_date reminder for task {task_id}: Date is missing.")
                    continue 

                if reminder_type in ["deadline", "before_deadline"] and not deadline_str:
                    logger.warning(f"Skipping deadline reminder for task {task_id}: Date is missing.")
                    continue

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

    if not request.task.get("deadline"):
        raise HTTPException(
            status_code=400,
            detail="Cannot send a reminder because the task does not have a deadline."
        )
    elif not request.task.get("startDate"):
        raise HTTPException(
            status_code=400,
            detail="Cannot send a reminder because the task does not have a start date."
        )

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
