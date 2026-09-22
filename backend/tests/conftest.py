import os
import sys
from dataclasses import replace
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("APP_ENV", "test")


def configured_settings(**overrides):
    from config import get_settings

    base = get_settings()
    defaults = {
        "app_env": "test",
        "supabase_url": "https://project.example.supabase.co",
        "supabase_publishable_key": "sb_publishable_test_public_key",
        "supabase_service_role_key": "sb_secret_test_server_key",
        "supabase_jwt_issuer": "https://project.example.supabase.co/auth/v1",
        "supabase_jwt_audience": "authenticated",
        "supabase_jwt_verification_mode": "asymmetric",
        "supabase_jwt_algorithms": ("ES256",),
    }
    defaults.update(overrides)
    return replace(base, **defaults)
