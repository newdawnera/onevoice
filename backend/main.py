# main.py
# This is the main file for my FastAPI app.
# It starts the server, handles CORS, and sets up all the API routes.
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Import my other project files
from config import setup_firebase, setup_gemini_api, load_html_templates, setup_logging
from routers import router as api_router

# Run all the setup functions when the app starts
setup_logging()
setup_firebase()
setup_gemini_api()
load_html_templates()


app = FastAPI()

origins = [
    "https://ally-frontend-vw00.onrender.com/"  
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(api_router, prefix="/api", tags=["Ally API"])

@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Welcome to Ally - the future of AI meeting management!"}


if __name__ == "__main__":
    
    uvicorn.run(app, host="0.0.0.0", port=8000)

