from scripts import expense_f_aggregator as mod


class Resp:
    def __init__(self, code, payload):
        self.status_code = code
        self._payload = payload
    def json(self):
        return self._payload


def _run(monkeypatch, props):
    monkeypatch.setenv("NOTION_TOKEN", "t")
    monkeypatch.setenv("EXPENSES_DB_ID", "d")
    calls = {}
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: Resp(200, {"properties": props}))
    def _post(*a, **k):
        calls["json"] = k["json"]
        return Resp(200, {"results": [], "has_more": False})
    monkeypatch.setattr(mod.requests, "post", _post)
    out = mod.aggregate_daily_expense_f("2026-03-20")
    return out, calls["json"]


def test_date_prop_strategy(monkeypatch):
    out, payload = _run(monkeypatch, {"F": {"type": "checkbox"}, "Merchant": {"type": "rich_text"}, "Amount": {"type": "number"}, "Date": {"type": "date"}})
    assert out.debug_summary["filter_strategy"] == "expense_date_prop"
    assert payload["filter"]["and"][0]["checkbox"]["equals"] is True
    assert payload["filter"]["and"][1]["date"]["on_or_after"] == "2026-03-20"


def test_received_at_strategy(monkeypatch):
    out, payload = _run(monkeypatch, {"F": {"type": "checkbox"}, "Merchant": {"type": "rich_text"}, "Amount": {"type": "number"}, "Received At": {"type": "date"}})
    assert out.debug_summary["filter_strategy"] == "received_at_prop"
    assert "T" in payload["filter"]["and"][1]["date"]["on_or_after"]
    assert "+" in payload["filter"]["and"][1]["date"]["on_or_after"]


def test_created_time_strategy(monkeypatch):
    out, payload = _run(monkeypatch, {"F": {"type": "checkbox"}, "Merchant": {"type": "rich_text"}, "Amount": {"type": "number"}})
    assert out.debug_summary["filter_strategy"] == "created_time_fallback"
    assert payload["filter"]["and"][1]["timestamp"] == "created_time"
    assert "T" in payload["filter"]["and"][1]["created_time"]["on_or_after"]
