from __future__ import annotations

from typing import Any


def mask_payload(payload: Any, mask_fields: list[str]) -> Any:
    """
    Recursively masks sensitive fields in a payload.

    Args:
        payload: The payload to mask (dict, list, or primitive type).
        mask_fields: A list of field names that should be masked.

    Returns:
        The masked payload (a new dictionary/list if modified, or the original primitive).
    """
    if not mask_fields:
        return payload

    mask_set = {f.strip().lower() for f in mask_fields}

    if isinstance(payload, dict):
        masked_dict = {}
        for key, value in payload.items():
            if str(key).lower() in mask_set:
                masked_dict[key] = "[MASKED]"
            else:
                masked_dict[key] = mask_payload(value, mask_fields)
        return masked_dict

    elif isinstance(payload, list):
        return [mask_payload(item, mask_fields) for item in payload]

    elif isinstance(payload, tuple):
        return tuple(mask_payload(item, mask_fields) for item in payload)

    return payload
