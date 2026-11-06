import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(channel="msedge", headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("http://218.14.150.108:8100/seeyon/index.jsp")
    with page.expect_popup() as page1_info:
        page.locator("[id=\"000_/cap4/busi_-7866127308096582419\"] > .navText").click()
    page1 = page1_info.value
    page1.locator("#cap4UnflowList").content_frame.get_by_role("textbox", name="搜索关键字").first.click()
    page1.locator("#cap4UnflowList").content_frame.get_by_role("textbox", name="搜索关键字").first.fill("1234")
    page1.locator("#cap4UnflowList").content_frame.get_by_role("button", name="筛选").click()
    page1.locator("#cap4UnflowList").content_frame.locator("div").filter(has_text=re.compile(r"^1234$")).first.click()
    page1.locator("#cap4UnflowList").content_frame.locator("#targetFrame").content_frame.locator("#cap4FormContent").content_frame.get_by_text(" 修改 ").click()
    page1.locator("#cap4UnflowList").content_frame.locator("#targetFrame").content_frame.locator("#cap4FormContent").content_frame.locator(".ui-input__suffix--clear > .CAP").first.click()
    page1.locator("#cap4UnflowList").content_frame.locator("#targetFrame").content_frame.locator("#cap4FormContent").content_frame.locator(".CAP.suffix_icon").first.click()
    page1.locator("#cap4UnflowList").content_frame.locator("#targetFrame").content_frame.locator("#cap4FormContent").content_frame.get_by_text("已归集为PDF").click()

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
