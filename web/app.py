"""FastAPI application setup."""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os

from database.db import init_db

app = FastAPI(title="Solana Wallet Scanner & Tax Analyzer")

# Static files and templates
static_dir = os.path.join(os.path.dirname(__file__), "static")
template_dir = os.path.join(os.path.dirname(__file__), "templates")

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=template_dir)

# Initialize database on startup
@app.on_event("startup")
def startup():
    init_db()

# Import routes after app creation to avoid circular imports
from web.routes import router
app.include_router(router)
