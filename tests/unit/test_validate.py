from typing import Any

from quantra_mcp.schema.validate import validate_request


def test_valid_calendar_request() -> None:
    body = {"calendar": "TARGET", "start_date": "2024-04-29", "end_date": "2024-06-21"}
    assert validate_request("/calendar-holidays", body) == []


def test_unknown_field_and_bad_enum_reported_with_paths() -> None:
    body = {"calendar": "Mars", "start_date": "2024-04-29", "bogus": 1}
    problems = [(p.path, p.message) for p in validate_request("/calendar-holidays", body)]
    assert any(path == "/calendar" and "'Mars' is not one of" in msg for path, msg in problems)
    assert any(path == "/" and "bogus" in msg for path, msg in problems)
    assert any(path == "/" and "end_date" in msg for path, msg in problems)


def test_union_with_no_matching_branch_reports_the_union_once(ois_example: dict[str, Any]) -> None:
    ois_example["pricing"]["rates"]["curves"][0]["points"][0]["point"] = {"nonsense": 1}
    problems = validate_request("/price-ois-swap", ois_example)
    assert len(problems) == 1
    assert problems[0].path == "/pricing/rates/curves/0/points/0/point"
    assert "not valid under any of the given schemas" in problems[0].message


def test_union_branch_error_is_the_deepest(ois_example: dict[str, Any]) -> None:
    ois_example["pricing"]["rates"]["curves"][0]["points"][0]["point"]["rate"] = "abc"
    problems = validate_request("/price-ois-swap", ois_example)
    assert problems[0].path == "/pricing/rates/curves/0/points/0/point/rate"
    assert "not of type 'number'" in problems[0].message
    assert len(problems) == 1


def test_wrong_top_level_type() -> None:
    problems = validate_request("/price-ois-swap", {"pricing": [], "swaps": {}})
    paths = {p.path for p in problems}
    assert paths == {"/pricing", "/swaps"}
