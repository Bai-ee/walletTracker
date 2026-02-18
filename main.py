"""Web application entry point."""
import logging
import uvicorn
from config import WEB_HOST, WEB_PORT
from database.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

if __name__ == "__main__":
    init_db()
    print(f"Starting Solana Scanner at http://{WEB_HOST}:{WEB_PORT}")
    uvicorn.run("web.app:app", host=WEB_HOST, port=WEB_PORT, reload=True)
