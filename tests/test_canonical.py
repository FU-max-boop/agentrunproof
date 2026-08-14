from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from agentrunproof._canonical import CanonicalizationError, canonical_bytes, sha256_hex


@dataclass(frozen=True)
class Example:
    value: int


def test_canonical_json_is_order_independent() -> None:
    left = {"b": [2, 1], "a": Example(3)}
    right = {"a": {"value": 3}, "b": [2, 1]}

    assert canonical_bytes(left) == canonical_bytes(right)
    assert sha256_hex(left) == sha256_hex(right)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_rejects_non_finite_floats(value: float) -> None:
    with pytest.raises(CanonicalizationError, match="Non-finite"):
        canonical_bytes(value)


def test_canonical_json_rejects_unknown_objects() -> None:
    with pytest.raises(CanonicalizationError, match="Unsupported"):
        canonical_bytes(object())


def test_canonical_json_rejects_non_string_mapping_keys() -> None:
    with pytest.raises(CanonicalizationError, match="string keys"):
        canonical_bytes({1: "value"})
