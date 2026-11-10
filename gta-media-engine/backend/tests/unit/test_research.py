from datetime import datetime, timedelta, timezone

from engine.enums import Category, Classification
from engine.research import classify, scoring
from engine.research.text import jaccard, keyset, normalize, relevance


def test_normalize_unifies_gta_names():
    assert "gta 6" in normalize("Grand Theft Auto VI")
    assert "gta 6" in normalize("GTA VI trailer")
    assert "gta 5" in normalize("GTA V physics")


def test_relevance_requires_gta6_terms():
    assert relevance("GTA 6 trailer breakdown") >= 0.7
    assert relevance("Minecraft update") == 0


def test_jaccard_similarity():
    assert jaccard(keyset("GTA 6 Vice City beach"), keyset("Vice City beach in GTA 6")) == 1.0
    assert jaccard(set(), {"a"}) == 0.0


def test_classification_levels():
    assert classify.classify(["Rockstar announces release date"], ["official"]) == Classification.OFFICIAL
    assert classify.classify(["IGN reports new trailer details"], ["credible"]) == Classification.CREDIBLE
    assert classify.classify(["My theory: Lucia is an undercover cop"], ["community"]) == Classification.THEORY
    assert classify.classify(["Leaked insider info on GTA 6 map size"], ["community"]) == Classification.RUMOUR
    assert classify.classify(["That GTA 6 map leak is fake, debunked"], ["credible"]) == Classification.DEBUNKED
    # one reputable outlet repeating an insider claim is still a rumour
    assert classify.classify(["Insider claims GTA 6 delay"], ["credible"]) == Classification.RUMOUR
    assert classify.classify(["Insider claims GTA 6 delay"], ["credible", "credible"]) == Classification.CREDIBLE


def test_unconfirmed_material_is_never_news():
    assert classify.compatible(Category.NEWS, Classification.RUMOUR) == Category.THEORIES
    assert classify.compatible(Category.NEWS, Classification.OFFICIAL) == Category.NEWS


def test_category_inference():
    assert classify.infer_category("GTA 6 vs GTA 5 physics comparison") == Category.VERSUS
    assert classify.infer_category("Hidden easter egg spotted in trailer") == Category.DISCOVERIES


def test_score_bounds_and_missing_signals_are_excluded_not_zero():
    now = datetime.now(timezone.utc)
    full = scoring.Signals(views=[200_000], comments=[3_000], newest=now, relevance=1.0, search_interest=80,
                           recent_competitor_videos=20, source_count=4)
    score_full, b_full = scoring.compute(full, Category.NEWS, Classification.OFFICIAL, now)
    assert 0 <= score_full <= 100 and b_full["missing_signals"] == []

    partial = scoring.Signals(views=[200_000], comments=[3_000], newest=now, relevance=1.0, source_count=4)
    score_partial, b_partial = scoring.compute(partial, Category.NEWS, Classification.OFFICIAL, now)
    assert set(b_partial["missing_signals"]) == {"search_demand", "low_saturation"}
    # If missing were treated as zero the score would drop by >= 25% of the weight; renormalisation keeps it close.
    assert score_partial > score_full * 0.8


def test_debunked_and_irrelevant_penalties():
    now = datetime.now(timezone.utc)
    sig = scoring.Signals(views=[50_000], newest=now, relevance=1.0, source_count=2)
    base, _ = scoring.compute(sig, Category.THEORIES, Classification.THEORY, now)
    debunked, bd = scoring.compute(sig, Category.THEORIES, Classification.DEBUNKED, now)
    assert debunked < base and bd["adjustments"]
    sig.relevance = 0.1
    low, _ = scoring.compute(sig, Category.THEORIES, Classification.THEORY, now)
    assert low < base * 0.6


def test_timeliness_decays():
    now = datetime.now(timezone.utc)
    fresh, _ = scoring.compute(scoring.Signals(newest=now, relevance=1), Category.NEWS, Classification.OFFICIAL, now)
    stale, _ = scoring.compute(scoring.Signals(newest=now - timedelta(days=10), relevance=1), Category.NEWS,
                               Classification.OFFICIAL, now)
    assert fresh > stale


def test_urgency_and_length():
    assert scoring.urgency(Category.NEWS, Classification.OFFICIAL, 0.9) == "urgent"
    assert scoring.urgency(Category.THEORIES, Classification.THEORY, 0.1) == "low"
    assert scoring.urgency(Category.NEWS, Classification.OFFICIAL, None) == "normal"
    assert scoring.recommended_length(Category.NEWS, 100) == "15-25"
    assert scoring.recommended_length(Category.THEORIES, 500) == "40-60"
