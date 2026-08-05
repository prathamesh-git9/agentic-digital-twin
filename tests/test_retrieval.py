"""Retrieval quality for the chat corpus.

The twin refuses when retrieval returns nothing that supports a claim. That
makes retrieval recall a *credibility* property, not a relevance nicety: a
recruiter who asks about a master's degree and is told "I don't have reliable
evidence for that" has been given a false negative about a real qualification.

These tests measure recall against a golden set argued from the CV itself, and
pin the ranker's behavior so a scoring change cannot quietly reintroduce the
refusals this replaced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from digital_twin.profile import ProfileCorpus, tokens
from digital_twin.retrieval import (
    ALIASES,
    BM25Index,
    conversational_query,
    expand_query,
    token_list,
)

ROOT = Path(__file__).resolve().parents[1]
EVAL = yaml.safe_load((ROOT / "data" / "retrieval_eval.yaml").read_text("utf-8"))

# Recorded on the committed corpus. These are floors, not targets: the point is
# that a scoring change cannot silently lose ground.
MINIMUM_RECALL = 1.0
MINIMUM_MRR = 0.75


@pytest.fixture(scope="module")
def corpus() -> ProfileCorpus:
    return ProfileCorpus(ROOT / "data" / "profile.yaml")


def _legacy_retrieve(corpus: ProfileCorpus, query: str, limit: int = 8):
    """The lexical-overlap ranker this replaced, kept to prove the delta."""

    query_tokens = tokens(query)
    scored = []
    for index, item in enumerate(corpus.evidence):
        haystack = tokens(f"{item.source} {item.text}")
        overlap = query_tokens & haystack
        score = len(overlap) * 2.0
        if query_tokens and query_tokens <= haystack:
            score += 2.0
        if overlap and "summary" in item.source.casefold():
            score += 0.15
        scored.append((score, -index, item))
    scored.sort(reverse=True, key=lambda row: (row[0], row[1]))
    return [item for score, _, item in scored if score > 0][:limit]


def _rank_of_expected(results, expected: list[str]) -> int | None:
    for position, item in enumerate(results, start=1):
        if item.source in expected:
            return position
    return None


# --- The gate ---------------------------------------------------------------


def test_golden_set_recall_and_ranking(corpus: ProfileCorpus) -> None:
    k = EVAL["k"]
    misses: list[str] = []
    reciprocal_ranks: list[float] = []

    for case in EVAL["cases"]:
        results = corpus.retrieve(case["question"], limit=k)
        rank = _rank_of_expected(results, case["expect_any"])
        if rank is None:
            misses.append(f"{case['id']}: {case['question']!r}")
            reciprocal_ranks.append(0.0)
        else:
            reciprocal_ranks.append(1 / rank)

    recall = 1 - len(misses) / len(EVAL["cases"])
    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)

    assert recall >= MINIMUM_RECALL, "questions with no correct source in top-k:\n" + (
        "\n".join(misses)
    )
    assert mrr >= MINIMUM_MRR, f"mean reciprocal rank fell to {mrr:.3f}"


def test_new_ranker_beats_the_one_it_replaced(corpus: ProfileCorpus) -> None:
    """The improvement, measured rather than asserted."""

    k = EVAL["k"]
    legacy_hits = 0
    current_hits = 0
    for case in EVAL["cases"]:
        expected = case["expect_any"]
        if _rank_of_expected(_legacy_retrieve(corpus, case["question"], k), expected):
            legacy_hits += 1
        if _rank_of_expected(corpus.retrieve(case["question"], limit=k), expected):
            current_hits += 1

    assert current_hits > legacy_hits


def test_lexical_overlap_produced_false_refusals(corpus: ProfileCorpus) -> None:
    """The specific failure this fixes: an answerable question with no evidence.

    Empty retrieval is not a ranking blemish. It is the input that makes the
    twin say it has no evidence, about a degree that is in the CV.
    """

    question = "Do you have a masters degree?"

    assert _legacy_retrieve(corpus, question) == []
    results = corpus.retrieve(question)
    assert any("Education" in item.source for item in results)


@pytest.mark.parametrize("case", EVAL["cases"], ids=lambda case: case["id"])
def test_each_golden_case_finds_its_source(corpus: ProfileCorpus, case) -> None:
    results = corpus.retrieve(case["question"], limit=EVAL["k"])

    assert _rank_of_expected(results, case["expect_any"]) is not None


# --- Guarding the alias map -------------------------------------------------


def test_every_alias_target_exists_in_the_corpus(corpus: ProfileCorpus) -> None:
    """An alias pointing at a word the CV lacks is a claim about experience.

    This is the test that keeps the map honest: expansions may only reach
    vocabulary that is genuinely in the corpus.
    """

    vocabulary: set[str] = set()
    for item in corpus.evidence:
        vocabulary.update(token_list(f"{item.source} {item.text}"))

    unsupported = {
        f"{term} -> {alias}"
        for term, expansions in ALIASES.items()
        for alias in expansions
        if alias not in vocabulary
    }

    assert not unsupported, f"alias targets absent from the CV: {sorted(unsupported)}"


@pytest.mark.parametrize("case", EVAL["unanswerable"], ids=lambda case: case["id"])
def test_unanswerable_questions_surface_no_supporting_claim(
    corpus: ProfileCorpus, case
) -> None:
    """Widening retrieval must not widen what the corpus appears to support.

    Retrieval may return loosely related entries; what must not happen is any
    returned evidence actually containing the technology asked about.
    """

    subject = case["question"].lower()
    results = corpus.retrieve(case["question"])

    for item in results:
        text = f"{item.source} {item.text}".lower()
        for term in ("kubernetes", "rust", "google", "terraform"):
            if term in subject:
                assert term not in text


# --- Ranker mechanics -------------------------------------------------------


def test_rare_terms_outrank_common_ones() -> None:
    index = BM25Index(
        [
            "engineer built services",
            "engineer built kafka pipelines",
            "engineer built apis",
            "engineer built dashboards",
        ]
    )

    ranked = index.score("kafka engineer")

    assert ranked[0][1] == 1  # the document with the rare term wins


def test_direct_terms_outrank_alias_expansions() -> None:
    index = BM25Index(["docker image build", "containers overview docker"])

    direct = dict((position, score) for score, position in index.score("docker"))
    expanded = dict((position, score) for score, position in index.score("containers"))

    assert direct[0] > expanded[0]


def test_expansion_keeps_full_weight_for_typed_terms() -> None:
    weights = expand_query("kafka queue")

    assert weights["kafka"] == 1.0  # typed directly, and also an alias target
    assert weights["sqs"] < 1.0


def test_empty_and_stopword_queries_return_nothing(corpus: ProfileCorpus) -> None:
    assert corpus.retrieve("") == []
    assert corpus.retrieve("the and of") == []


def test_ranking_is_deterministic(corpus: ProfileCorpus) -> None:
    question = "What is your backend experience?"

    first = [item.source for item in corpus.retrieve(question)]
    second = [item.source for item in corpus.retrieve(question)]

    assert first == second


def test_limit_is_respected(corpus: ProfileCorpus) -> None:
    assert len(corpus.retrieve("engineer", limit=3)) <= 3


# --- Follow-up questions ----------------------------------------------------


def test_a_self_contained_question_is_left_alone() -> None:
    history = [{"role": "user", "content": "Tell me about your Kafka work"}]

    assert (
        conversational_query("What databases have you worked with?", history)
        == "What databases have you worked with?"
    )


def test_a_follow_up_carries_the_visitors_earlier_terms() -> None:
    history = [{"role": "user", "content": "Tell me about your observability work"}]

    expanded = conversational_query("What did you use there?", history)

    assert "observability" in expanded
    assert expanded.startswith("What did you use there?")


def test_terms_are_carried_from_the_visitor_not_the_twin() -> None:
    """One loose answer must not steer every later retrieval."""

    history = [
        {"role": "user", "content": "Tell me about your Kafka work"},
        {"role": "assistant", "content": "I have worked on Kubernetes and Terraform."},
    ]

    expanded = conversational_query("And there?", history)

    assert "kafka" in expanded
    assert "kubernetes" not in expanded.lower()


def test_a_follow_up_with_no_history_is_unchanged() -> None:
    assert conversational_query("What about that?", []) == "What about that?"


def test_follow_up_retrieves_what_the_bare_question_cannot(
    corpus: ProfileCorpus,
) -> None:
    """The behaviour this exists for, measured on the real corpus."""

    history = [{"role": "user", "content": "Tell me about your observability work"}]
    question = "What did you use there?"

    bare = corpus.retrieve(question)
    carried = corpus.retrieve(conversational_query(question, history))

    assert not any("Observability" in item.source for item in bare)
    assert any("Observability" in item.source for item in carried)
