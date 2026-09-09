"""Exercise the built chat in a browser: python scripts/check_chat_ui.py site.

Requires the optional Playwright installation and Edge on Windows, or Playwright
Chromium elsewhere. The static site is served only on an ephemeral loopback port.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path)
    parser.add_argument("--out", type=Path, default=Path("artifacts/runtime/chat-ui"))
    args = parser.parse_args()
    if not (args.site / "index.html").is_file():
        parser.error("Build the static site before running browser checks.")
    args.out.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(QuietHandler, directory=str(args.site.resolve()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    report = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                **({"channel": "msedge"} if sys.platform == "win32" else {})
            )
            for name, width, height in (
                ("desktop", 1440, 900),
                ("tablet", 768, 1024),
                ("mobile", 390, 844),
                ("small", 320, 740),
            ):
                page = browser.new_page(
                    viewport={"width": width, "height": height}, reduced_motion="reduce"
                )
                errors: list[str] = []
                page.on("pageerror", lambda error, found=errors: found.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                page.locator("#starters button").first.wait_for()
                composer = page.locator("#composer-input")
                composer.fill("a\n" * 10)
                tall = composer.bounding_box()["height"]
                composer.fill("b" * 20)
                short = composer.bounding_box()["height"]
                assert short < tall / 2, (name, "replacement did not shrink", tall, short)

                # Enter finalizing an IME composition must not submit a question.
                composer.dispatch_event(
                    "keydown", {"key": "Enter", "isComposing": True, "keyCode": 229}
                )
                assert page.locator(".turn").count() == 0, "IME submitted a partial draft"
                composer.fill("")
                page.locator("#starters button").first.click()
                page.wait_for_function("!document.querySelector('#send-button').disabled")
                page.wait_for_function(
                    """() => {
                      const answer = document.querySelector('.turn.twin');
                      const nav = document.querySelector('.bar-island');
                      return answer.getBoundingClientRect().top
                        >= nav.getBoundingClientRect().bottom + 4;
                    }"""
                )
                assert page.locator(".turn.twin .cite").count() > 0
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.evaluate(
                    "() => new Promise(resolve => requestAnimationFrame("
                    "() => requestAnimationFrame(resolve)))"
                )
                page.screenshot(path=str(args.out / f"{name}-answer.png"))
                page.locator(".turn.twin [data-copy]").first.click(timeout=5000)

                composer.fill("a\n" * 12)
                if width <= 700:
                    page.wait_for_function(
                        """() => parseFloat(getComputedStyle(
                          document.querySelector('#messages')).paddingBottom)
                          > document.querySelector('.dock').getBoundingClientRect().height
                        """
                    )
                composer.fill("Tell me about his work on AI agents.")
                # Hold a reply so user interactions can occur while it is pending.
                page.evaluate(
                    """() => {
                      const engine = window.__TWIN_LOCAL__;
                      const original = engine.handle;
                      engine.handle = async (path, options) => {
                        if (path.endsWith('/chat')) {
                          await new Promise(resolve => {
                            window.releaseReply = resolve;
                          });
                        }
                        return original(path, options);
                      };
                    }"""
                )
                composer.press("Enter")
                page.wait_for_function("typeof window.releaseReply === 'function'")
                page.mouse.wheel(0, 700)
                page.locator("#retrieval-input").focus()
                page.evaluate("window.releaseReply()")
                page.wait_for_function("!document.querySelector('#send-button').disabled")
                assert page.evaluate("document.activeElement.id") == "retrieval-input"
                assert page.locator(".turn.twin .text").count() == 2
                assert not errors, errors
                report.append(
                    {
                        "viewport": name,
                        "size": [width, height],
                        "replacement_heights": [tall, short],
                        "answer_below_header": True,
                        "ime_does_not_send": True,
                        "mobile_space_tracks_composer": True,
                        "delayed_reply_preserves_focus": True,
                        "chat_and_citations": True,
                        "javascript_errors": errors,
                    }
                )
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    output = json.dumps(report, indent=2)
    (args.out / "report.json").write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
