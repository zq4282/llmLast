import json
from dataclasses import asdict

import fakeredis
import pytest

from app.session.store import MemorySessionStore, RedisSessionStore


def _flow(context: dict, *, unrecognized_count: int = 0) -> dict:
    return {
        "flow_id": "flow-refund",
        "business": "refund",
        "plugin_state": "CONFIRM_REFUND",
        "status": "ACTIVE",
        "context": context,
        "unrecognized_count": unrecognized_count,
        "completed_actions": [],
    }


def test_redis_store_round_trips_state_and_refreshes_ttl() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    store = RedisSessionStore(
        client=client,
        key_prefix="test-llmlast",
        ttl_seconds=600,
    )

    # 验证会话序列化、历史字段清理和 TTL。
    session_key = "test-llmlast:session:7:session-1"
    client.hset(
        session_key,
        mapping={
            "route_task": "REFUND",
            "route_confidence": "0.97",
            "route_locked": "1",
            "conversation_status": "BOT",
            "handoff_id": "legacy-id",
        },
    )
    store.save(
        "session-1",
        tenant_id=7,
        call_info={"caller": "13800138000"},
        flows=[_flow({"order_no": "ORD202405010001", "amount": 299.0}, unrecognized_count=2)],
        active_flow_id="flow-refund",
        user_message="adfadfadf",
        assistant_message="请换一种说法",
    )

    restored = store.get("session-1", tenant_id=7)
    assert restored.business == "refund"
    assert restored.plugin_state == "CONFIRM_REFUND"
    assert restored.unrecognized_count == 2
    assert restored.context["order_no"] == "ORD202405010001"
    assert restored.history[-1] == {"role": "assistant", "content": "请换一种说法"}
    assert not client.hexists(session_key, "route_task")
    assert not client.hexists(session_key, "conversation_status")
    assert not client.hexists(session_key, "handoff_id")
    assert 0 < client.ttl(session_key) <= 600


def test_redis_store_isolates_tenants_with_same_session_id() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    store = RedisSessionStore(client=client, key_prefix="test-llmlast")

    store.save(
        "same-id",
        tenant_id=1,
        call_info={},
        flows=[_flow({"tenant": 1})],
        active_flow_id="flow-refund",
        user_message="一",
        assistant_message="一",
    )
    store.save(
        "same-id",
        tenant_id=2,
        call_info={},
        flows=[_flow({"tenant": 2})],
        active_flow_id="flow-refund",
        user_message="二",
        assistant_message="二",
    )

    assert store.get("same-id", tenant_id=1).context == {"tenant": 1}
    assert store.get("same-id", tenant_id=2).context == {"tenant": 2}

    store.delete("same-id", tenant_id=1)

    assert store.get("same-id", tenant_id=1).context == {}
    assert store.get("same-id", tenant_id=2).context == {"tenant": 2}


def test_redis_store_round_trips_flow_stack_and_pending_switch() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    store = RedisSessionStore(client=client, key_prefix="test-llmlast")
    flows = [
        {
            "flow_id": "flow-refund",
            "business": "refund",
            "plugin_state": "CONFIRM_REFUND",
            "status": "ACTIVE",
            "context": {"order_no": "ORD202405010001"},
            "unrecognized_count": 0,
            "completed_actions": [],
        }
    ]
    pending = {
        "source_flow_id": "flow-refund",
        "target_business": "unsubscribe",
        "target_task": "UNSUBSCRIBE",
        "trigger_message": "我还要退订",
    }

    store.save(
        "flow-session",
        call_info={},
        user_message="我还要退订",
        assistant_message="是否切换？",
        flows=flows,
        active_flow_id="flow-refund",
        pending_switch=pending,
    )

    restored = store.get("flow-session")
    assert restored.flows == flows
    assert restored.active_flow_id == "flow-refund"
    assert restored.pending_switch == pending
    assert restored.business == "refund"
    assert restored.plugin_state == "CONFIRM_REFUND"


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_session_stores_only_flows_and_derives_current_business(backend: str) -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    store = MemorySessionStore() if backend == "memory" else RedisSessionStore(client=client)
    flows = [_flow({"order_no": "123"}, unrecognized_count=2)]
    store.save(
        "single-source", call_info={}, flows=flows, active_flow_id="flow-refund",
        user_message="退款", assistant_message="请确认",
    )
    flows[0]["context"]["order_no"] = "changed"
    restored = store.get("single-source")
    assert restored.context == {"order_no": "123"}
    assert restored.unrecognized_count == 2
    assert not {"business", "plugin_state", "context", "unrecognized_count"} & asdict(restored).keys()
    if backend == "redis":
        assert not {"business", "plugin_state", "context", "unrecognized_count"} & client.hgetall(
            "llmlast:session:1002:single-source"
        ).keys()


def test_redis_store_migrates_legacy_single_flow_and_cleans_duplicate_fields() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    store = RedisSessionStore(client=client)
    key = "llmlast:session:1002:legacy"
    client.hset(key, mapping={
        "business": "refund", "plugin_state": "CONFIRM_REFUND",
        "context": json.dumps({"order_no": "123"}), "unrecognized_count": "2",
        "history": json.dumps([{"role": "user", "content": "退款"}]),
    })
    restored = store.get("legacy")
    assert restored.business == "refund"
    assert restored.plugin_state == "CONFIRM_REFUND"
    assert restored.context == {"order_no": "123"}
    assert restored.unrecognized_count == 2
    assert restored.active_flow_id == restored.flows[0]["flow_id"]
    store.save(
        "legacy", call_info={}, flows=restored.flows, active_flow_id=restored.active_flow_id,
        user_message="确认", assistant_message="已处理",
    )
    assert store.get("legacy").flows == restored.flows
    assert len(store.get("legacy").history) == 3
    assert not {"business", "plugin_state", "context", "unrecognized_count"} & client.hgetall(key).keys()


@pytest.mark.parametrize("flows", [[], [{**_flow({}), "status": "COMPLETED"}]])
def test_redis_store_does_not_restore_stale_top_level_business(flows: list[dict]) -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    store = RedisSessionStore(client=client)
    client.hset("llmlast:session:1002:completed", mapping={
        "flows": json.dumps(flows), "active_flow_id": "",
        "business": "refund", "plugin_state": "CONFIRM_REFUND",
        "context": "invalid legacy JSON", "unrecognized_count": "invalid legacy count",
    })
    restored = store.get("completed")
    assert restored.flows == flows
    assert restored.business is None
    assert restored.context == {}


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_history_keeps_every_turn_beyond_previous_limit(backend: str) -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    store = MemorySessionStore() if backend == "memory" else RedisSessionStore(client=client)
    expected = []
    for turn in range(25):
        store.save(
            "full-history", call_info={}, flows=[],
            user_message=f"用户问题{turn}", assistant_message=f"客服回复{turn}",
        )
        expected.extend([
            {"role": "user", "content": f"用户问题{turn}"},
            {"role": "assistant", "content": f"客服回复{turn}"},
        ])
    assert store.get("full-history").history == expected
