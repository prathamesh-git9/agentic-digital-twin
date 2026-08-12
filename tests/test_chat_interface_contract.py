from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "agentic_digital_twin" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
CONFIG = (ROOT / "src" / "agentic_digital_twin" / "config.py").read_text(encoding="utf-8")
PROFILE = (ROOT / "data" / "profile.yaml").read_text(encoding="utf-8")
GROUNDING = (ROOT / "src" / "agentic_digital_twin" / "grounding.py").read_text(
    encoding="utf-8"
)
MAIN = (ROOT / "src" / "agentic_digital_twin" / "main.py").read_text(encoding="utf-8")
WIDGET = (STATIC / "widget.js").read_text(encoding="utf-8")
BUILD_STATIC = (ROOT / "scripts" / "build_static.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
PACKAGE = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_boot_has_a_fast_local_fallback_while_live_ai_warms() -> None:
    match = re.search(r"BOOT_SOFT_DEADLINE_MS\s*=\s*([0-9_]+)", APP)
    assert match
    assert int(match.group(1).replace("_", "")) <= 3_000
    assert 'state.transport = "local"' in APP
    assert "deferredRemote" in APP
    assert 'toast("Live AI connected.")' in APP


def test_static_build_excludes_unused_cloud_art_and_aws_icons() -> None:
    assert "COPY_DIRS: tuple[str, ...] = ()" in BUILD_STATIC
    assert "shutil.copytree(source, out / name, dirs_exist_ok=True)" in BUILD_STATIC
    assert '"cloud-infrastructure.webp"' not in BUILD_STATIC


def _scene() -> str:
    """The scene block with comments stripped.

    The comments in it name the very properties these checks ban, because they
    explain why those properties are banned. Matching on them makes the tests
    fail on their own documentation.
    """

    return _section("---------- scene ----------")


def _range() -> str:
    """The range block, same treatment."""

    return _section("---------- range ----------")


def _section(marker: str) -> str:
    # Split on the marker before stripping comments: the marker is itself a
    # comment, so stripping first deletes it and the slice runs to end of file.
    block = CSS.split(marker, maxsplit=1)[1].split("---------- bar")[0]
    return re.sub(r"/\*.*?\*/", "", block, flags=re.S)


def test_background_is_code_native_and_theme_aware() -> None:
    themed = ("--sky-high:", "--dawn:", "--dawn-hot:", "--facet:", "--snow:")
    for token in themed:
        assert CSS.count(token) == 2, f"{token} needs a light and a dark value"

    # Every range carries a lit value and a base value, and the gradient between
    # them is what gives it volume. A range with only one of the pair is a flat
    # fill, which is the version of this that read as a chart axis.
    for n in (1, 2, 3, 4):
        assert CSS.count(f"--ridge-{n}:") == 2, f"--ridge-{n} needs both themes"
        assert CSS.count(f"--ridge-{n}-lit:") == 2, f"--ridge-{n}-lit needs both themes"
        assert f"url(#lit{n})" in CSS, f"range {n} must use its gradient, not a flat fill"

    assert ".cloud-art" not in CSS
    assert "cloud-infrastructure.webp" not in HTML

    # The landscape is drawn, not downloaded.
    assert HTML.count('class="ridge ') == 4
    assert HTML.count("<linearGradient") == 4
    scene = _scene()
    assert 'url("data:image/svg+xml' in scene
    assert not re.search(r"url\([^)]*\.(png|jpe?g|webp|gif|svg|woff2?)", scene), (
        "the scene must not download anything"
    )


def test_the_picture_scrolls_but_the_tint_does_not() -> None:
    """The two background layers have different jobs and different positioning.

    `.scene` is fixed and deliberately plain -- it exists so the glass panels
    below the fold have a tinted ground to refract, because glass frosting flat
    white produces flat white. `.range` is the illustration, and it is anchored
    to the first screen: a horizon pinned to the bottom of the viewport sits
    behind body copy for the rest of the document.

    Neither may use `background-attachment: fixed`, which repaints on every
    scroll frame and is banned in the depth system for that reason.
    """

    scene = _scene()
    assert "position: fixed; inset: 0" in scene
    assert "background-attachment" not in scene

    rng = _range()
    assert "position: absolute" in rng and "height: 100dvh" in rng
    assert "position: fixed" not in rng


def test_the_background_cannot_cost_a_frame() -> None:
    """Nothing in the background animates, and that is a measured decision.

    This page has had continuous render lag removed once already, and it is
    made of glass, so a moving backdrop is charged for twice: once to
    composite the layer, and again for every `backdrop-filter` surface above
    it that must re-filter a backdrop that changed. Measured while scrolling
    the full document at 1440x900, median frame / frames over 32ms:

        3 moving layers  33.3ms / 95     2 layers  16.7ms / 76
        1 moving layer   16.7ms / 27     0 layers  16.7ms / 29

    The budget is one layer at most. The illustration spends it on nothing,
    which is why the ranges can carry four gradient-lit silhouettes, shadow
    facets and snow caps without costing anything.
    """

    background = _scene() + _range()
    moving = re.findall(r"animation:\s*(?!none)[a-z-]+", background)
    assert len(moving) <= 1, f"at most one animated layer, found {moving}"
    # A blur filter over a large layer is the other way this gets expensive.
    assert "filter: blur" not in background


def test_premium_depth_system_shapes_every_portfolio_section() -> None:
    premium = CSS.split("PREMIUM DEPTH SYSTEM", maxsplit=1)[1]
    assert "styles.css?v=76" in HTML
    assert HTML.count('class="chapter-meta"') == 6
    assert ".bands > .band" in premium
    assert "counter-increment: chapter" in premium
    assert ".projects" in premium and "grid-template-columns: repeat(3" in premium
    assert "#skills .skillcard:nth-child(3)" in premium
    assert ".retrieval::before" in premium
    assert "background-attachment: fixed" not in premium
    assert "backdrop-filter: none" in premium


def test_agentic_digital_twin_brand_and_frameworks_are_consistent() -> None:
    public_copy = "\n".join((HTML, APP, WIDGET, GROUNDING, MAIN, CONFIG, README, PACKAGE))
    assert "Agentic Digital Twin" in HTML
    assert "Ask his agentic digital twin anything." in HTML
    assert "ASK AGENTIC DIGITAL TWIN" in WIDGET
    assert "agentic digital twin" in README
    assert "agentic digital twin" in PACKAGE
    assert 'name = "prathamesh-agentic-digital-twin"' in PACKAGE
    assert "github.io/agentic-digital-twin/" in HTML
    assert "github.io/digital-twin/" not in HTML
    assert not (ROOT / "src" / "digital_twin").exists()
    assert "AI digital twin" not in public_copy
    assert "recruiter-facing AI twin" not in public_copy
    assert "Ask his digital twin" not in public_copy
    assert "Prathamesh Kalamkar's digital twin" not in public_copy
    assert "app.js?v=60" in HTML
    for framework in ("LangChain", "LangGraph"):
        assert HTML.count(framework) >= 4
        assert framework in PROFILE


def test_chat_renders_the_real_agent_plan_instead_of_a_decorative_status() -> None:
    assert 'src.addEventListener("agent.plan"' in APP
    assert 'src.addEventListener("agent.phase"' in APP
    assert "agentRunPanel(meta.agent_run, steps)" in APP
    assert "phaseRows(run.steps)" in APP
    assert ".phase-track" in CSS
    assert ".agent-live" in CSS
    assert "goal → plan → tools → evidence → verify" in HTML


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


def test_clawd_and_cloud_architecture_are_contained_and_lightweight() -> None:
    assert 'class="clawd-float"' in HTML
    assert 'aria-label="Clawd, the animated Claude Code mascot"' in HTML
    dock_start = HTML.index('class="dock reveal d2"')
    clawd_start = HTML.index('class="clawd-float"')
    composer_start = HTML.index('<form id="composer">')
    assert dock_start < clawd_start < composer_start
    header = HTML.split('<header class="bar">', maxsplit=1)[1].split(
        "</header>", maxsplit=1
    )[0]
    assert "clawd-float" not in header
    for pose in ("clawd-default", "clawd-look-right", "clawd-look-left", "clawd-arms-up"):
        assert pose in HTML
    assert 'viewBox="0 0 18 5"' in HTML
    assert 'class="agent-showcase' not in HTML
    assert 'class="cloud-art"' not in HTML
    assert "/static/cloud-infrastructure.webp" not in HTML
    assert not (STATIC / "cloud-infrastructure.webp").exists()
    assert 'class="aws-logo-clouds"' not in HTML
    assert 'class="aws-logo-cloud ' not in HTML
    assert "/static/aws/aws-cloud.svg" not in HTML
    assert 'class="aws-reference"' not in HTML
    assert 'class="aws-ref-' not in HTML
    assert 'class="cloud-service' not in HTML
    assert 'class="cloud-backbone"' not in HTML
    assert "request / response" not in HTML
    assert "evidence + state" not in HTML
    assert 'class="infra-sky"' not in HTML
    assert 'class="infra-component' not in HTML
    assert HTML.count("data-system=") == 7
    assert HTML.count('class="system-chip"') == 7
    assert 'class="runtime-map"' in HTML
    assert 'class="foot-runtime"' in HTML
    assert ".clawd-float" in CSS
    assert "fill: #d97757" in CSS
    assert "@keyframes clawd-look-left" in CSS
    assert "@keyframes clawd-track" in CSS
    assert "translate3d(var(--clawd-travel)" in CSS
    assert "grid-template-columns: 230px minmax(0, 720px) 230px" in CSS
    assert HTML.count("<animateMotion") == 0
    assert HTML.count('class="cloud-packet') == 0
    assert "animation: cloud-route" not in CSS
    assert "animation: cloud-node-float" not in CSS
    assert ".cloud-art" not in CSS
    assert ".aws-logo-clouds" not in CSS
    assert ".aws-logo-cloud {" not in CSS
    assert ".dock { margin-top: 32px; }" in CSS
    assert ".aws-reference {" not in CSS
    assert ".aws-ref-vpc {" not in CSS
    assert ".cloud-service" not in CSS
    assert ".cloud-backbone" not in CSS
    assert ".aws-zone" not in CSS
    assert 'class="aws-zone' not in HTML
    assert ".band > .band-head { grid-column: 1; position: sticky;" in CSS
    assert "  .band-head { grid-column: 1; position: sticky;" not in CSS
    assert ".band[data-system]::after" in CSS
    assert ".band[data-system] .band-head::after { display: none; }" in CSS
    assert ".bands" in CSS and "--infra-line-soft" in CSS
    assert "content-visibility: auto" in CSS
    assert "background-attachment: fixed" not in CSS
    assert "progress.style.transform" in APP
    assert "const queuePaint" in APP
    assert "@media (prefers-reduced-motion: reduce)" in CSS


def test_mobile_starters_use_a_bounded_grid_instead_of_a_clipped_scroller() -> None:
    repair_css = CSS.split("UI REPAIR CONTRACT", maxsplit=1)[1]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in repair_css
    assert "overflow: visible" in repair_css
    assert ".dock, .suggestions { min-width: 0; max-width: 100%; }" in repair_css
    assert (
        "body.has-thread .suggestions, body.has-thread .dock-note { display: none; }"
        in repair_css
    )
    assert "body.has-thread .dock" in repair_css
    assert "position: fixed" in repair_css
