# RPA框架

轻量级Python RPA框架，简单、通用、易扩展。支持Web自动化、Windows客户端自动化、数据处理、任务编排等场景。

## 项目结构

```
QX0003_Electronic_records_archiving/
├── main.py                    # 入口
├── requirements.txt           # 依赖
│
├── config/                    # 配置模块
│   ├── config.py              # 配置类
│   ├── config_loader.py       # Excel配置读取
│   └── credentials.py         # Windows凭据读取
│
├── log/                       # 日志模块
│   └── loggingConfig.py
│
├── init/                      # 初始化模块
│   └── init.py                # 进程清理、分辨率设置
│
├── core/                      # 核心引擎模块
│   ├── task_base.py           # 基础任务抽象类
│   ├── task_manager.py        # 任务编排器（可选）
│   ├── context.py             # 任务上下文
│   ├── errors.py              # 异常分类
│   └── retry.py               # 重试与回查结果
│
├── web/                       # Web自动化与接口适配
│   ├── browser.py             # Playwright浏览器封装
│   ├── base_page.py           # 页面操作基类
│   ├── oa/                    # OA录制适配
│   ├── yikatong/              # 一卡通HTTP接口
│   ├── bip/                   # BIP附件来源
│   └── qx0003/                # 外部系统适配器基类
│
├── client/                    # Windows客户端自动化模块
│
├── data_processing/           # 数据处理模块
│   └── excel.py
│
├── workflows/                 # 业务流程模块
│   └── qx0003_archive/
│       ├── main_task.py       # 主任务
│       ├── subtask_collect.py # 子任务：附件收集
│       ├── subtask_oa_sync.py # 子任务：OA同步
│       ├── subtask_merge_pdf.py # 子任务：合并PDF
│       ├── result_workbook.py # 运行结果表
│       ├── pdf_builder.py     # PDF合并与核验
│       ├── notify.py          # 邮件/企微通知
│       ├── run_state.py       # 运行基线与审计
│       └── lock.py            # 单实例锁
│
├── tests/
│   └── run_qx0003_without_bip.py  # 排除BIP的联调脚本
│
├── utils/                     # 工具模块
│   ├── helpers.py
│   ├── file_utils.py
│   ├── screenshot.py
│   └── atomic.py
│
└── data/                      # 数据目录
    └── QX0003_电子档案管理（单证归集与打印）/
        ├── 01_Config/
        ├── 02_Input/
        ├── 03_Output/
        ├── 04_Log/
        ├── 05_Expimage/
        └── 06_Temp/
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt

# 如果使用Web自动化，还需安装Playwright浏览器
playwright install
```

### 2. 配置路径（可选）

在 `config/config.py` 中可修改以下路径配置：

```python
# Python日志输出目录
PYHON_LOG_FOLDER_PATH = r"./log_data/"

# UserConfig配置文件路径
USER_CONFIG_PATH = r"./data/UserConfig.xlsx"
```

> 提示：通常使用默认配置即可，如需修改请确保路径正确。

### 3. 准备配置文件

在 `data/UserConfig.xlsx`：

**基础配置** sheet:
| 配置项 | 值 |
|--------|-----|
| 项目名称 | QX0003_电子档案管理（单证归集与打印） |
| 基础路径 | ./data |
| 初始化进程列表 | chrome,msedge |

**目录配置** sheet:
| 配置项 | 值 |
|--------|-----|
| 配置目录 | 01_Config |
| 输入目录 | 02_Input |
| 输出目录 | 03_Output |
| 日志目录 | 04_Log |
| 截图目录 | 05_Expimage |
| 临时目录 | 06_Temp |

业务配置位于 `01_Config/config.xlsx`，包含入参、BIP配置、一卡通配置、OA配置、通知配置、路径配置。

### 4. 运行

```bash
python main.py
```

## 核心模块说明

### 1. 配置模块 (config)

从Excel文件加载配置，支持多sheet读取：

```python
from config import config

# 获取配置项
project_name = config.get("基础配置.项目名称")
base_folder = config.get("基础配置.基础路径")

# 获取目录路径
input_dir = config.get_path('input')              # 输入目录
output_file = config.get_path('output', 'result.xlsx')  # 输出目录下的文件
```

**get_path(path_type, filename) 方法**：
- `path_type`: 目录类型，支持 `config`, `input`, `output`, `log`, `screenshot`, `temp`
- `filename`: 可选，目录下的文件名
- 返回 `Path` 对象，可直接用于文件操作

### 2. 日志模块 (log)

自动记录日志到文件和控制台：

```python
from log.loggingConfig import logger

logger.info("信息日志")
logger.warning("警告日志")
logger.error("错误日志")
```

**日志文件位置**：在 `config.py` 中设置：
```python
PYHON_LOG_FOLDER_PATH = r"./log_data/"
```

日志文件命名格式：`日期_机器名.log`，如 `20260331_LAPTOP-XXX.log`

### 3. 初始化模块 (init)

运行前清理环境：

```python
from init.init import initEnv

# 清理指定进程（默认从配置读取）
initEnv()

# 或手动指定进程列表
initEnv(["chrome", "firefox", "iexplore"])
```

### 4. 核心任务模块 (core)

**基础任务类**：

```python
from core.task_base import TaskBase

class MyTask(TaskBase):
    def __init__(self, context):
        super().__init__("我的任务", context)

    def execute(self) -> bool:
        # 具体业务逻辑
        return True

# 运行任务
task = MyTask(context)
task.run()  # 自动记录日志、管理状态
```

**任务上下文**：在任务间共享数据：

```python
from core.context import TaskContext

context = TaskContext()
context.config = config           # 存配置
context.set("input_data", data)   # 存数据
context.set_browser("oa", browser)  # 存OA浏览器
context.set_client("app_a", client)        # 存客户端应用

# 获取数据
data = context.get("input_data")
browser = context.get_browser("oa")
client = context.get_client("app_a")

# 清理资源
context.close_all_browsers()
context.close_all_clients()
```

### 5. Web自动化模块 (web)

按系统划分，每个系统下有独立的功能模块：

```python
from config.credentials import get_credential
from web.browser import BrowserManager
from web.oa.pages import ArchiveOaAdapter

# 启动浏览器
browser = BrowserManager(headless=False)
browser.start()

# 登录OA（凭据名来自 config.xlsx）
oa = ArchiveOaAdapter(browser, config=config)
oa.login(get_credential(config.require("OA配置.凭据名")))

# 关闭浏览器
browser.close()
```

**页面操作基类**：

```python
from web.base_page import BasePage

page = BasePage(browser.page)
page.goto("https://example.com")
page.fill("#username", "admin")
page.click("#login-btn")
page.wait_for_selector("#dashboard")
page.screenshot("error.png")
```

### 6. Windows客户端自动化模块 (client)

封装 `uiautomation` 库，支持 Windows 桌面应用自动化：

```python
from client import ClientManager, BaseWindow

# 方式1：启动新应用
client = ClientManager(
    app_path=r"C:\App\app.exe",
    window_name="应用窗口标题"
)
client.start()

# 方式2：连接已有窗口
client = ClientManager(window_name="已有窗口标题")
client.connect()

# 操作窗口
window = BaseWindow(client)

# 定位控件（支持多种方式组合）
btn = window.find_control(control_type='button', name='确定')
edit = window.find_control(control_type='edit', class_name='TextBox', index=0)

# 操作控件
window.click(btn)
window.input_text(edit, '测试内容')
window.double_click({'control_type': 'listitem', 'name': '选项1'})

# 等待控件出现
window.wait_for_control(control_type='button', name='完成', timeout=30000)

# 获取文本
text = window.get_text({'control_type': 'text', 'name': '状态'})

# 发送快捷键
window.send_keys('{Ctrl}{S}')  # Ctrl+S 保存
window.send_keys('{Enter}')    # 回车

# 窗口截图
window.screenshot("window.png")

# 关闭应用
client.close()
```

**控件定位方式**：
- `control_type`: 控件类型，如 `button`, `edit`, `text`, `list`, `combobox` 等
- `name`: 控件显示名称/文本
- `class_name`: 控件类名
- `index`: 在父容器中的索引位置

**常用控件类型**：
| 类型 | 说明 |
|------|------|
| button | 按钮 |
| edit | 输入框 |
| text | 文本标签 |
| list | 列表 |
| listitem | 列表项 |
| combobox | 下拉框 |
| checkbox | 复选框 |
| tree | 树形控件 |
| menu | 菜单 |
| tab | 标签页 |

### 7. 数据处理模块 (data_processing)

```python
from data_processing.excel import ExcelHandler

# 读取Excel
excel = ExcelHandler("input.xlsx")
data = excel.read_sheet("Sheet1")  # 返回字典列表

# 写入Excel
excel.write_sheet("Output", data, headers=["字段1", "字段2"])
excel.save("output.xlsx")
excel.close()
```

### 8. 工具模块 (utils)

**装饰器**：

```python
from utils.helpers import retry, log_execution

@retry(max_attempts=3, delay=1.0)
def unstable_func():
    # 失败自动重试3次
    pass

@log_execution
def my_func():
    # 自动记录开始/完成日志
    pass
```

**文件操作**：

```python
from utils.file_utils import copy_file, move_file, backup_file, get_latest_file

copy_file(src, dst)
backup_file(src, "./backup/")
latest = get_latest_file("./output/", "*.xlsx")
```

**系统截图**：

```python
from utils.screenshot import take_screenshot, take_error_screenshot

# 截取整个屏幕
take_screenshot("./05_错误截图/")

# 错误时截图
take_error_screenshot("./05_错误截图/", "登录失败")
```

## 业务流程编排

在 `workflows/` 下创建业务流程，主任务自由组装子任务：

```python
# workflows/my_flow/main_task.py
from core.task_base import TaskBase
from core.context import TaskContext
from log.loggingConfig import logger

class MyMainTask(TaskBase):
    def __init__(self, config):
        context = TaskContext()
        context.config = config
        super().__init__("我的流程", context)

    def execute(self) -> bool:
        # 自由组装子任务

        # 1. 登录
        login = LoginSubtask(self.context)
        if not login.run():
            return False

        # 2. 读取数据
        read_data = ReadDataSubtask(self.context)
        read_data.run()

        # 3. 根据条件决定是否执行
        data = self.context.get("input_data")
        if len(data) > 100:
            batch_process = BatchProcessSubtask(self.context)
            batch_process.run()
        else:
            single_process = SingleProcessSubtask(self.context)
            single_process.run()

        # 4. 循环处理
        for item in data:
            process_item = ProcessItemSubtask(self.context, item)
            process_item.run()

        # 5. 清理（始终执行）
        cleanup = CleanupSubtask(self.context)
        cleanup.run()

        return True
```

**优势**：
- 无需固定模式，自由编写执行逻辑
- 可根据条件跳过或选择执行路径
- 可循环执行
- 可自定义错误处理策略

## 添加新系统

在 `web/` 下创建新系统目录：

```
web/
└── new_system/              # 新系统
    ├── __init__.py
    ├── login.py             # 登录模块
    ├── query.py             # 查询模块
    └── export.py            # 导出模块
```

示例：

```python
# web/new_system/login.py
from web.browser import BrowserManager
from web.base_page import BasePage

class NewSystemLogin(BasePage):
    def __init__(self, browser: BrowserManager):
        super().__init__(browser.page)

    def login(self, url, username, password):
        self.goto(url)
        self.fill("#user", username)
        self.fill("#pwd", password)
        self.click("#submit")
        self.wait_for_selector("#home")
        return True
```

## 添加新客户端应用

在 `client/apps/` 下创建新应用的操作类：

```
client/apps/
└── my_app/                  # 新应用
    ├── __init__.py
    ├── login.py             # 登录模块
    └── operate.py           # 操作模块
```

示例：

```python
# client/apps/my_app/login.py
from client.base_window import BaseWindow
from client.client_manager import ClientManager
from log.loggingConfig import logger

class MyAppLogin(BaseWindow):
    def __init__(self, client: ClientManager):
        super().__init__(client)

    def login(self, username: str, password: str) -> bool:
        """登录应用"""
        logger.info(f"登录应用: {username}")

        try:
            # 输入用户名
            username_edit = self.find_control(control_type='edit', name='用户名')
            self.input_text(username_edit, username)

            # 输入密码
            password_edit = self.find_control(control_type='edit', name='密码')
            self.input_text(password_edit, password)

            # 点击登录
            login_btn = self.find_control(control_type='button', name='登录')
            self.click(login_btn)

            # 等待登录成功
            self.wait_for_control(control_type='text', name='欢迎', timeout=10000)

            logger.info("应用登录成功")
            return True

        except Exception as e:
            logger.error(f"应用登录失败: {e}")
            return False
```

## 添加新流程

在 `workflows/` 下创建新流程目录：

```
workflows/
└── monthly_report/          # 月报流程
    ├── __init__.py
    ├── main_task.py         # 主任务
    ├── subtask_collect.py   # 收集数据
    └── subtask_generate.py  # 生成报表
```

## 配置文件格式

`UserConfig.xlsx` 支持多sheet，每个sheet为键值对格式：

| 配置项 | 值 |
|--------|-----|
| key1 | value1 |
| key2 | value2 |

读取方式：`config.get("sheet名.配置项")`

可根据业务需要添加自定义sheet，如：

**系统A配置** sheet:
| 配置项 | 值 |
|--------|-----|
| 网址 | https://system-a.com |
| 用户名 | admin |
| 密码 | 123456 |
| 无头模式 | False |

## 依赖说明

```
openpyxl       # Excel读写
Pillow         # 图片处理、截图
psutil         # 进程管理
pywin32        # Windows API（分辨率设置）
playwright     # Web自动化
uiautomation   # Windows客户端自动化
```

## 注意事项

1. **路径配置**：运行前需确认 `config/config.py` 中的两个路径配置：
   - `PYHON_LOG_FOLDER_PATH`：Python代码日志输出目录
   - `USER_CONFIG_PATH`：UserConfig配置文件路径
2. **Web自动化**：首次使用需运行 `playwright install` 安装浏览器
3. **Windows客户端自动化**：仅Windows系统支持，依赖 `uiautomation`
4. **分辨率设置**：仅Windows系统支持，依赖 `pywin32`
5. **配置文件**：确保 `UserConfig.xlsx` 存在且格式正确