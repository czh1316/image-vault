# 图仓 · Image Vault

一个独立的图片采集与管理网站。
![Uploading dc0bc83c239ea8ae1b8c2201f135864a.png…]()


## 功能特性

### 核心功能
- **多来源图片采集**
  - 百度图片（缩略图，支持浏览器备用模式）
  - 必应图片（公开图片搜索）
  - Pixabay（官方 API，仅保存照片）
  - 自动模式：按百度 → 必应 → Pixabay 顺序智能切换

- **账户系统**
  - 独立注册、登录、退出
  - 密码 scrypt 哈希存储
  - 服务端会话管理，支持撤销
  - 账户间图片完全隔离

- **私有图库**
  - 关键词筛选、搜索、排序
  - 图片详情查看
  - 单张下载 / 批量 ZIP 下载（最多 200 张）
  - 批量删除

- **任务管理**
  - 后台异步采集，实时进度显示
  - 支持停止正在进行的任务
  - 每次采集 1–200 张
  - 每账户最多 1 个活动任务，全站最多 20 个

- **安全与去重**
  - 基于图片内容 SHA-256 去重
  - 图像格式验证（仅保存 Pillow 可识别的栅格图片）
  - 单图限制 12 MiB
  - 每账户默认配额 500 MiB

## 快速开始

### 环境要求
- Python 3.11+（已在 Python 3.14 测试）
- Chrome 浏览器（百度浏览器备用模式需要）

### 启动方式

**方式一：双击启动（推荐）**
```
双击 start.bat
```

**方式二：命令行启动**
```bash
.venv\Scripts\python.exe run.py
```

**方式三：PowerShell**
```powershell
./start.ps1
```

启动后访问 http://127.0.0.1:8765 ，首次使用需注册账户。

> **注意**：关闭终端会停止服务；仅关闭浏览器不会停止采集任务。

## 配置

通过环境变量配置（`.env.example` 仅供参考，不会自动加载）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VAULT_HOST` | `127.0.0.1` | 监听地址 |
| `VAULT_PORT` | `8765` | 监听端口 |
| `VAULT_DATA_DIR` | `项目内 data` | 持久化数据目录 |
| `VAULT_COVERS_DIR` | `项目内 image` | 登录页封面图片目录 |
| `VAULT_QUOTA_MB` | `500` | 每账户存储配额 (MB) |
| `VAULT_COOKIE_SECURE` | `0` | HTTPS 部署时设为 `1` |
| `PIXABAY_API_KEY` | - | Pixabay API 密钥 |
| `VAULT_BAIDU_BROWSER` | `1` | 启用百度浏览器备用模式 |
| `VAULT_BROWSER_CHANNEL` | `chrome` | 浏览器类型（`chrome` / `msedge`） |

### Pixabay API 密钥

两种方式配置（二选一）：
1. Set the `PIXABAY_API_KEY` environment variable to your own key.
2. 配置文件：在 `data/private-settings.json` 中添加 `{"PIXABAY_API_KEY": "your-key-here"}`

> `data/` 目录已被 Git 忽略，密钥不会泄露。

## 使用说明

### 图片采集

1. 登录后进入"采集"页面
2. 输入关键词，选择数量（1-200）
3. 选择来源（百度/必应/Pixabay/自动）
4. 提交任务，后台自动执行
5. 在"任务"页面查看进度，可随时停止

### 图库管理

- **浏览**：按关键词筛选，支持搜索和排序
- **查看**：点击图片查看详情（含原网页链接）
- **下载**：单张下载或批量 ZIP（最多 200 张，单次 200 MiB）
- **删除**：支持批量删除

### 登录页封面

登录页展示 `image/` 目录中的图片轮播，支持 JPG、PNG、WebP、GIF，最多 12 张。新增图片后刷新页面即可生效。

## 数据来源说明

| 来源 | 类型 | 说明 |
|------|------|------|
| 百度图片 | 缩略图 | 原工具逻辑，可能触发验证码 |
| 必应图片 | 缩略图 + 原图链接 | 公开搜索接口 |
| Pixabay | 原图 (最大 1280px) | 官方 API，需密钥 |

**限制**：
- 百度接口可能返回安全验证（`antiFlag`），此时自动切换浏览器模式或必应
- 不绕过验证码，不处理 SVG
- 分辨率和可用性由来源决定

## 网络代理

系统设置 HTTP 代理时，仅对以下地址使用代理：
- 百度/必应搜索端点
- 必应缩略图 CDN
- Pixabay API 及照片 CDN

其他外部图片地址强制使用公网 DNS/IP 校验，防止 SSRF。

## 开发与测试

### 安装开发依赖

```bash
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

### 运行测试

```bash
.venv\Scripts\python.exe -m pytest tests -q
```

当前测试覆盖：59 项 pytest 用例。

## 项目结构

```
爬取百度图片/
├── image_vault/          # 核心代码
│   ├── app.py            # Flask 应用主程序
│   ├── auth.py           # 认证与会话
│   ├── crawler.py        # 采集调度
│   ├── baidu_browser.py  # 百度浏览器模式
│   ├── pixabay.py        # Pixabay API
│   ├── network.py        # 网络与安全
│   ├── storage.py        # 图片存储
│   ├── jobs.py           # 任务管理
│   └── db.py             # 数据库
├── static/               # 静态资源
├── tests/                # 测试用例
├── image/                # 登录页封面
├── data/                 # 持久化数据（Git 忽略）
│   ├── vault.sqlite3     # 主数据库
│   ├── pixabay-cache.sqlite3  # Pixabay 缓存
│   └── images/           # 账户图片
├── run.py                # 入口
├── start.bat             # Windows 启动脚本
└── start.ps1             # PowerShell 启动脚本
```

## 生产部署

### 推荐架构

```
[浏览器] → [HTTPS 反向代理] → [Waitress 服务]
                ↓
           [持久化存储卷]
```

### 部署要点

1. **单进程运行**：不要同时启动多个指向同一 data 目录的进程
2. **反向代理**：Nginx/Caddy 前置 HTTPS，保留原始 Host/Origin
3. **持久卷**：data 目录使用持久化存储
4. **安全配置**：
   - 设置 `VAULT_COOKIE_SECURE=1`
   - 反向代理使用明确的代理协议传递 HTTPS 信息
   - 不可无条件信任公网代理头

### 备份

1. 停止服务
2. 备份整个 `data/` 目录（含 SQLite WAL/SHM 文件）
3. 不要只复制部分文件

## 逆向分析

本项目起源于对百度图片采集 EXE 的逆向分析：

开发过程参考了对原始工具的静态逆向分析，包括行为说明、字节码观察和可读的等价重建。相关分析材料属于开发背景，不随本公开源码发布。

> 未运行原 EXE，所有分析基于静态逆向。

## 常见问题

**Q: 百度采集失败？**
A: 百度可能触发验证码。自动模式会切换到必应或 Pixabay。可用 `VAULT_BAIDU_BROWSER=0` 禁用浏览器备用模式。

**Q: Pixabay 返回 403？**
A: 检查 API 密钥是否正确配置，以及是否超过 100 次/60 秒的请求限制。

**Q: 服务重启后任务丢失？**
A: 服务重启会将活动任务标记为"中断"，已保存的图片保留。

## 许可证

本项目仅供学习研究使用。采集的图片请遵守来源网站的使用条款。

---

**最后更新**：2026-10-27
