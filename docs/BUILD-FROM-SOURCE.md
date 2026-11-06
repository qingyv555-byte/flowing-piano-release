# 从本候选的源码构建

当前候选工具链：Windows x64、CPython 3.12.16（python-build-standalone 20261026
install-only-stripped）、PySide6 / Shiboken 6.12.0、PyInstaller 6.21.0、Inno Setup 6。
构建后端固定 setuptools 84.2.1。
`uv.lock` 固定 Python 包版本；`resources/release/SOURCE-ARCHIVES.json` 固定上游来源及 SHA-256。
本记录描述构建方法，不能替代发布验收记录。

1. 将 `Flowing-Piano-Source.zip` 解压到新目录，将 `Dependency-Sources.zip` 的内容解压到
   该目录中的 `.release-source-cache/`。这两个包与程序必须来自同一候选；
   `SOURCE-SNAPSHOT.json` 逐文件标识本地修改后的源码，不将旧 Git 提交冒充最终源码。
   构建脚本支持没有 `.git` 的源码目录，以快照记录读取基础提交。
2. 安装上述 Python 和 uv。用该 Python 创建 `.venv-dev`，例如
   `uv venv --python <Python3.12.16的完整路径> .venv-dev`，设置
   `$env:UV_PROJECT_ENVIRONMENT='.venv-dev'` 后运行 `uv sync --locked --extra dev`。
   Python 的完整构建归档、依赖许可证和构建脚本也在依赖源码包中；
   运行时构建提交为 `fa506e0858ac9f2cc5b289c137b888a2aa24d1ee`。
3. 如没有附带缓存，先运行 `.venv-dev/Scripts/python.exe tools/fetch-release-sources.py`。
   下载脚本按固定清单验证全部档案，不选择线上最新版本。
4. 完整离线集成测试还会验证可选的 MusicXML / 图片 / PDF 解析，先运行
   `tools/Install-MusicRuntime.ps1` 准备项目内隔离的 `.music-runtime`；
   这一步下载固定解析依赖，不会调用模型或播放音频。该运行时不打入普通安装包。
   静音测试用 `.venv-dev/Scripts/python.exe tools/run_silent_tests.py -q`；不要改产品音量。
   `tools/build-portable.ps1` 收集精确许可并构建目录式 Portable。
   `tools/build-installer.ps1 -Version 0.1.1` 用 Inno Setup 构建安装程序。
   完整候选检查入口是 `tools/build-release.ps1`，检查范围见 `CONSTRAINTS.md`。
5. Qt 使用动态链接。Portable 的 `_internal/PySide6/` 保留 DLL 和 QML 插件，
   可以更换 ABI 兼容的修改版 Qt/PySide6；本应用没有签名校验或限制更换这些库。
   构建自己的 Qt/PySide6 时使用附带的相应源码及上游构建说明，重新运行打包步骤。

`Dependency-Sources.zip` 包含 Qt、PySide/Shiboken、FFmpeg、CPython、运行时构建脚本
及当前构建环境的其他 Python 包源档案，各自许可不变。构建工具的存在不代表它会全部随程序分发。
PDF/WebEngine 浏览组件未被本应用使用，已从成品排除；它们的独立发行版本不属于本候选运行内容。
Microsoft 系统组件和 Visual C++ 运行库按其适用条款处理。

源码包不包含 Git 历史、用户作品/凭据、诊断转储、历史设计截图及五张无授权角色图片。
现有四张场景资源与完整音源保留在构建输入中。清理图片不重写已有 Git 历史。
旧版本的验收不代表当前准备分支已通过；下载包的检查结果以对应版本的发布说明为准。
不同电脑及长期运行仍需分别验证。
