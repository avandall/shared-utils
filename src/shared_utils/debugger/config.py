from __future__ import annotations

import os
from pydantic import BaseModel, Field


class DebuggerSettings(BaseModel):
    """Configuration settings for the WMS Debugger SDK loaded from environment variables."""

    enabled: bool = Field(default=False)
    exporter: str = Field(default="console")
    collector_url: str = Field(default="http://otel-collector:4317")
    mask_fields: list[str] = Field(default_factory=lambda: ["password", "token", "jwt_token"])
    file_path: str = Field(default="/home/avandall1999/Projects/WMS_Root/wms_debug.log")
    write_file: bool = Field(default=False)

    @classmethod
    def load_from_env(cls) -> DebuggerSettings:
        enabled_str = os.getenv("WMS_DEBUG_ENABLED", "false").strip().lower()
        enabled = enabled_str in ("true", "1", "yes", "on")

        mask_fields_str = os.getenv("WMS_DEBUG_MASK_FIELDS", "password,token,jwt_token")
        mask_fields = [f.strip() for f in mask_fields_str.split(",") if f.strip()]

        exporter = os.getenv("WMS_DEBUG_EXPORTER", "console").strip()
        
        write_file_str = os.getenv("WMS_DEBUG_WRITE_FILE", "false").strip().lower()
        write_file = write_file_str in ("true", "1", "yes", "on") or exporter == "file"

        file_path = os.getenv("WMS_DEBUG_FILE_PATH", "/home/avandall1999/Projects/WMS_Root/wms_debug.log").strip()

        return cls(
            enabled=enabled,
            exporter=exporter,
            collector_url=os.getenv("WMS_DEBUG_COLLECTOR_URL", "http://otel-collector:4317").strip(),
            mask_fields=mask_fields,
            file_path=file_path,
            write_file=write_file,
        )


settings = DebuggerSettings.load_from_env()
