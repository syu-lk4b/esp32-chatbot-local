"""Entry point: python -m voice_server"""
import logging

import uvicorn

from .app import create_app
from .config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = Settings()
settings.require_safe_exposure()
uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")
