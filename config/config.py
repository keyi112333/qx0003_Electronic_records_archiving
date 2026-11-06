#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置模块
"""
from pathlib import Path
from typing import Any, Dict, List, Optional
import shutil
from config.config_loader import ConfigLoader
from core.errors import ConfigError

# 项目python代码的log输出目录，根据实际情况修改
PYHON_LOG_FOLDER_PATH = r"./log_data/"
# UserConfig路径，后续需修改为项目工作目录下的指定文件路径
USER_CONFIG_PATH = r"./data/UserConfig.xlsx"


class Config:
    """项目配置"""

    def __init__(self, config_path: str = None):
        self._loader: Optional[ConfigLoader] = None
        self._paths: Dict[str, Path] = {}
        self._config_source_path: Optional[Path] = None  # 用户配置文件源路径
        self._business_source_path: Optional[Path] = None  # 业务配置文件路径

        if config_path:
            self.load(config_path)

    def load(self, config_path: str) -> None:
        """加载配置文件"""
        self._config_source_path = Path(config_path)  # 保存源路径
        self._loader = ConfigLoader(config_path)

        # 读取基础配置
        base_config = self._loader._config.get('基础配置', {})
        self.project_name = base_config.get('项目名称', 'XX流程')
        self.base_folder = base_config.get('基础路径', './data')

        # 读取目录配置
        dir_config = self._loader._config.get('目录配置', {})
        self._dir_names = {
            'config': dir_config.get('配置目录', '01_配置'),
            'input': dir_config.get('输入目录', '02_输入'),
            'output': dir_config.get('输出目录', '03_输出'),
            'log': dir_config.get('日志目录', '04_日志'),
            'screenshot': dir_config.get('截图目录', '05_错误截图'),
            'temp': dir_config.get('临时目录', '06_临时文件'),
        }
        self._init_paths()

    def _init_paths(self) -> None:
        """初始化路径"""
        base = Path(self.base_folder) / self.project_name
        for key, name in self._dir_names.items():
            self._paths[key] = base / name

    def get_path(self, path_type: str, filename: str = "") -> Path:
        """获取路径"""
        path = self._paths.get(path_type, self._paths.get('input'))
        if filename:
            return path / filename
        return path

    def get(self, key: str, default=None):
        """获取配置项（支持 sheet.key 格式）

        Args:
            key: 配置键，格式为 "sheet名.配置项"，如 "基础配置.项目名称"
            default: 默认值

        Returns:
            配置值，不存在则返回默认值
        """
        if self._loader:
            return self._loader.get(key, default)
        return default

    def require(self, key: str, default=None):
        """获取必填配置；空值抛 ConfigError。"""
        value = self.get(key, default)
        if value is None or str(value).strip() == "":
            raise ConfigError(f"缺少配置项: {key}")
        return value

    def get_bool(self, key: str, default: bool = False) -> bool:
        """读取布尔配置，兼容 是/否、true/false、1/0。"""
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in ("是", "true", "1", "yes", "y", "on", "开启"):
            return True
        if text in ("否", "false", "0", "no", "n", "off", "关闭", ""):
            return False
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        """读取整数配置。"""
        value = self.get(key, default)
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """读取浮点配置。"""
        value = self.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def get_list(self, key: str, default: Optional[List[str]] = None) -> List[str]:
        """读取多值配置，支持中英文逗号、顿号、分号与竖线分隔。"""
        value = self.get(key)
        if value is None or str(value).strip() == "":
            return list(default or [])
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value)
        for separator in ("，", "、", ";", "；", "|"):
            text = text.replace(separator, ",")
        return [item.strip() for item in text.split(",") if item.strip()]

    def get_input_params(self) -> Dict[str, Any]:
        """读取 config.xlsx「入参」sheet：第一行表头，第二行取值。"""
        path = self._business_source_path or self._config_source_path
        if not path or not Path(path).exists():
            return {}
        try:
            import openpyxl
            workbook = openpyxl.load_workbook(path, data_only=True)
            try:
                if "入参" not in workbook.sheetnames:
                    return {}
                sheet = workbook["入参"]
                headers = [cell.value for cell in sheet[1]]
                values = [cell.value for cell in sheet[2]] if sheet.max_row >= 2 else []
                result = {}
                for header, value in zip(headers, values):
                    name = str(header or "").strip()
                    if name:
                        result[name] = value
                return result
            finally:
                workbook.close()
        except Exception as exc:
            raise ConfigError(f"读取入参配置失败: {exc}") from exc

    def ensure_directories(self) -> None:
        """确保目录存在，并复制用户配置文件到配置目录"""
        for path in self._paths.values():
            path.mkdir(parents=True, exist_ok=True)

        # 复制用户配置文件到配置目录
        if self._config_source_path and self._config_source_path.exists():
            config_dir = self._paths.get('config')
            if config_dir:
                dest_path = config_dir / self._config_source_path.name
                if not dest_path.exists():
                    shutil.copy2(self._config_source_path, dest_path)

    def load_business(self, config_path: str) -> None:
        """加载业务配置 config.xlsx，并与 UserConfig 合并。

        UserConfig 的「基础配置」「目录配置」优先级更高，业务配置只补充
        BIP配置、一卡通配置、OA配置、通知配置、路径配置与入参。
        """
        path = Path(config_path)
        if not path.exists():
            raise ConfigError(f"业务配置文件不存在: {path}")
        loader = ConfigLoader(path)
        merged = loader._config
        if self._loader is None:
            self._loader = loader
        else:
            for sheet, values in merged.items():
                if sheet in ("基础配置", "目录配置"):
                    continue
                self._loader._config[sheet] = values
        self._business_source_path = path
        # 业务配置副本已位于配置目录，不再重复复制。


# 全局配置实例
config = Config()