"""线程安全的内存会话存储，生产环境可按同一接口替换成 Redis。"""

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class Session:
    business: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)


class MemorySessionStore:
    def __init__(self, max_history: int = 20) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = RLock()
        self._max_history = max_history

    def get(self, session_id: str) -> Session:
        with self._lock:
            return deepcopy(self._sessions.get(session_id, Session()))

    def save(
        self,
        session_id: str,
        *,
        business: str,
        context: dict[str, Any],
        user_message: str,
        assistant_message: str,
    ) -> None:
        with self._lock:
            session = self._sessions.setdefault(session_id, Session())
            session.business = business
            session.context = deepcopy(context)
            session.history.extend(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_message},
                ]
            )
            session.history = session.history[-self._max_history :]

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


session_store = MemorySessionStore()
