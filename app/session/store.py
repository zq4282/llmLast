"""跨进程会话存储；生产使用 Redis，内存实现仅供测试。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from threading import RLock
from time import time
from typing import Any, Literal, Protocol

import redis
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.exceptions import RedisError


_OBSOLETE_SESSION_FIELDS = (
    "route_task",
    "route_confidence",
    "route_locked",
    "conversation_status",
    "handoff_reason",
    "handoff_id",
)


class SessionStoreError(RuntimeError):
    """会话存储不可用或读写失败。"""


@dataclass
class Session:
    business: str | None = None
    plugin_state: str | None = None
    call_info: dict[str, str] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)
    unrecognized_count: int = 0


class SessionStore(Protocol):
    def get(self, session_id: str, *, tenant_id: int = 1002) -> Session: ...

    def delete(self, session_id: str, *, tenant_id: int = 1002) -> None: ...

    def save(
        self,
        session_id: str,
        *,
        business: str,
        plugin_state: str | None = None,
        call_info: dict[str, str],
        context: dict[str, Any],
        user_message: str,
        assistant_message: str,
        tenant_id: int = 1002,
        unrecognized_count: int = 0,
    ) -> None: ...

    def ping(self) -> None: ...


class MemorySessionStore:
    """单进程测试替身；生产环境不得依赖它共享状态。"""

    def __init__(self, max_history: int = 20) -> None:
        self._sessions: dict[str, Session] = {}
        self._guard = RLock()
        self._max_history = max_history

    @staticmethod
    def _key(session_id: str, tenant_id: int) -> str:
        return f"{tenant_id}:{session_id}"

    def get(self, session_id: str, *, tenant_id: int = 1002) -> Session:
        key = self._key(session_id, tenant_id)
        with self._guard:
            return deepcopy(self._sessions.get(key, Session()))

    def delete(self, session_id: str, *, tenant_id: int = 1002) -> None:
        key = self._key(session_id, tenant_id)
        with self._guard:
            self._sessions.pop(key, None)

    def save(
        self,
        session_id: str,
        *,
        business: str,
        plugin_state: str | None = None,
        call_info: dict[str, str],
        context: dict[str, Any],
        user_message: str,
        assistant_message: str,
        tenant_id: int = 1002,
        unrecognized_count: int = 0,
    ) -> None:
        key = self._key(session_id, tenant_id)
        with self._guard:
            session = self._sessions.setdefault(key, Session())
            _update_session(
                session,
                business=business,
                plugin_state=plugin_state,
                call_info=call_info,
                context=context,
                user_message=user_message,
                assistant_message=assistant_message,
                max_history=self._max_history,
                unrecognized_count=unrecognized_count,
            )

    def ping(self) -> None:
        return None

    def clear(self) -> None:
        with self._guard:
            self._sessions.clear()


class RedisSessionStore:
    """使用 Redis Hash 和 TTL 保存会话。"""

    def __init__(
        self,
        url: str = "redis://127.0.0.1:6379/0",
        *,
        key_prefix: str = "llmlast",
        ttl_seconds: int = 86400,
        max_history: int = 20,
        client: redis.Redis | None = None,
    ) -> None:
        self._client = client or redis.Redis.from_url(url, decode_responses=True)
        self._key_prefix = key_prefix.strip(":")
        self._ttl_seconds = ttl_seconds
        self._max_history = max_history

    def _key(self, session_id: str, tenant_id: int) -> str:
        return f"{self._key_prefix}:session:{tenant_id}:{session_id}"

    def get(self, session_id: str, *, tenant_id: int = 1002) -> Session:
        try:
            raw = self._client.hgetall(self._key(session_id, tenant_id))
        except RedisError as exc:
            raise SessionStoreError("读取 Redis 会话失败") from exc
        if not raw:
            return Session()
        try:
            return Session(
                business=raw.get("business") or None,
                plugin_state=raw.get("plugin_state") or None,
                call_info=_json_object(raw.get("call_info")),
                context=_json_object(raw.get("context")),
                history=_json_history(raw.get("history")),
                unrecognized_count=int(raw.get("unrecognized_count", "0")),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SessionStoreError("Redis 会话数据格式错误") from exc

    def delete(self, session_id: str, *, tenant_id: int = 1002) -> None:
        try:
            self._client.delete(self._key(session_id, tenant_id))
        except RedisError as exc:
            raise SessionStoreError("删除 Redis 会话失败") from exc

    def save(
        self,
        session_id: str,
        *,
        business: str,
        plugin_state: str | None = None,
        call_info: dict[str, str],
        context: dict[str, Any],
        user_message: str,
        assistant_message: str,
        tenant_id: int = 1002,
        unrecognized_count: int = 0,
    ) -> None:
        session = self.get(session_id, tenant_id=tenant_id)
        _update_session(
            session,
            business=business,
            plugin_state=plugin_state,
            call_info=call_info,
            context=context,
            user_message=user_message,
            assistant_message=assistant_message,
            max_history=self._max_history,
            unrecognized_count=unrecognized_count,
        )
        values = asdict(session)
        mapping = {
            "business": values["business"] or "",
            "plugin_state": values["plugin_state"] or "",
            "call_info": json.dumps(values["call_info"], ensure_ascii=False),
            "context": json.dumps(values["context"], ensure_ascii=False),
            "history": json.dumps(values["history"], ensure_ascii=False),
            "unrecognized_count": str(values["unrecognized_count"]),
            "updated_at": str(int(time())),
        }
        key = self._key(session_id, tenant_id)
        try:
            with self._client.pipeline(transaction=True) as pipe:
                # 兼容升级前的 Redis Hash；新会话结构不再保留这些历史字段。
                pipe.hdel(key, *_OBSOLETE_SESSION_FIELDS)
                pipe.hset(key, mapping=mapping)
                pipe.expire(key, self._ttl_seconds)
                pipe.execute()
        except RedisError as exc:
            raise SessionStoreError("保存 Redis 会话失败") from exc

    def ping(self) -> None:
        try:
            self._client.ping()
        except RedisError as exc:
            raise SessionStoreError("Redis 会话存储不可用") from exc

    def clear(self) -> None:
        """仅删除本服务命名空间内的会话数据，不执行 FLUSHDB。"""

        try:
            keys = list(self._client.scan_iter(match=f"{self._key_prefix}:session:*"))
            if keys:
                self._client.delete(*keys)
        except RedisError as exc:
            raise SessionStoreError("清理 Redis 会话失败") from exc


class SessionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    session_store_backend: Literal["redis", "memory"] = "redis"
    redis_url: str = "redis://127.0.0.1:6379/0"
    session_key_prefix: str = "llmlast"
    session_ttl_seconds: int = Field(default=86400, ge=60)
    session_max_history: int = Field(default=20, ge=2)


def build_session_store(settings: SessionSettings | None = None) -> MemorySessionStore | RedisSessionStore:
    settings = settings or SessionSettings()
    if settings.session_store_backend == "memory":
        return MemorySessionStore(max_history=settings.session_max_history)
    return RedisSessionStore(
        settings.redis_url,
        key_prefix=settings.session_key_prefix,
        ttl_seconds=settings.session_ttl_seconds,
        max_history=settings.session_max_history,
    )


def _update_session(
    session: Session,
    *,
    business: str,
    plugin_state: str | None,
    call_info: dict[str, str],
    context: dict[str, Any],
    user_message: str,
    assistant_message: str,
    max_history: int,
    unrecognized_count: int,
) -> None:
    session.business = business
    session.plugin_state = plugin_state
    session.call_info = deepcopy(call_info)
    session.context = deepcopy(context)
    session.unrecognized_count = unrecognized_count
    session.history.extend(
        [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]
    )
    session.history = session.history[-max_history:]


def _json_object(value: str | None) -> dict[str, Any]:
    data = json.loads(value or "{}")
    if not isinstance(data, dict):
        raise TypeError("expected JSON object")
    return data


def _json_history(value: str | None) -> list[dict[str, str]]:
    data = json.loads(value or "[]")
    if not isinstance(data, list):
        raise TypeError("expected JSON array")
    history: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            raise TypeError("history item must be object")
        history.append({str(key): str(item_value) for key, item_value in item.items()})
    return history


session_store = build_session_store()
