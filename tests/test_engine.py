from app.engine.loader import plugin_loader


def test_all_plugins_are_loaded() -> None:
    assert set(plugin_loader.load_all(force=True)) == {"refund"}


def test_refund_plugin_keeps_reserved_states_and_action_table() -> None:
    plugin = plugin_loader.get("refund")

    assert plugin.states == ("IDLE", "CONFIRM_REFUND", "ASK_ORDER_INFO", "ASK_OTHER", "END")
    assert plugin.terminal_states == {"END"}
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
