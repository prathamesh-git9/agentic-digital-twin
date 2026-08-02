from __future__ import annotations

import json

from digital_twin.grounding import ContextAssembler
from digital_twin.profile import EvidenceItem


def test_unconfirmed_research_is_structurally_absent_from_context() -> None:
    candidate = {
        "name": "Sarah Chen",
        "headline": "SECRETMARKER platform leader",
        "company": "Stripe",
        "source_link": "https://example.com/sarah",
        "confidence": 91,
        "why": ["SECRETMARKER observable signal"],
    }
    assembler = ContextAssembler()
    prompt = assembler.assemble(
        evidence=[EvidenceItem("CV › Summary", "Grounded CV fact")],
        messages=[],
        user_message="What is most relevant?",
        research_status="candidates",
        confirmed_candidate=candidate,
    )

    assert "SECRETMARKER" not in prompt
    assert "Sarah Chen" not in prompt
    assert "Stripe" not in prompt
    assert "CONFIRMED_VISITOR_CONTEXT_JSON" not in prompt


def test_confirmed_research_enters_context_as_untrusted_relevance_data() -> None:
    candidate = {
        "name": "Sarah Chen",
        "headline": "Platform leader",
        "company": "Stripe",
        "source_link": "https://example.com/sarah",
        "confidence": 91,
        "why": ["name tokens matched 2/2"],
    }
    prompt = ContextAssembler().assemble(
        evidence=[EvidenceItem("CV › Summary", "Grounded CV fact")],
        messages=[],
        user_message="What is most relevant?",
        research_status="confirmed",
        confirmed_candidate=candidate,
    )

    assert "CONFIRMED_VISITOR_CONTEXT_JSON" in prompt
    assert "Sarah Chen" in prompt
    assert "Stripe" in prompt
    assert json.dumps(candidate["why"])[2:-2] in prompt
