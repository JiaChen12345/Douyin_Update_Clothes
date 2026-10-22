# 抖店自动上款 v49 整理说明

版本号：`2026.08.19.49`

## 入口文件

- Python 自动化主入口：`douyin_automation_dual_store_v49.py`
- Python GUI 入口：`douyin_gui_v49.py`
- 安装包脚本：`installer.iss`

## 成品位置

- 安装包：`release/抖店自动上款安装包-2026.08.19.49.exe`
- 便携版目录：`release/DouyinDraftAutomation/`

## v49 特点

- 双抖店流程：分别使用 `edge-profile` 和 `edge-profile-shop2`
- 两个抖店都保存草稿成功后，自动删除 Excel 中对应任务行
- 支持 17网、搜款网、淘宝、天猫素材下载
- GUI 可配置洗水标图片和淘宝扩展目录
- 仍保持“只保存草稿、不发布”的安全边界
- 支持 GUI 选择单抖店或双抖店运行
- 背心/吊带类目跳过适用年龄设置；尺码定位改为规格卡片内定位
- 非关键表单步骤失败两次后记录警告并继续，任务结束清理新增浏览器页

## 运行方式

- 登录环境：`setup-login.bat`
- Python 版：`run-python.bat`
- 安装版：直接运行 `release/抖店自动上款安装包-2026.08.19.49.exe`
