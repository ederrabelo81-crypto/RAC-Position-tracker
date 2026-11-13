"""Lógica pura de nubimetrics/discover.py (sem rede)."""
import datetime as dt

from nubimetrics.discover import (
    NEVER_CALL,
    _harvest,
    first_record,
    guess_sources,
    plan_calls,
    render_mapping,
    unwrap_schema,
)

TODAY = dt.date(2026, 11, 13)


def _wrapped(name, props, required=None):
    return {
        "name": name,
        "description": "",
        "inputSchema": {
            "type": "object",
            "properties": {"inp": {"$ref": "#/$defs/In"}},
            "required": ["inp"],
            "$defs": {"In": {"type": "object", "properties": props, "required": required or []}},
        },
    }


def test_unwrap_inp_ref():
    tool = _wrapped("x", {"date": {"type": "string"}}, ["date"])
    inner, wrapped = unwrap_schema(tool)
    assert wrapped and inner["required"] == ["date"]


def test_rankings_expand_topics_and_wrap():
    tool = _wrapped(
        "meli_market_rankings",
        {"date": {"type": "string"}, "category_id": {"type": "string"},
         "topic": {"type": "string", "enum": ["items", "brands"]}},
        ["date", "topic", "category_id"],
    )
    calls = plan_calls(tool, {}, TODAY)
    assert [c[0] for c in calls] == ["items", "brands"]
    assert calls[1][1] == {"inp": {"date": "2026-11-01", "category_id": "MLB1646", "topic": "brands"}}
    assert all(not c[2] for c in calls)


def test_forecast_uses_last_closed_month():
    tool = _wrapped("nubi_category_forecast", {"date": {"type": "string"}, "category_id": {"type": "string"}})
    assert plan_calls(tool, {}, TODAY)[0][1]["inp"]["date"] == "2026-10-01"


def test_market_size_13_months():
    tool = _wrapped("nubi_category_market_size", {"date_from": {}, "date_to": {}, "category_id": {}})
    args = plan_calls(tool, {}, TODAY)[0][1]["inp"]
    assert (args["date_from"], args["date_to"]) == ("2025-11-01", "2026-11-01")


def test_chained_dependency_missing_then_filled():
    tool = _wrapped("nubi_competition_seller_metrics", {"seller_id_competitor": {"type": "string"}},
                    ["seller_id_competitor"])
    assert plan_calls(tool, {}, TODAY)[0][2] == ["seller_id_competitor"]
    ctx = {}
    _harvest("nubi_competition_seller_group_members",
             {"members": [{"member_code": "self", "is_self": True}, {"member_code": "abc"}]}, ctx)
    assert plan_calls(tool, ctx, TODAY)[0][1]["inp"]["seller_id_competitor"] == "abc"


def test_feedback_never_called():
    assert "nubi_user_feedback" in NEVER_CALL


def test_first_record_and_sources():
    assert first_record({"data": [{"a": 1}]}) == {"a": 1}
    assert "H" in guess_sources({"name": "nubi_category_forecast", "description": ""})


def test_render_marks_unavailable():
    md = render_mapping([], {}, "13/11/2026")
    assert "indisponível via MCP" in md
