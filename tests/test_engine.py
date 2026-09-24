from pathlib import Path

import pytest

from app.engine.graph import reply
from app.engine.loader import PluginConfigError, PluginLoader, plugin_loader
from app.engine.outputs import ALLOWED_OUTPUTS


def test_all_plugins_are_loaded() -> None:
    assert set(plugin_loader.load_all(force=True)) == {
        "human",
        "refund",
        "unsubscribe",
        "refund_unsubscribe",
    }


def test_external_outputs_include_business_action_values() -> None:
    assert ALLOWED_OUTPUTS == {
        "CHAT",
        "REFUND",
        "UNSUBSCRIBE",
        "REFUND_UNSUBSCRIBE",
        "HUMAN",
        "END",
    }


def test_plugin_rejects_unknown_output(tmp_path: Path) -> None:
    source = Path("app/businesses/refund/plugin.yaml").read_text(encoding="utf-8")
    invalid_plugin = tmp_path / "refund" / "plugin.yaml"
    invalid_plugin.parent.mkdir()
    invalid_plugin.write_text(
        source.replace("out: REFUND", "out: OTHER", 1),
        encoding="utf-8",
    )

    with pytest.raises(PluginConfigError, match="out 必须是"):
        PluginLoader(tmp_path).load_all()


def test_refund_plugin_keeps_reserved_states_and_action_table() -> None:
    plugin = plugin_loader.get("refund")

    assert plugin.states == ("IDLE", "CONFIRM_REFUND", "ASK_ORDER_INFO", "ASK_OTHER", "END")
    assert plugin.terminal_states == {"END"}
    assert len(plugin.recovery_steps_for("unknown")) == 3
    assert len(plugin.recovery_steps_for("unsupported")) == 3
    transition = plugin.transition_for("CONFIRM_REFUND", "affirm")
    assert transition is not None
    assert transition.action == "submit_refund"
    assert transition.next_state == "ASK_OTHER"
    assert transition.out == "REFUND"

    initial_lookup = plugin.transition_for("IDLE", "refund_request")
    assert initial_lookup is not None
    lookup_error = initial_lookup.error_transition_for("order_not_found")
    assert lookup_error is not None
    assert lookup_error.next_state == "ASK_ORDER_INFO"

    end_transition = plugin.transition_for("ASK_OTHER", "end")
    assert end_transition is not None
    assert end_transition.next_state == "END"
    assert end_transition.out == "END"


def test_human_plugin_asks_reason_before_handoff() -> None:
    plugin = plugin_loader.get("human")

    assert plugin.states == (
        "IDLE",
        "ASK_REASON",
        "INSULT_WARNED",
        "AFTER_CANCEL",
        "CONFIRM_TRANSFER",
        "END",
    )
    assert plugin.terminal_states == {"END"}
    initial = plugin.transition_for("IDLE", "transfer_request")
    assert initial is not None
    assert initial.action == "record_reason"
    assert initial.next_state == "END"
    assert initial.out == "HUMAN"
    missing_reason = initial.error_transition_for("missing_reason")
    assert missing_reason is not None
    assert missing_reason.next_state == "ASK_REASON"
    assert missing_reason.out == "CHAT"

    with_reason = plugin.transition_for("ASK_REASON", "provide_info")
    assert with_reason is not None
    assert with_reason.action == "record_reason"
    assert with_reason.next_state == "END"
    assert with_reason.out == "HUMAN"


def test_unsubscribe_plugin_matches_refund_flow_shape() -> None:
    plugin = plugin_loader.get("unsubscribe")

    assert plugin.route_task == "UNSUBSCRIBE"
    assert plugin.states == (
        "IDLE",
        "CONFIRM_UNSUBSCRIBE",
        "ASK_ORDER_INFO",
        "ASK_OTHER",
        "END",
    )
    initial_lookup = plugin.transition_for("IDLE", "unsubscribe_request")
    assert initial_lookup is not None
    assert initial_lookup.action == "query_order"
    assert initial_lookup.next_state == "CONFIRM_UNSUBSCRIBE"

    confirmed = plugin.transition_for("CONFIRM_UNSUBSCRIBE", "affirm")
    assert confirmed is not None
    assert confirmed.action == "submit_unsubscribe"
    assert confirmed.next_state == "ASK_OTHER"
    assert confirmed.out == "UNSUBSCRIBE"


def test_refund_unsubscribe_plugin_combines_both_actions() -> None:
    plugin = plugin_loader.get("refund_unsubscribe")

    assert plugin.route_task == "REFUND_UNSUBSCRIBE"
    initial_lookup = plugin.transition_for("IDLE", "refund_unsubscribe_request")
    assert initial_lookup is not None
    assert initial_lookup.action == "query_order"
    assert initial_lookup.next_state == "CONFIRM_REFUND_UNSUBSCRIBE"

    confirmed = plugin.transition_for("CONFIRM_REFUND_UNSUBSCRIBE", "affirm")
    assert confirmed is not None
    assert confirmed.action == "submit_refund_unsubscribe"
    assert confirmed.next_state == "ASK_OTHER"
    assert confirmed.out == "REFUND_UNSUBSCRIBE"


def test_recovery_step_count_is_derived_from_config(tmp_path: Path) -> None:
    source = Path("app/businesses/refund/plugin.yaml").read_text(encoding="utf-8")
    three_steps = """  unknown:
    - reply: unknown_first
      out: CHAT
    - reply: unknown_second
      out: CHAT
    - reply: unknown_handoff
      out: HUMAN
"""
    two_steps = """  unknown:
    - reply: unknown_first
      out: CHAT
    - reply: unknown_handoff
      out: HUMAN
"""
    assert three_steps in source
    plugin_path = tmp_path / "refund" / "plugin.yaml"
    plugin_path.parent.mkdir()
    plugin_path.write_text(source.replace(three_steps, two_steps, 1), encoding="utf-8")

    plugin = PluginLoader(tmp_path).load_all()["refund"]

    assert [step.reply for step in plugin.recovery_steps_for("unknown")] == [
        "unknown_first",
        "unknown_handoff",
    ]


def test_known_action_error_uses_dsl_before_global_fallback() -> None:
    state = {
        "business": "refund",
        "plugin_state": "ASK_ORDER_INFO",
        "intent": "provide_info",
        "context": {},
        "slots": {"order_no": "NOT_FOUND"},
        "action_result": {"ok": False, "error": "order_not_found"},
    }

    configured = reply(state)
    fallback = reply(
        {
            **state,
            "action_result": {"ok": False, "error": "upstream_timeout"},
        }
    )

    assert configured["reply"].startswith("根据您提供的信息仍未查到订单")
    assert configured["plugin_state"] == "ASK_ORDER_INFO"
    assert fallback["reply"] == "抱歉，我没太听明白，您能再说一遍吗？"
