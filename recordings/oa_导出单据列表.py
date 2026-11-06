import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright, credential) -> None:
    browser = playwright.chromium.launch(channel="msedge", headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("http://218.14.150.108:8100/seeyon/index.jsp")
    page.locator("#login_username").click()
    page.locator("#login_username").press("CapsLock")
    page.locator("#login_username").fill(credential.username)
    page.locator("#login_username").press("CapsLock")
    page.locator("#login_password-wrapper").click()
    page.locator("#login_password1").fill(credential.password)
    page.get_by_role("button", name="登 录").click()
    page.locator("li:nth-child(9) > .lev1Title > .navText").click()
    with page.expect_popup() as page1_info:
        page.locator("[id=\"000_/cap4/busi_-7866127308096582419\"] > .navText").click()
    page1 = page1_info.value
    page1.locator("#cap4UnflowList").content_frame.get_by_role("row", name=" 状态 报关单号 合同编号 报关单附件是否存在 放行单附件是否存在 合同附件是否存在 提单附件是否存在 销售发票附件是否存在 归集的PDF").get_by_label(" ").check()
    page1.locator("#cap4UnflowList").content_frame.get_by_role("button", name="导入导出").nth(1).click()
    page1.locator("#cap4UnflowList").content_frame.get_by_role("listitem", name="导出").nth(1).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 状态")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 报关单号")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 合同编号")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 报关单附件是否存在")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 放行单附件是否存在")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 合同附件是否存在")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 提单附件是否存在")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 销售发票附件是否存在")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 归集的PDF路径")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 创建时间")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 修改时间")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("listbox").first.select_option("[主表] 创建人")
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.locator("i").nth(5).click()
    page1.locator("iframe[name=\"layui-layer-iframe1\"]").content_frame.get_by_role("combobox").select_option("1")
    page1.get_by_text("确定").click()



if __name__ == "__main__":
    raise SystemExit("原始录制仅供对照，请使用web/oa/pages.py；补录请运行scripts/record_pages.py")
