#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时诊断脚本：按生产顺序检查OA组织选择器。"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import USER_CONFIG_PATH, config
from config.credentials import get_credential
from web.browser import BrowserManager
from web.oa.pages import ArchiveOaAdapter


def report(page, label):
    layers = page.locator('iframe[name^="layui-layer-iframe"]:visible')
    print(f"--- {label}: layers={layers.count()}")
    for index in range(layers.count()):
        item = layers.nth(index)
        print("  ", index, item.get_attribute("name"), (item.get_attribute("src") or "")[:140])
    print("layui visible:", page.locator(".layui-layer:visible").count(),
          "shade visible:", page.locator('[id^="layui-layer-shade"]:visible').count())


def main():
    config.load(USER_CONFIG_PATH)
    config.ensure_directories()
    config.load_business(str(config.get_path("config", "config.xlsx")))
    browser = BrowserManager(
        headless=False,
        browser_type=str(config.get("OA配置.浏览器类型") or "chromium").lower(),
        executable_path=str(config.get("OA配置.浏览器路径") or ""),
        maximized=config.get_bool("OA配置.窗口最大化", False),
    )
    browser.start()
    try:
        oa = ArchiveOaAdapter(browser, config=config)
        oa.login(get_credential(str(config.require("OA配置.凭据名"))))
        page = oa.open_archive_list()
        page.wait_for_timeout(12000)
        frame = oa.list_frame()
        frame.get_by_role("textbox", name="搜索关键字").first.wait_for(state="visible", timeout=30000)
        buttons = frame.locator("button:visible", has_text=re.compile(r"^\s*新建\s*$"))
        buttons.nth(1).click()
        page.wait_for_timeout(3000)
        form = oa.form_frame()
        form.locator(".cap4-text__cnt.is-relatinbrowse").click()
        page.wait_for_timeout(500)
        form.locator(".icon.CAP.cap-icon-biaoxuanzeqi, .icon.CAP.cap-icon-zhubiaoxuanzeqi").first.click()
        page.wait_for_timeout(2000)
        report(page, "after org picker open")
        picker = page.frame_locator('iframe[name="layui-layer-iframe1"]')
        search = picker.get_by_role("textbox", name="搜索关键字")
        search.first.wait_for(state="visible", timeout=30000)
        print("SEARCH COUNT:", search.count())
        search.nth(1).fill("TXJ")
        picker.get_by_role("button", name="筛选").click()
        page.wait_for_timeout(2500)
        print("PICKER BODY:", picker.locator("body").inner_text()[:1500].replace("\n", " | "))
        matches = picker.get_by_text("TXJ", exact=True)
        print("TXJ MATCHES:", matches.count())
        for index in range(matches.count()):
            print("  ", index, matches.nth(index).is_visible(), matches.nth(index).bounding_box())
        matches.first.click()
        page.wait_for_timeout(1500)
        report(page, "after company click")
        for step in range(15):
            layers = page.locator('iframe[name^="layui-layer-iframe"]:visible').count()
            confirm = page.locator(".layui-layer:visible").get_by_text("确定", exact=True)
            print(f"STEP {step}: layers={layers} confirm={confirm.count()}")
            if layers == 0:
                break
            if confirm.count():
                confirm.first.click()
            else:
                shade = page.locator('[id^="layui-layer-shade"]:visible')
                if shade.count():
                    shade.first.click()
                else:
                    break
            page.wait_for_timeout(900)
        page.wait_for_timeout(1000)
        print("FORM ORG HTML:", form.locator(".cap4-text__cnt.is-relatinbrowse").evaluate("e => e.outerHTML")[:1000])
        print("FORM BODY HEAD:", form.locator("body").inner_text()[:800].replace("\n", " | "))
        page.keyboard.press("Escape")
    finally:
        browser.close()


if __name__ == "__main__":
    main()
