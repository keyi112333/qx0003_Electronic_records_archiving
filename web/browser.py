#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用浏览器管理（Playwright封装）
"""
from pathlib import Path

from log.loggingConfig import logger

try:
    from playwright.sync_api import sync_playwright, Page, Browser
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    logger.warning("需要安装 playwright: pip install playwright && playwright install")


def resolve_shortcut(path: str) -> str:
    """把 Windows .lnk 快捷方式解析为真实可执行文件路径。"""
    text = str(path or "").strip()
    if not text.lower().endswith(".lnk"):
        return text
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        target = str(shell.CreateShortcut(text).TargetPath or "").strip()
        if target and Path(target).is_file():
            logger.info("浏览器快捷方式已解析: %s -> %s", text, target)
            return target
        logger.warning("浏览器快捷方式未解析到有效目标: %s", text)
    except Exception as exc:
        logger.warning("浏览器快捷方式解析失败，改用Playwright内置浏览器: %s", exc)
    return ""


class BrowserManager:
    """通用浏览器管理

    封装Playwright浏览器操作
    """

    def __init__(self, headless: bool = False, browser_type: str = "chromium",
                 channel: str = "", executable_path: str = "", maximized: bool = False):
        self.playwright = None
        self.browser = None
        self.page = None
        self.headless = headless
        self.browser_type = browser_type
        self.channel = channel
        self.executable_path = executable_path
        self.maximized = maximized

    def start(self) -> bool:
        """启动浏览器"""
        if not HAS_PLAYWRIGHT:
            raise ImportError("需要安装 playwright: pip install playwright && playwright install")

        logger.info(f"启动浏览器: {self.browser_type}, headless={self.headless}, channel={self.channel or '-'}")
        self.playwright = sync_playwright().start()

        options = {"headless": self.headless}
        executable = resolve_shortcut(self.executable_path)
        if executable:
            options["executable_path"] = executable
        elif self.executable_path.lower().endswith(".lnk"):
            # 快捷方式无法解析时，按名称尝试已安装的 Chrome/Edge 通道。
            shortcut_name = Path(self.executable_path).stem.lower()
            if "chrome" in shortcut_name:
                options["channel"] = "chrome"
            elif "edge" in shortcut_name:
                options["channel"] = "msedge"
        if self.maximized:
            options["args"] = ["--start-maximized"]
        if self.browser_type == "edge":
            if "executable_path" not in options and "channel" not in options:
                options["channel"] = self.channel or "msedge"
            self.browser = self.playwright.chromium.launch(**options)
        elif self.browser_type == "firefox":
            self.browser = self.playwright.firefox.launch(**options)
        elif self.browser_type == "webkit":
            self.browser = self.playwright.webkit.launch(**options)
        elif self.browser_type == "chromium":
            if self.channel and "executable_path" not in options and "channel" not in options:
                options["channel"] = self.channel
            self.browser = self.playwright.chromium.launch(**options)
        else:
            self.browser = self.playwright.chromium.launch(**options)

        self.page = self.browser.new_page()
        logger.info("浏览器启动成功")
        return True

    def close(self) -> None:
        """关闭浏览器"""
        if self.browser:
            self.browser.close()
            logger.info("浏览器已关闭")
        if self.playwright:
            self.playwright.stop()

    def get_page(self) -> Page:
        """获取页面对象"""
        return self.page