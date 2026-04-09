from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, pf, posts, notifications, chat

app = FastAPI(
    title="Weve API",
    description="Auth API for Weve (Supabase)",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "https://weve-dev.pages.dev",
        "https://weve-frontend-production.up.railway.app",
        "https://joinweve.com",
        "https://www.joinweve.com",
    ],
    allow_origin_regex=r"https://.*(\.pages\.dev|\.up\.railway\.app|\.run\.app|joinweve\.com)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(pf.router, prefix="/api")
app.include_router(posts.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(chat.router, prefix="/api")

@app.get("/")
def root():
    return {
        "message": "Welcome to Weve API",
        "status": "running",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health():
    return {"status": "ok"}
