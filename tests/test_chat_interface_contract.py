from __future__ import annotations

import re
from pathlib import Path

APP = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "digital_twin"
    / "static"
    / "app.js"
).read_text(encoding="utf-8")


def test_boot_has_a_fast_local_fallback_while_live_ai_warms() -> None:
    match = re.search(r"BOOT_SOFT_DEADLINE_MS\s*=\s*([0-9_]+)", APP)
    assert match
    assert int(match.group(1).replace("_", "")) <= 3_000
    assert 'state.transport = "local"' in APP
    assert "deferredRemote" in APP
    assert 'toast("Live AI connected.")' in APP


def test_local_fallback_does_not_bypass_api_rate_or_budget_rejections() -> None:
    assert "error.httpStatus = res.status" in APP
    assert "if (error.httpStatus) throw error" in APP


def test_chat_cannot_leave_the_composer_busy_for_over_a_minute() -> None:
    match = re.search(r"setTimeout\(\(\) => controller\.abort\(\),\s*([0-9_]+)\)", APP)
    assert match
    assert int(match.group(1).replace("_", "")) <= 35_000
    assert 'el.composer.removeAttribute("aria-busy")' in APP


def test_mobile_turns_align_to_their_start() -> None:
    assert 'matchMedia("(max-width: 700px)").matches ? "start" : "nearest"' in APP
