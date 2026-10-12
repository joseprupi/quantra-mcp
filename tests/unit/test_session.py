import pytest

from quantra_mcp.errors import LocalValidationError
from quantra_mcp.session import (
    LOCAL_SESSION,
    SessionStore,
    SessionStores,
    bind_session,
    current_session_id,
    is_ref,
    resolve_refs,
)

CURVE = {"id": "c1", "reference_date": "2025-01-15", "points": []}
INDEX = {"id": "IX", "name": "ix"}


def test_put_get_delete_and_lru_eviction() -> None:
    s = SessionStore(max_items=2)
    item, evicted = s.put("a", "curve", CURVE)
    assert item.kind == "curve" and evicted is None and item.summary()["curve_id"] == "c1"
    s.put("b", "index", INDEX)
    s.get("a")  # touch -> b is now least recently used
    _, evicted = s.put("c", "market", {"curves": [CURVE], "indices": [INDEX]})
    assert evicted == "b" and [i.name for i in s.items()] == ["a", "c"]
    assert s.delete("a") is True and s.delete("a") is False and len(s) == 1
    with pytest.raises(LocalValidationError, match="no session item named 'zzz'"):
        s.get("zzz")


def test_put_validates_kind_and_shape() -> None:
    s = SessionStore()
    with pytest.raises(LocalValidationError, match="kind must be one of"):
        s.put("a", "curves", CURVE)
    with pytest.raises(LocalValidationError, match="does not look like"):
        s.put("a", "index", CURVE)
    with pytest.raises(LocalValidationError, match="does not look like"):
        s.put("a", "market", {"foo": 1})
    with pytest.raises(LocalValidationError, match="non-empty"):
        s.put(" ", "curve", CURVE)
    with pytest.raises(LocalValidationError, match="value must be"):
        s.put("a", "curve", [CURVE])


def test_build_curve_result_keeps_its_indices() -> None:
    s = SessionStore()
    s.put("sofr", "curve", {"ok": True, "curve": CURVE, "indices": [INDEX], "notes": []})
    assert s.get("sofr").value == CURVE and s.attached_indices("sofr") == [INDEX]


def test_resolve_refs_expands_everything_and_notes_it() -> None:
    s = SessionStore()
    s.put("sofr", "curve", {"curve": CURVE, "indices": [INDEX]})
    s.put("ix", "index", {"id": "IX2", "name": "ix2"})
    s.put(
        "mkt",
        "market",
        {"curves": [{"id": "c2", "points": []}], "indices": [{"id": "IX3", "name": "3"}]},
    )
    curves, indices, notes = resolve_refs(
        s,
        [
            {"session": "sofr"},
            {"session": "mkt"},
            {"curve": {"id": "c3", "points": []}, "indices": [INDEX]},
            {"id": "c4", "points": []},
        ],
        [{"session": "ix"}, {"session": "mkt"}, {"id": "IX4", "name": "4"}],
    )
    assert [c["id"] for c in curves] == ["c1", "c2", "c3", "c4"]
    assert [i["id"] for i in indices] == ["IX", "IX3", "IX", "IX2", "IX3", "IX4"]
    assert notes[0] == (
        "curves[0] <- session 'sofr' (curve c1, +1 attached index; stored market_data_source=None)"
    )
    assert any("market: 1 curves, 1 indices" in n for n in notes)
    assert is_ref({"session": "x"}) and not is_ref({"session": "x", "id": 1}) and not is_ref("x")


def test_resolve_refs_kind_mismatch_and_unknown() -> None:
    s = SessionStore()
    s.put("ix", "index", INDEX)
    s.put("c", "curve", CURVE)
    with pytest.raises(LocalValidationError, match="is an index, not a curve"):
        resolve_refs(s, [{"session": "ix"}], None)
    with pytest.raises(LocalValidationError, match="is a curve, not an index"):
        resolve_refs(s, [], [{"session": "c"}])
    with pytest.raises(LocalValidationError, match="no session item named"):
        resolve_refs(s, [{"session": "nope"}], None)
    with pytest.raises(LocalValidationError, match="expected a TermStructure"):
        resolve_refs(s, ["c"], None)


def test_total_bytes_bound_evicts_lru_and_refuses_oversize() -> None:
    small = {"id": "s", "reference_date": "2025-01-15", "points": []}
    size = len(__import__("json").dumps(small, separators=(",", ":")))
    s = SessionStore(max_items=10, max_total_bytes=size * 2 + 1)
    s.put("a", "curve", small)
    s.put("b", "curve", small)
    assert s.total_bytes == 2 * size and s.items()[0].size_bytes == size
    _, evicted = s.put("c", "curve", small)  # third pushes total over the byte cap
    assert evicted == "a" and [i.name for i in s.items()] == ["b", "c"]
    assert s.items()[0].summary()["size_bytes"] == size
    big = {"id": "big", "reference_date": "2025-01-15", "points": [{"x": "y" * 400}]}
    with pytest.raises(LocalValidationError, match="holds at most"):
        s.put("big", "curve", big)
    with pytest.raises(ValueError, match="max_total_bytes"):
        SessionStore(max_total_bytes=0)


# --- per-MCP-session registry (M5.5) ---------------------------------------


def test_stores_are_private_per_session_and_errors_list_only_own_names() -> None:
    reg = SessionStores(max_items=4, max_total_bytes=1 << 20, max_sessions=8)
    with bind_session("A"):
        reg.put("sofr", "curve", CURVE, "user_pasted")
        assert [i.name for i in reg.items()] == ["sofr"] and len(reg) == 1
    with bind_session("B"):
        assert reg.items() == [] and len(reg) == 0
        with pytest.raises(LocalValidationError) as exc:
            reg.get("sofr")
        assert "(none)" in exc.value.error and "sofr" not in exc.value.error.split("stored:")[1]
        reg.put("sofr", "curve", {**CURVE, "id": "B"}, "user_pasted")
        assert reg.get("sofr").value["id"] == "B"
        assert reg.delete("nope") is False
    with bind_session("A"):
        assert reg.get("sofr").value["id"] == "c1"  # B's put did not clobber A's
        assert resolve_refs(reg, [{"session": "sofr"}], None)[0][0]["id"] == "c1"
    assert reg.session_count == 2
    # no session bound (stdio, in-memory client) = the implicit local session
    assert current_session_id() is None
    reg.put("x", "index", INDEX)
    assert reg.for_session(None) is reg.for_session(LOCAL_SESSION)
    assert [i.name for i in reg.for_session(None).items()] == ["x"]
    assert reg.session_count == 3
    assert reg.drop_session("B") and not reg.drop_session("B") and reg.session_count == 2


def test_registry_evicts_whole_sessions_lru_and_holds_a_global_byte_budget() -> None:
    reg = SessionStores(max_items=8, max_total_bytes=1 << 20, max_sessions=2)
    for sid in ("A", "B"):
        with bind_session(sid):
            reg.put("c", "curve", CURVE, "user_pasted")
    with bind_session("A"):
        reg.get("c")  # A is now the most recently used
    with bind_session("C"):
        reg.put("c", "curve", CURVE, "user_pasted")  # third session: B (LRU) goes
    assert reg.session_count == 2
    assert reg.drop_session("B") is False  # gone; A (recently used) survived
    with bind_session("A"):
        assert len(reg) == 1
    with bind_session("B"):
        assert reg.items() == []  # re-created empty, which in turn evicts the LRU session
    assert reg.session_count == 2
    # the byte budget is global: filling one session drops the least-recently-used others
    big = {**CURVE, "points": [{"n": "x" * 300}]}
    reg2 = SessionStores(max_items=8, max_total_bytes=1000, max_sessions=8)
    with bind_session("A"):
        reg2.put("c", "curve", big, "user_pasted")
    with bind_session("B"):
        reg2.put("c", "curve", big, "user_pasted")
    with bind_session("C"):
        reg2.put("c", "curve", big, "user_pasted")
    assert reg2.total_bytes <= 1000 and reg2.session_count == 2
    with bind_session("A"):
        assert reg2.items() == []  # A was the oldest and was dropped
    with pytest.raises(ValueError):
        SessionStores(max_sessions=0)


def test_bind_session_restores_the_previous_binding() -> None:
    assert current_session_id() is None
    with bind_session("outer"):
        assert current_session_id() == "outer"
        with bind_session("inner"):
            assert current_session_id() == "inner"
        assert current_session_id() == "outer"
    assert current_session_id() is None
