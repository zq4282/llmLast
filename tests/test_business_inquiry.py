import json
import shutil

import pytest
import yaml
from fastapi.testclient import TestClient

from app.businesses.business_inquiry.handlers import query_order
from app.businesses.business_inquiry.scenario_registry import (
    SCENARIO_IDS, SCENARIO_ROOT, load_scenario,
)
from app.engine.llm import IntentLLM, intent_llm
from app.engine.runtime import DialogueEngine
from app.main import app
from app.services import order_inquiry, order_lookup
from app.session.store import session_store


class Client:
    def __init__(self, *decisions):
        self.responses = [json.dumps(d, ensure_ascii=False) for d in decisions]
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.responses.pop(0)


def decision(intent, **slots):
    return {"intent": intent, "confidence": 0.98, "slots": slots}


def engine_for(*decisions):
    llm = IntentLLM()
    llm.client = Client({"task": "BUSINESS_QA", "confidence": 0.98}, *decisions)
    return DialogueEngine(llm=llm), llm.client


def followup(first, message):
    return {**first, "message": message, "history": [
        {"role": "user", "content": first["message"]},
        {"role": "assistant", "content": first["reply"]},
    ]}


@pytest.mark.parametrize("scenario", SCENARIO_IDS)
@pytest.mark.parametrize("last_intent,output", [("needs_handling", "HUMAN"), ("end", "END")])
def test_scenarios_query_then_close_without_business_actions(scenario, last_intent, output):
    engine, client = engine_for(decision(scenario, order_no="ORD202405020002"), decision(last_intent))
    first = engine.run({"message": "核查订单", "history": []})
    assert first["business"] == "business_inquiry"
    assert first["plugin_state"] == "WAIT_REQUEST"
    assert first["out"] == "CHAT"
    assert first["context"]["inquiry_meta"]["scenario_id"] == scenario
    assert first["context"]["verified_order"]["refund_status"] == "退款成功"
    assert "很抱歉" in first["reply"]
    assert "还有需要处理的诉求" in first["reply"]
    assert "{" not in first["reply"]
    second = engine.run(followup(first, "请继续处理"))
    assert second["out"] == output
    assert second["action"] == "none"
    assert second["active_flow_id"] is None
    assert second["flows"][-1]["status"] == "COMPLETED"
    assert second["flows"][-1]["completed_actions"] == []
    assert len(client.calls) == 3
    assert client.responses == []


def test_refund_progress_reads_refund_channel_without_inventing_from_payment():
    result = query_order({"intent": "refund_progress", "slots": {"order_no": "ORD202405020002"}})
    assert "退款成功" in result["inquiry_reply"]
    assert "退款去向待核实" in result["inquiry_reply"]
    assert "退款渠道为支付宝" not in result["inquiry_reply"]
    assert "已为您提交" not in result["inquiry_reply"]


def test_scan_does_not_invent_channel_or_conclude_wrong_hotline():
    result = query_order({"intent": "scan_confusion", "slots": {"order_no": "ORD202405010001"}})
    assert "订购渠道待核实" in result["inquiry_reply"]
    assert "暂未提供扫码与订购的关联记录" in result["inquiry_reply"]
    assert "错打" not in result["inquiry_reply"]


@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_missing_information_recovers_correct_scenario(scenario):
    engine, _ = engine_for(decision(scenario), decision("provide_info", scenario_id=scenario,
                                                           order_no="ORD202405030003"))
    first = engine.run({"message": "核查订单", "history": []})
    assert first["plugin_state"] == "ASK_ORDER_INFO"
    assert not first["context"]
    second = engine.run(followup(first, "订单号ORD202405030003"))
    assert second["out"] == "CHAT"
    assert second["context"]["inquiry_meta"]["scenario_id"] == scenario
    assert second["context"]["verified_order"]["refund_status"] == "退款失败"


def test_multiple_orders_require_selection_instead_of_latest():
    engine, _ = engine_for(decision("charge_dispute"), decision("provide_info",
                            scenario_id="charge_dispute", order_no="ORD202405030003"))
    first = engine.run({"message": "为什么扣钱", "call_info": {"caller": "13800138000"}, "history": []})
    assert first["plugin_state"] == "SELECT_ORDER"
    assert "99.0元" in first["reply"] and "19.9元" in first["reply"]
    assert "verified_order" not in first["context"]
    second = engine.run(followup(first, "查ORD202405030003"))
    assert second["plugin_state"] == "WAIT_REQUEST"
    assert second["context"]["verified_order"]["order_no"] == "ORD202405030003"


def test_amount_filters_candidates_and_preserves_failed_payment_status():
    result = query_order({"intent": "charge_dispute", "slots": {"amount": 19.9},
                          "call_info": {"caller": "13800138000"}})
    assert result["verified_order"]["order_no"] == "ORD202405040008"
    unpaid = query_order({"intent": "charge_dispute", "slots": {"order_no": "ORD202405040004"}})
    assert unpaid["verified_order"]["pay_status"] == "支付失败"


def test_query_exception_hands_off_instead_of_asking_for_another_phone(monkeypatch):
    def unavailable(**kwargs):
        raise RuntimeError("API down")
    monkeypatch.setattr(order_inquiry, "find_orders", unavailable)
    engine, _ = engine_for(decision("refund_progress", order_no="ORD202405020002"))
    result = engine.run({"message": "退款没到账", "history": []})
    assert result["out"] == "HUMAN"
    assert result["action_result"]["error"] == "query_unavailable"
    assert "查无" not in result["reply"]
    assert result["active_flow_id"] is None


def test_three_failed_lookups_end_with_handoff():
    engine, _ = engine_for(decision("charge_dispute", order_no="NO_SUCH_ORDER"),
        decision("provide_info", scenario_id="charge_dispute", order_no="BAD2"),
        decision("provide_info", scenario_id="charge_dispute", order_no="BAD3"))
    result = engine.run({"message": "查NO_SUCH_ORDER", "history": []})
    assert result["plugin_state"] == "ASK_ORDER_INFO"
    result = engine.run(followup(result, "查BAD2"))
    assert result["plugin_state"] == "RETRY_ORDER_INFO"
    result = engine.run(followup(result, "查BAD3"))
    assert result["out"] == "HUMAN"
    assert result["active_flow_id"] is None


def test_explicit_human_never_requires_query(monkeypatch):
    def must_not_query(**kwargs):
        raise AssertionError("人工诉求不应执行查询")
    monkeypatch.setattr(order_inquiry, "find_orders", must_not_query)
    engine, _ = engine_for(decision("human"))
    result = engine.run({"message": "转人工", "history": []})
    assert result["out"] == "HUMAN"
    assert result["action"] == "none"


def test_ambiguous_reply_is_not_end_and_repeated_ambiguity_hands_off():
    engine, _ = engine_for(decision("charge_dispute", order_no="ORD202405010001"),
                          decision("unclear"), decision("unclear"))
    first = engine.run({"message": "核查订单", "history": []})
    second = engine.run(followup(first, "知道了"))
    assert second["out"] == "CHAT"
    assert second["plugin_state"] == "WAIT_REQUEST"
    third = engine.run(followup(second, "嗯"))
    assert third["out"] == "HUMAN"


def test_waiting_followup_does_not_switch_to_refund_or_requery(monkeypatch):
    engine, _ = engine_for(decision("charge_dispute", order_no="ORD202405010001"),
                          decision("needs_handling", order_no="ORD202405020002"))
    first = engine.run({"message": "为什么扣费", "history": []})
    def must_not_query(**kwargs):
        raise AssertionError("后续诉求不应重复查询")
    monkeypatch.setattr(order_inquiry, "find_orders", must_not_query)
    second = engine.run(followup(first, "给我退款，再查这笔"))
    assert second["business"] == "business_inquiry"
    assert second["out"] == "HUMAN"
    assert len(second["flows"]) == 1


def test_scene_configuration_change_is_isolated(tmp_path):
    shutil.copytree(SCENARIO_ROOT, tmp_path / "scenarios")
    root = tmp_path / "scenarios"
    before = {name: load_scenario(name, root).explain({})[0] for name in SCENARIO_IDS}
    path = root / "charge_dispute" / "replies.yaml"
    replies = yaml.safe_load(path.read_text())
    replies["apology"] = "扣费场景专属安抚。"
    path.write_text(yaml.safe_dump(replies, allow_unicode=True))
    assert load_scenario("charge_dispute", root).explain({})[0] != before["charge_dispute"]
    for name in ("refund_progress", "scan_confusion"):
        assert load_scenario(name, root).explain({})[0] == before[name]
    replies["summary"] = "{out}"
    path.write_text(yaml.safe_dump(replies))
    with pytest.raises(ValueError, match="不支持的话术变量"):
        load_scenario("charge_dispute", root)


def test_changing_order_replaces_all_scene_facts_and_uses_new_phone():
    old = query_order({"intent": "refund_progress", "slots": {"order_no": "ORD202405020002"}})
    result = query_order({"intent": "provide_info", "context": old,
                          "slots": {"scenario_id": "scan_confusion", "backup_phone": "13800138000", "amount": 19.9}})
    assert result["verified_order"]["order_no"] == "ORD202405040008"
    assert result["scenario_data"]["scenario_id"] == "scan_confusion"
    assert "refund_time" not in result["scenario_data"]["facts"]
    assert "退款成功" not in result["inquiry_reply"]
    # 新查询结果不修改演示数据库。
    assert order_lookup._ORDERS["ORD202405020002"]["refundStatus"] == "退款成功"


def test_business_fields_are_used_without_overwriting_engine_controls(monkeypatch):
    raw = {"order_no": "REAL_ORDER", "productName": "生活包", "payAmount": 49.9,
           "subscriptionTime": "2026-09-20", "subscriptionChannel": "业务确认渠道",
           "refundStatus": "退款处理中", "refundChannel": "微信余额",
           "refundRequestTime": "2026-09-24", "out": "REFUND"}
    monkeypatch.setattr(order_inquiry, "find_orders", lambda **kwargs: [raw])
    result = query_order({"intent": "refund_progress", "slots": {"order_no": "REAL_ORDER"}})
    assert "业务确认渠道" in result["inquiry_reply"]
    assert "微信余额" in result["inquiry_reply"]
    assert result["verified_order"]["refund_request_time"] == "2026-09-24"
    assert "out" not in result and "out" not in result["verified_order"]


def test_provider_value_error_is_an_interface_failure_not_bad_user_information(monkeypatch):
    def invalid_response(**kwargs):
        raise ValueError("业务接口返回结构错误")
    monkeypatch.setattr(order_inquiry, "find_orders", invalid_response)
    result = query_order({"intent": "charge_dispute", "slots": {"order_no": "ORD1"}})
    assert result == {"ok": False, "error": "query_unavailable"}


def test_api_handoff_clears_active_session_and_returns_no_action_receipt(monkeypatch):
    monkeypatch.setattr(intent_llm, "client", Client(
        {"task": "BUSINESS_QA", "confidence": 0.99},
        decision("refund_progress", order_no="ORD202405020002"), decision("needs_handling")))
    session_store.clear()
    with TestClient(app) as client:
        first = client.post("/api/chat", json={"sessionId": "inquiry-api", "currentUserText": "退款到哪了"})
        assert first.status_code == 200
        assert first.json()["out"] == "CHAT"
        second = client.post("/api/chat", json={"sessionId": "inquiry-api", "currentUserText": "继续帮我处理"})
        assert second.status_code == 200
        assert second.json()["out"] == "HUMAN"
        assert "eta" not in second.json()["data"]
    stored = session_store.get("inquiry-api")
    assert stored.business is None and stored.context == {}
    assert stored.active_flow_id is None
    assert stored.flows[-1]["status"] == "COMPLETED"
