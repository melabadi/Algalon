#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import secrets
import sys
import time

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import copilot_value
from scripts.release_smoke import post_json, smoke_trace_payload, wait_for_app_session


APP_URL = "http://127.0.0.1:3000"
COLLECTOR_URL = "http://127.0.0.1:4318"


@dataclass(frozen=True)
class BackfilledSession:
    experiment: str
    prompt: str


def seed_backfilled_session(days_ago: int) -> BackfilledSession:
    started_milliseconds = int(time.time() * 1000) - days_ago * 24 * 60 * 60 * 1000
    session_id = f"release-user-journey-{secrets.token_hex(12)}"
    conversation_id = f"release-user-conversation-{secrets.token_hex(12)}"
    prompt = f"Synthetic browser journey backfill from {days_ago} days ago"
    started_at = datetime.fromtimestamp(started_milliseconds / 1000, timezone.utc)
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()[:10]
    experiment = f"session-{started_at:%Y%m%d-%H%M%S}-{session_hash}"

    post_json(
        f"{COLLECTOR_URL}/v1/traces",
        smoke_trace_payload(
            session_id,
            conversation_id,
            prompt,
            started_milliseconds,
            include_tool=True,
        ),
        retry_seconds=90,
    )
    wait_for_app_session(
        experiment,
        (prompt,),
        expected_started_milliseconds=started_milliseconds,
    )
    return BackfilledSession(experiment, prompt)


def run_user_journey(page: Page, sessions: list[BackfilledSession]) -> None:
    page.goto(APP_URL, wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Overall Copilot value")).to_be_visible()

    session_rows = page.locator("table.session-table tbody tr")
    expect(session_rows).to_have_count(3)
    for session in sessions:
        expect(page.get_by_text(session.experiment, exact=True)).to_be_visible()

    page.get_by_label("Time range").select_option("7")
    expect(session_rows).to_have_count(1)
    for session in sessions:
        expect(page.get_by_text(session.experiment, exact=True)).to_have_count(0)

    page.get_by_label("Time range").select_option("30")
    expect(session_rows).to_have_count(3)
    for session in sessions:
        expect(page.get_by_text(session.experiment, exact=True)).to_be_visible()

    page.get_by_role("button", name="Optimistic").click()
    expect(
        page.locator("tr.selected-row").get_by_text("Optimistic", exact=True)
    ).to_be_visible()

    selected = sessions[-1]
    page.get_by_role("searchbox", name="Filter sessions").fill(
        selected.experiment.rsplit("-", 1)[-1]
    )
    expect(session_rows).to_have_count(1)
    expect(session_rows).to_contain_text(selected.experiment)
    session_rows.click()

    expect(page.get_by_role("heading", name="Session detail")).to_be_visible()
    page.get_by_role("button", name=re.compile(r"Level 3.*Prompts")).click()
    expect(page.get_by_role("heading", name="Prompts in this session")).to_be_visible()
    page.get_by_text(selected.prompt, exact=True).click()
    expect(page.get_by_role("heading", name="Prompt detail")).to_be_visible()
    expect(page.locator("pre.prompt-content")).to_have_text(selected.prompt)

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(APP_URL, wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Overall Copilot value")).to_be_visible()
    if not page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    ):
        raise AssertionError("The packaged application overflows the mobile viewport.")


def run_browser_test(screenshot_path: Path) -> None:
    sessions = [seed_backfilled_session(14), seed_backfilled_session(21)]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            run_user_journey(page, sessions)
        except Exception:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)
            raise
        finally:
            browser.close()


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install an exact Algalon bundle and run its browser user journey."
    )
    parser.add_argument("--bundle", required=True)
    parser.add_argument(
        "--failure-screenshot",
        default=os.environ.get(
            "ALGALON_USER_JOURNEY_SCREENSHOT",
            "artifacts/release-user-journey-failure.png",
        ),
    )
    return parser


def main() -> int:
    arguments = create_parser().parse_args()
    smoke_arguments = argparse.Namespace(
        bundle=arguments.bundle,
        skip_build=True,
        skip_telemetry=False,
        keep_extracted=False,
    )
    screenshot_path = Path(arguments.failure_screenshot)
    return copilot_value.command_smoke_bundle(
        smoke_arguments,
        lambda: run_browser_test(screenshot_path),
    )


if __name__ == "__main__":
    raise SystemExit(main())