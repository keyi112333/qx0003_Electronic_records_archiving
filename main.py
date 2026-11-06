#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RPA框架入口
"""
import time
from pathlib import Path

from log.loggingConfig import logger
from config.config import USER_CONFIG_PATH
from config import config
from init.init import initEnv
from workflows.qx0003_archive.main_task import Qx0003MainTask
from utils.helpers import format_time


def main():
    """主函数"""
    # 记录流程开始时间
    flow_start_time = time.time()

    # 加载配置
    config_path = Path(USER_CONFIG_PATH)
    if config_path.exists():
        config.load(str(config_path))
        config.ensure_directories()
        business_path = config.get_path('config', 'config.xlsx')
        if not business_path.exists():
            raise Exception(f"业务配置文件不存在: {business_path}")
        config.load_business(str(business_path))
    else:
        logger.error(f"配置文件不存在，请检查路径是否正确: {config_path}")
        raise Exception(f"配置文件不存在，请检查路径是否正确: {config_path}")

    #初始化环境
    initEnv()

    # 创建并运行主任务
    logger.info(f"{config.project_name}开始执行")

    main_task = Qx0003MainTask(config)

    if main_task.run():
        flow_end_time = time.time()
        elapsed_time = flow_end_time - flow_start_time
        logger.info(f"{config.project_name}完成执行, 总耗时: {format_time(elapsed_time)}")
    else:
        flow_end_time = time.time()
        elapsed_time = flow_end_time - flow_start_time
        logger.error(f"{config.project_name}执行失败，状态: {main_task.status}, 总耗时: {format_time(elapsed_time)}")


if __name__ == '__main__':
    main()