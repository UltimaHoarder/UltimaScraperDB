import logging
import os
import sys
import time
import warnings
from pathlib import Path

from sqlalchemy.exc import SAWarning
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.config import LOGGING_CONFIG

# Modify the formatters to include PID at the end
LOGGING_CONFIG["formatters"]["default"][
    "fmt"
] = "%(levelprefix)s %(message)s - PID:%(process)d"
LOGGING_CONFIG["formatters"]["access"][
    "fmt"
] = '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s - PID:%(process)d'

logging.config.dictConfig(LOGGING_CONFIG)  # type: ignore

sys.path.append(str(Path(__file__).resolve().parents[4]))
from ultima_scraper_db.databases.ultima_archive.api.client import UAClient

app = UAClient()
