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
