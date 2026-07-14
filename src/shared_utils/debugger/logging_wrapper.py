from __future__ import annotations

import json
import time
from typing import Any

from shared_utils.observability.http import json_log
from .config import settings


def debug_log(*, service: str, level: str, message: str, **fields: Any) -> None:
    """
    Wrapper for logging that outputs structured JSON to stdout
    and optionally writes it to a file.
    """
    # 1. Print structured log to stdout (standard behavior)
    json_log(service=service, level=level, message=message, **fields)

    # 2. Append to log file if writing is active
    if settings.write_file:
        try:
            record = {
                "ts": time.time(),
                "service": service,
                "level": level,
                "msg": message,
            }
            record.update(fields)
            
            # Ensure folder directory exists
            import os
            dir_name = os.path.dirname(settings.file_path)
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)

            with open(settings.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            # Silence logging file errors to prevent crashing business logic
            pass
