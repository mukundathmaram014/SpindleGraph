from spindlegraph import graph


def spec(i, number, files, status="draft", depends_on=None, actual=None):
    return {
        "id": i, "number": number, "status": status,
        "files_planned": [{"path": p} for p in files],
        "files_actual": [{"path": p} for p in (actual or [])],
        "depends_on": depends_on or [],
    }


# the worked example from docs/SPEC.md / the composer mockup
S7 = spec(1, 7, ["src/api/middleware.py", "src/config.py", "tests/test_middleware.py"])
S9 = spec(2, 9, ["src/auth_views.py"])
S12 = spec(3, 12, ["src/config.py", "src/settings/loader.py", "src/settings/schema.py"])
S14 = spec(4, 14, ["frontend/theme.ts", "frontend/Header.tsx"])
S15 = spec(5, 15, ["src/api/middleware.py", "src/audit/logger.py"])
ALL = [S7, S9, S12, S14, S15]


def test_edges_overlap_coefficient():
    edges = graph.compute_edges(ALL)
    keyed = {(e["spec_a"], e["spec_b"]): e for e in edges}
    assert set(keyed) == {(1, 3), (1, 5)}
    e = keyed[(1, 3)]
    assert e["shared_files"] == ["src/config.py"]
    assert e["weight"] == 1 / 3  # 1 shared / min(3, 3)


def test_containment_scores_maximal():
    a = spec(1, 1, ["x.py", "y.py"])
    b = spec(2, 2, ["x.py", "y.py"] + [f"f{i}.py" for i in range(38)])
    (e,) = graph.compute_edges([a, b])
    assert e["weight"] == 1.0  # small spec fully contained in big one


def test_built_spec_uses_actual_files():
    built = spec(1, 1, ["planned.py"], status="built", actual=["real.py"])
    other = spec(2, 2, ["real.py"])
    (e,) = graph.compute_edges([built, other])
    assert e["shared_files"] == ["real.py"]


def test_archived_excluded():
    a = spec(1, 1, ["x.py"], status="archived")
    b = spec(2, 2, ["x.py"])
    assert graph.compute_edges([a, b]) == []


def test_check_selection_flags_conflicts():
    edges = graph.compute_edges(ALL)
    res = graph.check_selection(ALL, edges, [1, 3, 5])
    assert not res["safe"]
    assert {(c["spec_a"], c["spec_b"]) for c in res["conflicts"]} == {(1, 3), (1, 5)}

    res = graph.check_selection(ALL, edges, [2, 3, 4])
    assert res["safe"]
    assert res["waves"] == [[2, 3, 4]]


def test_wave_suggestion_two_waves():
    edges = graph.compute_edges(ALL)
    res = graph.check_selection(ALL, edges, [1, 2, 3, 4, 5])
    waves = res["waves"]
    assert len(waves) == 2
    flat = [i for w in waves for i in w]
    assert sorted(flat) == [1, 2, 3, 4, 5]
    # 7 conflicts with 12 and 15 (which don't conflict with each other), so
    # everything else parallelizes and 7 builds alone
    assert waves == [[2, 3, 4, 5], [1]]
    # no wave contains a conflicting pair
    conflict_pairs = {(1, 3), (1, 5)}
    for w in waves:
        for a in w:
            for b in w:
                assert (a, b) not in conflict_pairs and (b, a) not in conflict_pairs


def test_unknown_footprint_is_conservative():
    mystery = spec(6, 20, [])
    specs = ALL + [mystery]
    edges = graph.compute_edges(specs)
    res = graph.check_selection(specs, edges, [2, 4, 6])
    assert not res["safe"]
    assert res["unknown_footprint"] == [6]
    # mystery spec never shares a wave
    for w in res["waves"]:
        if 6 in w:
            assert w == [6]


def test_depends_on_ordering():
    a = spec(1, 1, ["a.py"])
    b = spec(2, 2, ["b.py"], depends_on=[1])
    res = graph.check_selection([a, b], [], [1, 2])
    assert res["waves"] == [[1], [2]]


def test_dependency_cycle_breaks():
    a = spec(1, 1, ["a.py"], depends_on=[2])
    b = spec(2, 2, ["b.py"], depends_on=[1])
    res = graph.check_selection([a, b], [], [1, 2])
    flat = [i for w in res["waves"] for i in w]
    assert sorted(flat) == [1, 2]
