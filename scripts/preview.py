#!/usr/bin/env python
"""Load a URL in headless Chromium and save a PNG screenshot.

Usage:
    ./venv/bin/python scripts/preview.py [url] [out_path]

Defaults: http://127.0.0.1:7781 → /tmp/games-preview.png
"""
from __future__ import annotations

import sys
from playwright.sync_api import sync_playwright


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7781/bridge/"
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/games-preview.png"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 720})
        page.goto(url)
        page.wait_for_load_state("networkidle")
        page.screenshot(path=out, full_page=True)
        browser.close()

    print(out)


if __name__ == "__main__":
    main()
