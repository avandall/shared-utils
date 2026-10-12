"""Outbox event model and persistence helper functions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, DateTime, Integer, String, Text

try:
    from app.shared.core.database import Base
except ImportError:
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()


class OutboxEventModel(Base):
    """Transactional Outbox event record mapped to outbox_events table."""

    __tablename__ = "outbox_events"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(128), unique=True, index=True, nullable=False)
    stream_name = Column(String(128), default="wms.events", nullable=False)
    event_type = Column(String(128), index=True, nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(String(32), default="PENDING", index=True, nullable=False)
    retry_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    published_at = Column(DateTime, nullable=True)

    def get_payload_dict(self) -> dict[str, Any]:
        """Deserialize payload string into dictionary."""
        if isinstance(self.payload, dict):
            return self.payload
        try:
            return json.loads(self.payload)
        except Exception:
            return {}


def record_outbox_event(
    session: Any,
    event_type: str,
    payload: dict[str, Any],
    event_id: Optional[str] = None,
    stream_name: str = "wms.events",
) -> OutboxEventModel:
    """Record an outbox event in the current active database session."""
    if not event_id:
        event_id = f"evt_{uuid.uuid4().hex}"

    payload_str = json.dumps(payload, default=str)
    record = OutboxEventModel(
        event_id=event_id,
        stream_name=stream_name,
        event_type=event_type,
        payload=payload_str,
        status="PENDING",
        retry_count=0,
        created_at=datetime.utcnow(),
    )
    session.add(record)
    return record
