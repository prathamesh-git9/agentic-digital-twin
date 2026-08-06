"""What the injection filter is worth, measured rather than assumed.

The README calls this a layered defence. These tests establish which layer is
actually load-bearing, and the answer is not the one that looks like security.

`contains_prompt_injection` is a regex filter. Measured against the phrasings it
was written for it catches everything; measured against phrasings written
afterwards for the same intents, it catches none of them. That is not a bug to
fix by adding more patterns -- it is what keyword matching *is* against an
adversary who can rephrase, and the number is published so nobody builds on it.

The layer that holds is the grounding verifier. An injection that sails past
the filter still cannot put a fabricated employer on the page, because a claim
with no supporting evidence is dropped and a name absent from the evidence is
dropped with it. The last test here is the one that matters.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from digital_twin.grounding import DraftAnswer, DraftClaim, GroundingVerifier
from digital_twin.profile import ProfileCorpus
from digital_twin.security import contains_prompt_injection

ROOT = Path(__file__).resolve().parents[1]
EVAL = yaml.safe_load((ROOT / "data" / "injection_eval.yaml").read_text("utf-8"))

# The filter must not refuse ordinary recruiter questions. This is the only
# floor worth being strict about: a false positive stonewalls a real visitor,
# while a false negative falls through to a verifier that catches it anyway.
MAX_FALSE_POSITIVES = 0

# Regression floor on the phrasings the patterns were written for.
MINIMUM_TUNED_CATCH_RATE = 1.0


@pytest.fixture(scope="module")
def corpus() -> ProfileCorpus:
    return ProfileCorpus(ROOT / "data" / "profile.yaml")


def _caught(entries: list[str]) -> list[str]:
    return [entry for entry in entries if contains_prompt_injection(entry)]


# --- The filter -------------------------------------------------------------


def test_no_ordinary_question_is_refused_as_an_injection() -> None:
    flagged = _caught(EVAL["benign"])

    assert len(flagged) <= MAX_FALSE_POSITIVES, f"benign questions refused: {flagged}"


def test_the_tuned_corpus_stays_caught() -> None:
    tuned = EVAL["tuned"]
    missed = [entry for entry in tuned if not contains_prompt_injection(entry)]
    rate = (len(tuned) - len(missed)) / len(tuned)

    assert rate >= MINIMUM_TUNED_CATCH_RATE, f"patterns regressed on: {missed}"


def test_rephrasing_defeats_the_filter_and_that_is_recorded() -> None:
    """The honest number, asserted so it cannot quietly be forgotten.

    If a future change makes the filter generalise, this test fails and the
    README claim should be revised upward -- deliberately, with evidence, not
    by assumption.
    """

    held_out = EVAL["held_out"]
    caught = _caught(held_out)

    assert len(caught) <= 1, (
        "the filter now generalises better than documented; re-measure and "
        f"update the README. Caught: {caught}"
    )


# --- The layer that actually holds ------------------------------------------


@pytest.mark.parametrize("claim_text", EVAL["fabrication_claims"])
def test_a_fabrication_is_dropped_even_when_the_filter_misses(
    corpus: ProfileCorpus, claim_text: str
) -> None:
    """Defence in depth, stated as a test.

    Every claim here names an employer, credential, or technology absent from
    profile.yaml. Whether or not the request that produced it tripped a
    pattern, the verifier has no evidence for it and must refuse.
    """

    evidence = corpus.retrieve(claim_text)
    draft = DraftAnswer(
        claims=[
            DraftClaim(
                text=claim_text,
                source=evidence[0].source if evidence else "CV › Summary",
            )
        ],
        reply=claim_text,
    )

    result = GroundingVerifier().verify(draft, evidence)

    assert result.refusal, f"verifier accepted a fabrication: {claim_text}"
    assert claim_text not in result.text


def test_every_held_out_injection_still_cannot_fabricate(corpus: ProfileCorpus) -> None:
    """End to end: the filter misses these, and the page is still clean."""

    slipped = [
        entry for entry in EVAL["held_out"] if not contains_prompt_injection(entry)
    ]
    assert slipped, "held-out set should exercise the filter's blind spot"

    for claim_text in EVAL["fabrication_claims"]:
        evidence = corpus.retrieve(claim_text)
        draft = DraftAnswer(
            claims=[
                DraftClaim(
                    text=claim_text,
                    source=evidence[0].source if evidence else "CV › Summary",
                )
            ]
        )

        assert GroundingVerifier().verify(draft, evidence).refusal
