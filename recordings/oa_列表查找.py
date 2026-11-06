import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(channel="msedge", headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("http://218.14.150.108:8100/seeyon/index.jsp")
    page.locator("#login_username").click()
    page.locator("#login_username").fill("")
    page.locator("#login_username").press("CapsLock")
    page.locator("#login_username").fill("RPA")
    page.locator("#login_password1").click()
    page.locator("#login_password1").press("ControlOrMeta+V")
    page.locator("#login_password1").fill("1qaz!QAZ")
    page.get_by_role("button", name="登 录").click()
    with page.expect_popup() as page1_info:
        page.locator("[id=\"000_/cap4/busi_-7866127308096582419\"] > .navText").click()
    page1 = page1_info.value
    page1.locator("#cap4UnflowList").content_frame.get_by_role("textbox", name="搜索关键字").first.click()
    page1.locator("#cap4UnflowList").content_frame.get_by_role("textbox", name="搜索关键字").first.fill("1234")
    page1.locator("#cap4UnflowList").content_frame.get_by_role("button", name="筛选").click()
    page1.locator("#cap4UnflowList").content_frame.get_by_role("row", name=" 状态 报关单号 合同编号 报关单附件是否存在 放行单附件是否存在 合同附件是否存在 提单附件是否存在 销售发票附件是否存在 归集的PDF").get_by_label(" ").check()
    page1.locator("#cap4UnflowList").content_frame.get_by_text("已选1/ 1查看").click()
    page1.locator("#cap4UnflowList").content_frame.get_by_text("条/共1条记录").click()
    page1.locator("#cap4UnflowList").content_frame.get_by_text("报关单号").nth(1).click()
    with page1.expect_popup() as page3_info:
        page1.locator("#cap4UnflowList").content_frame.locator("div").filter(has_text=re.compile(r"^1234$")).first.click()
    page3 = page3_info.value
    page1.locator("#cap4UnflowList").content_frame.locator("#targetFrame").content_frame.locator("#cap4FormContent").content_frame.locator("#mineFormPage section").filter(has_text="单证收集 No").click()
    page1.locator("#cap4UnflowList").content_frame.locator("#targetFrame").content_frame.locator("#cap4FormContent").content_frame.get_by_text(" 修改 ").click()
    page1.locator("#cap4UnflowList").content_frame.locator("#targetFrame").content_frame.locator("#cap4FormContent").content_frame.locator("div").nth(4).click()
    page1.locator("#cap4UnflowList").content_frame.locator("div").filter(has_text=" 修改 全屏后不保存已填写内容 ").nth(5).click()

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
