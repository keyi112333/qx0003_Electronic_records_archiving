#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QX0003 联调脚本：运行完整流程，但排除 BIP 模块。

使用场景：
- OA 规则表已配置完成，且规则中不包含 BIP 销售发票/运费发票；
- 需要在不访问 BIP 的情况下，验证共享盘、一卡通、OA 同步、PDF 合并、
  结果表、通知等其余模块。

运行：
    python tests/run_qx0003_without_bip.py

可选环境变量：
    QX0003_COMPANIES / QX0003_DECLARATIONS / QX0003_PERIOD / QX0003_STAGE
        与正式流程一致，用于限定本次测试范围。
    QX0003_INIT_ENV=1
        运行前清理浏览器进程（默认不清理，避免影响本机已打开的浏览器）。
    QX0003_TEST_NO_NOTIFY=1
        测试时不发送企微/邮件（默认按 config.xlsx 的通知配置发送）。
"""
import os
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import USER_CONFIG_PATH, config  # noqa: E402
from workflows.qx0003_archive.main_task import Qx0003MainTask  # noqa: E402
from workflows.qx0003_archive.models import Snapshot  # noqa: E402
from workflows.qx0003_archive import subtask_collect as collect_module  # noqa: E402


class NoBipAdapter:
    """空 BIP 适配器：不登录、不查询、不下载，返回空快照。"""

    name = "BIP"
    api_based = True

    def __init__(self, config=None, browser=None):
        self.config = config
        self.browser = browser

    def snapshot(self, declaration):
        return Snapshot([], complete=True, scope=declaration.key, source="BIP")


class SilentNotifier:
    """测试用通知器：只记录，不实际发送。"""

    def notify_start(self, run_id, params_summary):
        print(f"[通知已跳过] 启动: run_id={run_id}, {params_summary}")

    def notify_error(self, stage, declaration, error, screenshot=""):
        print(f"[通知已跳过] 异常: {stage} {declaration} {type(error).__name__} {error}")

    def notify_end(self, items, workbooks_by_org):
        total = len(items)
        pdf_count = sum(1 for item in items if item.pdf)
        failed = sum(1 for item in items if item.status == "失败")
        manual = sum(1 for item in items if item.status == "待人工")
        print(f"[通知已跳过] 结束: 共{total}条，PDF{pdf_count}条，失败{failed}条，待人工{manual}条")


def load_config() -> None:
    """按 main.py 的顺序加载 UserConfig.xlsx 与业务 config.xlsx。"""
    config_path = Path(USER_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    config.load(str(config_path))
    config.ensure_directories()
    business_path = config.get_path("config", "config.xlsx")
    if not business_path.exists():
        raise FileNotFoundError(f"业务配置文件不存在: {business_path}")
    config.load_business(str(business_path))


def main() -> int:
    print("=" * 72)
    print("QX0003 联调：排除 BIP 模块")
    print("=" * 72)
    load_config()

    if os.environ.get("QX0003_INIT_ENV") == "1":
        from init.init import initEnv
        initEnv()

    # 关键：在创建任务前替换采集模块中的 BipAdapter，流程其余逻辑不变。
    collect_module.BipAdapter = NoBipAdapter

    task = Qx0003MainTask(config)
    if os.environ.get("QX0003_TEST_NO_NOTIFY") == "1":
        task.notifier = SilentNotifier()

    print(f"项目: {config.project_name}")
    print(f"共享盘: {config.get('路径配置.报关单证存放路径')}")
    print(f"结果表目录: {config.get_path('log')}")
    print(f"入参: {os.environ.get('QX0003_COMPANIES') or '全部公司'}"
          f" / {os.environ.get('QX0003_DECLARATIONS') or '共享盘全部'}"
          f" / {os.environ.get('QX0003_PERIOD') or '不限期间'}")
    print("-" * 72)

    ok = task.execute()

    print("-" * 72)
    print(f"执行结果: {'成功' if ok else '失败'}")
    print(f"运行ID: {task.run_id}")
    print(f"单据数: {len(task.items)}")
    for item in task.items:
        pdf = item.pdf.path if item.pdf else (item.declaration.collected_pdf_path or "")
        print(f"  {item.declaration.company} / {item.declaration.declaration_no}"
              f" | 状态={item.status}"
              f" | PDF={pdf or '-'}"
              f" | 异常={item.error_message or '-'}")
    for org, paths in task.workbooks.items():
        for path in paths:
            print(f"结果表: {org} -> {path}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("用户中断", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        traceback.print_exc()
        print(f"测试执行异常: {exc}", file=sys.stderr)
        raise SystemExit(2)
