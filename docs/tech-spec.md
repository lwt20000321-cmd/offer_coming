# 面试陪伴 Agent 七层技术方案

status: Confirmed  
specification: default  
project_id: offer_coming  
关联决策：`docs/decisions.md`（D-001–D-004 已于 2026-09-13 确认；D-005-A、D-006-A、D-007-C、D-008-B、D-009-B 已于 2026-09-14 确认）

覆盖已确认 PRD REQ-001–011 / AC-001–059。本轮补充：今日五题与催促邮件对用户只出题干；分配、优先级、考查点留到点评。沿用 API-001–015 路径与核心语义。

---

## 一、用户要完成什么

引用 PRD「用户目标与完整流程」「需求与验收」「界面约定」。页面四类：

| 页面 | 入口 | 原型 |
| --- | --- | --- |
| UI-01 邮箱 + 简历 + 百炼 Key | 无有效接续时 | [初次见面](prototypes/ui-prototype.html#welcome)；本轮须加 Key 栏，阶段 B 更新 |
| UI-02 小凹对话 | 顶栏「日常对话」 | [日常对话](prototypes/ui-prototype.html#chat) |
| UI-03 我的投递 | 顶栏**最右侧**，与日常对话齐平 | 待阶段 B 补原型；风格见 [docs/ui-style.md](ui-style.md) |
| UI-04 我的面经 | 顶栏、在「我的投递」**左侧**、与日常对话齐平 | 待阶段 B |
| 账号头像 / 任务栏 | 已接续后，各业务页；点头像下方展开 | 布局与面板留给阶段 B；本文件锁定接口字段与等待/失败 |

岗位跟进、催促、五题、发面经、心理辅导仍在同一条小凹对话。后台为主体 + 四个子 Agent，五套不同模型配置（名称由运营 `.env` 配置，用户不填）。过程说明只写业务，不出现主体/子 Agent/工具名。求职者虚拟头像由产品提供静态资源，不上传真人照片，不是第二个对话角色。

数据链路（用户输入 → 系统处理 → 用户结果）：

1. 填写邮箱、粘贴一把百炼 Key 并上传简历（API-001）→ 格式校验后向百炼探测（D-007-C）→ 401 则拒绝进入；其它探测失败仍加密存 Key 并提示「这次没连上」→ `session_token` 写入 `localStorage` → 进入对话，顶栏「初次见面」变为虚拟头像（AC-001、050）。未完成邮箱、简历与 Key 不能进对话、投递表、面经页（AC-002、023、043）。
2. 同浏览器有令牌：API-003/004/005/007/008/012 接上，不必再填邮箱、简历或 Key（AC-017，D-001-A）。换设备或令牌丢失：只填同一邮箱调 API-002；该邮箱已有简历与 Key 则不必再贴 Key（AC-021）。已有简历无 Key：API-002 返回须补贴，贴 Key 后再进入（同样走 D-007-C 探测），不必再传简历（AC-049）。
3. 进入后点头像下方任务栏（阶段 B 布局）：「我的简历」走 API-016 替换文件（AC-051）；「我的key」走 API-017 只提交新 Key、不回显旧 Key（AC-052，保存时同样探测）；「退出」调用 API-018 作废本张令牌并清本机接续、不删档（AC-053，D-008-B）。
4. 真实对话 / 出题 / 催促文案 / 面经检索：`LlmClient` 只用该求职者解密后的 Key 调 EXT-001 / EXT-004，**禁止**回落到运营 `.env` 的 `llm_api_key`。Key 无效或被拒绝：对话不假装成功，提示到「我的key」更换（AC-054）。
5. 对话发送已投岗位与 JD 链接（API-006 SSE）→ **先**由主体模型规划（AC-033）→ 面试子 Agent 读 JD（EXT-002）出考查点；投递催促子 Agent 写/更新投递表（同一链接不新增行，投递时间默认当天北京日期）；知识库子 Agent **异步**检索面经（不阻塞考查点）。读失败不写只有失败链接的完整行（AC-003、004、027、035、041）。上述模型调用均带该求职者 Key。
6. 对话或投递表维护进度、状态、面试时间（API-006 / API-009–011）。已挂不出题；面试时间为空不以截止日加急（AC-005、006、045）。改为已挂且库中有相关面经时，知识库先判断整篇/局部是否仍有用，**对话里询问，未经同意不删**（AC-047、048）。在投递表保存已挂后，自动回到日常对话并马上展示该询问（D-006-A）。
7. 面经文件在「我的面经」上传（API-015）；对话可粘贴（API-006）。「我的面经」看列表/正文、可手删（API-012–014）。检索走百炼联网搜索（D-005-A / EXT-004，用求职者 Key），考查点仍先出；检索成功后对话提一句，失败说明并可改上传或粘贴，不编造，不保证来自小红书，也不抓取小红书（AC-037–040、042–044、046）。
8. 北京时间 10:00–24:00 整点：投递催促模型（求职者 Key）生成对话催促 + SMTP 邮件（EXT-003）。越晚越狠。辅导中不发狠催。当日完成则停催（AC-007–009、012、015、016、019、036）。Key 无效或缺失：不调模型、不发骂醒催促；对话 `error_notice` + 当日至多一封固定模板邮件（D-009-B）。
9. 出题日对「等待面试」合计五题（默认 3 道简历深挖 + 2 道业务场景）；催促模型按缓急决定对准哪些岗位，面试模型出题并尽量用知识库。答完点评并**追加**写入相关岗位「面试总结」，次日若仍有等待面试则 10:00 出新五题。未完成则 24:00 作废，第二天不催这些题，第三天再出（AC-010、011、018、020、040，D-004-A）。
10. 表达焦虑 → 主体只分给心理辅导（AC-013、030、034）。说心情变好 → 同一轮再派投递催促和/或面试，不等下一整点（AC-014、031）。外观始终小凹（AC-032）。

业务规则以 PRD 为准，此处不另写第二套需求。流程图与时序图以 PRD「流程图与时序图」为准（已按 D-005-A / D-006-A / D-007-C / D-008-B / D-009-B 对齐）。接口字段只在本文件维护。

---

## 二、前后端怎么分工，接口怎么拆

### 分工与栈

| 侧 | 职责 | 选型（default） |
| --- | --- | --- |
| 前端 | UI-01–04、接续、SSE 对话、投递表离格保存、面经列表/上传/删除、轮询催促与已挂询问、已接续后虚拟头像与任务栏（简历/Key/退出） | React + TypeScript + Vite + React Router；Hooks / Context；Axios 经 `services/` |
| 后端 | 求职者与会话、简历解析、主体规划 + 四子 Agent 工具循环、投递表、知识库、北京时间调度、发信 | Python 3.11+ FastAPI + PyCore（`PYTHONPATH=..`）；SQLAlchemy 异步 + SQLite |
| 外部 | 五套模型；拉 JD；已投岗位公开面经检索（D-005-A）；SMTP | 百炼 OpenAI 兼容 HTTP（httpx，`trust_env=False`，禁止 dashscope SDK）；联网搜索 `enable_search`；目标站点 GET；用户 SMTP |

禁止：完整注册密码体系、dashscope SDK、进程环境读取业务配置、前端硬编码后端端口、把 Mock 写成真实 AC 通过、用户流量回落到运营 `llm_api_key`、响应或日志回显完整 Key、用户自填五个模型名或其他厂商。

- 后端基础 URL：Vite 把 `/api` 代理到 Agent 端口 `8099`（门禁 `8003`）。`VITE_API_BASE_URL=/api`。
- 鉴权：`Authorization: Bearer <session_token>`。公开接口仅 API-001、API-002。
- 前端请求顺序：打开 → 有 token 则 API-003；401 或无 token 则初次见面。新人：邮箱 + Key + 简历 → API-001。邮箱已有简历与 Key：只填邮箱 → API-002。邮箱已有简历无 Key：API-002 先返回须补贴，再带 `llm_api_key` 重发 API-002（AC-049，D-007-C 探测）。成功后写 `localStorage`。进入后 API-004 + API-005；发送走 API-006；每 20 秒用 `after_id` 拉催促/已挂询问。任务栏：简历 API-016，Key API-017，退出先 API-018 再清 `localStorage`（D-008-B）。投递表：API-007 列表，离格 API-010，加行 API-009，删行 API-011。API-010 返回 `knowledge_review_pending=true` 时立刻切到日常对话再拉 API-005（D-006-A）。面经页：API-012 列表，API-015 上传，点开 API-013，删除 API-014。
- 已接续时顶栏不再请求「初次见面」路由作为表单页；头像与任务栏由前端根据有效 token 渲染，不新增第四个业务 API 页。
- JSON 接口统一 PyCore 信封：`success`、`data`、`error`、`error_code`、`message`、`timestamp`、`request_id`、`metadata`。API-006 成功路径为 SSE。

内部调度不是用户 HTTP 资源：后端按 `Asia/Shanghai` 整点执行催促/作废/出题。整点催促**直接**调投递催促模型，不伪装成用户说了一句话。

资源词来自 PRD 名词：求职者 `candidates`、接续 `sessions`、对话 `conversations`、投递 `applications`、题集 `question_sets`、面经/知识库条目 `knowledge_items`。

### 接口索引

| 编号 | Method / URL | 用途 | 关联 |
| --- | --- | --- | --- |
| API-001 | POST `/api/candidates` | 邮箱+简历+百炼 Key 创建求职者并开会话 | REQ-001；AC-001、002 |
| API-002 | POST `/api/sessions` | 用邮箱接续；无 Key 时本接口补贴 | REQ-001；AC-017、021、049 |
| API-003 | GET `/api/candidates/current` | 当前求职者、简历、任务摘要、Key 是否已存（不回显 Key） | REQ-001、011；AC-017、050–052 |
| API-004 | GET `/api/conversations/current` | 取或创建唯一对话 | REQ-001；AC-001、017 |
| API-005 | GET `/api/conversations/{conversation_id}/messages` | 拉历史、催促、已挂询问 | REQ-004–007、010；AC-007–019、047 |
| API-006 | POST `/api/conversations/{conversation_id}/messages` | 用户说话或粘贴面经；成功为 SSE | REQ-002–006、009、010、011；AC-003–006、010–014、026、027、029–041、047、048、054 |
| API-007 | GET `/api/applications` | 投递表列表（对话接续 + UI-03） | REQ-002、003、008；AC-005、006、017、022、026 |
| API-008 | GET `/api/question-sets/current` | 当日题集状态 | REQ-005；AC-010–012、018、020 |
| API-009 | POST `/api/applications` | 投递表加一行 | REQ-008；AC-025、028 |
| API-010 | PATCH `/api/applications/{application_id}` | 改格子（含面试时间、已挂） | REQ-003、008、010；AC-024、045、047 |
| API-011 | DELETE `/api/applications/{application_id}` | 删一行，不再出题、不再检索 | REQ-008；AC-025 |
| API-012 | GET `/api/knowledge-items` | 「我的面经」列表 | REQ-010；AC-037–039、042–044 |
| API-013 | GET `/api/knowledge-items/{knowledge_item_id}` | 面经正文 | REQ-010；AC-042 |
| API-014 | DELETE `/api/knowledge-items/{knowledge_item_id}` | 用户手删一条，立即生效 | REQ-010；AC-046 |
| API-015 | POST `/api/knowledge-items` | 「我的面经」上传文件并收录 | REQ-010；AC-039 |
| API-016 | POST `/api/candidates/current/resume` | 任务栏替换简历 | REQ-011；AC-051 |
| API-017 | PUT `/api/candidates/current/llm-key` | 任务栏只提交新 Key | REQ-011；AC-052、054 |
| API-018 | DELETE `/api/sessions/current` | 退出时作废本张令牌（D-008-B，必须实现） | REQ-011；AC-053 |
| EXT-001 | POST 百炼 `.../chat/completions` | 五套模型与工具调用；Bearer = **求职者** Key | 第四、五层 |
| EXT-002 | GET 用户提供的 JD URL | 读取岗位文本 | AC-003、004 |
| EXT-003 | SMTP 发信 | 催促邮件 | REQ-007；AC-015、016 |
| EXT-004 | 百炼 Chat Completions + `enable_search` | 已投岗位公开面经检索（D-005-A） | REQ-010；AC-037、038 |

---

### API-001 创建求职者（邮箱 + 简历 + 百炼 Key）

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-001；AC-001、AC-002 |
| Method / URL | POST `/api/candidates` |
| 请求 | `multipart/form-data`。`email` string 必填；`resume` file 必填；`llm_api_key` string 必填（用户只贴一把百炼 Key，不填模型名）。无 Token。 |
| 成功响应 | 201。`data`: `SessionStartPublic`。`candidate` 含 `has_llm_api_key=true`、`llm_key_status`（见第三层）。另含 `llm_key_probe_status`：`ok` \| `unreachable`（本次探测结果；**禁止**回传 `llm_api_key` 或密文）。 |
| 失败响应 | 400 `VALIDATION_ERROR`（缺邮箱/简历/Key、格式不符）；400 `LLM_KEY_INVALID`（探测为 401/`invalid_api_key`，D-007-C，**不建档**）；409 `CONFLICT`（该邮箱已有求职者：请走 API-002）。见第六层。 |
| 鉴权 | 公开。成功后前端写 `session_token`（D-001-A）。 |

成功示例：

```json
{
  "success": true,
  "data": {
    "session_token": "opaque-session-token",
    "candidate": {
      "id": "c_01",
      "email": "user@example.com",
      "resume_filename": "resume.pdf",
      "resume_parse_ok": true,
      "has_llm_api_key": true,
      "llm_key_status": "saved",
      "created_at": "2026-09-14T06:40:00Z"
    },
    "conversation_id": "cv_01",
    "llm_key_probe_status": "ok"
  },
  "error": null,
  "error_code": null,
  "message": "已进入小凹",
  "timestamp": "2026-09-14T06:40:00.000000",
  "request_id": "req_001",
  "metadata": {}
}
```

失败示例（缺 Key）：400，`error` 为「请先填写邮箱、粘贴百炼 Key 并上传简历，才能和小凹对话。」，`error_code`=`VALIDATION_ERROR`。缺简历同文案。邮箱已存在：409，「这个邮箱已有记录，请直接用邮箱进入」。探测 401：400 `LLM_KEY_INVALID`，「这把百炼 Key 不能用，请检查后重贴。」

Key 只做 trim，须以 `sk-` 开头（[百炼错误码：API Key 以 sk- 开头](https://help.aliyun.com/zh/model-studio/error-code)）。不接受空串。保存前按 D-007-C 探测：仅 401/`invalid_api_key` 拒绝；429/5xx/超时仍加密落库，`llm_key_probe_status=unreachable`，信封 `message` 为「已进入小凹。这次没连上百炼，之后若失败请到「我的key」再试。」等待：提交后按钮「正在确认 Key」，约 2–10 秒，不可连点。

---

### API-002 用邮箱接续

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-001；AC-017、AC-021、AC-049 |
| Method / URL | POST `/api/sessions` |
| 请求 | `application/json`。`SessionCreate`：`email` string 必填；`llm_api_key` string 可选。无 Token、无验证码字段。 |
| 成功响应 | 200。`data`: `SessionStartPublic`（同 API-001 结构，不含完整 Key）。 |
| 失败响应 | 400 `VALIDATION_ERROR`；404 `NOT_FOUND`（尚无简历，走 API-001）；409 `LLM_KEY_REQUIRED`（已有简历、库中无 Key，且本次未带可用 Key）；400 `LLM_KEY_INVALID`（补贴 Key 时探测为 401，D-007-C）。 |

分支（同步）：

| 条件 | 结果 |
| --- | --- |
| 邮箱无记录 | 404，「这个邮箱还没有简历记录，请先上传简历并粘贴 Key。」 |
| 已有简历且已有 Key，本次不带或忽略多余 Key | 200，不必再贴 Key（AC-021）。**已有 Key 时忽略请求里多带的 `llm_api_key`，不在此接口覆盖**；换 Key 只走已登录的 API-017。 |
| 已有简历、无 Key，且未带 `llm_api_key` | 409 `LLM_KEY_REQUIRED`，「这个邮箱还没有百炼 Key，请补贴后才能进入。」前端展示 Key 栏后，用同一邮箱 + Key 再调本接口（AC-049）。 |
| 已有简历、无 Key，且带了 Key | 先 D-007-C 探测：401 则 400 不写入；否则加密保存后 200，不必再传简历。`llm_key_probe_status` 为 `ok` 或 `unreachable`。 |

成功体默认 `message` 为 `"已接上原来的简历和任务"`。补贴 Key 且探测 `unreachable` 时改为「已接上原来的简历和任务。这次没连上百炼，之后若失败请到「我的key」再试。」仅邮箱接续（AC-021）不探测，`llm_key_probe_status` 为 `null`。

---

### API-003 当前求职者摘要

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-001、REQ-011；AC-017、AC-050–052 |
| Method / URL | GET `/api/candidates/current` |
| 请求 | 无 body。`Authorization: Bearer <session_token>`。 |
| 成功响应 | 200。`data`: `CandidateCurrentPublic`：沿用 `id`、`email`、`resume_filename`、`resume_parse_ok`、`application_count`、`waiting_interview_count`、`today`；**新增** `has_llm_api_key` bool、`llm_key_status`（`missing` \| `saved` \| `invalid`）。**禁止**任何 Key 明文、密文、指纹出现在响应。 |
| 失败响应 | 401 `UNAUTHORIZED`。 |

`today`：`beijing_date`、`is_question_day`、`is_rest_day`、`question_set_status`、`counseling_active`。

「我的简历」面板用 `resume_filename` + `resume_parse_ok` 确认在档，不另开 GET 下载正文接口（对话侧仍用库内解析文本）。`has_llm_api_key` / `llm_key_status` 只用于「我的key」展示「已保存 / 请更新」，输入框始终空，只接受新 Key。

无虚拟头像字段：头像是前端静态资源，不经本接口下发、不上传。

---

### API-004 当前对话

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-001；AC-001、AC-017 |
| Method / URL | GET `/api/conversations/current` |
| 请求 | 无 body。Bearer token。一名求职者仅一条对话。 |
| 成功响应 | 200。`data`: `ConversationPublic`（`id`、`candidate_id`、`created_at`）。 |
| 失败响应 | 401。 |

---

### API-005 拉取消息

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-004–007、REQ-010；AC-007–016、018、019、047 |
| Method / URL | GET `/api/conversations/{conversation_id}/messages` |
| 请求 | Query：`after_id` string 可选；`limit` int 可选，默认 200，最大 200。无 `after_id` 时返回该对话**最近** `limit` 条（按 `created_at` 正序）。有 `after_id` 时返回该条之后的新消息。Bearer。路径对话必须属于当前求职者。 |
| 成功响应 | 200。`data`: `{ "messages": MessagePublic[] }`。 |
| 失败响应 | 401；403 `FORBIDDEN`；404 `NOT_FOUND`。 |

`MessagePublic`：`id`、`role`（`user`\|`assistant`）、`message_type`、`content`、`created_at`。

`message_type` 沿用并扩展：`chat` | `nudge` | `questions` | `review` | `counseling` | `jd_summary` | `error_notice` | `kb_notice` | `kb_ask`。

成功示例（整点催促）与旧契约相同，`message_type`=`nudge`。已挂询问为 `kb_ask`。

---

### API-006 发送用户消息（小凹；含对话内粘贴）

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-002–006、009、010；见索引 |
| Method / URL | POST `/api/conversations/{conversation_id}/messages` |
| 请求 | **文本（沿用）**：`application/json`，`MessageCreate`：`content` string 必填，1–8000 字。**兼容 multipart**：`content` 可选，`file` 可选；产品主入口已改为 API-015，对话页不再发文件。Bearer。同一对话同时只允许一轮 Agent；第二发 409。 |
| 成功 | HTTP 200，`Content-Type: text/event-stream`（D-002-B）。`Accept: text/event-stream`。 |
| 失败 | 入参 400、鉴权/属主 401/403/404、并发 409：JSON 信封，不开流。模型或工具失败：HTTP 200，流内 `error`。 |

事件名：

- `status`：`{ "stage": "thinking" \| "fetch_jd" \| "update_application" \| "search_experiences" \| "ingest_document" \| "counseling" \| "questions" \| "review" \| "reply", "text": "小凹正在思考..." }`。首事件必须是 `thinking` / 「小凹正在思考...」（AC-055）；随后可换成更具体的业务句。`text` 只写业务（AC-041），不出现主体/子 Agent/工具名。
- `delta`：`{ "text": "逐字增量" }`。用户可见正文必须随模型或节拍增量发出，禁止等整段生成完再一次性切片假装流式。
- `done`：`{ "message": MessagePublic, "snapshot": CandidateCurrentPublic }`
- `error`：`{ "error": "……", "error_code": "JD_FETCH_FAILED" \| "KB_SEARCH_FAILED" \| "LLM_UNAVAILABLE" \| "LLM_KEY_INVALID" \| "LLM_KEY_MISSING" \| "LLM_QUOTA_EXCEEDED" }`

SSE 成功片段示例：

```
event: status
data: {"stage":"thinking","text":"小凹正在思考..."}

event: status
data: {"stage":"fetch_jd","text":"正在读取岗位链接"}

event: status
data: {"stage":"search_experiences","text":"正在查找面经"}

event: delta
data: {"text":"对照你的简历，这个岗位主要考查"}

event: done
data: {"message":{"id":"m_11","role":"assistant","message_type":"jd_summary","content":"对照你的简历……","created_at":"2026-09-14T06:50:00Z"},"snapshot":{"id":"c_01","email":"user@example.com","resume_filename":"resume.pdf","resume_parse_ok":true,"has_llm_api_key":true,"llm_key_status":"saved","application_count":1,"waiting_interview_count":1,"today":{"beijing_date":"2026-09-14","is_question_day":true,"is_rest_day":false,"question_set_status":"pending","counseling_active":false}}}
```

并发 409 示例沿用：`error` 为「小凹还在回复这一句，请等它说完再发。」

本接口**必须先**调用主体模型再分派（AC-033）。用户侧无工具按钮。本轮 EXT-001 / 子 Agent / EXT-004 的 Bearer **只**用该求职者解密 Key；缺 Key 或 401 `invalid_api_key` 不得改用运营 `llm_api_key`。Key 问题走流内 `LLM_KEY_INVALID` / `LLM_KEY_MISSING`，用户可见文案指向任务栏「我的key」（AC-054）。

---

### API-007 投递列表

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-002、003、008；AC-005、006、017、022、026 |
| Method / URL | GET `/api/applications` |
| 请求 | Bearer。无 body。UI-03 与对话接续共用；**不再**「仅供页头、不做表页」。 |
| 成功响应 | 200。`data`: `{ "applications": ApplicationPublic[] }`。 |
| 失败响应 | 401。 |

`ApplicationPublic` **沿用** `id`、`company_name`、`role_title`、`jd_url`、`exam_points`、`progress_text`、`status_text`、`normalized_status`、`deadline`、`updated_at`；**新增**表列所需字段，不删除旧字段：

| 字段 | 类型 | 表列 / 用途 |
| --- | --- | --- |
| `applied_at` | string `YYYY-MM-DD` | 投递时间（北京日期） |
| `interview_at` | string \| null | 面试时间：可空；`YYYY-MM-DD` 或带偏移的 ISO 时刻 |
| `interview_summary` | string | 面试总结；点评追加不覆盖 |
| `deadline` | string \| null | **兼容旧字段**：等于 `interview_at` 的北京日期；空则不按截止日加急 |
| `knowledge_review_pending` | bool | **仅 API-010**：本轮因已挂且有相关面经而写入了 `kb_ask` 则为 true；列表接口不带此字段 |

成功示例：

```json
{
  "success": true,
  "data": {
    "applications": [
      {
        "id": "a_01",
        "company_name": "示例公司",
        "role_title": "后端开发",
        "jd_url": "https://example.com/job/1",
        "exam_points": "结合简历中的 Python 项目，考查……",
        "progress_text": "已约下周一面",
        "status_text": "等待面试",
        "normalized_status": "waiting_interview",
        "applied_at": "2026-09-14",
        "interview_at": "2026-09-20",
        "interview_summary": "",
        "deadline": "2026-09-20",
        "updated_at": "2026-09-14T07:00:00Z"
      }
    ]
  },
  "error": null,
  "error_code": null,
  "message": "ok",
  "timestamp": "2026-09-14T07:00:01.000000",
  "request_id": "req_009",
  "metadata": {}
}
```

对话写入仍由 API-006 工具完成；表页写入走 API-009–011。

---

### API-008 当日题集

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-005；AC-010、011、012、018、020 |
| Method / URL | GET `/api/question-sets/current` |
| 请求 | Bearer。按北京「今天」。休息日 `status=rest_day` 且 `questions` 为空。 |
| 成功响应 | 200。`data`: `QuestionSetPublic`。无题集时 `status=none` 空壳，不是 404。 |
| 失败响应 | 401。 |

`questions[]`：`id`、`kind`（`common`\|`role`）、`prompt`、`answer`、`target_application_id`。`status`：`none` | `pending` | `in_progress` | `completed` | `voided` | `rest_day`。

---

### API-009 投递表加行

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-008；AC-025、AC-028 |
| Method / URL | POST `/api/applications` |
| 请求 | `application/json`。`ApplicationCreate`：下列均可空。`company_name`、`role_title`、`jd_url`、`applied_at`、`interview_at`、`status_text`、`interview_summary`、`progress_text`。Bearer。 |
| 成功响应 | 201。`data`: `ApplicationPublic`。未手填 `applied_at` 时服务端填当天北京日期。空行可保存。 |
| 失败响应 | 400；401；409（`jd_url` 非空且该求职者已有同一链接：应改走 API-010，不新增）。 |

成功示例：`data` 为一行 `ApplicationPublic`，`applied_at` 为当天，`interview_at`/`deadline` 为 null，`normalized_status` 由 `status_text` 归一（空则 `other`）。

若本次写入后公司与岗位均可识别，且尚未为该行检索过：后台启动知识库检索（不阻塞 201）。无链接则不读 JD、不出考查点。

---

### API-010 改投递表格子

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-003、008、010；AC-024、045、047 |
| Method / URL | PATCH `/api/applications/{application_id}` |
| 请求 | `application/json`。`ApplicationUpdate`：只传要改的字段。可改 `company_name`、`role_title`、`jd_url`、`applied_at`、`interview_at`（`null` 表示清空）、`status_text`、`interview_summary`、`progress_text`。Bearer。属主校验。 |
| 成功响应 | 200。`data`: 更新后的 `ApplicationPublic`，并含 `knowledge_review_pending` bool。`deadline` 与 `interview_at` 的北京日期同步。 |
| 失败响应 | 400；401；403；404；409（改后的非空 `jd_url` 与另一行冲突）。 |

前端：格子失焦且值变化才调用（AC-024、045）。`status_text` 归一为已挂后：该行不再出题；若有相关面经，写入 `kb_ask` 且 `knowledge_review_pending=true`（未经同意不删）。此时前端立刻切到「日常对话」并用 API-005 展示询问（D-006-A）。无相关面经则为 `false`，不跳转。

清空 `interview_at` 后催促不得把该行当「截止日更近」（AC-045）。

---

### API-011 删除投递行

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-008；AC-025 |
| Method / URL | DELETE `/api/applications/{application_id}` |
| 请求 | 无 body。Bearer。 |
| 成功响应 | 200。`data`: `{ "id": "a_01", "deleted": true }`。该岗位不再出题、不再检索。已入库面经**不**因删行自动删除（PRD 只对「已挂」要求先问再删；手删岗位后不再为该岗检索，既有条目仍在「我的面经」，用户可手删）。 |
| 失败响应 | 401；403；404。 |

---

### API-012 「我的面经」列表

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-010；AC-037–039、042–044 |
| Method / URL | GET `/api/knowledge-items` |
| 请求 | Bearer。Query：`application_id` 可选。 |
| 成功响应 | 200。`data`: `{ "items": KnowledgeItemListPublic[] }`。无条目时 `items` 为空数组，不是 404（AC-044）。 |
| 失败响应 | 401。未完成邮箱、简历与 Key 不能持有可进业务页的 token，前端拦在提示页（AC-043）。 |

`KnowledgeItemListPublic`：`id`、`title`、`source_type`（`search`\|`upload`\|`paste`）、`source_url`（可空）、`application_id`（可空）、`company_name`、`role_title`、`created_at`、`excerpt`（正文前 200 字）。**不含**伪造「已找到」条目。

---

### API-013 面经正文

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-010；AC-042 |
| Method / URL | GET `/api/knowledge-items/{knowledge_item_id}` |
| 请求 | 无 body。Bearer。必须属于当前求职者。 |
| 成功响应 | 200。`data`: `KnowledgeItemPublic`（列表字段 + `body` + `segments`）。 |
| 失败响应 | 401；403；404。 |

`segments[]`：`id`、`ordinal`、`text`、`status`（`active`\|`pending_delete`\|`deleted`）。已挂询问期间建议删除的段为 `pending_delete`，用户回答前仍出现在正文（AC-047）。

---

### API-014 用户手删面经

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-010；AC-046 |
| Method / URL | DELETE `/api/knowledge-items/{knowledge_item_id}` |
| 请求 | 无 body。Bearer。 |
| 成功响应 | 200。`data`: `{ "id": "k_01", "deleted": true }`。立即从列表和检索中消失，后续出题不再用这条。不必再问。 |
| 失败响应 | 401；403；404。 |

Agent 建议删除**不**走本接口，走 API-006 用户回答后由知识库工具执行（AC-048）。

---

### API-015 「我的面经」上传

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-010；AC-039 |
| Method / URL | POST `/api/knowledge-items` |
| 请求 | Bearer。`multipart/form-data`，字段 `file` 必填。格式 `.pdf` / `.docx` / `.txt` / `.md`，上限 `knowledge_max_bytes`。 |
| 成功响应 | 201。`data`: `KnowledgeItemPublic`。正文已在库中则返回已有条目，不新增行。 |
| 失败响应 | 400 空文件/不支持格式/读不出正文；401；文件过大。 |

本接口直接解析入库，不走对话 SSE。对话粘贴仍走 API-006。

---

### API-016 任务栏替换简历

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-011；AC-051 |
| Method / URL | POST `/api/candidates/current/resume` |
| 请求 | Bearer。`multipart/form-data`，字段 `resume` file 必填。格式与上限同 API-001（`.pdf` / `.docx` / `.txt`，`resume_max_bytes`）。不传邮箱、不传 Key。 |
| 成功响应 | 200。`data`: `CandidateCurrentPublic`（含新的 `resume_filename`、`resume_parse_ok`）。用户仍留在当前业务页。 |
| 失败响应 | 400 空文件/不支持格式/过大/解析失败（**旧简历文件与文本保持不变**）；401；403（无已存 Key 时不得改简历，先走补贴 Key）。 |

等待：按钮「保存中」，不可连点。成功：面板提示「已换成 {filename}」，任务栏可收起，不必重新填邮箱或 Key。失败：面板内中文原因，输入框可再选文件。

成功后库内 `resume_text` 立即供后续考查点/出题使用（AC-051）。不重签发 `session_token`。

---

### API-017 任务栏更换百炼 Key

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-011；AC-052、AC-054 |
| Method / URL | PUT `/api/candidates/current/llm-key` |
| 请求 | Bearer。`application/json`。`LlmKeyUpdate`：`llm_api_key` string 必填。只接受新 Key；没有「读取旧 Key」的 GET。 |
| 成功响应 | 200。`data`: `{ "has_llm_api_key": true, "llm_key_status": "saved", "llm_key_probe_status": "ok" \| "unreachable" }`。探测成功时 `message` 为「已保存新的百炼 Key」；`unreachable` 时 `message` 为「已保存新的百炼 Key。这次没连上百炼，之后若失败请到「我的key」再试。」**禁止**回传新旧 Key、密文、后四位。 |
| 失败响应 | 400 `VALIDATION_ERROR`（空/非 `sk-` 开头）；400 `LLM_KEY_INVALID`（探测为 401/`invalid_api_key`，D-007-C，**旧 Key 不变**）；401。 |

等待：按钮「正在确认 Key」，约 2–10 秒，不可连点。成功：输入框清空，展示「已保存」（`unreachable` 时同时展示「这次没连上」），而非任何 Key 字符。失败：Key 未覆盖，旧 Key 仍在；用户可再贴。保存成功后，后续 API-006 / 调度 / EXT-004 使用新 Key。

本接口是已登录后换 Key 的**唯一**写入口。初次见面补贴走 API-002，不走本接口（当时可能还没有 token）。

---

### API-018 退出：作废本张接续令牌（D-008-B）

| 项目 | 内容 |
| --- | --- |
| 关联需求 | REQ-011；AC-053 |
| Method / URL | DELETE `/api/sessions/current` |
| 请求 | 无 body。Bearer。 |
| 成功响应 | 200。`data`: `{ "revoked": true }`。只删除**当前这条** `token_hash` 对应会话，不删求职者、简历、Key、投递、对话。其它设备上其它 token 仍有效。 |
| 失败响应 | 401（视为已退出，前端仍清本地）。 |

**必须实现。** 前端点「退出」：先调本接口，再清 `localStorage` 的 `session_token`，回到 UI-01。本机其它标签再用旧 token 调 API-003 得 401，不再自动接上。同一邮箱之后仍走 API-002（不必再传简历、不必再贴 Key）。

---

### EXT-001 百炼 Chat Completions

来源：[通过 OpenAI 接口调用千问](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[文本生成模型列表](https://help.aliyun.com/zh/model-studio/text-generation-model/)。北京共享域名：`https://dashscope.aliyuncs.com/compatible-mode/v1`。

| 项目 | 内容 |
| --- | --- |
| Method / URL | POST `{llm_base_url}/chat/completions` |
| 请求头 | `Authorization: Bearer {candidate_llm_api_key}`；`Content-Type: application/json`。**禁止**填运营 `.env` 的 `llm_api_key`。 |
| 请求体 | `model`（本轮该职责的配置名，五套不得相同）；`messages`；可选 `tools`、`temperature`、`max_tokens`、`stream`；工具角色建议 `enable_thinking: false`（顶层字段，httpx 写入 JSON，不用 SDK `extra_body`） |
| 成功 | 非流式：`choices[0].message.content`；或 `finish_reason=tool_calls` 且 `tool_calls[]`。对用户可见的编排回复：`stream=true`，按 SSE `choices[0].delta.content` 转发为 API-006 `delta`（推理过程 `reasoning_content` 不写出，继续显示思考提示）。 |
| 失败 | HTTP 401/403/404/429/5xx。401/`invalid_api_key` → 用户路径标 `llm_key_status=invalid`，走 AC-054，**不得**改用运营 Key 重试。429 额度 → `LLM_QUOTA_EXCEEDED`，同样不回落。 |
| 约束 | httpx，`trust_env=False`，禁止 `dashscope` SDK。`LlmClient.chat_completions` **必须**接收求职者 Key 参数；未传入或空串则拒绝调用，不读取 `settings.llm_api_key`。模型名、超时、重试只来自 §七。日志只记 status、model、`candidate_id`，禁止记 Authorization 与 Key。 |

`llm_base_url` 仍由运营配置（默认北京兼容域名）。用户只贴 Key。套餐专属 `sk-sp-` Key 与该域名不匹配时，官方返回 401（[百炼错误码](https://help.aliyun.com/zh/model-studio/error-code)）；对用户说明「请使用北京地域通用百炼 Key」，不在产品内改 Base URL、不支持其他厂商。

---

### EXT-002 读取 JD

| 项目 | 内容 |
| --- | --- |
| Method / URL | GET 用户给出的绝对 `http`/`https` URL |
| 请求 | `JD_FETCH_USER_AGENT`；超时 `JD_FETCH_TIMEOUT_SECONDS`；`trust_env=False`；禁止跟随到内网（第五层 SSRF） |
| 成功 | 2xx 且抽到可读文本；工具观察回传截断正文 |
| 失败 | 非 2xx、超时、空文、拦截、禁止地址 → 对话说明，请换链接或补 JD（AC-004）；**不**写入只有失败链接的完整投递行 |

招聘站反爬是预期失败。本次不做无头浏览器或登录抓取。

**小红书链接**：即使用户把 `xiaohongshu.com` / `xhslink.com` 当作 JD 或面经链接发出，服务端**直接判定不可抓**，走 AC-004 / AC-038，不发起对该主机的 GET。依据见第五层「小红书约束」。

---

### EXT-003 催促邮件（SMTP，D-003-A；Key 失效模板 D-009-B）

| 项目 | 内容 |
| --- | --- |
| 协议 | STARTTLS 或 SSL；主机/端口/用户名来自配置 |
| 邮件（正常催促） | `From`=`SMTP_FROM`；`To`=求职者邮箱；主题+正文与当时对话催促同一待办、同一语气档 |
| 邮件（Key 无效，D-009-B） | 不调模型。主题建议「请更新小凹的百炼 Key」。固定正文：「你的百炼 Key 已失效，今日催促和出题发不出来，请到小凹「我的key」更新。」每个求职者每个北京日期至多成功一封（去重键 `llm_key_invalid:{beijing_date}`）。 |
| 成功 | SMTP 接受投递（不保证未进垃圾箱） |
| 失败 | 正常催促：对话催促仍插入，并说明原因（AC-016）。Key 失效模板：对话 `error_notice` 不回滚；当日下一整点可再试发，直到已成功或催促窗口结束。 |

密钥只在 `backend/.env`。未配置 SMTP：对话照发 + AC-016（Key 失效时仍只有对话说明）。

---

### EXT-004 公开面经检索（D-005-A）

知识库检索走百炼 **OpenAI 兼容 Chat Completions** 的内置联网搜索，**不是**小红书爬虫。考查点仍先出，检索异步或同轮后半段完成。

来源：[大模型如何联网搜索](https://help.aliyun.com/zh/model-studio/web-search)（2026 年文档）。httpx JSON 顶层字段（禁止 dashscope SDK）：

```json
{
  "model": "{llm_knowledge_model}",
  "messages": [{"role": "user", "content": "请查找「{公司} {岗位}」已公开的面试经验摘要，不要编造。不要输出小红书帖文伪造成功入库。"}],
  "enable_search": true,
  "search_options": { "forced_search": true },
  "enable_thinking": false
}
```

官方约束（必须写入实现，不能假装没有）：

| 事实 | 对用户的结果 |
| --- | --- |
| Chat Completions **不返回搜索来源列表**（与 DashScope 协议不同） | 无法在接口层核对「是不是小红书」；只能要求模型输出可核对的面经正文；无正文则按失败 |
| 联网搜索按次计费；北京地域 turbo/max 策略自 2026-02-27 起收费；限流 15 RPS（主账号合计，超限时请求成功但**不触发搜索**） | 可能静默搜不到 → 必须走 AC-038，不得编造 |
| `qwen-plus` / `qwen-plus-latest` / `qwen-flash` / `qwen-turbo` / `qwen-max` 等支持 `enable_search` | 知识库模型默认用支持该参数的名称 |

服务端硬规则：不对 `xiaohongshu.com`、`xhslink.com` 发 GET；若模型唯一产出是「小红书上有帖」而无其它可用正文 → `KB_SEARCH_FAILED`，对话按 AC-038 说明并请发文件或粘贴。结果**不保证**来自小红书。检索请求的 Bearer 与 EXT-001 相同，为该求职者 Key；无求职者 Key、Key 无效或搜索未真正触发时走 AC-038 / AC-054，不得编造、不得改用运营 Key、不得把 Mock 写成 AC-037 真实通过。

---

## 三、接口请求数据的类型选型

### 传输格式

| 格式 | 适用 |
| --- | --- |
| `multipart/form-data` | API-001；API-015；API-016；API-006 兼容文件 |
| `application/json` | 其余自有接口（含 API-002 可选 Key、API-017）；API-006 纯文本与 4xx/409 |
| SSE `text/event-stream` | API-006 成功与流内失败（D-002-B） |
| 文件落盘 | 简历 `backend/data/uploads/`；面经原件 `backend/data/uploads/knowledge/` |

前端 Axios 默认 JSON；上传用 `FormData`。对话超时建议 180s（检索+多模型），不要用 10s 示例。

### 共享类型

时区：库内时间戳 UTC ISO8601；业务日、整点、投递/面试日期用 `Asia/Shanghai`。

`normalized_status`：`waiting_interview` | `rejected` | `other`。`status_text` 保留用户原话。不把 `other` 改成未确认枚举。

`interview_at` 可空。仅日期则存 `YYYY-MM-DD`；带时刻则存带 `+08:00` 的 ISO。`deadline` = 该值的北京日期或 null。空则催促/出题排序**不**按截止日加急。

简历与面经文件：`.pdf` / `.docx` / `.txt`；面经另允 `.md`。上限 `resume_max_bytes` 默认 10485760（10MB），`knowledge_max_bytes` 默认 20971520（20MB，按落盘文件大小计，分块写入磁盘，不把整文件读进内存再量大小）。扫描件不做 OCR。面经文件主入口为 API-015。

邮箱：trim + 小写后唯一。一名求职者一条对话。

`llm_api_key`（传输）：string，trim 后非空，须匹配 `^sk-`。最长 256 字（拒绝超长粘贴）。只出现在 API-001、API-002（补贴时）、API-017 的**请求**中；任何成功/失败响应、SSE、`llm_call_logs`、应用日志都不得包含该字段值。

`has_llm_api_key`：bool，由库中是否有密文推导。

`llm_key_status`：`missing` | `saved` | `invalid`。`invalid` 仅在百炼返回 401/`invalid_api_key`（或等价鉴权拒绝）后写入；429/5xx **不**改成 invalid。

`llm_key_probe_status`：`ok` | `unreachable` | `null`。仅出现在本次提交了 Key 的 API-001/002/017 响应：探测 2xx 为 `ok`；429/5xx/超时为 `unreachable`（Key 仍已保存，D-007-C）。API-002 仅邮箱接续为 `null`。不写入对外 GET。

`source_type`：`search` | `upload` | `paste`。

### 持久化（SQLite `data/offer_coming.db`）

归属均挂 `candidate_id`。无密码。令牌只存 SHA-256。求职者 Key **加密**落库，不明文列。

沿用：`candidates`、`sessions`、`conversations`、`messages`、`question_sets`、`questions`、`nudge_logs`。

`candidates` **增加**（可空以兼容旧行，业务上进入对话前必须有密文）：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `llm_api_key_ciphertext` | Text 可空 | Fernet/AES-GCM 密文；加密密钥由 `secret_key` 派生（HKDF-SHA256 → 32 字节）。禁止第二份明文备份。 |
| `llm_key_status` | String | `missing` \| `saved` \| `invalid`，默认 `missing` |
| `llm_key_updated_at` | DateTime 可空 | 上次成功写入新 Key 的时间 |

不存 Key 后四位、不存指纹到对外 API。改 `secret_key` 将导致旧密文无法解密，用户须到「我的key」重贴（实现约束，写入 README，不另开产品决策）。

`applications` **增加**：`applied_at`（Date，默认写入日北京日期）、`interview_at`（可空字符串，日期或 ISO）、`interview_summary`（Text，默认空）。保留 `deadline` 与 `interview_at` 日期同步，避免已实现读路径断裂。同一求职者下非空 `jd_url` 唯一。

新增：

- `knowledge_items`：`id`，`candidate_id`，`application_id` 可空，`title`，`source_type`，`source_url` 可空，`body`，`created_at`，`updated_at`
- `knowledge_segments`：`id`，`knowledge_item_id`，`ordinal`，`text`，`status`（`active`\|`pending_delete`\|`deleted`）
- `knowledge_search_runs`：`id`，`application_id`，`status`（`running`\|`ok`\|`failed`），`error_text`，`created_at`（保证「考查点先出、检索后到」且同一岗位不重复空跑）
- `llm_call_logs`：`id`，`candidate_id`，`conversation_id` 可空，`role`（`orchestrator`\|`nudge`\|`interview`\|`knowledge`\|`counseling`），`model`，`purpose`，`created_at`。只记模型名与用途，禁止记密钥、简历全文、提示词全文（AC-029–035 对照用）

生命周期：本次不做用户删档。上传随记录保留。测试库与业务库隔离。

跨请求：浏览器持 `session_token`；业务对象用服务端 id。

---

## 四、模型选型与提示词设计

本项目依赖模型。接入：httpx → EXT-001，Bearer 为**该求职者**解密后的 Key。不使用 PyCore `OpenAIProvider`。对话轮次在 Service 内做工具循环，工具用 `pycore.plugins.BasePlugin` 注册。

上线求职者路径：无有效求职者 Key 则不调模型、不把运营 Key 当备用、不把空回复假装成模型成功。本地/pytest：`llm_allow_mock=true` 时，在已保存求职者 Key 字段（可为测试占位串 `sk-` 开头）下跳过 HTTP、返回 Mock；Mock **不得**读取运营 `llm_api_key` 去打真实百炼。真实 AC（对话/出题/催促文案/检索）须该求职者 Key 的真实调用。

PRD：五套**模型名**必须不同且由运营配置；用户只贴一把 Key。用户消息先过主体；整点催促由投递催促生成。界面仍是小凹。

### 五套配置字段与建议默认名

同一 `llm_base_url`（运营配置）。五个 `model` 字符串必须互不相同（启动校验，否则 AC-029 无法成立）。建议值来自[百炼文本生成列表](https://help.aliyun.com/zh/model-studio/text-generation-model/)与[联网搜索支持模型](https://help.aliyun.com/zh/model-studio/web-search)，均可走 OpenAI 兼容 Chat Completions。**建议，不是已确认产品名。** 每次调用的 API Key 来自该求职者密文，不来自运营 `llm_api_key`。

| 职责 | 配置字段 | 建议默认 | 温度 | 依据 |
| --- | --- | --- | --- | --- |
| 主体：意图、规划、分派、编排工具 | `llm_orchestrator_model` | `qwen-plus` | 0.2 | Function calling 稳定；每条用户消息先调它 |
| 投递催促：写表、缓急、整点催促、当日对准哪些岗 | `llm_nudge_model` | `qwen-flash` | 0.7 | 整点高频，费用较低 |
| 面试：考查点、五题、点评、用知识库辅导 | `llm_interview_model` | `qwen-max` | 0.3 | 对照简历与面经的生成质量 |
| 知识库：检索面经、收录文档、已挂取舍判断 | `llm_knowledge_model` | `qwen-plus-latest` | 0.2 | 与主体名不同；支持 `enable_search` |
| 心理辅导 | `llm_counseling_model` | `qwen-turbo` | 0.6 | 走心多轮，不当催促模型 |

旧字段 `llm_model` **废弃**，不得再作为五套的唯一来源。可保留读取但启动时若五套未配齐则失败，避免静默退回单模型。

各套另可配 `llm_*_temperature`；未配用上表。工具循环统一 `agent_max_steps`。

### 调用时机

| 时机 | 模型 | 输出 |
| --- | --- | --- |
| 每条 API-006 用户消息（含文件） | 主体 | 分派 tool_calls 或（仅闲聊且无工具时）对用户中文 |
| 主体分到面试 | 面试 | 考查点 / 题目讲解 / 点评；可再 tool_calls |
| 主体分到投递催促 | 投递催促 | 写表、更新缓急；辅导结束后的接上催促文案 |
| 主体分到知识库；或投递写入后的后台检索；或表改为已挂 | 知识库 | 检索摘要 JSON / 收录确认 / 删留判断 JSON |
| 主体分到辅导 | 心理辅导 | 走心询问；禁止骂醒催促 |
| 调度整点 | **只**投递催促 | 对话段 + 邮件；再由服务层按缓急决定是否调面试出题 |
| 调度出题 | 面试 | 严格五题 JSON |
| 五题答完 | 主体识别后 → 面试 | 缺点+建议；服务层追加 `interview_summary` |

### 主体可调用的分派工具（用户不可见）

1. `call_counseling_agent` — 参数：`user_text`。观察：辅导回复文本。
2. `call_nudge_agent` — 参数：`task`（`record`\|`update`\|`resume_after_counseling`）、业务字段。观察：写库结果 + 可选催促短文。
3. `call_interview_agent` — 参数：`task`（`exam_points`\|`coach`\|`review`）、`application_id` 可选。观察：考查点/辅导/点评。
4. `call_knowledge_agent` — 参数：`task`（`search`\|`ingest`\|`evaluate_rejected`\|`apply_user_decision`）。观察：入库/失败/询问稿/已执行删除。
5. `get_snapshot` — 简历摘要、投递表、知识库标题、当日题、辅导标记、北京时间。

主体系统提示：一条消息里既有情绪又有求职事项时**先** `call_counseling_agent`，本轮不得 `call_nudge_agent` 生成骂醒催促。未说心情变好不得结束辅导。过程 status 只能让服务层发业务句。

### 子 Agent 工具

投递催促：`record_application`（`jd_url` 可空；有 URL 则按 URL 去重更新）、`update_application`（进度/状态/`interview_at`）、`get_snapshot`。

面试：`fetch_jd`、`save_exam_points`、`get_knowledge_excerpts`（按岗位取 `active` 段）、`save_answers`、`append_interview_summary`（已有手写则追加）、`get_snapshot`。

知识库：`search_public_experiences`（调 EXT-004）、`ingest_user_document`、`evaluate_items_for_rejected_role`（输出建议删的 `item_id`/`segment_id` + 仍有用说明，**只标 `pending_delete`，不真删**）、`apply_deletion_decision`（`accepted_ids` / `keep_all`）、`get_snapshot`。

心理辅导：无写投递/催促工具；仅 `set_counseling_state`、`get_snapshot`。

停止：无 `tool_calls` 的 assistant 文本，或 `AGENT_MAX_STEPS`（默认 8）。格式错误最多修 2 次。禁止为格式无限重试付费调用。

### 提示词文件

`backend/src/prompts/`（运行时读文件）：

- `orchestrator.md` — 小凹对外身份；先识别意图再分派；情绪优先；禁止在用户可见文本里写主体/子 Agent/工具名。
- `exam_points.md` — 面试岗位画像：必须同时用 JD 与简历；有面经则对照；先出考查点。
- `questions.md` — 面试 Agent 出题：合计五道；默认 3 道 `common` + 2 道 `role`；调度出题只输出 JSON；用户可见题干不含分配/优先级/考查点。已挂 id 不得出现。不做完整模拟面试场次；对话内可就上一句追问 1～3 句。
- `review.md` — 五题答完后点评与复盘，并补上题型、对准岗位、优先级理由和考查点；用户追问时可再挖 1～3 句，不另开整场对练。
- `nudge.md` — 内部决定岗位优先级与每日五题归属；整点催促对用户只输出催促口气 + 五题题干，按 `tone_level` 1–4；`counseling_active` 时禁止生成催促。不在邮件/对话催促里写进度表或考查点。
- `counseling.md` — 闺蜜人设（称呼「诡秘」= 闺蜜谐音）；先接住情绪，不幼稚化、不强行积极；未说心情变好不转骂醒；用户暂停则本轮收尾不催五题。另填 `recent_event`、`current_emotion`、`ready_for_action`、`session_ended`。
- `knowledge_search.md` — 求职知识库：检索优先级、来源可信度、为五题准备材料；公开检索只出可核对 JSON。不得编造；不能把「小红书上应该有」写成已入库；不对小红书域名发 GET。
- `knowledge_ingest.md` — 从文件/粘贴抽面经正文与标题；保留来源标识，不覆盖冲突材料。
- `knowledge_evaluate.md` — 已挂后判断整篇/局部；输出严格 JSON。

所有提示词文件含同一组共享变量，运行时替换，缺省「（暂无）」，不编造未单独建档的字段：

- `{{current_date}}` — 当前日期（北京日期）
- `{{user_profile}}` — 用户背景、求职方向、工作年限、城市、偏好等；年限/城市/偏好未单独建档时标明未单独登记
- `{{resume}}` — 用户最新简历（文件名、解析状态与正文摘录）
- `{{job_list}}` — 岗位及投递记录（状态、进度、投递日、面试时间、deadline、考查点）
- `{{calendar}}` — 面试安排和用户可用时间；可用时段未单独建档时标明，并给出出题日催促窗口
- `{{uploaded_documents}}` — 用户上传的 JD、面经、笔记、截图等；截图未单独建档时标明
- `{{knowledge_base}}` — 知识库检索结果（`active` 段）；出题可覆盖为本次选用摘录

出题 JSON 为五题数组。校验失败重试一次；仍失败则规则模板（三道共性 + 两道结合已记录考查点的占位业务题），日志标明降级。允许 2+3 或 4+1，但必须同时包含两种题型。写入对话的 `questions` 消息与邮件催促只列题干，不写题型标签、分配方案或考查点。答完后的点评再补上这些内部信息。

检索入库 JSON（知识库模型）：

```json
{
  "items": [
    {"title": "…", "body": "可核对的面经正文", "source_hint": "非小红书的公开页描述"}
  ],
  "failed": false,
  "fail_reason": null
}
```

`items` 为空或 `failed=true` → AC-038。`body` 空或仅「小红书有相关帖」→ 按失败。最多写入 `knowledge_search_max_items`（默认 3）。

已挂评估 JSON：

```json
{
  "keep": [{"item_id": "k_01", "segment_ids": [], "reason": "其它岗仍能用"}],
  "suggest_delete": [{"item_id": "k_02", "segment_ids": ["s_3"], "reason": "只针对已挂岗位"}]
}
```

---

## 五、接口逻辑算法设计

北京时间 `zoneinfo.ZoneInfo("Asia/Shanghai")`。催促窗口：当天 10:00 至 24:00（24:00 = 次日 00:00，作废当日题并停当日催促）。整点：10–23 各催一次；00:00 只作废、不发第 15 次催题。

### 共用：状态归一

`status_text` 九项：简历筛选中、简历挂、待测评、已测评、一面、二面、三面、等待面试、已挂。归一：简历挂 / 已挂 / 挂了 / 被拒 / 淘汰 / offer 黄了 → `rejected`。一面 / 二面 / 三面 / 等待面试 / 等面试 / 约面 → `waiting_interview`。简历筛选中、待测评、已测评及其余 → `other`（催进度，不出题）。对话写入时尽量落成九项标签；未说进度则追问，不编造。

### 共用：缓急（AC-036）

等待面试岗位：有 `interview_at` 的按该时刻升序更急；时间为空的**不**因截止日排到更急，只按用户明示「更急」或投递更早就序。已挂不进入出题候选。

### 共用：出题日（AC-018、020，D-004-A）

与已确认规则相同：完成则 `next_question_date=完成日+1`；24:00 未完成则作废，`next_question_date=出题日+2`，中间 `rest_day`。多岗位合计 1 `common` + 2 `role`；`role` 对准更急等待面试岗。

`tone_level`：10–12=1；13–17=2；18–21=3；22–23=4。辅导中调度跳过催促。

### 共用：小红书与公开检索（必须遵守）

2026-09-14 实测 [https://www.xiaohongshu.com/robots.txt](https://www.xiaohongshu.com/robots.txt)：`User-agent: *` + `Disallow: /`。搜索引擎蜘蛛另有少量 Allow，不适用于本系统服务器抓取。

[小红书开放平台文档中心](https://open.xiaohongshu.com/document/api) 当前可见能力偏电商/面单等，**未核到**可供本项目个人求职场景使用的、字段完整的「笔记搜索」官方契约。第三方「笔记搜索 API」与 Web 端 `X-s`/`X-t` 签名绕过均不纳入方案。

因此：禁止实现小红书爬虫、签名逆向、Cookie 池。用户举例「小红书面经」只表示产品期望「公开面试经验」，不构成可达性承诺。失败必须说明，并请发文件或粘贴（AC-038）。

已锁定 D-005-A：EXT-004 搜公开网页；能抽出非空正文再入库。这不是「已经爬到小红书」。

### 共用：考查点与检索并行

记录可识别公司与岗位后：面试考查点在本轮 SSE 先出；`knowledge_search_runs` 置 `running`，检索在同一请求内后半段或请求返回后由后台任务完成。考查点**不等**检索。检索结束后插入 `kb_notice` 或失败说明（API-005 可拉到）。若仍在同一轮 SSE 未结束，可用第二次 `status` + 一句入库/失败（AC-037、038、041）。

### API-001 / API-002 / API-003 / API-004

算法：

**API-001**：校验邮箱、简历、Key 格式 → **必须**百炼探测（D-007-C）→ 401 则 400 不建档；429/5xx/超时仍加密 Key 并签发 session → 解析简历落盘落库。邮箱已存在：**409，不替换简历、不覆盖 Key**，须走 API-002。换设备不补传简历。

**API-002**：规范化邮箱 → 无记录 404 → 无密文且请求无 Key 则 409 `LLM_KEY_REQUIRED` → 无密文且带 Key 则同样 D-007-C 探测后加密写入并签发 → 已有 Key 则签发并忽略多余 Key（不探测）。无验证码（D-001-A、AC-021、049）。

**API-003**：鉴权后读摘要 + Key 状态，永不解密到响应。`has_llm_api_key=false` 时前端不得进入对话/投递/面经，只允许补贴 Key 或退出。

**API-004**：取或创建唯一对话。无模型。

探测（D-007-C，保存路径必做）：对运营配置的 `llm_base_url`，用用户刚提交的 Key 发一次最小请求。优先 `GET {llm_base_url}/models`（OpenAI 兼容常见探活）；若该部署 404，则一次 `chat/completions`、`max_tokens=1`、`model=llm_orchestrator_model`。401/`invalid_api_key` → 拒绝保存。429/5xx/超时 → **不**判定 Key 无效，仍写入，`llm_key_probe_status=unreachable`。探测也计用户额度，日志禁止记 Key。来源：[百炼 OpenAI 兼容](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[错误码](https://help.aliyun.com/zh/model-studio/error-code)。

| 技术选择与触发场景 | 用户可见结果或代价 | 当前处理与依据 | 验收或验证 |
| --- | --- | --- | --- |
| 关页再开如何认出 | 同浏览器不必再填邮箱/简历/Key；填对邮箱也能进 | D-001-A；Key 存在服务端 | AC-017、021 |
| 旧档无 Key | 只填邮箱进不去，须补贴 Key | API-002 `LLM_KEY_REQUIRED` | AC-049 |
| 无接续时进表/面经 | 不能进，看到先填邮箱、粘贴 Key 并上传简历 | 无 token 不调 007/012；前端提示 | AC-023、043 |
| 保存 Key 时立刻试百炼 | 提交后「正在确认 Key」约数秒；错 Key 进不去；网络不好仍能进并看到「这次没连上」 | D-007-C | AC-001、049、052、054 |

### API-005

鉴权与属主 → 无 `after_id` 取最近 `limit` 条再正序返回；有 `after_id` 则过滤其后的新消息。20s 轮询。关页期间调度仍写库，再打开能看到漏掉的催促与 `kb_ask`（落在最近窗口内）。发送后前端合并本页已有气泡与最近窗口，不用最旧一页整表替换。

| 技术选择与触发场景 | 用户可见结果或代价 | 当前处理与依据 | 验收或验证 |
| --- | --- | --- | --- |
| 轮询而非长连接 | 已打开网页上催促最多晚约 20s | 整点粒度足够 | AC-007、012、047 |

### API-006（主体循环）

1. 校验属主、长度/文件、对话锁。  
2. 落库 user 消息（有文件则先存原件，`content` 可记文件名）。  
3. `get_snapshot` + 主体提示（北京时间、辅导、当日题、投递表、面经标题）。  
4. 解密该求职者 Key；缺密文 → 流内 `LLM_KEY_MISSING`，不调模型。调 EXT-001（`llm_orchestrator_model` + 求职者 Key），写 `llm_call_logs`。401 → 标 `invalid`，流内 `LLM_KEY_INVALID`。**禁止**改用运营 Key。  
5. 按 tool_calls **真实执行**子 Agent / 工具，观察回传，直到最终文本或步数用尽。子 Agent 各用自己的模型再开循环，Bearer 仍是同一把求职者 Key。  
6. 意图规则（产品已确认，不是建议）：焦虑 → 只辅导；变好 → 同轮催促/题目；「我投了 / 我投递了这个岗位 / 加入到我的投递」或岗位链接 → 读 JD 成功则写完整行 + 考查点并启动检索；读失败且只有失败链接、没有任何岗位信息则不写空行（AC-004）；用户已提供公司/岗位/JD 正文或明确要求写入时必须**先写表再调模型**，不要等「请补录」，也不得口头声称已写入。考查点模型超时或子调用失败时，已写入的投递行保留，本轮收口为失败说明而不是回滚。未说进度则追问九项状态；同一 `jd_url` 更新该行；进度/面试时间 → 更新；答题 → 齐则点评并追加面试总结；对话粘贴面经 → 收录并 `kb_notice`；已挂/简历挂询问的回答 → `apply_deletion_decision`。  文件上传走 API-015，不经本轮对话。  模型若漏写表，编排层按同一意图补写。  
7. SSE：立刻 `status(thinking)`「小凹正在思考...」→ 工具阶段换成业务 `status` → 用户可见正文随 EXT-001 `stream=true` 的 `delta.content` 转发（无工具的闲聊当场出字；有工具则等拼好最终稿后再按节拍 `delta`）→ `done`/`error`（D-002-B，AC-055）。禁止模型非流式完成后按整段切片冒充逐字。主体调用带 `max_tokens=llm_reply_max_tokens`；闲聊落库前按 `llm_reply_max_chars` 截断，当日五题与点评不截题干（AC-056）。用户说「今日五题」或抱怨截断时，直接回放当日题集五道题干，不再让主体重新生成一版被截断的长文。主体系统提示保持短指令 + 共享上下文；`## 当前快照` 只带时间、辅导开关、解析状态和投递计数，不重复整表。调用前读取该对话最近 `llm_history_max_messages` 条用户/助手气泡（每条截到 `llm_history_max_chars`），插在系统提示与本轮用户消息之间；子 Agent 同样带上这段历史（AC-057）。

| 技术选择与触发场景 | 用户可见结果或代价 | 当前处理与依据 | 验收或验证 |
| --- | --- | --- | --- |
| 先主体再子模型 | 仍是小凹；等待可能比单模型略长 | REQ-009；AC-033 | 调用记录 |
| 求职者 Key 无效 | 对话说明去「我的key」更换，没有假成功回复 | 禁止回落运营 Key | AC-054 |
| 考查点与检索 | 先看到考查点，稍后一句入库或失败 | PRD 已确认 | AC-003、037、038、041 |
| JD / 小红书读不到 | 无考查点或不入库 | EXT-002 规则 | AC-004、038 |
| 连发两条 | 第二条 409 | 对话锁 | 操作观察 |

### API-007

鉴权 → 按 `updated_at` 倒序列表。无模型。

### API-008

按北京日取题集。无模型。

### API-009 / API-010 / API-011

校验属主与字段 → 写库 → 归一状态 → 同步 `deadline`。009 默认 `applied_at`。010 清空面试时间则 `deadline=null`。010 变为 `rejected` 且有相关面经：知识库模型评估后插入 `kb_ask`，响应 `knowledge_review_pending=true`；前端立刻切到日常对话并用 API-005 展示（D-006-A）。011 删行。

| 技术选择与触发场景 | 用户可见结果或代价 | 当前处理与依据 | 验收或验证 |
| --- | --- | --- | --- |
| 离格 PATCH | 刷新后仍是改后的值；对话能读到 | AC-024 | 改表后刷新再问小凹 |
| 表页保存已挂且有相关面经 | 自动回到日常对话，马上看到删/留询问 | D-006-A；`knowledge_review_pending` | AC-047 |
| 手加空行 | 可以保存；无链接不读 JD | AC-025、028 | 网页观察 |

### API-012 / API-013 / API-014 / API-015

属主过滤。012 空列表。014 硬删条目及段，出题检索不再使用。015 解析上传文件并入库。

| 技术选择与触发场景 | 用户可见结果或代价 | 当前处理与依据 | 验收或验证 |
| --- | --- | --- | --- |
| 手删不必问 | 立刻从「我的面经」消失 | PRD | AC-046 |
| 检索失败不造行 | 空列表 + 对话失败说明 | PRD | AC-038、044 |
| 本页上传 | 标题旁选文件后列表出现该条 | API-015 | AC-039 |

### API-016 / API-017 / API-018

**API-016**：属主与已存 Key 校验 → 校验文件 → 解析成功才替换磁盘与 `resume_text`；失败保留旧档。无模型。用户留在当前页。

**API-017**：属主校验 → 格式 → D-007-C 探测 → 401 则不覆盖；否则加密覆盖密文、`llm_key_status=saved`。响应不含 Key。旧密文仅在成功写入后丢弃。

**API-018**（D-008-B）：按当前 Bearer 找到 `token_hash` 删除该行。不删 `candidates`。前端再清 `localStorage`。

| 技术选择与触发场景 | 用户可见结果或代价 | 当前处理与依据 | 验收或验证 |
| --- | --- | --- | --- |
| 替换简历 | 确认在档；新文件立刻用于对话 | API-016；不重新填邮箱/Key | AC-051 |
| 更换 Key | 输入框不回显旧 Key；之后调用走新 Key | API-017 | AC-052 |
| 退出 | 回到初次见面；本机其它标签也不再自动接上；记录仍在；邮箱可再进 | D-008-B；必须调 API-018 | AC-053 |
| 头像 / 任务栏布局 | 点头像下方三项 | 无新业务页、无真人照片；布局阶段 B | AC-050 |

### 调度

启动后睡到下一上海整点。对每名有投递或当日待办的求职者：

0. 无求职者 Key 密文或 `llm_key_status=invalid`：**不调模型、不使用运营 Key、不发骂醒催促**；对话插入 `error_notice`（请到「我的key」更新）。按 D-009-B 发固定模板邮件（当日至多成功一封）。  
1. 00:00：作废未完成出题日题集，设休息日。  
2. 小时不在 10–23：结束。  
3. `counseling_active`：不发对话/邮件狠催（AC-019）。  
4. 出题日且无题集：投递催促模型（求职者 Key）给出优先岗位 → 面试模型出五题 → 插入只含题干的 `questions` 消息。  
5. 有未完成题或休息日待办：投递催促模型生成催促（催促口气 + 题干）→ 消息 + EXT-003。正文不含进度表、deadline 列表、分配或考查点。  
6. 题集 `completed`：当日不再催；`next_question_date=完成日+1`。  
7. 休息日：只短催跟进，禁止催已作废题，也不列进度表。

整点催促**直接**调投递催促模型，不伪装成用户说了一句话。Bearer 为该求职者 Key。

| 技术选择与触发场景 | 用户可见结果或代价 | 当前处理与依据 | 验收或验证 |
| --- | --- | --- | --- |
| 进程不在线 | 整点催促和邮件都不发 | 单进程调度；README 写明需保持后端运行 | AC-012、015 |
| SMTP 失败 | 对话催促在，说明邮件没发出 | D-003-A | AC-016 |
| 求职者 Key 失效时的整点 | 对话说明须更新 Key；当日至多一封固定模板邮件；无骂醒文案 | D-009-B | AC-054 |

---

## 六、接口失败异常设计

共享 JSON 错误体即第二层信封。`error` 给用户中文。日志记 URL、status、`error_code`，禁止密钥和简历/面经全文。

规范码：`VALIDATION_ERROR` 400、`UNAUTHORIZED` 401、`FORBIDDEN` 403、`NOT_FOUND` 404、`CONFLICT` 409、`INTERNAL_ERROR` 500。附加：`LLM_UNAVAILABLE`（流内或 503）、`JD_FETCH_FAILED`、`KB_SEARCH_FAILED`、`EMAIL_SEND_FAILED`（只反映在对话说明）、`LLM_KEY_REQUIRED`（409，补贴 Key）、`LLM_KEY_INVALID`、`LLM_KEY_MISSING`、`LLM_QUOTA_EXCEEDED`。

分类：参数校验 / 业务规则 / 外部依赖 / 系统内部。禁止空 `except`。Bearer 业务接口（对话、投递、面经、换简历）在无求职者 Key 密文时 403 `LLM_KEY_REQUIRED`；例外：API-003（让前端知道要补贴）、API-017（写入 Key）、API-018（退出）。

### API-001 / API-002

缺邮箱/简历/Key 或 Key 不以 `sk-` 开头：400，文案「请先填写邮箱、粘贴百炼 Key 并上传简历，才能和小凹对话。」邮箱已存在：409 `CONFLICT`。无记录：404。已有简历无 Key 且未补贴：409 `LLM_KEY_REQUIRED`。探测 401：400 `LLM_KEY_INVALID`，「这把百炼 Key 不能用，请检查后重贴。」（D-007-C，不写入）。探测 429/5xx/超时：仍 2xx 成功并提示「这次没连上」。磁盘失败 500。任何失败响应都不得带回用户提交的 Key。

### API-003–018 鉴权

无/错/过期 token → 401，前端清 token，回初次见面。资源不属于此人 → 403。

### API-006

| 触发 | 表现 | 用户提示 | 停止或恢复 |
| --- | --- | --- | --- |
| 空内容且无文件 / 超长 / 文件类型大小不符 | 400 JSON | 写出内容 / 缩短 / 说明格式大小 | 不调模型 |
| 进行中重复发送 | 409 | 等小凹说完 | 保留第一轮 |
| 客户端断开 | 服务端仍跑完并落库 | 再打开或轮询能看到 | 手动重发视为新消息 |
| JD 失败 / 小红书 URL | 对话说明（AC-004） | 换链接或粘贴 JD | 不写完整失败行 |
| 检索失败 | `kb` 失败说明（AC-038） | 可在「我的面经」上传或对话粘贴 | 考查点与表行仍在 |
| 工具 JSON 非法 | 观察错误，最多再试 2 次 | 不假装已记录/已入库 | 已成功写库保留 |
| 无求职者 Key | SSE `error` `LLM_KEY_MISSING` | 请到「我的key」粘贴百炼 Key | 用户消息已落库；不调运营 Key |
| Key 401 / invalid_api_key | SSE `error` `LLM_KEY_INVALID`；`llm_key_status=invalid` | 这把 Key 百炼不接受，请到「我的key」更换 | 无假成功回复；不回落运营 Key（AC-054） |
| 额度不足 429 quota | SSE `error` `LLM_QUOTA_EXCEEDED` | 你的百炼额度不够了，请充值或到「我的key」换一把 | 不回落运营 Key |
| LLM 超时/其它 429/5xx | SSE `error` `LLM_UNAVAILABLE` | 小凹这会儿连不上，请稍后再发 | 不把 Key 标为 invalid |
| 联网搜索超限未真正搜 | 视为检索失败 | 同 AC-038 | 不编造 |

LLM：连接 10s，读 90s（检索轮可配更长）。429 指数退避最多 2 次；**鉴权 401 不重试、不换 Key**。

### API-009–011

非法日期 400；重复链接 409；已挂评估模型失败：状态仍保存，对话插入说明「还没判断完面经是否还留着，你可以先在「我的面经」里自己看」，`knowledge_review_pending=true` 以便按 D-006-A 回到对话看到这句话，不自动删。评估所用 Key 仍是求职者 Key；401 则按 AC-054 插入更新 Key 说明，不自动删面经。

### API-012–015

空列表 200。删除不存在 404。上传空文件或不支持格式 400。

### API-016 / API-017 / API-018

API-016：文件失败 400，旧简历不变。API-017：空 Key 400；探测 401 则 400 `LLM_KEY_INVALID`，旧 Key 不变；探测超时仍 200 并提示「这次没连上」。API-018：必须调用；401 时前端仍清本地（幂等退出）。

「我的简历 / 我的key」等待与失败均在任务栏面板内展示，不跳进新的顶栏页；布局阶段 B。

### 调度 / EXT-003 / EXT-004

邮件失败不回滚对话催促。同一 `beijing_hour_key` 已成功不重发。EXT-004 失败只影响知识库，不影响考查点。求职者 Key 失效：不生成模型催促/出题；对话 `error_notice`；固定模板邮件当日至多一封（D-009-B）。

### 前端保留

401 回初次见面，不丢输入框未发送的字。API-006 失败保留输入，并让头像任务栏可点到「我的key」。表格拉格失败：该格回滚显示并提示，不假装已保存。API-016/017 失败保留用户刚选的文件/刚贴的 Key 于面板内，不清成功态。

---

## 七、项目本地层级设计

```
Projects_Repo/offer_coming/
├── docs/                      # PRD、本方案、decisions、ui-style、原型
├── pycore/                    # PYTHONPATH 引入，不 pip 安装，不纳入项目 lint
├── backend/
│   ├── .env / .env.example
│   ├── requirements.txt
│   ├── data/                  # gitignore；offer_coming.db；uploads/；uploads/knowledge/
│   ├── src/
│   │   ├── main.py            # APIServer，CORS，调度，init_db
│   │   ├── api/deps.py
│   │   ├── api/routes/        # candidates（含 resume/llm-key）, sessions（含 current 删除，D-008）, conversations, applications, question_sets, knowledge_items
│   │   ├── db/models.py session.py
│   │   ├── models/
│   │   ├── repositories/
│   │   ├── plugins/           # PyCore BasePlugin：分派与写库工具
│   │   ├── services/          # 简历、主体循环、四子 Agent、调度、邮件、JD、检索、httpx LLM（按求职者 Key）
│   │   ├── prompts/
│   │   ├── utils/             # 邮箱规范化、令牌哈希、Key 加解密（明文不落日志）
│   │   └── config/            # AppSettings，ConfigManager.load(..., use_env=False)
│   └── tests/
├── frontend/
│   ├── .env / .env.example
│   └── src/
│       ├── pages/             # OnboardingPage, ChatPage, ApplicationsPage, KnowledgePage
│       ├── components/        # AppHeader 顶栏三入口（投递最右）；已接续后虚拟头像+任务栏（阶段 B）
│       ├── assets/            # 产品提供的虚拟头像静态资源，不经 API 上传
│       ├── stores/ hooks/ services/ router/ types/ mocks/ utils/
└── pyproject.toml
```

运行环境：

- Python：项目 `.venv`，3.11.15。
- 后端：`cd backend && PYTHONPATH=.. <python> -m uvicorn src.main:app --reload --host 127.0.0.1 --port 8099`
- 前端：`cd frontend && npm run dev -- --host 127.0.0.1 --port 5199`；门禁 5175 + `VITE_BACKEND_PROXY_TARGET=http://localhost:8003`
- CORS：5199 与 5175 的 localhost 与 127.0.0.1
- pytest：`python3.11 -m pytest backend/tests --timeout=120`
- SQLite 相对路径解析为绝对路径并创建父目录
- 依赖增补：`cryptography`（Key 加密；版本在 requirements 锁定，实现时添加）

### 配置表（仅字段名；真实值在 .env）

| 字段 | 类型 | 默认 | 用途 | 敏感 |
| --- | --- | --- | --- | --- |
| debug | bool | true | 调试 | 否 |
| secret_key | string | 必填 | 会话哈希盐 **兼** 求职者 Key 加密派生 | 是 |
| host / port | string / int | 127.0.0.1 / 8099 | 监听 | 否 |
| cors_origins | JSON list | 规范四地址 | CORS | 否 |
| database_path | string | `data/offer_coming.db` | SQLite | 否 |
| upload_dir | string | `data/uploads` | 简历与面经原件根 | 否 |
| resume_max_bytes | int | 10485760 | 简历上传上限（10MB） | 否 |
| knowledge_max_bytes | int | 20971520 | 面经**文件**大小上限（20MB，按落盘字节计，不是进程内存上限） | 否 |
| session_ttl_days | int | 30 | 令牌有效期 | 否 |
| timezone | string | Asia/Shanghai | 调度与业务日 | 否 |
| llm_base_url | string | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 百炼兼容 BASE（运营配置，用户不填） | 否 |
| llm_api_key | string | 空 | **仅**本地/验收非求职者路径或探活脚本；**禁止**作为上线求职者对话/出题/催促/检索的 Bearer。`LlmClient` 用户流量不得读取此字段。保留以免旧 `.env` 缺字段启动失败。 | 是 |
| llm_allow_mock | bool | false | pytest/本地跳过 HTTP 的 Mock 开关；Mock 仍不得用运营 Key 代发真实请求，也不得标真实 AC 通过 | 否 |
| llm_orchestrator_model | string | qwen-plus | 主体模型名（运营配置） | 否 |
| llm_nudge_model | string | qwen-flash | 投递催促 | 否 |
| llm_interview_model | string | qwen-max | 面试 | 否 |
| llm_knowledge_model | string | qwen-plus-latest | 知识库 | 否 |
| llm_counseling_model | string | qwen-turbo | 心理辅导 | 否 |
| llm_timeout_seconds | float | 90 | 读超时 | 否 |
| llm_connect_timeout_seconds | float | 10 | 连接超时 | 否 |
| llm_max_retries | int | 2 | 429/5xx | 否 |
| llm_enable_thinking | bool | false | 工具调用轮关闭思考 | 否 |
| llm_reply_max_tokens | int | 2048 | 主体对外生成上限，需能一次写完五道题干 | 否 |
| llm_reply_max_chars | int | 500 | 闲聊落库字符封顶；当日五题与点评不套此上限 | 否 |
| llm_history_max_messages | int | 16 | 带给模型的最近对话条数 | 否 |
| llm_history_max_chars | int | 400 | 历史每条截断 | 否 |
| agent_max_steps | int | 8 | 单模型循环上限 | 否 |
| knowledge_search_max_items | int | 3 | 单岗最多入库检索条数 | 否 |
| jd_fetch_timeout_seconds | float | 15 | 拉 JD | 否 |
| jd_fetch_user_agent | string | offer_coming/1.0 | 拉 JD | 否 |
| smtp_host / smtp_port / smtp_user / smtp_password / smtp_from / smtp_use_tls | 见名 | 空 | SMTP（D-003-A） | 密码为是 |

前端：`VITE_API_BASE_URL=/api`；`VITE_BACKEND_PROXY_TARGET=http://localhost:8099`；`VITE_USE_MOCK=false`。

`data/`、`.env`、`.venv` 忽略。不提交简历、面经原件与业务库。

### 外部服务清单

| 名称 | 用途 | 依赖 | 配置状态 | Mock / 真实 | 真实验证所需 |
| --- | --- | --- | --- | --- | --- |
| 阿里云百炼 | 五套模型 | EXT-001；API-006 与调度 | 模型名在运营 `.env`；**调用 Key 在求职者密文** | `llm_allow_mock=true` 可 Mock；上线用户路径无求职者 Key 则拒绝调用、不回落运营 Key。真实 AC-003/010/011/013/029–035/037/054 需该求职者北京地域 Key | 求职者自己的北京地域 Key；用户授权付费 |
| 百炼联网搜索 | 已投岗位公开面经 | EXT-004（D-005-A） | 复用**求职者** Key | 无求职者 Key 或未真正搜到则不得报 AC-037 真实通过 | 同上；知悉搜索按次计费与 15 RPS |
| 岗位 JD 站点 | 读用户链接 | EXT-002 | 无需账号 | 公网真实 GET；失败走 AC-004 | 可访问链接 |
| 小红书 | 用户举例的来源 | **不接入** | 无 | 任何「已爬到小红书」均为虚假 | — |
| SMTP | 催促邮件 | EXT-003 | 通道已锁定 | 未配置走 AC-016 | 用户 SMTP；看真实收件箱 |

缺少求职者 Key 或 SMTP 不阻塞：落库、会话、表页结构、面经页空态、调度状态机。阻塞的是依赖真实模型/检索/邮件的 AC。运营 `.env` 缺 `llm_api_key` **不再**作为「用户对话走 Mock」的开关。

---

## REQ / AC 覆盖

| AC | 路径 |
| --- | --- |
| AC-001 | API-001（邮箱+简历+Key）→ 004/006 |
| AC-002 | 无 session 不进对话；API-001 缺项校验（含 Key） |
| AC-017 | API-003/004/005/007/008/012；localStorage（D-001-A）；不必再贴 Key |
| AC-021 | API-002 仅邮箱（库中已有 Key） |
| AC-049 | API-002 `LLM_KEY_REQUIRED` → 带 Key 再提交 |
| AC-050 | 前端头像+任务栏（阶段 B 布局）；不依赖新业务页 |
| AC-051 | API-016 替换简历；API-003 确认在档 |
| AC-052 | API-017 只收新 Key；响应/日志不回显 |
| AC-053 | API-018 作废本张 token + 清 localStorage（D-008-B） |
| AC-054 | API-006 / 调度：求职者 Key 401 不回落运营 Key；整点对话说明 + 当日模板邮件（D-009-B） |
| AC-055 | API-006 首事件 `thinking`；前端发送后立刻展示「小凹正在思考...」；`delta` 逐字 |
| AC-056 | 主体 `max_tokens`；闲聊落库字符封顶；当日五题与点评不截题干；orchestrator.md 短指令 |
| AC-057 | API-006 将最近对话轮次插入主体/子 Agent messages |
| AC-058 | 「我投了」或岗位链接即 `record`；未说进度则追问九项状态 |
| AC-059 | 投递表状态下拉九项；一面等出题，简历挂/已挂不出题 |
| AC-003 / 027 / 035 | API-006 + 主体 + 面试 + 催促写表 + EXT-002（求职者 Key） |
| AC-004 | fetch_jd / 禁抓主机失败 |
| AC-005 / 045 | API-006 或 API-010；调度读 `interview_at` |
| AC-006 | `rejected` 排除出题；面经走 047 |
| AC-007/008/009/012/036 | 调度 + 催促模型 + API-005 + EXT-003 |
| AC-010/011/018/020/040 | 调度出题 + 面试模型 + `append_interview_summary` + 知识库 excerpts |
| AC-013/014/019/030/031/034 | 主体分派 + `counseling_active` |
| AC-015/016 | EXT-003 |
| AC-022/023/024/025/026/028 | API-007/009/010/011 + 顶栏 UI-03 |
| AC-029–033 / 041 | 五套配置 + `llm_call_logs` + SSE 业务 status |
| AC-037/038 | EXT-004（D-005-A）+ `kb_notice` / 失败说明 + API-012 |
| AC-039 | API-015 上传 + API-012/013 |
| AC-042/043/044/046 | API-012–015 + 顶栏 UI-04 |
| AC-047/048 | API-010 或对话改已挂 → `kb_ask` → 表页则自动回对话（D-006-A）→ API-006 决定 |

---

## 待确认事项

无未决体验取舍。D-001–D-009 均已按建议锁定（本轮 D-007-C、D-008-B、D-009-B）。

头像与任务栏**布局**留给阶段 B，不改变已锁定接口与等待/失败。不挡技术方案确认。

不挡方案阅读、只影响真实验收：

- 每位求职者自己的百炼 Key 与付费（含联网搜索按次计费）。无求职者 Key 时不得用运营 Key 代打，也不得把 Mock 写成 AC-037/真实对话 AC 通过。
- SMTP 账号。未配置时走 AC-016（Key 失效模板邮件同样发不出去，对话说明仍在）。

用户已于 2026-09-14 确认本技术方案（含 D-007–D-009）。
