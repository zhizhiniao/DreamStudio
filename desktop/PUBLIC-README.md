# 造梦室 DreamStudio

Windows 本机视频内容工作台。支持视频导入与 YouTube 下载、字幕与配音、横竖封面、人工审核和发布准备。保留 Y2A-Auto 来源标识。

当前正式版本为 **0.1.1**，提供 Windows x64 独立安装包；应用版本与 [v0.1.1 Release](https://github.com/zhizhiniao/DreamStudio/releases/tag/v0.1.1) 一致。

## 下载

[Windows x64 安装包](https://github.com/zhizhiniao/DreamStudio/releases/latest/download/DreamStudio-Setup-x64.exe) · [发行记录](https://github.com/zhizhiniao/DreamStudio/releases)

安装后从桌面「造梦室」打开。关闭窗口转入托盘；彻底退出使用菜单「退出造梦室」。未签名的 Windows 包可能显示来源确认，请核对 GitHub 来源与 SHA256。

账号、任务与素材保存在 `%LOCALAPPDATA%/DreamRoom/workspace`，与安装文件分开。更新不会打包或覆盖个人数据。软件只监听 127.0.0.1；在线下载、翻译、配音及发布需要用户自行配置服务。AI 候选与平台发布均保留人工确认，发布成功回执不代表平台审核通过。

## 自动更新

菜单「造梦室 → 检查更新」。支持启动后自动检查、下载校验、确认后退出并运行安装包；可关闭自动检查或跳过当前版本。下载只接受本仓库的正式 Release 和 SHA256 匹配的 Windows 安装包。

## 可选外部采集组件

公开版不包含 CreatorHub 源码或其专用 Python 环境。关键词采集、跟踪与运营读取需用户自行部署有权使用的 CreatorHub 及 DreamStudio 桥接服务，再在工作空间的 `data/external-hub.json` 配置：

```json
{"url":"http://127.0.0.1:8021","token":"替换为你自己桥接服务的令牌"}
```

未接入时会显示明确提示；本地导入、YouTube 内容处理、字幕、配音、封面与人工发布准备仍可使用。桥接代码见 `tools/hub_server.py`，只开放读取与采集接口，不自动互动或投稿。

## 源码与构建

版本号以 `desktop/package.json` 为准。准备 `runtime`（Python 3.11、env、packages、Chromium、FFmpeg、Whisper 模型与 Tesseract）、Electron 45 和 Inno Setup 6 后运行 `node desktop/build-release.cjs`。运行环境未提交到 Git；完整 Windows 环境随 Release 安装包提供。

发布新版本时构建同名安装资产 `DreamStudio-Setup-x64.exe`，发布 `vX.Y.Z` 正式 Release 并附 SHA256SUMS，网站和客户端即可共用最新发行版。

## 许可

GPL-3.0-or-later；第三方适配器、字体及其他依赖保留各自许可，见 LICENSE、NOTICE.md 与 vendor 中的许可文件。可选 CreatorHub 不在本仓库分发。
