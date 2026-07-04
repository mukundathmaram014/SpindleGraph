"""Graph engine: conflict edges, safety checks, wave suggestion.

Pure functions over spec records (plus thin DB sync helpers), per
docs/SPEC.md §6. Overlap edges are *conflict* edges (undirected, "don't
parallelize"), not ordering constraints.
"""
from __future__ import annotations

import json
import sqlite3
from itertools import combinations


def effective_files(spec: dict) -> set[str]:
    """files_actual once built, else files_planned."""
    key = "files_actual" if spec.get("status") == "built" and spec.get("files_actual") \
        else "files_planned"
    return {f["path"] for f in spec.get(key) or []}


def compute_edges(specs: list[dict]) -> list[dict]:
    """All pairwise conflict edges among non-archived specs.

    weight = |A ∩ B| / min(|A|, |B|)  (overlap coefficient).
    """
    out = []
    active = [s for s in specs if s.get("status") != "archived"]
    for a, b in combinations(active, 2):
        fa, fb = effective_files(a), effective_files(b)
        shared = sorted(fa & fb)
        if not shared:
            continue
        lo, hi = sorted((a, b), key=lambda s: s["id"])
        out.append({
            "spec_a": lo["id"],
            "spec_b": hi["id"],
            "shared_files": shared,
            "weight": len(shared) / min(len(fa), len(fb)),
        })
    return out


def recompute(conn: sqlite3.Connection, project_id: int) -> None:
    """Re-derive edges for a project. Overridden edges keep their pinned
    weight (shared files still refresh); everything else is replaced."""
    from . import db as dbm

    specs = [dbm.row_to_dict(r, dbm.SPEC_JSON) for r in conn.execute(
        "SELECT * FROM spec WHERE project_id=?", (project_id,))]
    fresh = {(e["spec_a"], e["spec_b"]): e for e in compute_edges(specs)}
    pinned = {
        (r["spec_a"], r["spec_b"]): r["weight"]
        for r in conn.execute(
            "SELECT spec_a, spec_b, weight FROM edge WHERE project_id=? AND overridden=1",
            (project_id,))
    }
    conn.execute("DELETE FROM edge WHERE project_id=?", (project_id,))
    for key, e in fresh.items():
        overridden = key in pinned
        conn.execute(
            "INSERT INTO edge (project_id, spec_a, spec_b, shared_files_json, weight,"
            " overridden) VALUES (?,?,?,?,?,?)",
            (project_id, e["spec_a"], e["spec_b"], json.dumps(e["shared_files"]),
             pinned.get(key, e["weight"]), int(overridden)),
        )
    conn.commit()


def _conflict_map(specs: list[dict], edges: list[dict],
                  selection: set[int]) -> dict[int, set[int]]:
    """Adjacency of conflicts within the selection. Specs with an unknown
    footprint (no effective files) conservatively conflict with everything."""
    by_id = {s["id"]: s for s in specs}
    adj: dict[int, set[int]] = {i: set() for i in selection}
    for e in edges:
        a, b = e["spec_a"], e["spec_b"]
        if a in selection and b in selection:
            adj[a].add(b)
            adj[b].add(a)
    unknown = {i for i in selection if not effective_files(by_id[i])}
    for u in unknown:
        for other in selection - {u}:
            adj[u].add(other)
            adj[other].add(u)
    return adj


def check_selection(specs: list[dict], edges: list[dict],
                    selection_ids: list[int]) -> dict:
    """Safety check + wave suggestion for a proposed parallel set."""
    selection = set(selection_ids)
    by_id = {s["id"]: s for s in specs}
    adj = _conflict_map(specs, edges, selection)
    conflicts = [
        {"spec_a": e["spec_a"], "spec_b": e["spec_b"],
         "shared_files": e["shared_files"], "weight": e["weight"]}
        for e in edges
        if e["spec_a"] in selection and e["spec_b"] in selection
    ]
    unknown = sorted(i for i in selection if not effective_files(by_id[i]))
    safe = not conflicts and not (unknown and len(selection) > 1)
    return {
        "safe": safe,
        "conflicts": conflicts,
        "unknown_footprint": unknown,
        "waves": suggest_waves(specs, edges, selection_ids),
    }


def suggest_waves(specs: list[dict], edges: list[dict],
                  selection_ids: list[int]) -> list[list[int]]:
    """Greedy repeated maximal-independent-set over the conflict subgraph,
    respecting depends_on. Heuristic, not optimal — fine at < 50 specs."""
    selection = set(selection_ids)
    by_id = {s["id"]: s for s in specs}
    adj = _conflict_map(specs, edges, selection)
    weight_of: dict[tuple[int, int], float] = {}
    for e in edges:
        weight_of[(e["spec_a"], e["spec_b"])] = e["weight"]
        weight_of[(e["spec_b"], e["spec_a"])] = e["weight"]

    deps = {
        i: {d for d in (by_id[i].get("depends_on") or []) if d in selection}
        for i in selection
    }

    waves: list[list[int]] = []
    done: set[int] = set()
    remaining = set(selection)
    while remaining:
        eligible = [i for i in remaining if deps[i] <= done]
        if not eligible:  # dependency cycle — break it, sequential by number
            eligible = sorted(remaining, key=lambda i: by_id[i]["number"])[:1]
        # low conflict degree first, then low total conflict weight, then number
        def key(i: int):
            nbrs = adj[i] & set(eligible)
            return (len(nbrs),
                    sum(weight_of.get((i, n), 1.0) for n in nbrs),
                    by_id[i]["number"])
        wave: list[int] = []
        for i in sorted(eligible, key=key):
            if not (adj[i] & set(wave)):
                wave.append(i)
        wave.sort(key=lambda i: by_id[i]["number"])
        waves.append(wave)
        done |= set(wave)
        remaining -= set(wave)
    return waves
