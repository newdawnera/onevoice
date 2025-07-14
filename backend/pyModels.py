
# This file defines the structure of the data I expect to get from the frontend.
from pydantic import BaseModel
from typing import Optional, Dict

class ResultRequest(BaseModel):
    text: str
    role: Optional[str] = None
    target_language: Optional[str] = None

class AiHelperRequest(BaseModel):
    task_type: str
    context: Dict
    is_json: Optional[bool] = False

class ManualEmailRequest(BaseModel):
    task: dict
    userId: str

class WelcomeEmailRequest(BaseModel):
    email: str
    username: str
