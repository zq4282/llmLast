"""跨进程会话存储；生产使用 Redis，内存实现仅供测试。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from threading import RLock
from time import time
from typing import Any, ContextManager, Iterator, Literal, Protocol

import redis
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.exceptions import LockError, RedisError


class SessionStoreError(RuntimeError):
    """会话存储不可用或读写失败。"""


class SessionBusyError(SessionStoreError):
    """同一会话已有一轮请求在处理中。"""


@dataclass
class Session:
    business: str | None = None
    plugin_state: str | None = None
    route_task: str | None = None
    route_confidence: float | None = None
    route_locked: bool = False
    call_info: dict[str, str] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)
    unrecognized_count: int = 0
    conversation_status: str = "BOT"
    handoff_reason: str | None = None
    handoff_id: str | None = None


class SessionStore(Protocol):
    def get(self, session_id: str, *, tenant_id: int = 1002) -> Session: ...

    def save(
        self,
        session_id: str,
        *,
        business: str,
        plugin_state: str | None = None,
        route_task: str | None = None,
        route_confidence: float | None = None,
        route_locked: bool = False,
        call_info: dict[str, str],
        context: dict[str, Any],
        user_message: str,
        assistant_message: str,
        tenant_id: int = 1002,
        unrecognized_count: int = 0,
        conversation_status: str = "BOT",
        handoff_reason: str | None = None,
        handoff_id: str | None = None,
    ) -> None: ...

    def lock(self, session_id: str, *, tenant_id: int = 1002) -> ContextManager[None]: ...

    def ping(self) -> None: ...


class MemorySessionStore:
    """单进程测试替身；生产环境不得依赖它共享状态。"""

    def __init__(self, max_history: int = 20) -> None:
        self._sessions: dict[str, Session] = {}
        self._guard = RLock()
        self._session_locks: dict[str, RLock] = {}
        self._max_history = max_history

    @staticmethod
    def _key(session_id: str, tenant_id: int) -> str:
        return f"{tenant_id}:{session_id}"

    def get(self, session_id: str, *, tenant_id: int = 1002) -> Session:
        key = self._key(session_id, tenant_id)
        with self._guard:
            return deepcopy(self._sessions.get(key, Session()))

    def save(
        self,
        session_id: str,
        *,
        business: str,
        plugin_state: str | None = None,
        route_task: str | None = None,
        route_confidence: float | None = None,
        route_locked: bool = False,
        call_info: dict[str, str],
        context: dict[str, Any],
        user_message: str,
        assistant_message: str,
        tenant_id: int = 1002,
        unrecognized_count: int = 0,
        conversation_status: str = "BOT",
        handoff_reason: str | None = None,
        handoff_id: str | None = None,
    ) -> None:
        key = self._key(session_id, tenant_id)
        with self._guard:
            session = self._sessions.setdefault(key, Session())
            _update_session(
                session,
                business=business,
                plugin_state=plugin_state,
                route_task=route_task,
                route_confidence=route_confidence,
                route_locked=route_locked,
                call_info=call_info,
                context=context,
                user_message=user_message,
                assistant_message=assistant_message,
                max_history=self._max_history,
                unrecognized_count=unrecognized_count,
                conversation_status=conversation_status,
                handoff_reason=handoff_reason,
                handoff_id=handoff_id,
            )

    @contextmanager
    def lock(self, session_id: str, *, tenant_id: int = 1002) -> Iterator[None]:
        key = self._key(session_id, tenant_id)
        with self._guard:
            session_lock = self._session_locks.setdefault(key, RLock())
        with session_lock:
            yield

    def ping(self) -> None:
        return None

    def clear(self) -> None:
        with self._guard:
            self._sessions.clear()
            self._session_locks.clear()


class RedisSessionStore:
    """使用 Redis Hash、TTL 和分布式锁保存会话。"""

    def __init__(
        self,
        url: str = "redis://127.0.0.1:6379/0",
        *,
        key_prefix: str = "llmlast",
        ttl_seconds: int = 86400,
        lock_timeout_seconds: int = 30,
        lock_blocking_timeout_seconds: float = 5.0,
        max_history: int = 20,
        client: redis.Redis | None = None,
    ) -> None:
        self._client = client or redis.Redis.from_url(url, decode_responses=True)
        self._key_prefix = key_prefix.strip(":")
        self._ttl_seconds = ttl_seconds
        self._lock_timeout_seconds = lock_timeout_seconds
        self._lock_blocking_timeout_seconds = lock_blocking_timeout_seconds
        self._max_history = max_history

    def _key(self, session_id: str, tenant_id: int) -> str:
        return f"{self._key_prefix}:session:{tenant_id}:{session_id}"

    def _lock_key(self, session_id: str, tenant_id: int) -> str:
        return f"{self._key_prefix}:session-lock:{tenant_id}:{session_id}"

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
                route_task=raw.get("route_task") or None,
                route_confidence=(
                    float(raw["route_confidence"])
                    if raw.get("route_confidence")
                    else None
                ),
                route_locked=raw.get("route_locked") == "1",
                call_info=_json_object(raw.get("call_info")),
                context=_json_object(raw.get("context")),
                history=_json_history(raw.get("history")),
                unrecognized_count=int(raw.get("unrecognized_count", "0")),
                conversation_status=raw.get("conversation_status") or "BOT",
                handoff_reason=raw.get("handoff_reason") or None,
                handoff_id=raw.get("handoff_id") or None,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SessionStoreError("Redis 会话数据格式错误") from exc

    def save(
        self,
        session_id: str,
        *,
        business: str,
        plugin_state: str | None = None,
        route_task: str | None = None,
        route_confidence: float | None = None,
        route_locked: bool = False,
        call_info: dict[str, str],
        context: dict[str, Any],
        user_message: str,
        assistant_message: str,
        tenant_id: int = 1002,
        unrecognized_count: int = 0,
        conversation_status: str = "BOT",
        handoff_reason: str | None = None,
        handoff_id: str | None = None,
    ) -> None:
        session = self.get(session_id, tenant_id=tenant_id)
        _update_session(
            session,
            business=business,
            plugin_state=plugin_state,
            route_task=route_task,
            route_confidence=route_confidence,
            route_locked=route_locked,
            call_info=call_info,
            context=context,
            user_message=user_message,
            assistant_message=assistant_message,
            max_history=self._max_history,
            unrecognized_count=unrecognized_count,
            conversation_status=conversation_status,
            handoff_reason=handoff_reason,
            handoff_id=handoff_id,
        )
        values = asdict(session)
        mapping = {
            "business": values["business"] or "",
            "plugin_state": values["plugin_state"] or "",
            "route_task": values["route_task"] or "",
            "route_confidence": (
                str(values["route_confidence"])
                if values["route_confidence"] is not None
                else ""
            ),
            "route_locked": "1" if values["route_locked"] else "0",
            "call_info": json.dumps(values["call_info"], ensure_ascii=False),
            "context": json.dumps(values["context"], ensure_ascii=False),
            "history": json.dumps(values["history"], ensure_ascii=False),
            "unrecognized_count": str(values["unrecognized_count"]),
            "conversation_status": values["conversation_status"],
            "handoff_reason": values["handoff_reason"] or "",
            "handoff_id": values["handoff_id"] or "",
            "updated_at": str(int(time())),
        }
        key = self._key(session_id, tenant_id)
        try:
            with self._client.pipeline(transaction=True) as pipe:
                pipe.hset(key, mapping=mapping)
                pipe.expire(key, self._ttl_seconds)
                pipe.execute()
        except RedisError as exc:
            raise SessionStoreError("保存 Redis 会话失败") from exc

    @contextmanager
    def lock(self, session_id: str, *, tenant_id: int = 1002) -> Iterator[None]:
        redis_lock = self._client.lock(
            self._lock_key(session_id, tenant_id),
            timeout=self._lock_timeout_seconds,
            blocking_timeout=self._lock_blocking_timeout_seconds,
        )
        try:
            acquired = redis_lock.acquire(blocking=True)
        except RedisError as exc:
            raise SessionStoreError("获取 Redis 会话锁失败") from exc
        if not acquired:
            raise SessionBusyError("当前会话正在处理上一条消息，请稍后重试")
        try:
            yield
        finally:
            try:
                if redis_lock.owned():
                    redis_lock.release()
            except (RedisError, LockError) as exc:
                raise SessionStoreError("释放 Redis 会话锁失败") from exc

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
    session_lock_timeout_seconds: int = Field(default=30, ge=5)
    session_lock_blocking_timeout_seconds: float = Field(default=5.0, gt=0)
    session_max_history: int = Field(default=20, ge=2)


def build_session_store(settings: SessionSettings | None = None) -> SessionStore:
    settings = settings or SessionSettings()
    if settings.session_store_backend == "memory":
        return MemorySessionStore(max_history=settings.session_max_history)
    return RedisSessionStore(
        settings.redis_url,
        key_prefix=settings.session_key_prefix,
        ttl_seconds=settings.session_ttl_seconds,
        lock_timeout_seconds=settings.session_lock_timeout_seconds,
        lock_blocking_timeout_seconds=settings.session_lock_blocking_timeout_seconds,
        max_history=settings.session_max_history,
    )


def _update_session(
    session: Session,
    *,
    business: str,
    plugin_state: str | None,
    route_task: str | None,
    route_confidence: float | None,
    route_locked: bool,
    call_info: dict[str, str],
    context: dict[str, Any],
    user_message: str,
    assistant_message: str,
    max_history: int,
    unrecognized_count: int,
    conversation_status: str,
    handoff_reason: str | None,
    handoff_id: str | None,
) -> None:
    session.business = business
    session.plugin_state = plugin_state
    session.route_task = route_task
    session.route_confidence = route_confidence
    session.route_locked = route_locked
    session.call_info = deepcopy(call_info)
    session.context = deepcopy(context)
    session.unrecognized_count = unrecognized_count
    session.conversation_status = conversation_status
    session.handoff_reason = handoff_reason
    session.handoff_id = handoff_id
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
