from app.engine.graph import run_graph


def test_refund_collects_order_id_across_turns() -> None:
    first = run_graph(
        {
            "session_id": "refund-test",
            "message": "我要申请退款",
            "history": [],
            "active_business": None,
            "context": {},
        }
    )
    assert first["missing_slots"] == ["order_id"]
    assert "订单号" in first["reply"]

    second = run_graph(
        {
            "session_id": "refund-test",
            "message": "A1001，因为买错了",
            "history": [],
            "active_business": "refund",
            "context": first["context"],
        }
    )
    assert second["action_result"]["ok"] is True
    assert second["action_result"]["order_id"] == "A1001"
    assert "退款申请已提交" in second["reply"]
