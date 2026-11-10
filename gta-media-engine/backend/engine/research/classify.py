"""Accuracy classification and content-category inference.

Classification is evidence based and conservative: a topic is only
"officially confirmed" when an official source (Rockstar/Take-Two domain or a
configured official channel) is attached. Anything else is at best
"credibly reported".
"""

from __future__ import annotations

from ..config import get_settings
from ..enums import Category, Classification
from .text import domain_matches, normalize

RUMOUR_WORDS = ("leak", "leaked", "rumor", "rumour", "insider", "allegedly", "reportedly", "claims")
THEORY_WORDS = ("theory", "theories", "predict", "prediction", "speculat", "could be", "might",
                "what if", "i think", "possibly", "hint")
DEBUNK_WORDS = ("fake", "debunk", "debunked", "hoax", "not real", "false", "misinformation", "denied")

CATEGORY_RULES: list[tuple[Category, tuple[str, ...]]] = [
    (Category.VERSUS, (" vs ", "versus", "gta 5", "compared", "comparison", "difference")),
    (Category.THEORIES, THEORY_WORDS),
    (Category.NEWS, ("announce", "release date", "delay", "official", "take-two", "earnings",
                     "price", "pre-order", "preorder", "trailer 3", "launch", "confirmed", "statement")),
    (Category.ENTERTAINMENT, ("funny", "top 5", "top five", "moments", "best", "craziest", "fails")),
    (Category.DISCOVERIES, ("detail", "missed", "easter egg", "hidden", "map", "breakdown", "spotted",
                            "noticed", "character", "location", "vehicle", "weapon")),
]


def source_credibility(domain: str, connector: str, channel_id: str | None = None) -> str:
    s = get_settings()
    if domain_matches(domain, s.official_domains):
        return "official"
    if channel_id and channel_id in s.official_youtube_channel_ids:
        return "official"
    if domain_matches(domain, s.credible_domains):
        return "credible"
    return "community"


def classify(texts: list[str], credibilities: list[str]) -> Classification:
    blob = normalize(" ".join(texts))
    credible_count = credibilities.count("credible")
    official = "official" in credibilities
    if any(w in blob for w in DEBUNK_WORDS) and (official or credible_count):
        return Classification.DEBUNKED
    if official:
        return Classification.OFFICIAL
    if credible_count >= 1 and not any(w in blob for w in RUMOUR_WORDS):
        return Classification.CREDIBLE
    if credible_count >= 2:
        # multiple reputable outlets reporting a leak/insider claim
        return Classification.CREDIBLE
    if any(w in blob for w in THEORY_WORDS):
        return Classification.THEORY
    if any(w in blob for w in RUMOUR_WORDS):
        return Classification.RUMOUR
    return Classification.THEORY if credibilities else Classification.RUMOUR


def infer_category(text: str) -> Category:
    blob = f" {normalize(text)} "
    for category, words in CATEGORY_RULES:
        if any(w in blob for w in words):
            return category
    return Category.DISCOVERIES


def compatible(category: Category, classification: Classification) -> Category:
    """Unconfirmed material can never be framed as news."""
    if category == Category.NEWS and classification in (
        Classification.THEORY, Classification.RUMOUR
    ):
        return Category.THEORIES
    return category


DISCLAIMERS = {
    Classification.OFFICIAL: "",
    Classification.CREDIBLE: "",
    Classification.THEORY: "This is a fan theory, not confirmed by Rockstar.",
    Classification.RUMOUR: "This is an unverified rumour. Rockstar has not confirmed it.",
    Classification.DEBUNKED: "This claim has been shown to be false.",
}
