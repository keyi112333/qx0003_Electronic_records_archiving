import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(channel="msedge", headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("http://218.14.150.108:8100/seeyon/index.jsp")
    page.locator("#login_username").click()
    page.locator("#login_username").press("CapsLock")
    page.locator("#login_username").fill("RPA")
    page.locator("#login_username").press("CapsLock")
    page.locator("#login_password1").click()
    page.locator("#login_password1").fill("1qaz!QAZ")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.get_by_role("button", name="登 录").click()
    page.close()

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
