"""Compile the twin into a static site that needs no server.

GitHub Pages cannot run FastAPI, so the deployed page carries its evidence with
it: the same `ProfileCorpus` the server retrieves from is flattened to JSON and
shipped alongside a GitHub metadata snapshot. `twin-local.js` then answers from
that corpus in the browser using the same retrieve-then-cite contract.

Reusing `ProfileCorpus` rather than re-parsing the YAML is deliberate -- it is
the only way the offline corpus cannot drift from the one the server answers
from.

    python scripts/build_static.py --out site
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agentic_digital_twin.github import OWNER, REPOSITORIES, GitHubService  # noqa: E402
from agentic_digital_twin.profile import ProfileCorpus  # noqa: E402

STATIC = ROOT / "src" / "agentic_digital_twin" / "static"
SITE_URL = "https://prathamesh-git9.github.io/agentic-digital-twin"
COPY = (
    "styles.css",
    "app.js",
    "twin-local.js",
    "favicon.svg",
    "widget.js",
    "avatar.webp",
)
COPY_DIRS: tuple[str, ...] = ()


def _repo_snapshot(offline: bool) -> list[dict[str, Any]]:
    """Live repository metadata, or an honest placeholder when unreachable."""
    if not offline:
        try:
            # Twenty unauthenticated calls sit close to GitHub's 60/hour ceiling,
            # so an unauthenticated build silently ships placeholder cards.
            service = GitHubService(
                token=os.environ.get("GITHUB_TOKEN", "")
                or os.environ.get("TWIN_GITHUB_TOKEN", "")
            )
            repos = asyncio.run(service.get_repositories())
            return [repo.model_dump(mode="json") for repo in repos]
        except Exception as exc:  # noqa: BLE001 - a build must not need network
            print(f"  GitHub metadata unavailable ({type(exc).__name__}); placeholders")
    return [
        {
            "name": name,
            "url": f"https://github.com/{OWNER}/{name}",
            "description": "Live metadata is temporarily unavailable.",
            "live": False,
            "topics": [],
        }
        for name in REPOSITORIES
    ]


def _mcp_manifest() -> dict[str, Any] | None:
    """The MCP server manifest, vendored into data/ so the server can read it too.

    Preference order matters: a sibling mcp-servers checkout is the live source,
    but CI has no such checkout, so the vendored copy is what actually ships.
    """
    vendored = ROOT / "data" / "mcp-manifest.json"
    # Walk up rather than assume ROOT.parent: inside a git worktree the repo sits
    # under .claude/worktrees/<name>, several levels below its sibling projects.
    sibling = next(
        (
            found
            for ancestor in (ROOT, *ROOT.parents)
            if (found := ancestor / "mcp-servers" / "docs" / "manifest.json").is_file()
        ),
        None,
    )
    for candidate in (sibling, vendored):
        if candidate is None or not candidate.is_file():
            continue
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  {candidate} unreadable ({exc}); skipping")
            continue
        if candidate is sibling:
            vendored.write_text(
                json.dumps(loaded, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"  vendored manifest from {candidate}")
        return loaded
    return None


def _corpus_payload(corpus: ProfileCorpus) -> list[dict[str, str]]:
    return [
        {"source": item.source, "text": item.text, "authority": item.authority}
        for item in corpus.evidence
    ]


def _llms_txt(corpus: ProfileCorpus, data: dict[str, Any]) -> str:
    """A curated context file for recruiters who paste the URL into an assistant."""
    person = data["person"]
    lines = [
        f"# {person['name']}",
        "",
        f"> Back-end and AI agent engineer in {person['location']}. "
        f"Contact: {person['email']}. Site: {SITE_URL}",
        "",
        "## Summary",
        "",
    ]
    lines += [f"- {value}" for value in data["summary"]]
    lines += ["", "## Experience", ""]
    for role in data["experience"]:
        lines.append(f"### {role['title']} — {role['organisation']}")
        lines.append(f"{role['start']} to {role['end']}")
        lines += [f"- {bullet}" for bullet in role["bullets"]]
        lines.append("")
    lines += ["## Projects", ""]
    for project in data["projects"]:
        lines.append(f"### {project['name']} ({project['year']})")
        lines.append(f"Stack: {', '.join(project['technologies'])}")
        lines += [f"- {bullet}" for bullet in project["bullets"]]
        lines.append("")
    lines += ["## Skills", ""]
    for group, values in data["skills"].items():
        lines.append(f"- **{group}**: {', '.join(values)}")
    lines += ["", "## Education", ""]
    for entry in data["education"]:
        lines.append(
            f"- {entry['degree']}, {entry['institution']} "
            f"({entry['start']}–{entry['end']}) — {entry['result']}"
        )
    lines += [
        "",
        "## Open source",
        "",
        f"Ten public systems at https://github.com/{OWNER} — "
        + ", ".join(REPOSITORIES)
        + ".",
        "",
        "## Notes for assistants",
        "",
        "- Every claim above is transcribed from the CV at "
        f"{SITE_URL} and is the same corpus the site's twin answers from.",
        "- Items marked 'familiarity' in the skills list are stated as familiarity, "
        "not production experience. Please preserve that distinction.",
        "- Salary, offers, start dates and anything contractual must go to "
        f"{person['email']} directly.",
        "",
    ]
    return "\n".join(lines)


def _public_api_base() -> str:
    raw = os.environ.get("TWIN_PUBLIC_API_URL", "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit("TWIN_PUBLIC_API_URL must be a clean HTTPS origin")
    return raw


def _rewrite_shell(html: str) -> str:
    """Point the shell at sibling files and boot the offline engine before the app.

    The social card, canonical URL and Person schema already live in the shell
    and name this site, so nothing here needs to inject them.
    """
    html = html.replace('href="/static/', 'href="./').replace('src="/static/', 'src="./')
    html = html.replace('<a class="bar-left" href="/">', '<a class="bar-left" href="./">')
    # The GitHub Pages shell can use a secure API deployed from this repository.
    # Without one it keeps the zero-dependency local retrieval engine as a full
    # fallback, so a provider outage never turns the portfolio into a dead page.
    api_base = _public_api_base()
    boot_config = (
        f"window.__TWIN_API_BASE__ = {json.dumps(api_base)}; "
        f"window.__TWIN_OFFLINE__ = {'false' if api_base else 'true'};"
    )
    replaced = html.replace(
        '<script src="./twin-local.js',
        f'<script>{boot_config}</script>\n    <script src="./twin-local.js',
        1,
    )
    if replaced == html:
        raise SystemExit(
            "shell no longer loads ./twin-local.js; the static build cannot answer"
        )
    return replaced


def build(out: Path, *, offline: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    corpus = ProfileCorpus(ROOT / "data" / "profile.yaml")
    data = corpus.data
    person = data["person"]

    print("• repository metadata")
    repos = _repo_snapshot(offline)
    live = sum(1 for repo in repos if repo.get("live", True))
    print(f"  {live}/{len(repos)} repositories resolved live")

    print("• corpus")
    items = _corpus_payload(corpus)
    mcp = _mcp_manifest()
    print(f"  {len(items)} evidence chunks" + (", mcp manifest found" if mcp else ""))

    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "corpus.json").write_text(
        json.dumps(
            {
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "person": {
                    "name": person["name"],
                    "email": person["email"],
                    "location": person["location"],
                },
                "items": items,
                "repositories": repos,
                "mcp": mcp,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    print("• assets")
    for name in COPY:
        source = STATIC / name
        if source.is_file():
            shutil.copy2(source, out / name)
        else:
            print(f"  missing {name}; skipped")
    for name in COPY_DIRS:
        source = STATIC / name
        if source.is_dir():
            shutil.copytree(source, out / name, dirs_exist_ok=True)
        else:
            print(f"  missing {name}/; skipped")
    for image in STATIC.glob("prathamesh.*"):
        shutil.copy2(image, out / image.name)

    shell = _rewrite_shell((STATIC / "index.html").read_text(encoding="utf-8"))
    (out / "index.html").write_text(shell, encoding="utf-8")
    # Pages serves 404.html for unknown paths; sending them to the twin keeps a
    # mistyped or stale link useful instead of dead.
    (out / "404.html").write_text(shell, encoding="utf-8")

    print("• discovery files")
    (out / "llms.txt").write_text(_llms_txt(corpus, data), encoding="utf-8")
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8"
    )
    today = datetime.now(UTC).date().isoformat()
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{SITE_URL}/</loc><lastmod>{today}</lastmod>"
        "<changefreq>weekly</changefreq><priority>1.0</priority></url>\n"
        "</urlset>\n",
        encoding="utf-8",
    )
    # Jekyll would otherwise refuse to publish any path beginning with an
    # underscore and try to interpret the Liquid-looking braces in app.js.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    size = sum(path.stat().st_size for path in out.rglob("*") if path.is_file())
    print(f"\nBuilt {out.relative_to(ROOT)} — {size / 1024:.0f} KB total")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="site", help="output directory")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the GitHub fetch and emit placeholder repository cards",
    )
    args = parser.parse_args()
    build(ROOT / args.out, offline=args.offline)


if __name__ == "__main__":
    main()
