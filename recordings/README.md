# 页面录制

录制后将脚本放在本目录。codegen产物不自动接入生产流程，需要整理成页面适配器方法。

```powershell
python -m pip install -r requirements.txt
python scripts/record_pages.py --system OA
python scripts/record_pages.py --system BIP
python scripts/record_pages.py --system 一卡通
# 确认命令后实际打开Edge录制：
python scripts/record_pages.py --system OA --launch
# 配置网址未填时也可显式提供：
python scripts/record_pages.py --system OA --url "http://实际OA地址" --launch
```

直接命令：

```powershell
python -m playwright codegen --target=python --channel=msedge --output recordings/oa.py "http://实际OA地址"
python -m playwright codegen --target=python --channel=msedge --output recordings/bip.py "http://实际BIP地址"
python -m playwright codegen --target=python --channel=msedge --output recordings/yikatong.py "http://实际一卡通地址"
```

需要已安装Microsoft Edge。使用Playwright自带Chromium时先执行 python -m playwright install chromium，录制命令移除 --channel=msedge。

各系统分别录制登录与成功标识、组织切换、单据查询及分页、附件列表/下载。OA另外录制配置表读取、按报关单唯一查重、新增主表、附件上传/删除后的重查、PDF回传。分别保留有数据/无数据/无权限场景。iframe、新窗口、保存成功后的字段均需录入。

录制可能包含登录输入、会话或业务数据；提交前删除密码、Token、真实单据内容。不自动保存storage_state。

整理位置：web/oa/pages.py、web/bip/pages.py、web/yikatong/pages.py（覆盖web/qx0003/adapters.py契约）。登录使用Windows凭据对象，不把录制时的密码写死。


PDF打印暂不实施，无需录制打印按钮、打印机或打印时间回写。
