# 快速开始

在本地一条命令运行完整的 AIRI 演示。**无需 Spark、无需 MySQL、无需 LLM API
密钥、无需内网。** 默认模式为 SQLite + 确定性的演示 LLM 替身 + 随仓库附带的合成
数据，完全离线。

---

## 前置条件

| 工具 | 要求 | 验证版本 |
| --- | --- | --- |
| Python | `>=3.12`（取自 `pyproject.toml`） | 3.13.12 |
| [uv](https://docs.astral.sh/uv/) | 任意较新版本 | 0.9.x |
| Node.js | `>=20.19`（Vite 8 的要求） | 22.22.2 |
| npm | 随 Node 一起提供 | 10.x |

缺少的工具请从各自官网安装。演示脚本不会替你安装 Python 或 Node，也从不要求
管理员权限。

---

## 一条命令运行演示

### Windows（主要平台，已验证）

```powershell
git clone https://github.com/huangJJ8/airi.git
cd airi

.\scripts\start-demo.ps1
```

该脚本会：

1. 检查 `uv`、`node`、`npm` 是否在 `PATH` 上，以及端口 8000/5173 是否空闲
2. 同步后端依赖（`uv sync --frozen --extra dev`）
3. 对本地 SQLite 演示数据库执行迁移
4. 通过真实的受治理 API 链播种合成演示数据
5. 首次运行时安装前端依赖（`npm ci`）
6. 启动后端 + 前端并对二者做健康检查
7. 打印 URL，并把 PID 写入 `.demo/demo-processes.json`

运行结束时应当看到：

```text
== AIRI local demo is running ==
   Web UI      : http://localhost:5173
   API docs    : http://localhost:8000/docs
   Stop with  : .\scripts\stop-demo.ps1
```

停止全部：

```powershell
.\scripts\stop-demo.ps1
```

### macOS / Linux（尽力而为，本仓库未做运行时验证）

```bash
git clone https://github.com/huangJJ8/airi.git
cd airi

./scripts/start-demo.sh
./scripts/stop-demo.sh
```

> shell 脚本与 PowerShell 流程一致，但维护者验证过的环境是 Windows。请把 `.sh`
> 变体视为尽力而为。

---

## 手动搭建

如果希望自己执行这些步骤：

```bash
# 1. backend
uv sync --frozen --extra dev
cp .env.example .env                     # optional; the demo works with defaults
mkdir -p .demo                           # .demo/ is gitignored: a fresh clone does not have it
uv run --frozen alembic upgrade head
uv run --frozen python scripts/seed_demo.py

# 2. backend server
uv run --frozen uvicorn airi.main:app --host 127.0.0.1 --port 8000

# 3. frontend (second terminal)
cd airi-web
npm ci
npm run dev
```

> `mkdir -p .demo` 是 POSIX 写法；在 Windows PowerShell 上请用 `mkdir .demo`。
> SQLite 不会在一个并不存在的目录里创建数据库文件，因此在全新克隆的仓库里这一步
> 是必需的 —— `scripts/start-demo.*` 会自动替你完成。

打开 <http://localhost:5173>。

---

## 点击哪里

两个演示场景都走**同一组**页面 —— `/development` → `/testing` → `/experiments`。

| 场景 | 需求 | 能力 |
| --- | --- | --- |
| **发票风险** | 统计企业近30天开票金额 | `metric_sum`, `metric_window` |
| **企业关联** | 统计企业关联自然人控制的其他企业数量 | `metric_join`, `metric_count` |

在 `/development` 页面上用 **Demo Examples** 下拉菜单填入需求，然后依次
Generate → Submit → Approve，继续到 Testing → Experiment。

完整走查见[演示指南](demo.md)。

---

## 验证安装

```bash
# backend
uv run --frozen pytest                                  # 675 passed / 14 skipped
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts

# frontend
cd airi-web && npm run build && npm run test            # 28 passed
```

---

## 配置

所有设置都是带 `AIRI_` 前缀的环境变量（见 `.env.example` 和
`airi-web/.env.example`）。与演示相关的几个：

| 变量 | 演示取值 | 含义 |
| --- | --- | --- |
| `AIRI_DATABASE_URL` | `sqlite+pysqlite:///.demo/airi_web_demo.db` | 本地演示数据库 |
| `AIRI_LLM_MODE` | `demo_mock` | 确定性的 LLM 替身，不联网 |
| `AIRI_EXECUTION_MODE` | `mock` | 在合成夹具（fixture）上做 mock 执行 |
| `AIRI_DEMO_FIXTURES` | `true` | 加载随仓库附带的合成数据集 |
| `AIRI_CORS_ORIGINS` | `["http://localhost:5173"]` | 允许开发服务器来源 |

`.env` 文件是可选的：进程环境变量优先，并且 `start-demo` 会显式设置自己需要的
一切，所以即使你的 `.env` 指向 MySQL 或真实 LLM，演示依然可复现。

`AIRI_LLM_MODE=demo_mock` 是**有意的替身**，不是静默回退。如果你配置了真实 LLM，
而它返回了严格 schema 会拒绝的内容，AIRI 会报错 —— 绝不会悄悄降级到 mock。

---

## 故障排查

| 现象 | 原因 / 处理 |
| --- | --- |
| `[ERROR] 'uv' not found on PATH` | 安装 uv，重新打开终端 |
| `[ERROR] Port 8000 is already in use` | 运行 `.\scripts\stop-demo.ps1`，或释放该端口 |
| 后端在 60 秒后不健康 | 查看 `.demo/backend.log` |
| 前端在 60 秒后不健康 | 查看 `.demo/frontend.log` |
| 演示数据看起来是旧的 | 重新运行 `.\scripts\start-demo.ps1`；播种会重建演示数据库 |
| `alembic` 试图连 MySQL | 把 `AIRI_DATABASE_URL` 设为上面的 SQLite URL |

演示数据库位于 `.demo/airi_web_demo.db`，已被 git 忽略。删除它是安全的 —— 下一次
`start-demo` 会重建并重新播种它。

---

## 下一步

- [演示指南](demo.md) —— 每个场景演示了什么
- [新增场景](adding-scenario.md) —— 在不 fork AIRI 的前提下扩展它
- [API 指南](api.md) —— 领域端点
