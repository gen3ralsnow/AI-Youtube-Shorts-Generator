from engine.agents.base import AgentContext
from engine.agents.factcheck import FactCheckerAgent, lexical_support
from engine.agents.scriptwriter import (
    ScriptwriterAgent,
    choose_hook,
    fit_bucket,
    latest_script,
    rule_based_draft,
    score_hook,
    validate_script,
)
from engine.models import ContentJob
from tests.conftest import seed_topic


def _draft(session, **kw):
    topic = seed_topic(session, **kw)
    return topic, rule_based_draft(topic, "25-40", 165)


def test_rule_based_script_only_uses_source_sentences(session):
    topic, draft = _draft(session)
    corpus = " ".join(f"{s.title}. {s.summary}" for s in topic.sources).lower()
    assert draft.claims, "factual script must carry claims"
    for claim in draft.claims:
        assert claim.text.rstrip(".").lower() in corpus
        assert set(claim.source_ids) <= {s.id for s in topic.sources}
    assert draft.sections["hook"] == draft.chosen_hook
    assert draft.scenes[0].section == "hook" and draft.scenes[-1].section == "ending"
    assert validate_script(draft, {s.id for s in topic.sources}, 165)["passed"]


def test_theory_scripts_carry_a_disclaimer(session):
    topic, draft = _draft(session, classification="community_theory", category="C_theories", credibility="community",
                          domain="reddit.com")
    assert "theory" in draft.voiceover_text.lower()
    assert validate_script(draft, {s.id for s in topic.sources}, 165)["passed"]


def test_validator_catches_problems(session):
    topic, draft = _draft(session)
    allowed = {s.id for s in topic.sources}
    bad = draft.model_copy(deep=True)
    bad.voiceover_text = "In today's video we dive into GTA 6. " + bad.voiceover_text
    assert any("generic" in e for e in validate_script(bad, allowed, 165)["errors"])

    bad = draft.model_copy(deep=True)
    bad.classification = "unverified_rumour"
    bad.voiceover_text = "Rockstar confirmed the map is huge. It is officially the biggest map ever made by anyone."
    errs = validate_script(bad, allowed, 165)["errors"]
    assert any("disclaimer" in e for e in errs) and any("confirmation" in e for e in errs)

    bad = draft.model_copy(deep=True)
    bad.claims[0].source_ids = [99999]
    assert any("unknown source" in e for e in validate_script(bad, allowed, 165)["errors"])

    bad = draft.model_copy(deep=True)
    bad.claims = []
    assert any("no sourced claims" in e for e in validate_script(bad, allowed, 165)["errors"])

    bad = draft.model_copy(deep=True)
    bad.voiceover_text = "Short. Too short."
    assert any("too thin" in e for e in validate_script(bad, allowed, 165)["errors"])


def test_duration_buckets_never_stretch():
    assert fit_bucket(18) == "15-25"
    assert fit_bucket(33) == "25-40"
    assert fit_bucket(59) == "40-60"
    assert fit_bucket(75) is None


def test_hook_selection_disqualifies_false_certainty():
    hooks = ["Rockstar confirmed the GTA 6 map size.", "Did you catch this hidden GTA 6 detail?"]
    assert choose_hook(hooks, "community_theory") == hooks[1]
    assert score_hook(hooks[0], "community_theory") < -50
    assert score_hook(hooks[0], "officially_confirmed") > -50


def test_lexical_support(session):
    topic = seed_topic(session)
    assert lexical_support("The trailer shows a Vice City beach in Leonida.", topic.sources) >= 0.6
    assert lexical_support("GTA 6 will cost $150 and launch on Switch.", topic.sources) < 0.6


def test_fact_checker_script_mode(session):
    topic = seed_topic(session)
    job = ContentJob(topic_id=topic.id, category=topic.category, duration_bucket="15-25")
    session.add(job)
    session.flush()
    ctx = AgentContext(session, job.id)
    assert FactCheckerAgent().run(ctx, {"job_id": job.id, "mode": "topic"}).passed
    ScriptwriterAgent().run(ctx, {"job_id": job.id})
    res = FactCheckerAgent().run(ctx, {"job_id": job.id, "mode": "script"})
    assert res.passed and res.confidence == 1.0
    # Inject an unsupported claim: confidence must drop below threshold and block.
    script = latest_script(session, job.id)
    script.claims = script.claims + [{"text": "GTA 6 costs 150 dollars on Nintendo Switch", "source_ids": [topic.sources[0].id]}]
    res = FactCheckerAgent().run(ctx, {"job_id": job.id, "mode": "script"})
    assert not res.passed and res.confidence < res.threshold


def test_news_requires_credible_sources(session):
    topic = seed_topic(session, classification="community_theory", category="B_news", credibility="community")
    job = ContentJob(topic_id=topic.id, category="B_news", duration_bucket="15-25")
    session.add(job)
    session.flush()
    res = FactCheckerAgent().run(AgentContext(session, job.id), {"job_id": job.id, "mode": "topic"})
    assert not res.passed
