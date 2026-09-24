import fakeredis

from app.session.store import RedisSessionStore


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
        business="refund",
        plugin_state="CONFIRM_REFUND",
        call_info={"caller": "13800138000"},
        context={"order_no": "ORD202405010001", "amount": 299.0},
        user_message="adfadfadf",
        assistant_message="请换一种说法",
        unrecognized_count=2,
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
        business="refund",
        call_info={},
        context={"tenant": 1},
        user_message="一",
        assistant_message="一",
    )
    store.save(
        "same-id",
        tenant_id=2,
        business="refund",
        call_info={},
        context={"tenant": 2},
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
        business="refund",
        plugin_state="CONFIRM_REFUND",
        call_info={},
        context=flows[0]["context"],
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
