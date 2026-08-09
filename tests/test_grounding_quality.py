"""Measured quality of the claim verifier.

The verifier is the last thing between a fluent model and a recruiter reading
something Prathamesh never did, and until this file existed nothing checked
whether it worked. It did not: at the shipped 0.34 overlap threshold it
accepted 12 of the 20 labelled fabrications here, including "At Google, he
refactored nearly 50% of a legacy authentication module."

The two errors are not symmetric. A false refusal costs a refusal on an
answerable question. A false acceptance puts an invented employer in front of a
hiring manager. The gate below is set accordingly: recall must stay perfect on
this set, and precision may not slip.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentic_digital_twin.grounding import (
    SUPPORT_THRESHOLD,
    DraftAnswer,
    DraftClaim,
    GroundingVerifier,
)
from agentic_digital_twin.profile import EvidenceItem

ROOT = Path(__file__).resolve().parents[1]
EVAL = yaml.safe_load((ROOT / "data" / "grounding_eval.yaml").read_text("utf-8"))
EVIDENCE: dict[str, str] = EVAL["evidence"]
CASES = EVAL["cases"]

# Recorded against the committed corpus. Floors, not targets.
MINIMUM_PRECISION = 0.94
MINIMUM_RECALL = 1.0

# The one labelled fabrication this design still accepts. Named so the gap is
# visible in the suite instead of hiding inside a rounded metric.
KNOWN_FALSE_ACCEPT = {"invented-ownership"}


def _accepts(claim: str, evidence: str) -> bool:
    """Run one claim through both verifier layers, as verify() does."""

    if GroundingVerifier.unsupported_entities(claim, evidence):
        return False
    return GroundingVerifier._supported(claim, evidence)


def _confusion() -> tuple[int, int, int, int, list[str], list[str]]:
    true_positive = false_positive = true_negative = false_negative = 0
    false_accepts: list[str] = []
    false_refusals: list[str] = []
    for case in CASES:
        accepted = _accepts(case["claim"], EVIDENCE[case["evidence"]])
        if accepted and case["accept"]:
            true_positive += 1
        elif accepted:
            false_positive += 1
            false_accepts.append(case["id"])
        elif case["accept"]:
            false_negative += 1
            false_refusals.append(case["id"])
        else:
            true_negative += 1
    return (
        true_positive,
        false_positive,
        true_negative,
        false_negative,
        false_accepts,
        false_refusals,
    )


def test_verifier_precision_and_recall() -> None:
    tp, fp, _, fn, false_accepts, false_refusals = _confusion()
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0

    assert recall >= MINIMUM_RECALL, f"refused supported claims: {false_refusals}"
    assert precision >= MINIMUM_PRECISION, f"accepted fabrications: {false_accepts}"


def test_the_only_accepted_fabrication_is_the_known_one() -> None:
    """Pin the gap. If this fails, either it was fixed or a new one appeared."""

    _, _, _, _, false_accepts, _ = _confusion()

    assert set(false_accepts) == KNOWN_FALSE_ACCEPT


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["accept"]], ids=lambda case: case["id"]
)
def test_supported_claims_survive(case) -> None:
    assert _accepts(case["claim"], EVIDENCE[case["evidence"]])


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if not c["accept"] and c["id"] not in KNOWN_FALSE_ACCEPT],
    ids=lambda case: case["id"],
)
def test_fabricated_claims_are_rejected(case) -> None:
    assert not _accepts(case["claim"], EVIDENCE[case["evidence"]])


# --- The entity layer -------------------------------------------------------


def test_named_entities_ignore_sentence_openers() -> None:
    found = GroundingVerifier.named_entities("He built services with Kafka and Java.")

    assert found == {"kafka", "java"}
    assert "he" not in found  # capitalised by grammar, not because it names anything
    assert "built" not in found


def test_a_name_in_the_opening_position_is_not_detected() -> None:
    """A known limitation, pinned so it stays a decision rather than a surprise.

    Sentence-initial capitalisation is ambiguous: "Building reliable services"
    and "Google paid for it" look identical to this rule. Flagging that
    position would refuse ordinary prose, so it is left alone. The fabrications
    a model actually produces are appended mid-sentence -- "At Google, ...",
    "in Rust and Kubernetes", "and Gemini fine-tuning" -- which is the position
    that is checked.
    """

    assert GroundingVerifier.named_entities("Google paid for the migration.") == set()
    assert GroundingVerifier.named_entities("He worked at Google.") == {"google"}


def test_an_invented_employer_is_rejected_despite_heavy_overlap() -> None:
    """The failure that motivated this layer, kept as a named test."""

    evidence = EVIDENCE["auth"]
    honest = "He refactored nearly 50% of a legacy authentication module."
    fabricated = "At Google, he refactored nearly 50% of a legacy authentication module."

    assert _accepts(honest, evidence)
    assert not _accepts(fabricated, evidence)
    assert GroundingVerifier.unsupported_entities(fabricated, evidence) == {"google"}


def test_entities_may_be_supported_by_any_retrieved_evidence() -> None:
    """A claim citing one bullet may name something a sibling bullet establishes."""

    evidence = [
        EvidenceItem("CV › Skills › Languages", "Java, Python, SQL, Bash."),
        EvidenceItem(
            "CV › Experience › matriXploit Pvt. Ltd. › Software Engineer",
            "Built and maintained back-end applications using Java and Spring Boot.",
        ),
    ]
    draft = DraftAnswer(
        claims=[
            DraftClaim(
                text="He builds back-end applications with Java and Spring Boot.",
                source="CV › Experience › matriXploit Pvt. Ltd. › Software Engineer",
            )
        ],
        reply="I build back-end applications with Java and Spring Boot.",
    )

    result = GroundingVerifier().verify(draft, evidence)

    assert result.grounded
    assert not result.refusal


def test_a_fabricated_name_in_the_reply_falls_back_to_claims() -> None:
    evidence = [
        EvidenceItem(
            "CV › Experience › matriXploit Pvt. Ltd. › Software Engineer",
            "Built and maintained back-end applications using Java and Spring Boot.",
        )
    ]
    draft = DraftAnswer(
        claims=[
            DraftClaim(
                text="He built back-end applications using Java and Spring Boot.",
                source="CV › Experience › matriXploit Pvt. Ltd. › Software Engineer",
            )
        ],
        reply="I built back-end applications at Google using Java and Spring Boot.",
    )

    result = GroundingVerifier().verify(draft, evidence)

    assert result.grounded
    assert "Google" not in result.text


def test_threshold_is_the_measured_one() -> None:
    # A silent revert to the old value would restore the fabrications above.
    assert SUPPORT_THRESHOLD == 0.65
