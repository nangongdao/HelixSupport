# 桌面应用部署指南 (v1.4.0-desktop)

## 构建流程

### 前置条件

- Rust 1.98+ (cargo)
- Node v24+ / npm 11+
- Python 3.11.4 (rls-venv)
- Windows 11 (WebView2 Evergreen 自带)

### 1. 打包 Python sidecar

```bash
artifacts/rls-venv/Scripts/python.exe -m PyInstaller desktop/helix-server.spec --noconfirm
```

产出：`desktop/dist/helix-server/helix-server.exe` + `_internal/`

冒烟测试：
```bash
artifacts/rls-venv/Scripts/python.exe desktop/smoke_sidecar.py
```

### 2. 构建 Vite React 岛产物

```bash
cd frontend
npm install
npx vite build
```

产出：`app/static/dist/` (manifest.json + content-hashed chunks + island-loader.js)

### 3. 部署 sidecar 到 Tauri 资源目录

```bash
cp -r desktop/dist/helix-server src-tauri/resources/server/
```

### 4. 构建 Tauri 桌面壳

```bash
cd src-tauri
cargo tauri build
```

产出：`src-tauri/target/release/bundle/nsis/Helix Support_1.4.0_x64-setup.exe`

### 5. 代码签名（D5 阶段）

需要 OV 代码签名证书（1-2 周采购周期）：

```bash
# 配置 signtool 或 tauri.conf.json 中的签名设置
signtool sign /f cert.pfx /p <password> /t http://timestamp.digicert.com "Helix Support_1.4.0_x64-setup.exe"
```

向 Microsoft 提交误报白名单（本项目有 EICAR 拦截前科）。

## 自动更新

Tauri updater 配置在 `tauri.conf.json` 的 `plugins.updater` 节：
- 更新源：GitHub Releases (`latest.json` manifest)
- 需要生成签名密钥对：`cargo tauri signer generate -w ~/.tauri/helix.key`
- 公钥填入 `pubkey` 字段，私钥用于 CI 签名构建

## 性能验收 (§6.1)

### 冷启动 SLO (<3s)

桌面包内置遥测，启动后检查：
```
%APPDATA%/HelixSupport/telemetry/startup.json
```

包含三个时间戳：
- `t_window_created_ms`：窗口创建耗时
- `t_backend_ready_ms`：后端就绪耗时
- `t_ui_ready_ms`：UI 可交互耗时

CI nightly 断言 p95 < 3s。

### 运行时性能 (§6.2/§6.3)

| 指标 | 目标 |
|------|------|
| LCP | < 1000ms (桌面口径) |
| CLS | < 0.1 |
| 10k 渲染 | < 500ms (React 化后) |
| 长任务 | < 50ms |
| heap 波动 | ≤ 15MB |

### 包体预算 (§6.3)

| 项 | 预算 |
|----|------|
| operator JS (app.js + js/*.js + dist/assets/*.js) | ≤ 700KB raw / ≤ 210KB gzip |
| operator CSS | ≤ 125KB raw |
| widget JS/CSS | ≤ 25KB |
| 首屏 chunk (workspace 岛) | ≤ 180KB raw |
| Tauri exe | ~5-10MB (不含 sidecar) |
| Python sidecar | ~48-130MB |

## 数据目录

- 桌面版 DB：`%APPDATA%/HelixSupport/data/support.db`
- 遥测：`%APPDATA%/HelixSupport/telemetry/startup.json`
- 开发覆盖：设置 `HELIX_DATA_DIR` 环境变量

## 故障排查

| 问题 | 解决 |
|------|------|
| 杀软拦截 sidecar exe | 代码签名 + Microsoft 误报提交 |
| 后端启动超时 | 检查 `startup.json` 中的 error 字段 |
| WebView2 缺失 | 安装 [WebView2 Evergreen](https://go.microsoft.com/fwlink/p/?LinkId=2124703) |
| 端口冲突 | supervisor 自动分配空闲端口，无需手动配置 |
