from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings, load_html_templates, setup_gemini_api, setup_logging
from routers import router as api_router


setup_logging()
settings = get_settings()
setup_gemini_api()
load_html_templates()

app = FastAPI(title="Ally API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    expose_headers=["Retry-After"],
    max_age=600,
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=()"
    return response


app.include_router(api_router)


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Welcome to Ally - the future of AI meeting management!"}


@app.api_route("/health", status_code=200, methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}
