from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "digital_twin" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
CONFIG = (ROOT / "src" / "digital_twin" / "config.py").read_text(encoding="utf-8")


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


def test_owner_linkedin_profile_is_canonical_everywhere() -> None:
    profile = "https://www.linkedin.com/in/prathamesh-kalamkar/"
    assert HTML.count(profile) == 3
    assert profile in APP
    assert profile in CONFIG
    assert "prathameshkalamkar" not in HTML + APP + CONFIG


def test_cloud_architecture_visuals_are_site_wide_and_lightweight() -> None:
    for component in (
        "edge-node",
        "api-node",
        "agent-node",
        "data-node",
        "tools-node",
        "trace-node",
    ):
        assert component in HTML
    assert HTML.count("data-system=") == 7
    assert 'class="runtime-map"' in HTML
    assert 'class="foot-runtime"' in HTML
    assert ".infra-component" in CSS
    assert ".bands" in CSS and "--infra-line-soft" in CSS
    assert "@media (prefers-reduced-motion: reduce)" in CSS
