from app.engine.graph import run_graph
from app.engine.loader import plugin_loader


def test_all_plugins_are_loaded() -> None:
    assert set(plugin_loader.load_all(force=True)) == {"refund", "query", "suspend", "chat"}


def test_balance_query_runs_through_single_graph() -> None:
    result = run_graph(
        {
            "session_id": "engine-test",
            "message": "查询一下余额",
            "history": [],
            "active_business": None,
            "context": {},
        }
    )
    assert result["business"] == "query"
    assert result["intent"] == "query_balance"
    assert result["action_result"]["ok"] is True
    assert "余额" in result["reply"]
