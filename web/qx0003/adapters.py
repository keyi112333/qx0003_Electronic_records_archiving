#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部系统适配器基类。"""


class SourceAdapter:
    """附件来源适配器基类。

    browser 仅对页面型系统有意义；接口型来源保持为 None。
    """

    name = "source"
    api_based = False

    def __init__(self, browser=None):
        self.browser = browser


class OaAdapter(SourceAdapter):
    """OA 页面适配器基类。"""

    name = "OA"
