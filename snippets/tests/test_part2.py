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
