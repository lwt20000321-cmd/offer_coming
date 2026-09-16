# 项目经验

存放位置：`<active_project_path>/.sdd/experience.md`。仅由编排器在修复复验后判断、去重并更新；Developer/Tester 返回建议与证据。开始任务时按关键词检索相关标题，无命中就继续，不读取全部历史。

流程依据：`<harness_root>/harness-core/protocols/experience-loop.md`。经验不覆盖当前项目契约与所选规范；未复验、原因推测、一次性问题留在错误记录，不为每次报错增加条目。

## 条目格式（说明，不是已发生的经验）

实际记录时使用有辨识度的标题，填写：

- 关键词与适用条件：已知的技术栈、版本或触发场景。
- 现象与已证实根因；有效修复；下次如何避免。
- 来源任务或独立 Bugfix 描述；复验日期；证据的项目相对路径和具体章节，交付时返回实际绝对路径。
- 同类复发时更新原条目，说明旧经验未能避免问题的已核实原因，补充新的验证证据；失效结论标明并保留旧来源。

有跨项目价值时在原条目附简短全局候选；经用户授权，由编排器更新 `<harness_root>/memory/harness-experience.md` 并回链对应标题。全局记录不是自动生效的强制规则，不自动修改 Skill 或技术规范。不记录密钥、敏感原始数据或完整日志。

## 工具快照是 dict 时不要 json.loads

- 关键词与适用条件：Python 3.11、FastAPI SSE Agent、`ToolExecutor.get_snapshot` 返回 dict；`execute()` 才会 `json.dumps` 成工具观察字符串。
- 现象：发送消息后 SSE 只有 `error` / `LLM_UNAVAILABLE`，日志 `the JSON object must be str, bytes or bytearray, not dict`。
- 已证实根因：`get_snapshot()` 返回 dict，对返回值 `json.loads` 会 TypeError；工具参数字符串和 tool 消息 content 才是 JSON 文本。
- 有效修复：系统提示直接使用 dict，需要字符串时 `json.dumps`；`json.loads` 只解析确认是 str/bytes 的载荷。
- 下次如何避免：公开方法返回 dict 就当对象用；只有 tool 观察字符串才当 JSON 解析。
- 来源：T-004 Developer 自验修复，Tester 1d7ac097-14e2-44ff-9e4e-1fb5e3861c94 复验。
- 复验日期：2026-09-13。
- 证据：`.sdd/test-reports/test-T-004.md`「Developer 自验修复核对（get_snapshot dict / json.loads）」。绝对路径：`/Users/liuwentao/vibecoding_projects/Develop_Helper 2/Projects_Repo/offer_coming/.sdd/test-reports/test-T-004.md`。
- 全局候选：同类 Agent 循环混用 dict / JSON 字符串时易再犯；未写入全局经验。

## 已有 SQLite 表加非空列必须带 server_default

- 关键词与适用条件：Python 3.11、SQLAlchemy + SQLite；给已有 `applications` 等表增加 `nullable=False` 列；旧测试或裸 SQL 插入可能不写新列。
- 现象：旧测试插入缺 `interview_summary` → `NOT NULL` 失败。
- 已证实根因：SQLite 给已有行补非空列时没有默认值，且 ORM 未声明 `server_default`。
- 有效修复：`interview_summary` 使用 `server_default=""`；补丁 SQL 为 `TEXT DEFAULT ''`。
- 下次如何避免：已有表加非空列时同时写 ORM `server_default` 与 `ALTER ... DEFAULT`；测试库重建与旧库补列都要覆盖。
- 来源：T-015 Developer 自验修复，Tester 185485b2-d453-4ff3-a173-a880ea43e3d4 复验。
- 复验日期：2026-09-14。
- 证据：`.sdd/test-reports/test-T-015.md` TC-05。绝对路径：`/Users/liuwentao/vibecoding_projects/Develop_Helper 2/Projects_Repo/offer_coming/.sdd/test-reports/test-T-015.md`。
- 全局候选：SQLite 演进加非空列时的默认值约束；未写入全局经验。

## 加库级唯一约束前先核对接种子是否故意重复

- 关键词与适用条件：SQLite 唯一索引 / UNIQUE；投递表 `jd_url`；已有调度测试夹具。
- 现象：若建 `(candidate_id, jd_url)` 唯一索引，`test_scheduler.py` 对同一求职者插入两行同一 `https://example.com/job/1` 会失败。
- 已证实根因：旧夹具故意重复链接；产品唯一性只需在 API-009/010 服务层 409。
- 有效修复：不建库级 UNIQUE；`create`/`update` 查重后 409。`apply_sqlite_patches` 注释仍写「唯一索引」但未建，属注释漂移，不另开条目。
- 下次如何避免：加库约束前先搜测试种子是否故意重复；接口契约能保证的唯一性不必先落库约束。
- 来源：T-015 Developer 自验修复，Tester 185485b2-d453-4ff3-a173-a880ea43e3d4 复验。
- 复验日期：2026-09-14。
- 证据：`.sdd/test-reports/test-T-015.md` TC-02 / TC-05。绝对路径：`/Users/liuwentao/vibecoding_projects/Develop_Helper 2/Projects_Repo/offer_coming/.sdd/test-reports/test-T-015.md`。
- 全局候选：测试夹具与库约束冲突；未写入全局经验。

## 真实子 Agent 分派后编排层要补 Mock 路径的写库步骤

- 关键词与适用条件：Python 3.11、五模型 orchestrator、`_run_role_loop` 与 Mock 工具路径并存。考查点在 `record_application` 之前生成、当时没有 `application_id`；knowledge `task=ingest` 时模型可能只回「已收录」不调 `ingest_user_document`。
- 现象：有 Key 时投递表写出但 `exam_points` 为空；或对话声称已收录面经但 `knowledge_items` 为 0、「我的面经」仍空。
- 已证实根因：Mock 路径会立刻写库并设置收尾拼接条件；真实子循环结束后若编排层不补 `save_exam_points` / ingest，用户可见句与库不一致。
- 有效修复：record 成功后确定性补 `save_exam_points`；有附件/粘贴且未入库时确定性 ingest，`kb_notice` 以库为准。
- 下次如何避免：Mock 里紧跟写库的步骤，真实 `_run_role_loop` 结束后由编排层补上，不要只信模型口头确认。
- 来源：T-019 Developer 8775f633-68c8-416b-9653-d4b2e54d0dce、T-020 Developer ca0c1b07-bfec-48ad-a0e5-2aed1176fc76；Tester 49c2781a-bfcc-41df-b664-3caba35ba9f3、668777f5-a256-4d17-a1d6-a8ac51ee915b 复验。
- 复验日期：2026-09-14。
- 证据：`.sdd/test-reports/test-T-019.md` AC-003；`.sdd/test-reports/test-T-020.md` AC-039。绝对路径：`/Users/liuwentao/vibecoding_projects/Develop_Helper 2/Projects_Repo/offer_coming/.sdd/test-reports/test-T-019.md`、`/Users/liuwentao/vibecoding_projects/Develop_Helper 2/Projects_Repo/offer_coming/.sdd/test-reports/test-T-020.md`。
- 全局候选：多 Agent 编排里 Mock 写库步骤与真实子循环不对齐时易漏持久化；未写入全局经验。

## 出题 JSON 降级模板必须编入已有面经摘录

- 关键词与适用条件：Python 3.11、面试 `purpose=questions` JSON 校验失败或无 Key 走规则模板；`knowledge_items` 已有该岗或用户文档。
- 现象：库中已有可核对面经，当日三题仍是 STAR/权衡/考查点占位，看不出用了知识库。
- 已证实根因：`_fallback_template` 只拼简历套话，不读面经摘录。
- 有效修复：降级与无 Key 模板把面经可核对事实编进题目；LLM 入参带 `knowledge_excerpts`。
- 下次如何避免：有知识库的生成路径，降级文案也要引用可核对事实；用「已入库 + JSON 失败」测试双锁住。
- 来源：T-020 Developer 223ef51f-b546-46b7-80f5-5da04f49e038，Tester 668777f5-a256-4d17-a1d6-a8ac51ee915b 复验。
- 复验日期：2026-09-14。
- 证据：`.sdd/test-reports/test-T-020.md` AC-040。绝对路径：`/Users/liuwentao/vibecoding_projects/Develop_Helper 2/Projects_Repo/offer_coming/.sdd/test-reports/test-T-020.md`。
- 全局候选：模型输出校验失败后的规则模板漏业务上下文；未写入全局经验。

## 心情变好或答完三题后编排层要补派子 Agent

- 关键词与适用条件：Python 3.11、五模型 orchestrator、真实百炼；用户说心情变好或按题作答；测试双若已正确 tool_calls 则不触发。
- 现象：有 Key 时主体只回菜单或问心情；无 `nudge`/`purpose=review` 日志；答案与面试总结不落库。
- 已证实根因：主体未强制调子 Agent，编排层原先只在子循环跑过之后补写库，漏派时不会 `save_answers` / review。
- 有效修复：识别心情变好且有待办时补 `_run_nudge`；按题作答则 `save_answers`，齐套再 `_run_interview(review)` 并追加总结。
- 下次如何避免：Mock 会立刻分派的步骤，真实主体只出文本时编排层也要做；用「orchestrator 只回菜单」测试双锁住。
- 来源：T-021 Developer a512b0b2-9370-4cf5-8d8b-1474d9fa057a，Tester 3bb48c91-a56f-4500-b071-94ba9651172e 复验。
- 复验日期：2026-09-14。
- 证据：`.sdd/test-reports/test-T-021.md` AC-031 / AC-011。绝对路径：`/Users/liuwentao/vibecoding_projects/Develop_Helper 2/Projects_Repo/offer_coming/.sdd/test-reports/test-T-021.md`。
- 全局候选：真实模型不保证 tool_call 时编排层需按明确用户意图补派；未写入全局经验。

## 跳转前写入的 Context 提示必须在目的页渲染

- 关键词与适用条件：React Context 跨页提示；写入后立刻 `navigate`；本项目 `entryNotice` 欢迎页 → `/chat`。
- 现象：Mock 超时 Key 已进入对话，但「这次没连上」只在回到欢迎页才看见。
- 已证实根因：提示写进共享 Context 后只在源页（欢迎页）读取；目的页（当前对话）未渲染同一字段。
- 有效修复：目的页读取并展示同一 `entryNotice`（对话页 `.chat-entry-notice`）。
- 下次如何避免：先写 Context 再跳转时，核对目的页是否渲染该字段；不要只改源页。本条只覆盖「同源 Context 字段 + 立即跳转」；未验证其它跨页通知机制。
- 来源：T-023 Tester e086e746-2af9-482e-8663-0c2b99cda200 FAIL TC-03；Developer 927ea669-26ac-4497-86a4-680ece04320d 修复；Tester d33aeb3a-6609-4f2f-ad38-80b0dd444b18 复验。
- 复验日期：2026-09-14。
- 证据：`.sdd/test-reports/test-T-023.md` TC-03。绝对路径：`/Users/liuwentao/vibecoding_projects/Develop_Helper 2/Projects_Repo/offer_coming/.sdd/test-reports/test-T-023.md`。
- 全局候选：跨页通知只在源页渲染；未写入全局经验。

## 已登录访问欢迎路由必须持续重定向

- 关键词与适用条件：React Router 欢迎路由 `/`；`status==='authenticated'`；本项目 `App.tsx` + `OnboardingPage`。
- 现象：若只在首次启动把已登录用户从 `/` 跳到 `/chat`，之后再打开 `/` 仍会露出欢迎表单。
- 已证实根因：重定向必须跟当前鉴权状态和路径一起生效，不能只在首屏 boot 跳一次。
- 有效修复：`App.tsx` 的 `useEffect` 依赖 `status` 与 `pathname`，已登录访问 `/` 持续 `replace` 到 `/chat`；`OnboardingPage` 在 authenticated 时直接 `<Navigate to="/chat" replace />`，不渲染欢迎表单。
- 下次如何避免：欢迎页与登录态并存时，每次进入 `/` 都要重定向；用「进入后再打开 `/`」点两次，不要只测冷启动。
- 来源：T-025 Developer d7be5605-a6f2-474d-a921-02b6c69622e1 建议，Tester 9efe6d4c-7720-482d-80c5-e698679e5d55 复验。
- 复验日期：2026-09-14。
- 证据：`.sdd/test-reports/test-T-025.md` TC-01。绝对路径：`/Users/liuwentao/vibecoding_projects/Develop_Helper 2/Projects_Repo/offer_coming/.sdd/test-reports/test-T-025.md`。
- 全局候选：登录后欢迎路由若只跳一次，后续再进会露表单；未写入全局经验。

## 浏览器 Mock 会话表随 opaque token 落盘且禁止写 Key

- 关键词与适用条件：`VITE_USE_MOCK=true`；本项目 `frontend/src/mocks/session.ts`；localStorage 键 `offer_coming_mock_store_v1`。真实后端会话不走此表。
- 现象：仅用内存 Map 存 Mock 会话时，整页刷新后无法接上。
- 已证实根因：浏览器刷新会丢掉内存表；opaque token 必须与求职者摘要一起持久化，且表里不能有 Key。
- 有效修复：把 token 与求职者摘要写入 localStorage；扫描存储无 Key 字段、页面不回显完整 Key；刷新后仍在 `/chat`。
- 下次如何避免：Mock 刷新接续要同时落盘 token 与摘要；验收时打开存储面板确认没有 Key。不要把这条套到真实 API 会话。
- 来源：T-025 Developer d7be5605-a6f2-474d-a921-02b6c69622e1 建议，Tester 9efe6d4c-7720-482d-80c5-e698679e5d55 复验。
- 复验日期：2026-09-14。
- 证据：`.sdd/test-reports/test-T-025.md` TC-01 / TC-02。绝对路径：`/Users/liuwentao/vibecoding_projects/Develop_Helper 2/Projects_Repo/offer_coming/.sdd/test-reports/test-T-025.md`。
- 全局候选：仅内存的 Mock 会话表刷新即丢；未写入全局经验。

