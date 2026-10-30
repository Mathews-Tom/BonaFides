# SPDX-License-Identifier: MIT OR Apache-2.0
"""Synthetic browser fixtures cover the proof's write-readiness boundary."""

from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, sync_playwright

from tools.runtime_proof import ProbeFailure, wait_for_profile_save_ready


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<details class="connection-details"><summary>'
            "Workspace connection needs attention</summary></details>"
            "<button disabled>Save profile</button>"
        )
        yield page
        browser.close()


def test_profile_save_waits_for_connection_and_enabled_button(page: Page) -> None:
    page.evaluate(
        """() => {
            setTimeout(() => {
                document.querySelector('summary').textContent = 'Connected to your installation';
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
    with pytest.raises(ProbeFailure, match="^browser_profile_save_workspace_not_connected$"):
        wait_for_profile_save_ready(page, timeout=100)


def test_profile_save_requires_enabled_button_even_when_connected(page: Page) -> None:
    page.locator("summary").evaluate(
        "summary => { summary.textContent = 'Connected to your installation'; }"
    )
    with pytest.raises(ProbeFailure, match="^browser_profile_save_button_not_enabled$"):
        wait_for_profile_save_ready(page, timeout=100)
