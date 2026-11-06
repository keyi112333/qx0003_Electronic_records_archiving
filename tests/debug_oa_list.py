#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时诊断脚本：检查OA新建抽屉表单。"""
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


def dump(locator, label):
    try:
        count = locator.count()
        print(f"{label}: count={count}")
        for index in range(min(count, 10)):
            item = locator.nth(index)
            print(f"  [{index}] visible={item.is_visible()} box={item.bounding_box()} text={item.inner_text()[:80]!r}")
    except Exception as exc:
        print(f"{label}: ERROR {exc}")


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
        print("NEW BUTTONS:", buttons.count())
        buttons.nth(1 if buttons.count() > 1 else 0).click()
        page.wait_for_timeout(3000)
        print("PAGE URL:", page.url)
        print("FRAMES:", [(f.url[:120]) for f in page.frames])
        try:
            form = oa.form_frame()
            print("FORM BODY:", form.locator("body").inner_text()[:2500].replace("\n", " | "))
            dump(form.locator(".cap4-text__cnt.is-relatinbrowse"), "org-text")
            dump(form.locator(".icon.CAP.cap-icon-zhubiaoxuanzeqi"), "org-icon")
            dump(form.locator("#field0079_id"), "declaration-field")
            dump(form.locator("#field0080_id"), "date-field")
            dump(form.locator("#field0087_id"), "contract-field")
        except Exception as exc:
            print("FORM FRAME ERROR:", exc)
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
    finally:
        browser.close()


if __name__ == "__main__":
    main()
