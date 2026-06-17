"""Unit tests for the Part II dataset-engineering tools.

Each tool ships its own planted-defect sample; these tests assert the tools
actually catch what was planted, so the snippets stay honest as the code
moves. Run from the snippets/ directory:

    python -m pytest tests/ -q

Stdlib + pytest only. The modules live in sibling folders, so we load them
by path with importlib rather than relying on a package layout.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SNIPPETS = Path(__file__).resolve().parent.parent


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, SNIPPETS / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: dataclasses resolves annotations via
    # sys.modules[cls.__module__], which is None for an unregistered module.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


audit = _load("dataset-quality/audit_corpus.py", "audit_corpus")
adher = _load("glossary-eval/glossary_adherence.py", "glossary_adherence")
ab = _load("experiments/strategy_ab.py", "strategy_ab")
lex = _load("knowledge-graph/build_lexicon.py", "build_lexicon")
bias = _load("responsible-ai/coverage_bias.py", "coverage_bias")
nlu = _load("nlu/build_intent_dataset.py", "build_intent_dataset")


# --------------------------------------------------------------------------
# audit_corpus
# --------------------------------------------------------------------------


def test_base_lang_normalizes_forms():
    assert audit.base_lang("ko-KR") == "ko"
    assert audit.base_lang("ko_KR") == "ko"
    assert audit.base_lang("EN") == "en"


def test_mojibake_detector_catches_latin1_korean():
    moji = "보통".encode("utf-8").decode("latin-1")   # -> ë³´í...
    assert audit.has_mojibake(moji)
    assert not audit.has_mojibake("보통")
    assert not audit.has_mojibake("Normal text")


def test_not_nfc_detects_decomposed_hangul():
    import unicodedata
    assert audit.is_not_nfc(unicodedata.normalize("NFD", "설정"))
    assert not audit.is_not_nfc("설정")


def test_placeholder_extraction():
    ph = audit.placeholders("Hello {name}, you have %d items <br>")
    assert ph["{name}"] == 1
    assert ph["%d"] == 1
    assert ph["<br>"] == 1


def test_audit_flags_planted_defects():
    headers = ["term", "ko", "ko-KR", "en-US"]
    rows = [
        {"term": "a", "ko": "공격", "ko-KR": "공격", "en-US": "Attack"},
        {"term": "a", "ko": "방어", "ko-KR": "방어", "en-US": "Defense"},  # dup key
    ]
    rep = audit.audit(rows, headers, "utf-8", "x.csv", key_col="term", base="ko")
    # ko vs ko-KR is a lang-code conflict
    assert "ko" in rep.lang_code_conflicts
    assert rep.metrics.get("duplicate_key") == 1


# --------------------------------------------------------------------------
# glossary_adherence
# --------------------------------------------------------------------------


def test_contains_term_word_boundary_vs_cjk():
    # Latin: must respect word boundaries
    assert not adher.contains_term("He Said yes", "AI", case_sensitive=False)
    assert adher.contains_term("The AI Director", "AI", case_sensitive=False)
    # CJK: substring is correct
    assert adher.contains_term("戦利品を集める", "戦利品", case_sensitive=False)


def test_adherence_counts_applied_and_missed():
    glossary = [{"term": "loot", "ja": "戦利品"}]
    segments = [
        {"id": "s1", "source": "collect loot", "ja": "戦利品を集める"},   # applied
        {"id": "s2", "source": "more loot", "ja": "もっとアイテム"},        # missed
    ]
    rep = adher.evaluate(glossary, segments, case_sensitive=False)
    ja = rep.scores["ja"]
    assert ja["applicable"] == 2 and ja["applied"] == 1
    assert len(rep.misses) == 1 and rep.misses[0].segment_id == "s2"


# --------------------------------------------------------------------------
# strategy_ab
# --------------------------------------------------------------------------


def test_substring_overfires_word_boundary_does_not():
    assert ab.strat_substring("He Said yes", "AI")          # false positive
    assert not ab.strat_word_boundary("He Said yes", "AI")  # correct
    assert ab.strat_word_boundary("The AI wins", "AI")


def test_experiment_scores_precision_recall():
    gold = [
        {"text": "He Said yes", "term": "AI", "gold": "0"},
        {"text": "The AI Director", "term": "AI", "gold": "1"},
    ]
    res = ab.run_experiment(gold, {
        "substring": ab.strat_substring,
        "word_boundary": ab.strat_word_boundary,
    })
    assert res["word_boundary"].precision == 1.0
    assert res["substring"].fp == 1   # fired inside 'Said'


# --- ch.16: statistical significance --------------------------------------


def test_matchers_fold_case_and_width():
    # casefold (ß) and NFKC width (full-width ＡＩ) must match; lower()/raw would fail
    assert ab.strat_substring("Die STRASSE heute", "straße")        # ß casefold
    assert ab.strat_substring("press ＡＩ now", "AI")                # full-width NFKC
    assert adher.contains_term("STRASSE", "straße", case_sensitive=False)
    assert adher.contains_term("ＡＩ ディレクター", "AI", case_sensitive=False)


def test_mcnemar_flags_a_real_difference():
    a = [(True, True)] * 40                                   # A always correct
    b = [(True, True)] * 20 + [(False, True)] * 20            # B wrong on 20
    b_only, c_only, p = ab.mcnemar_exact(a, b)
    assert (b_only, c_only) == (20, 0)
    assert p < 0.001                                          # clearly significant


def test_mcnemar_not_significant_with_one_discordant():
    a = [(True, True)] * 11
    b = [(True, True)] * 10 + [(False, True)]                 # 1 vs 0 discordant
    _, _, p = ab.mcnemar_exact(a, b)
    assert p == pytest.approx(1.0)                            # can't conclude


def test_bootstrap_ci_brackets_the_point_estimate():
    pairs = [(True, True)] * 7 + [(True, False)] * 3          # F1 ~ 0.82
    lo, hi = ab.bootstrap_f1_ci(pairs, n_boot=1000, seed=1)
    point = ab.f1_of(pairs)
    assert 0.0 <= lo <= point <= hi <= 1.0


# --------------------------------------------------------------------------
# build_lexicon
# --------------------------------------------------------------------------


def test_lexicon_cross_lingual_lookup_and_broader():
    rows = [
        {"id": "item", "en": "item", "ja": "アイテム", "domain": "", "synonyms": "", "broader": ""},
        {"id": "loot", "en": "loot", "ja": "戦利品", "domain": "Combat",
         "synonyms": "plunder", "broader": "item"},
    ]
    g, warnings = lex.build(rows)
    assert not warnings                       # broader 'item' resolves
    c = g.lookup("戦利品")                      # reverse index, Japanese surface
    assert c is not None and c.id == "loot"
    assert c.labels["en"] == "loot"
    assert g.lookup("plunder").id == "loot"   # synonym resolves too


def test_lexicon_warns_on_unresolved_broader():
    rows = [{"id": "loot", "en": "loot", "broader": "ghost", "synonyms": ""}]
    _, warnings = lex.build(rows)
    assert any("ghost" in w for w in warnings)


# --------------------------------------------------------------------------
# coverage_bias
# --------------------------------------------------------------------------


def test_bias_flags_underserved_language():
    rows = [
        {"key": "a", "en": "Attack", "th": "โจมตี"},
        {"key": "b", "en": "Defense", "th": ""},
        {"key": "c", "en": "Loot", "th": ""},
    ]
    rep = bias.analyze(rows, base="en", short_ratio=0.3)
    assert "th" in rep.underserved
    assert rep.coverage_gap == pytest.approx(2 / 3, abs=1e-3)


def test_bias_detects_copy_through():
    rows = [{"key": "a", "en": "Respawn", "de": "Respawn"}]
    rep = bias.analyze(rows, base="en", short_ratio=0.3)
    assert rep.stats["de"]["untranslated"] == 1


# --------------------------------------------------------------------------
# build_intent_dataset
# --------------------------------------------------------------------------


def test_fill_computes_exact_spans():
    ex = nlu.fill("buy {count} {item}", {"count": "two", "item": "sword"})
    assert ex.text == "buy two sword"
    spans = {s["name"]: (s["start"], s["end"]) for s in ex.slots}
    assert ex.text[slice(*spans["count"])] == "two"
    assert ex.text[slice(*spans["item"])] == "sword"


def test_fill_computes_cjk_spans():
    ex = nlu.fill("{item} 구매", {"item": "검"})
    span = ex.slots[0]
    assert ex.text[span["start"]:span["end"]] == "검"


def test_generate_skips_intents_missing_a_language_slot():
    spec = {
        "slots": {"item": {"en": ["sword"]}},   # no 'ko' values
        "intents": {"buy": {"en": ["buy {item}"], "ko": ["{item} 구매"]}},
    }
    import random
    out = nlu.generate(spec, max_per_template=5, rng=random.Random(0))
    langs = {e.lang for e in out}
    assert "en" in langs and "ko" not in langs   # ko dropped: no slot values


# --------------------------------------------------------------------------
# pandas view (ch.7 bonus) — must return the same numbers as the stdlib tool
# --------------------------------------------------------------------------


def test_pandas_parity_with_stdlib_audit():
    pd = pytest.importorskip("pandas")   # skip cleanly if pandas absent
    pdm = _load("pandas/corpus_metrics_pandas.py", "corpus_metrics_pandas")

    sample = SNIPPETS / "dataset-quality" / "sample_corpus.csv"
    rows, enc = audit.read_csv_smart(sample)
    rep = audit.audit(rows, list(rows[0].keys()), enc, "x", "term", "ko")

    df = pd.read_csv(sample, dtype="string", keep_default_na=False, encoding="utf-8-sig")
    prep = pdm.analyze(df, "term")

    assert prep["coverage"]["ja_JP"] == rep.coverage["ja_JP"]
    assert prep["duplicate_keys"] == rep.metrics.get("duplicate_key")
    assert list(prep["lang_code_conflicts"]) == list(rep.lang_code_conflicts)
    assert prep["length_outliers"] == rep.metrics.get("length_outlier")


# --------------------------------------------------------------------------
# ch.15 scaling: benchmark correctness, Welford, out-of-core parity
# --------------------------------------------------------------------------


def test_bench_naive_and_ac_agree():
    import random
    bench = _load("benchmark/bench_matching.py", "bench_matching")
    rng = random.Random(0)
    terms = bench.make_terms(300, rng)
    segs = bench.make_segments(500, terms, rng)
    matcher = bench.ac.build_matcher(terms, min_len=2)
    # The whole point of the benchmark: the fast path must be a drop-in for the
    # slow path, identical results — only faster.
    assert bench.naive_match(segs, terms, 2) == bench.ac_search(segs, matcher)


def test_welford_matches_batch_statistics():
    import statistics
    svl = _load("benchmark/stream_vs_load.py", "stream_vs_load")
    vals = list(svl.ratios(5000))
    w = svl.Welford()
    for v in vals:
        w.update(v)
    assert w.mean == pytest.approx(statistics.fmean(vals), rel=1e-9)
    assert w.stdev == pytest.approx(statistics.stdev(vals), rel=1e-6)


def test_out_of_core_backends_match_stdlib(tmp_path):
    ooc = _load("scale/out_of_core.py", "out_of_core")
    path = tmp_path / "corpus.csv"
    ooc.write_corpus(path, 6000)
    base = ooc.coverage_stdlib(path)
    for backend in (ooc.coverage_duckdb, ooc.coverage_polars):
        res = backend(path)
        if res is None:
            continue   # optional dependency absent -> skip that backend
        for lang, cov in base.items():
            assert abs(res.get(lang, 0) - cov) < 1e-3


def test_error_cases_each_reproduce_a_bug():
    ec = _load("debug/error_cases.py", "error_cases")
    # Each field-note case returns (title, broken, fixed); broken must actually
    # show the failure (an exception text or a wrong value), fixed must differ.
    for fn in ec.CASES:
        title, broke, fixed = fn()
        assert title and broke and fixed
        assert broke != fixed
    assert ec.main([]) == 0   # the whole catalog runs clean


# --------------------------------------------------------------------------
# ch.17 data -> model: Naive Bayes + label-noise curve
# --------------------------------------------------------------------------


def test_naive_bayes_learns_then_label_noise_collapses_it():
    dqi = _load("data-model/data_quality_impact.py", "data_quality_impact")
    curve = {r["noise"]: r["test_acc"] for r in dqi.run(n=1500, rates=[0.0, 0.5], seed=0)}
    assert curve[0.0] > 0.75            # learns the clean task
    assert curve[0.5] < 0.65            # ~50% wrong labels collapse it toward chance


def test_naive_bayes_predicts_obvious_doc():
    dqi = _load("data-model/data_quality_impact.py", "data_quality_impact")
    import random
    model = dqi.NaiveBayes().fit(dqi.make_dataset(800, random.Random(1)))
    assert model.predict(["buy", "buy", "price", "cart"]) == "buy"


# --------------------------------------------------------------------------
# ch.18 similarity matching
# --------------------------------------------------------------------------


def test_fuzzy_matches_variants_rejects_unrelated():
    fz = _load("matching-similarity/fuzzy_match.py", "fuzzy_match")
    m = fz.FuzzyMatcher().fit(["cooldown", "respawn", "loot"])
    assert m.match("cooldwn", threshold=0.5)[0] == "cooldown"     # typo
    assert m.match("cool-down", threshold=0.5)[0] == "cooldown"   # variant
    assert m.match("banana", threshold=0.5) is None               # unrelated


# --------------------------------------------------------------------------
# ch.19 KG depth: transitive reasoning + entity linking
# --------------------------------------------------------------------------


def test_lexicon_transitive_ancestors_and_linking():
    rows = [
        {"id": "object", "en": "object", "broader": "", "synonyms": ""},
        {"id": "item", "en": "item", "broader": "object", "synonyms": ""},
        {"id": "loot", "en": "loot", "broader": "item", "synonyms": ""},
    ]
    g, _ = lex.build(rows)
    assert g.ancestors("loot") == ["item", "object"]        # transitive chain
    assert set(g.descendants("object")) == {"item", "loot"}
    linked = dict(g.link("collect the loot now"))
    assert "loot" in linked


def test_lexicon_detects_broader_cycle():
    rows = [
        {"id": "a", "en": "a", "broader": "b", "synonyms": ""},
        {"id": "b", "en": "b", "broader": "a", "synonyms": ""},
    ]
    g, _ = lex.build(rows)
    assert g.find_cycles()              # a -> b -> a must be reported


# --------------------------------------------------------------------------
# ch.20 multi-source integration
# --------------------------------------------------------------------------


def test_build_corpus_canon_lang():
    bc = _load("multi-source/build_corpus.py", "build_corpus")
    assert bc.canon_lang("ko_KR", False) == "ko-KR"
    assert bc.canon_lang("KO-kr", False) == "ko-KR"
    assert bc.canon_lang("EN", False) == "en"


def test_build_corpus_merges_and_flags_conflict(tmp_path):
    bc = _load("multi-source/build_corpus.py", "build_corpus")
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("key,ko-KR\nattack,공격\n", encoding="utf-8")
    b.write_text("key,ko_KR\nattack,어택\n", encoding="cp949")   # cp949 + underscore
    res = bc.merge([a, b], key_col="key", merge_base=False)
    assert res["encodings"][b.name] == "cp949"
    assert len(res["conflicts"]) == 1                  # 공격 vs 어택, same (key,lang)
    assert res["value"][("attack", "ko-KR")][0] == "공격"   # first source wins


# --------------------------------------------------------------------------
# edit distance (matching-similarity)
# --------------------------------------------------------------------------


def test_levenshtein_basic_edits_and_symmetry():
    ed = _load("matching-similarity/edit_distance.py", "edit_distance")
    assert ed.levenshtein("cooldown", "cooldown") == 0
    assert ed.levenshtein("cooldown", "cooldwn") == 1      # one deletion
    assert ed.levenshtein("inventary", "inventory") == 1   # one substitution
    assert ed.levenshtein("kitten", "sitting") == 3        # textbook case
    assert ed.levenshtein("abc", "cba") == ed.levenshtein("cba", "abc")  # symmetric


def test_bounded_levenshtein_matches_full_within_budget():
    ed = _load("matching-similarity/edit_distance.py", "edit_distance")
    for a, b in [("cooldown", "cooldwn"), ("loot", "loots"), ("abc", "xyz")]:
        full = ed.levenshtein(a, b)
        bounded = ed.bounded_levenshtein(a, b, 2)
        # bounded agrees with full while the true distance is within budget,
        # and caps out (budget+1) once it provably exceeds it
        assert bounded == full if full <= 2 else bounded == 3


def test_closest_recovers_typo_rejects_unrelated():
    ed = _load("matching-similarity/edit_distance.py", "edit_distance")
    terms = ["cooldown", "respawn", "loot"]
    assert ed.closest("cooldwn", terms, max_dist=2)[0] == "cooldown"
    assert ed.closest("banana", terms, max_dist=2) is None
    assert ed.closest("ＣＯＯＬＤＯＷＮ", terms, max_dist=1)[0] == "cooldown"  # NFKC width


# --------------------------------------------------------------------------
# prefix index (trie)
# --------------------------------------------------------------------------


def test_trie_prefix_and_membership():
    pi = _load("glossary-matching/prefix_index.py", "prefix_index")
    t = pi.Trie()
    for term in ["cool", "cooldown", "coop", "respawn"]:
        t.insert(term)
    assert t.keys_with_prefix("coo") == ["cool", "cooldown", "coop"]
    assert "cooldown" in t and "cooldo" not in t
    assert t.keys_with_prefix("zz") == []


def test_trie_longest_match_segmentation():
    pi = _load("glossary-matching/prefix_index.py", "prefix_index")
    t = pi.Trie()
    for term in ["アイテム", "アイテム化", "戦利品"]:
        t.insert(term)
    # longest match wins: アイテム化, not アイテム
    assert t.longest_prefix_of("アイテム化する") == "アイテム化"
    assert t.segment("戦利品をアイテム化") == ["戦利品", "を", "アイテム化"]


# --------------------------------------------------------------------------
# duplicate clustering (union-find)
# --------------------------------------------------------------------------


def test_union_find_transitive_clustering():
    cd = _load("multi-source/cluster_duplicates.py", "cluster_duplicates")
    clusters = cd.cluster(cd.DEMO_RECORDS, "id", ["name", "ext_id"])
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 1, 4]               # r1..r4 merge transitively
    big = max(clusters, key=len)
    assert {r["id"] for r in big} == {"r1", "r2", "r3", "r4"}


def test_disjoint_set_path_compression_and_rank():
    cd = _load("multi-source/cluster_duplicates.py", "cluster_duplicates")
    dsu = cd.DisjointSet()
    for x in "abcd":
        dsu.add(x)
    dsu.union("a", "b")
    dsu.union("c", "d")
    dsu.union("b", "c")                      # joins the two pairs
    assert len({dsu.find(x) for x in "abcd"}) == 1


# --------------------------------------------------------------------------
# top-K terms (bounded heap)
# --------------------------------------------------------------------------


def test_top_k_matches_full_sort():
    tt = _load("dataset-quality/top_terms.py", "top_terms")
    counts = {"a": 5, "b": 3, "c": 9, "d": 1, "e": 7}
    for k in (1, 3, 5):
        assert tt.top_k(counts, k) == tt.top_k_sorted(counts, k)
    assert tt.top_k(counts, 2) == [("c", 9), ("e", 7)]


def test_stream_untranslated_counts_blank_and_copy_through():
    tt = _load("dataset-quality/top_terms.py", "top_terms")
    counts = tt.count_untranslated(tt.DEMO_ROWS, "en", ["ko", "ja"])
    assert counts["Cooldown"] == 3          # 2 blanks + 1 ja-blank
    assert "Loot" not in counts             # fully translated, never counted
    assert counts["Inventory"] == 2         # copy-through + blank both count


# --------------------------------------------------------------------------
# k-way merge of sorted shards (heap)
# --------------------------------------------------------------------------


def test_kway_merge_equals_full_sort():
    ms = _load("scale/merge_shards.py", "merge_shards")
    shards = ms.make_shards(5, 50)
    merged = list(ms.kway_merge(shards))
    assert merged == sorted(v for s in shards for v in s)


def test_kway_merge_handles_empty_and_uneven_shards():
    ms = _load("scale/merge_shards.py", "merge_shards")
    shards = [[1, 4, 7], [], [2, 2, 9], [0]]
    assert list(ms.kway_merge(shards)) == [0, 1, 2, 2, 4, 7, 9]


# --------------------------------------------------------------------------
# topological order of the concept hierarchy (Kahn)
# --------------------------------------------------------------------------


def test_topo_order_places_broader_before_narrower():
    rows = [
        {"id": "object", "en": "object", "broader": "", "synonyms": ""},
        {"id": "item", "en": "item", "broader": "object", "synonyms": ""},
        {"id": "loot", "en": "loot", "broader": "item", "synonyms": ""},
    ]
    g, _ = lex.build(rows)
    order = g.topo_order()
    assert order.index("object") < order.index("item") < order.index("loot")


def test_topo_order_total_even_with_cycle():
    rows = [
        {"id": "a", "en": "a", "broader": "b", "synonyms": ""},
        {"id": "b", "en": "b", "broader": "a", "synonyms": ""},
    ]
    g, _ = lex.build(rows)
    assert sorted(g.topo_order()) == ["a", "b"]    # still a total order


# --------------------------------------------------------------------------
# annotation span merge + conflict (sort-and-sweep)
# --------------------------------------------------------------------------


def test_merge_spans_joins_same_label_overlaps():
    sp = _load("nlu/merge_spans.py", "merge_spans")
    spans = [sp.Span(8, 10, "ORG"), sp.Span(8, 19, "ORG"), sp.Span(20, 24, "ITEM")]
    merged = sp.merge_spans(spans)
    assert sp.Span(8, 19, "ORG") in merged and sp.Span(20, 24, "ITEM") in merged
    assert len(merged) == 2


def test_merge_spans_reports_cross_label_conflict():
    sp = _load("nlu/merge_spans.py", "merge_spans")
    conflicts = sp.find_conflicts(sp.DEMO_SPANS)
    assert len(conflicts) == 1
    labels = {conflicts[0][0].label, conflicts[0][1].label}
    assert labels == {"ORG", "TITLE"}


# --------------------------------------------------------------------------
# reservoir sampling (uniform, single pass)
# --------------------------------------------------------------------------


def test_reservoir_sample_size_and_membership():
    import random
    rs = _load("scale/reservoir_sample.py", "reservoir_sample")
    sample = rs.reservoir_sample(range(100), 10, random.Random(0))
    assert len(sample) == 10
    assert len(set(sample)) == 10                 # no duplicates
    assert all(0 <= x < 100 for x in sample)
    assert rs.reservoir_sample(range(3), 10, random.Random(0)) == [0, 1, 2]  # k>n


def test_reservoir_sample_is_approximately_uniform():
    rs = _load("scale/reservoir_sample.py", "reservoir_sample")
    n, k = 20, 5
    rates = rs.inclusion_rates(n, k, trials=4000, seed=1)
    expected = k / n
    # every element should land within a few points of k/n over many trials
    assert max(abs(r - expected) for r in rates) < 0.06


# --------------------------------------------------------------------------
# concept_paths: BFS / DFS / Dijkstra
# --------------------------------------------------------------------------


def test_bfs_fewest_hops_vs_dijkstra_least_cost():
    cp = _load("knowledge-graph/concept_paths.py", "concept_paths")
    adj = cp.make_undirected(cp.DEMO_EDGES)
    hop = cp.bfs_path(adj, "sword", "gold")
    dist, parent = cp.dijkstra(adj, "sword")
    cheap = cp.path_from(parent, "gold")
    # BFS takes the direct (4-hop) route; Dijkstra takes the cheaper 5-hop route
    assert len(hop) - 1 == 4
    assert dist["gold"] == 6.0
    assert len(cheap) - 1 == 5 and "currency" in cheap


def test_bfs_returns_none_when_unreachable():
    cp = _load("knowledge-graph/concept_paths.py", "concept_paths")
    adj = cp.make_undirected([("a", "b", 1.0), ("c", "d", 1.0)])
    assert cp.bfs_path(adj, "a", "d") is None
    assert cp.dfs_reachable(adj, "a") == {"a", "b"}


# --------------------------------------------------------------------------
# threshold_search: binary search + parametric
# --------------------------------------------------------------------------


def test_lower_and_upper_bound_match_bisect():
    import bisect
    ts = _load("experiments/threshold_search.py", "threshold_search")
    xs = [0.1, 0.3, 0.3, 0.5, 0.9]
    for target in (0.0, 0.3, 0.4, 0.9, 1.0):
        assert ts.lower_bound(xs, target) == bisect.bisect_left(xs, target)
        assert ts.upper_bound(xs, target) == bisect.bisect_right(xs, target)


def test_parametric_threshold_respects_budget():
    ts = _load("experiments/threshold_search.py", "threshold_search")
    scores = ts.DEMO_SCORES
    cands = sorted(set(scores))
    t = ts.search_threshold(cands, lambda x: ts.matches_at(scores, x) <= 3)
    assert ts.matches_at(scores, t) <= 3
    # the next-lower candidate must break the budget (smallest feasible threshold)
    lower = cands[cands.index(t) - 1]
    assert ts.matches_at(scores, lower) > 3


# --------------------------------------------------------------------------
# windowed_metrics: sliding window + two pointers
# --------------------------------------------------------------------------


def test_sliding_window_mean_matches_brute_force():
    wm = _load("scale/windowed_metrics.py", "windowed_metrics")
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    got = list(wm.sliding_window_mean(vals, 3))
    assert got == [(2, 2.0), (3, 3.0), (4, 4.0)]


def test_longest_ok_run_two_pointer():
    wm = _load("scale/windowed_metrics.py", "windowed_metrics")
    flags = [0, 0, 1, 0, 1, 1, 0, 0, 0]
    length, start, end = wm.longest_ok_run(flags, max_bad=1)
    assert length == 4 and flags[start:end].count(1) <= 1   # e.g. [1,0,0,0] tail
    assert wm.longest_ok_run([1, 1, 1], max_bad=0)[0] == 0


# --------------------------------------------------------------------------
# constraint_expand: backtracking with pruning
# --------------------------------------------------------------------------


def test_backtracking_generates_only_valid_and_prunes():
    ce = _load("nlu/constraint_expand.py", "constraint_expand")
    combos = ce.expand(ce.DEMO_SLOTS, ce.all_distinct)
    assert len(combos) == 12                               # 3 * 2 * 2 after pruning
    assert all(c["give"] != c["receive"] for c in combos)
    valid, visited, product = ce.count_nodes(ce.DEMO_SLOTS, ce.all_distinct)
    assert valid == 12 and product == 18                   # pruned below the product


# --------------------------------------------------------------------------
# sequence_align: LCS + LIS
# --------------------------------------------------------------------------


def test_lcs_finds_unchanged_spine():
    sa = _load("matching-similarity/sequence_align.py", "sequence_align")
    a = ["the", "ancient", "loot", "chest", "respawns"]
    b = ["the", "rare", "loot", "chest", "now", "respawns"]
    assert sa.lcs(a, b) == ["the", "loot", "chest", "respawns"]
    assert sa.lcs_length(a, b) == 4
    assert sa.lcs(list("abc"), list("xyz")) == []


def test_lis_returns_increasing_subsequence():
    sa = _load("matching-similarity/sequence_align.py", "sequence_align")
    run = sa.lis([0.62, 0.55, 0.66, 0.70, 0.68, 0.74, 0.80, 0.79])
    assert len(run) == 5
    assert all(run[i] < run[i + 1] for i in range(len(run) - 1))   # strictly up


# --------------------------------------------------------------------------
# budget_allocation: 0/1 knapsack + coin change
# --------------------------------------------------------------------------


def test_knapsack_picks_optimal_subset_within_capacity():
    ba = _load("experiments/budget_allocation.py", "budget_allocation")
    value, chosen = ba.knapsack_01(ba.DEMO_ITEMS, 8)
    assert value == 12.0
    cost = sum(it.cost for it in ba.DEMO_ITEMS if it.label in chosen)
    assert cost <= 8


def test_coin_change_minimizes_pieces_or_none():
    ba = _load("experiments/budget_allocation.py", "budget_allocation")
    count, combo = ba.coin_change([50, 30, 20], 100)
    assert count == 2 and sum(combo) == 100
    assert ba.coin_change([50, 30], 10) is None            # unreachable target
