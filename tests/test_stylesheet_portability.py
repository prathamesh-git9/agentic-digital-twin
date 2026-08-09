"""Vendor-prefix regressions the maintainer's own browser cannot show them.

This site leans on frosted-glass panels. `backdrop-filter` still needs its
`-webkit-` twin in Safari and every iOS browser, and the failure is invisible
in Chromium: the developer sees blur, a visitor on an iPhone sees a flat or
transparent panel over a photographic background, which is where text stops
being readable.

Four declarations had drifted without the prefix -- the sticky nav bar, the
onboarding modal backdrop, the copy buttons, and a `backdrop-filter: none`
reset that could not reset anything in Safari because only the unprefixed
property was cleared. Nothing in a Chromium-only test run could have caught it,
so the check is textual.
"""

from __future__ import annotations

import re
from pathlib import Path

STYLESHEET = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agentic_digital_twin"
    / "static"
    / "styles.css"
)
CSS = STYLESHEET.read_text(encoding="utf-8")
LINES = CSS.split("\n")

# Properties whose unprefixed form is still not enough for Safari/iOS.
PREFIX_REQUIRED = ("backdrop-filter",)


def _declarations(prop: str) -> list[tuple[int, str]]:
    pattern = re.compile(rf"(?<!-)\b{re.escape(prop)}\s*:")
    return [(i + 1, line.strip()) for i, line in enumerate(LINES) if pattern.search(line)]


def test_every_backdrop_filter_has_a_webkit_twin() -> None:
    missing: list[str] = []
    for prop in PREFIX_REQUIRED:
        for number, text in _declarations(prop):
            # The prefixed twin belongs in the same declaration block, which in
            # this stylesheet means the same line or one immediately around it.
            neighbourhood = "\n".join(LINES[max(0, number - 4) : number + 3])
            if f"-webkit-{prop}" not in neighbourhood:
                missing.append(f"line {number}: {text}")

    assert not missing, (
        "backdrop-filter without -webkit-backdrop-filter renders unblurred on "
        "Safari and every iOS browser:\n" + "\n".join(missing)
    )


def test_the_check_can_actually_find_a_declaration() -> None:
    """Guard the guard: a broken regex would make the test above pass vacuously."""

    assert len(_declarations("backdrop-filter")) >= 20


def test_heavy_glass_does_not_regress_into_an_opaque_white_box() -> None:
    values = re.findall(r"--g-fill-heavy:\s*rgba\([^)]*,\s*([0-9.]+)\)", CSS)
    assert values
    assert float(values[0]) <= 0.7


def test_mobile_chat_dock_stays_in_flow_and_has_no_veil() -> None:
    assert "body.has-thread .dock {\n    position: relative;" in CSS
    assert ".thread:not(:empty) ~ .dock::before {\n    display: none;" in CSS
