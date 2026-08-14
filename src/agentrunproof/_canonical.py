from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, TypeAlias, cast

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class CanonicalizationError(TypeError):
    """Raised when a value cannot be represented as fail-closed canonical JSON."""


def to_json_value(value: Any) -> JsonValue:
    """Convert supported values into detached JSON data or fail closed."""

    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("Non-finite floats are not valid evidence values.")
        return value
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_json_value(dataclasses.asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return to_json_value(model_dump(mode="json", by_alias=True, exclude_none=True))
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("Evidence mappings require string keys.")
            result[key] = to_json_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [to_json_value(item) for item in value]
    raise CanonicalizationError(f"Unsupported evidence value: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    payload = to_json_value(value)
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def deep_json_copy(value: Any) -> JsonValue:
    return cast(JsonValue, json.loads(canonical_bytes(value)))
