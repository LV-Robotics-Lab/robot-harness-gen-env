#!/usr/bin/env python3
"""Render and verify all four static report bundles with Playwright."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
REPORTS = {
    "sceneagent": ROOT / "reports/sceneagent_selection2env",
    "text2env": ROOT / "reports/text2env_literature_review",
    "openxsim": ROOT / "reports/openxsim_command_loop",
    "harness": ROOT / "reports/embodied_harness",
}


def local_link_targets(page: Page, report: Path) -> list[Path]:
    targets = []
    for href in page.locator("a[href]").evaluate_all("nodes => nodes.map(node => node.getAttribute('href'))"):
        if not href or href.startswith(("#", "http://", "https://", "mailto:")):
            continue
        parsed = urlparse(href)
        if parsed.scheme and parsed.scheme != "file":
            continue
        path = unquote(parsed.path)
        target = Path(path) if parsed.scheme == "file" else report / path
        targets.append(target.resolve())
    return targets


def inspect_page(page: Page, report: Path) -> dict[str, object]:
    missing_links = [str(path) for path in local_link_targets(page, report) if not path.exists()]
    images = page.locator("img").evaluate_all(
        "nodes => nodes.map(node => ({src: node.getAttribute('src'), width: node.naturalWidth, height: node.naturalHeight, complete: node.complete}))"
    )
    broken_images = [row for row in images if not row["complete"] or row["width"] <= 0 or row["height"] <= 0]
    videos = page.locator("video").evaluate_all(
        "nodes => nodes.map(node => { const rect = node.getBoundingClientRect(); return ({src: node.currentSrc || node.getAttribute('src') || node.querySelector('source')?.getAttribute('src'), poster: node.poster, readyState: node.readyState, duration: node.duration, width: node.videoWidth, height: node.videoHeight, renderedWidth: rect.width, renderedHeight: rect.height, error: node.error && node.error.message}); })"
    )
    broken_videos = [
        row
        for row in videos
        if row["readyState"] < 1 or not row["duration"] or row["duration"] <= 0 or row["width"] <= 0 or row["height"] <= 0 or row["error"]
    ]
    overflow = page.evaluate(
        "() => ({scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth})"
    )
    if missing_links:
        raise AssertionError(f"Missing local links: {missing_links}")
    if broken_images:
        raise AssertionError(f"Broken images: {broken_images}")
    if broken_videos:
        raise AssertionError(f"Broken videos: {broken_videos}")
    if overflow["scrollWidth"] > overflow["clientWidth"] + 1:
        raise AssertionError(f"Page overflow: {overflow}")
    return {
        "local_link_count": len(local_link_targets(page, report)),
        "image_count": len(images),
        "video_count": len(videos),
        "videos": videos,
        "overflow": overflow,
    }


def qa_report(browser, report_id: str, report: Path) -> dict[str, object]:
    index = report / "index.html"
    if not index.is_file():
        raise FileNotFoundError(index)
    qa_dir = report / "qa"
    qa_dir.mkdir(exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    viewport_results: dict[str, object] = {}
    for viewport_id, viewport in (
        ("desktop", {"width": 1440, "height": 1000}),
        ("mobile", {"width": 390, "height": 844}),
    ):
        page = browser.new_page(viewport=viewport)
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(index.resolve().as_uri(), wait_until="load")
        page.locator("img").evaluate_all("nodes => nodes.forEach(node => { node.loading = 'eager'; })")
        page.evaluate(
            "async () => { await Promise.all([...document.images].map(image => image.complete ? Promise.resolve() : new Promise(resolve => { image.addEventListener('load', resolve, {once:true}); image.addEventListener('error', resolve, {once:true}); }))); }"
        )
        page.wait_for_timeout(1500)
        viewport_results[viewport_id] = inspect_page(page, report)
        if report_id == "openxsim" and viewport_id == "desktop":
            page.locator(".benchmarks").screenshot(path=str(qa_dir / "desktop-benchmarks.png"))
            page.locator(".fallbacks").screenshot(path=str(qa_dir / "desktop-fallbacks.png"))
        if report_id == "harness" and viewport_id == "desktop":
            page.locator(".result-videos").screenshot(path=str(qa_dir / "desktop-bounded-evidence-videos.png"))
        page.evaluate(
            "() => { document.querySelectorAll('video[poster]').forEach(video => { const rect = video.getBoundingClientRect(); const image = document.createElement('img'); image.src = video.poster; image.alt = video.getAttribute('aria-label') || 'Video poster used for static QA screenshot'; image.style.display = 'block'; image.style.width = `${rect.width}px`; image.style.maxWidth = '100%'; image.style.height = `${rect.height}px`; image.style.objectFit = 'cover'; image.style.background = '#111'; video.replaceWith(image); }); }"
        )
        page.evaluate(
            "async () => { await Promise.all([...document.images].map(image => image.decode().catch(() => undefined))); }"
        )
        page.screenshot(path=str(qa_dir / f"{viewport_id}-viewport.png"), full_page=True)
        page.close()
    if console_errors or page_errors:
        raise AssertionError(f"Browser errors: console={console_errors} page={page_errors}")
    result = {
        "status": "pass_browser_qa",
        "report": report_id,
        "index": str(index),
        "viewports": viewport_results,
        "console_errors": console_errors,
        "page_errors": page_errors,
    }
    (report / "assets" / "browser_qa.txt").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", choices=sorted(REPORTS))
    args = parser.parse_args()
    if not CHROME.is_file():
        raise FileNotFoundError(CHROME)
    results = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(CHROME),
            args=["--allow-file-access-from-files"],
        )
        try:
            selected = args.report or list(REPORTS)
            for report_id in selected:
                report = REPORTS[report_id]
                results[report_id] = qa_report(browser, report_id, report)
        finally:
            browser.close()
    print(json.dumps({"status": "pass_all_report_browser_qa", "reports": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
