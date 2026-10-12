"""OutboxDispatcher worker for scanning and publishing pending outbox events."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from shared_utils.outbox.models import OutboxEventModel

logger = logging.getLogger(__name__)


class OutboxDispatcher:
    """Dispatches pending outbox events to the message broker."""

    def __init__(self, publisher: Optional[Any] = None, max_retries: int = 3):
        self.publisher = publisher
        self.max_retries = max_retries

    def dispatch_pending(self, session: Any, batch_size: int = 50) -> int:
        """Scan PENDING outbox records, publish via publisher, and mark PUBLISHED.

        Returns the number of successfully published records.
        """
        stmt = (
            select(OutboxEventModel)
            .where(
                OutboxEventModel.status == "PENDING",
                OutboxEventModel.retry_count < self.max_retries,
            )
            .order_by(OutboxEventModel.created_at.asc())
            .limit(batch_size)
        )
        records = session.execute(stmt).scalars().all()
        published_count = 0

        for record in records:
            if self.publisher is None:
                continue
            try:
                self.publisher.publish(
                    event_type=record.event_type,
                    payload=record.get_payload_dict(),
                )
                record.status = "PUBLISHED"
                record.published_at = datetime.utcnow()
                published_count += 1
            except Exception as exc:
                record.retry_count += 1
                if record.retry_count >= self.max_retries:
                    record.status = "FAILED"
                logger.warning(
                    f"Outbox dispatch failed for event {record.event_id} "
                    f"(retry {record.retry_count}/{self.max_retries}): {exc}"
                )

        session.commit()
        return published_count
