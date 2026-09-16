# SDD 项目

本项目由 SDD Harness 管理。身份、项目类型和规范选择见 `.sdd/project.json`。

- `docs/PRD.md`：需求、业务规则和验收标准。
- `docs/tech-spec.md`：技术方案、接口与数据约定。
- `.sdd/tasks.json`：任务和运行状态，由编排器维护。
- `docs/ui-style.md`：有界面时的设计风格，由 UI Skill 生成并供实现、验收引用。
- `docs/prototypes/`：可选界面设计。
- `.sdd/`：阶段摘要、工作日志、经验与验证报告。

`specification` 为集合名称时按需读取该集合，为 `null` 时不加载规范集。核心工作规则位于 Harness 根目录；本项目 `AGENTS.md` 提供轻量入口。

## 运行环境

- Python：3.11+（项目内 `.venv`，不 pip 安装 `pycore`）。
- 后端配置：复制 `backend/.env.example` 为 `backend/.env`，填写 `secret_key`；百炼与 SMTP 可留空。
- 前端：`VITE_API_BASE_URL=/api`，代理目标默认 `http://localhost:8099`。

## 启动命令

后端（必须从 `backend/` 启动，并用 `PYTHONPATH=..` 引入 pycore）：

```bash
cd backend
PYTHONPATH=.. ../.venv/bin/python -m uvicorn src.main:app --reload --host 127.0.0.1 --port 8099
```

前端（Agent 开发端口）：

```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5199
```

用户门禁前端端口为 `5175`，并把后端代理切到 `8003`：

```bash
cd frontend
VITE_BACKEND_PROXY_TARGET=http://localhost:8003 npm run dev -- --host 127.0.0.1 --port 5175
```

## 测试

```bash
.venv/bin/python -m pip show pytest-timeout
.venv/bin/python -m pytest backend/tests --timeout=120
```

## 页面功能导航

打开 `docs/project-console.html`（用本机浏览器直接打开即可）。这是页面→功能→接口→算法的静态导航，不启动业务服务，也不要占用门禁端口 `5175`。

结构化数据在 `docs/project-map.json`。HTML 内嵌了同一份地图，便于 `file://` 打开；也可用页面里「刷新与核对」手选 JSON。

产物沿用 T-011 / T-022 的路径与结构。T-031 只刷新导航内容（欢迎 Key、虚拟头像任务栏、API-016/017/018），不改业务代码或原任务状态。

### 刷新方式

1. 对 `project-map.json` 里列出的源码路径重算指纹（sha256 前 16 位）：

```bash
python3 - <<'PY'
from hashlib import sha256
from pathlib import Path
root = Path(".")  # 在本项目根目录执行（含 docs/、backend/、frontend/）
for path in [
    "frontend/src/pages/OnboardingPage.tsx",
    "frontend/src/pages/ChatPage.tsx",
    "frontend/src/pages/ApplicationsPage.tsx",
    "frontend/src/pages/KnowledgePage.tsx",
    "frontend/src/components/AppHeader.tsx",
    "frontend/src/components/AccountMenu.tsx",
    "frontend/src/components/KeyPanel.tsx",
    "frontend/src/components/ResumePanel.tsx",
    "backend/src/services/agent.py",
    "backend/src/services/tools.py",
    "backend/src/services/knowledge_search.py",
    "backend/src/services/knowledge_store.py",
    "backend/src/services/candidate.py",
    "backend/src/services/llm_client.py",
    "backend/src/services/llm_probe.py",
    "backend/src/services/scheduler.py",
    "backend/src/config/settings.py",
    "backend/.env.example",
]:
    p = root / path
    print(sha256(p.read_bytes()).hexdigest()[:16], path)
PY
```

2. 若指纹与地图中 `sources[].fingerprint` 不一致：把对应功能/接口/算法的 `implementation_status` 改为 `来源已变化待复核`，不要只改 `generated_at`。
3. 定向重读源码后，才能改回 `源码已核对` 并更新指纹。
4. 保存 `docs/project-map.json` 后，重新打开 HTML，或在「刷新与核对」粘贴/手选新 JSON。
5. 打开页面后点「隔离样例」：源码变更应变成待复核、来源缺失与解析失败应明确提示；过期算法不得继续标已核对。
