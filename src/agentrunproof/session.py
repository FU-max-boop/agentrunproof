from __future__ import annotations

import copy
from dataclasses import dataclass

from agents.items import TResponseInputItem
from agents.memory.session_settings import SessionSettings

from ._canonical import JsonValue, deep_json_copy


@dataclass(frozen=True)
class SessionOperation:
    operation: str
    item_count: int
    limit: int | None = None


class RecordingSession:
    """A detached in-memory ``Session`` implementation with an operation log."""

    session_settings: SessionSettings | None = None

    def __init__(
        self,
        session_id: str = "agentrunproof-session",
        items: list[TResponseInputItem] | None = None,
    ) -> None:
        self.session_id = session_id
        self._items = copy.deepcopy(items or [])
        self._operations: list[SessionOperation] = []

    @property
    def operations(self) -> tuple[SessionOperation, ...]:
        return tuple(self._operations)

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        selected = self._items if limit is None else self._items[-limit:] if limit > 0 else []
        self._operations.append(
            SessionOperation(operation="get_items", item_count=len(selected), limit=limit)
        )
        return copy.deepcopy(selected)

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        self._operations.append(SessionOperation(operation="add_items", item_count=len(items)))
        self._items.extend(copy.deepcopy(items))

    async def pop_item(self) -> TResponseInputItem | None:
        item = self._items.pop() if self._items else None
        self._operations.append(
            SessionOperation(operation="pop_item", item_count=1 if item is not None else 0)
        )
        return copy.deepcopy(item)

    async def clear_session(self) -> None:
        removed = len(self._items)
        self._items.clear()
        self._operations.append(SessionOperation(operation="clear_session", item_count=removed))

    def snapshot(self) -> list[JsonValue]:
        return [deep_json_copy(item) for item in self._items]
