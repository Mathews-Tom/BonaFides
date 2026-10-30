# SPDX-License-Identifier: MIT OR Apache-2.0
"""Synthetic browser fixtures cover the proof's write-readiness boundary."""

from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, sync_playwright

from tools.runtime_proof import ProbeFailure, profile_connection_phase, wait_for_profile_save_ready


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<details class="connection-details"><summary>'
            "Workspace connection needs attention</summary></details>"
            "<button disabled>Save profile</button>"
            '<section aria-labelledby="connection-title"><p role="status">'
            "Checking the saved workspace before enabling writes.</p></section>"
        )
        yield page
        browser.close()


def test_profile_save_waits_for_connection_and_enabled_button(page: Page) -> None:
    page.evaluate(
        """() => {
            setTimeout(() => {
                document.querySelector('summary').textContent = 'Connected to your installation';
                document.querySelector('section').remove();
                setTimeout(() => { document.querySelector('button').disabled = false; }, 100);
            }, 100);
        }"""
    )
    wait_for_profile_save_ready(page, timeout=5_000)
    assert page.get_by_role("button", name="Save profile", exact=True).is_enabled()


def test_profile_save_requires_connected_workspace_even_with_enabled_button(page: Page) -> None:
    page.get_by_role("button", name="Save profile", exact=True).evaluate(
        "button => { button.disabled = false; }"
    )
    with pytest.raises(
        ProbeFailure, match="^browser_profile_save_workspace_not_connected:checking$"
    ):
        wait_for_profile_save_ready(page, timeout=100)


def test_profile_save_requires_enabled_button_even_when_connected(page: Page) -> None:
    page.locator("summary").evaluate(
        "summary => { summary.textContent = 'Connected to your installation'; }"
    )
    page.locator('section[aria-labelledby="connection-title"]').evaluate(
        "section => section.remove()"
    )
    with pytest.raises(ProbeFailure, match="^browser_profile_save_button_not_enabled:online$"):
        wait_for_profile_save_ready(page, timeout=100)


@pytest.mark.parametrize(
    ("status", "phase"),
    [
        ("Checking the saved workspace before enabling writes.", "checking"),
        (
            "Maintenance requires operator recovery. Writers remain closed; elapsed time is not proof of completion.",
            "maintenance",
        ),
        ("Maintenance is active. Writers remain closed.", "maintenance"),
        ("This owner session is unavailable or expired.", "expired"),
        ("The installation could not be reached.", "offline"),
        ("Automatic connection monitoring reached its bound.", "monitor_bound"),
        ("Review the current workspace before resuming writes.", "review"),
        ("Unrecognized status with synthetic private text", "unknown"),
    ],
)
def test_profile_save_failure_reports_redacted_connection_phase(
    page: Page, status: str, phase: str
) -> None:
    page.locator('section[aria-labelledby="connection-title"] [role="status"]').evaluate(
        "(element, text) => { element.textContent = text; }", status
    )
    with pytest.raises(
        ProbeFailure, match=f"^browser_profile_save_workspace_not_connected:{phase}$"
    ):
        wait_for_profile_save_ready(page, timeout=100)


def test_profile_connection_phase_does_not_wait_for_a_missing_panel(page: Page) -> None:
    page.set_content("<p>No connection panel exists.</p>")
    assert profile_connection_phase(page) == "unknown"
