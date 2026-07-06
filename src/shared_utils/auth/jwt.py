from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import jwt


@dataclass(slots=True)
class JWTValidationError(Exception):
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return self.message


def decode_jwt(token: str, *, secret: str, algorithms: list[str] | None = None) -> dict[str, Any]:
    algorithms = algorithms or ["HS256"]
    try:
        payload = jwt.decode(token, secret, algorithms=algorithms)
    except jwt.PyJWTError as exc:
        raise JWTValidationError("Invalid token") from exc

    exp = payload.get("exp")
    if exp is not None:
        now = datetime.now(tz=timezone.utc).timestamp()
        if float(exp) < now:
            raise JWTValidationError("Token expired")
    return payload


def encode_jwt(payload: dict[str, Any], *, secret: str, algorithm: str = "HS256") -> str:
    return jwt.encode(payload, secret, algorithm=algorithm)

