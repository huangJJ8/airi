# 发布检查清单

打发布 tag 之前先走一遍本清单。每一条都必须*验证*过，而不是假定 —— 背后没有命令
的绿色勾选框不算证据。

---

## 1. 质量门禁

```bash
# backend
uv sync --frozen --extra dev
uv run --frozen pytest -q
uv run --frozen pytest --cov=airi --cov-report=term-missing
uv run --frozen ruff check src tests scripts examples
uv run --frozen ruff format --check src tests scripts examples

# frontend
cd airi-web
npm ci
npm run build
npm run test
cd ..
```

- [ ] 后端测试通过（相对上一次基线无回归：675 passed / 14 skipped）
- [ ] 覆盖率有报告，且不明显低于上一个发布
- [ ] `ruff check` 与 `ruff format --check` 干净
- [ ] 前端构建通过（`vue-tsc -b && vite build`）且单元测试通过（28）
- [ ] 跳过的测试仍然只有那些被有意跳过的集成套件

## 2. 数据库

```bash
mkdir -p .demo   # SQLite will not create a file inside a missing directory; .demo/ is gitignored
AIRI_DATABASE_URL="sqlite+pysqlite:///.demo/release_check.db" uv run --frozen alembic upgrade head
AIRI_DATABASE_URL="sqlite+pysqlite:///.demo/release_check.db" uv run --frozen alembic current
AIRI_DATABASE_URL="sqlite+pysqlite:///.demo/release_check.db" uv run --frozen alembic check
```

- [ ] 在空数据库上 `upgrade head` 成功
- [ ] `current` 报告预期的 head
- [ ] `check` 报告没有待处理的模型/迁移漂移

## 3. 安全与开源安全

```bash
uv run --frozen python scripts/check_open_source_safety.py
cd airi-web && npm audit --omit=dev; cd ..
```

- [ ] 安全检查脚本通过（无内部域名、密钥或凭据）
- [ ] 没有真实客户、公司或人名；没有真实标识符
- [ ] 没有暂存 `.env` 文件或真实密钥
- [ ] 新依赖已审查；没有无法解释的大版本升级
- [ ] 高危/严重告警已分流处理（未修复的有记录）

## 4. 本地演示

```powershell
.\scripts\start-demo.ps1
# verify: http://localhost:5173 renders, http://localhost:8000/api/v1/meta responds
.\scripts\stop-demo.ps1
# verify: ports 8000 and 5173 are free afterwards
```

- [ ] 在干净检出上一条命令即可启动
- [ ] 停止会释放两个端口
- [ ] 终端演示可运行：`uv run --frozen python examples/quick_demo.py`
- [ ] 两个演示场景都能在浏览器里跑完（发票风险、企业关联）
- [ ] 关键页面没有意外的 `console.error` 或 404

## 5. 文档

- [ ] README 反映当前功能集、快速开始和版本
- [ ] 截图来自当前发布，而不是旧版 UI
- [ ] `CHANGELOG.md` 有本版本的条目
- [ ] `docs/architecture/` 与代码一致（没有写了却并不存在的模块）
- [ ] 版本号在 `pyproject.toml`、`src/airi/__init__.py` 中一致地提升
- [ ] `docs/guides/release-checklist.md` —— 也就是本文件 —— 是最新的
- [ ] 未验证的集成仍标注为未验证；见
      [真实环境检查清单](real-environment-checklist.md)

## 6. 仓库卫生

- [ ] `LICENSE` 存在且意图未变
- [ ] `.gitignore` 覆盖 `.env`、数据库、构建产物和本地状态
- [ ] `docs/screenshots/` **确实**已提交（没有被宽泛的 `*.png` 规则拦掉）
- [ ] 没有暂存构建产物、缓存或 `.demo/` 内容
- [ ] 发布提交上 CI workflow 为绿

## 7. 宣称审计

最难的一份清单，也是最常被跳过的：

- [ ] 没有任何表述暗示已可用于生产
- [ ] 没有任何表述暗示真实金融表现
- [ ] 合成演示数据已标注为合成
- [ ] 未验证的集成已标记为未验证
- [ ] “在 Windows 上验证过”的宣称确实是在 Windows 上验证的

## 8. GitHub 仓库元数据

建议的描述：

```text
AI-assisted risk metric research & development platform with Metric IR,
Skills, deterministic SQL generation, experiments, reflection and governed
metric lifecycle.
```

建议的 topics：

```text
ai-agent  llm  risk-management  fastapi  vue  pydantic
agentic-workflow  metric-engineering
```

- [ ] 描述已设置
- [ ] topics 已添加
- [ ] `README.md` 中的 `OWNER/REPO` 占位符已替换（或移除 CI badge）
- [ ] 仓库已公开且默认分支为 `main`
- [ ] `docs/screenshots/` 与 `docs/demo/` 已提交（未被忽略）

## 9. 打 tag

```bash
git init
git add .
git commit -m "feat: release AIRI v1.0 portfolio edition"
git tag -a v1.0.0 -m "AIRI v1.0.0 — Portfolio Edition"
```

推送，并用 `CHANGELOG.md` 中的说明创建 GitHub release。

> 推送是手动、有意的步骤。本仓库中没有任何东西会替你推送。
