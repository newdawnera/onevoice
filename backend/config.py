
# This file handles all my setup and configuration for the app.
import os, json
from dotenv import load_dotenv
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
import logging


load_dotenv()


db = None
welcome_template = None
email_template = None
reminder_template = None
success_template = None


def setup_firebase():
    
    global db
    try:
        if not firebase_admin._apps:
            cred_data = json.loads(os.environ["FIREBASE_CREDENTIALS_JSON"])
            cred = credentials.Certificate(cred_data)
            firebase_admin.initialize_app(cred, {
                'projectId': os.getenv("FIREBASE_PROJECT_ID", "alliance-2025"),
            })
        db = firestore.client()
        print("Firebase connected.")
    except Exception as e:
        print(f"Couldn't connect to Firebase. Check your service account file. Error: {e}")
        db = None


def setup_gemini_api():
    
    try:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not found in .env file. AI features will fail.")
        genai.configure(api_key=api_key)
        print("Gemini API configured.")
    except Exception as e:
        print(f"Error configuring Gemini API: {e}")


def load_html_templates():
    
    global welcome_template, email_template, reminder_template, success_template
    
    try:
        with open("welcome-email-template.html", "r", encoding="utf-8") as f:
            welcome_template = f.read()
        print("Loaded welcome-email-template.html")
    except FileNotFoundError:
        print("Warning: welcome-email-template.html not found. Welcome emails won't work.")

    try:
        with open("email-template.html", "r", encoding="utf-8") as f:
            email_template = f.read()
        print("Loaded email-template.html")
    except FileNotFoundError:
        print("Warning: email-template.html not found. Emails will look basic.")

    try:
        with open("task-reminder-email-template.html", "r", encoding="utf-8") as f:
            reminder_template = f.read()
        print("Loaded task-reminder-email-template.html")
    except FileNotFoundError:
        print("Warning: task-reminder-email-template.html not found. Reminder emails won't work.")

    try:
        with open("success.html", "r", encoding="utf-8") as f:
            success_template = f.read()
        print("Loaded success.html")
    except FileNotFoundError:
        print("Warning: success.html not found. The status update page will be broken.")
        success_template = "<h1>Success</h1><p>Task updated successfully.</p>"


def setup_logging():
    
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__)
    print("Logging configured.")

