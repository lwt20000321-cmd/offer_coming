# 工作日志

由编排器记录关键进展、验证证据、用户确认和阻塞原因。任务运行状态以 `.sdd/tasks.json` 为准，日志不维护第二份任务清单。

## 2026-09-15 对话五题被截断

- 用户截图：说「又截断了」后小凹承认长度受限，重发五题仍缺第五题，并带了「简历深挖 · 3题」标题。
- 根因：主体 `max_tokens=640`、落库 `_clip_user_reply` 500 字；闲聊路径把当日五题当普通气泡写，第四题后被切断。
- 处理：生成上限改为 2048；五题/点评不套 500 字截断；「今日五题 / 截断」直接回放库里五道题干。AC-056 改为闲聊仍短、五题与点评给完。未另派 Planner/Tester。

## 2026-09-15 今日五题与邮件只给题干

- 用户确认：对话里的今日五题只展示五道题干；题目分配、优先级、考查点等到答完点评时再给。邮件只催促并附上五题，不写进度表或考查点。
- 已改 PRD AC-007/010/011/015、出题/催促/点评提示词、对话气泡与整点邮件正文。进度仍可在对话页「查看进度」和「我的投递」看。未另派 Planner/Tester。

## 2026-09-15 录入时好时坏与对话突然消失

- 用户：录入信息时好时坏；有时对话会突然消失看不到。
- 录入根因：对话口头「已补录」不等于写库；考查点子模型超时会中断本轮，补写表走不到。已改为有公司/岗位/JD/补录意图时先落库；子模型超时只收口失败说明、不回滚已写入行。
- 对话消失根因：当前对话已超过默认 100 条。无 `after_id` 的 API-005 取的是最旧一页；发送后前端整表替换，刚聊的内容被裁掉。改为返回最近 `limit` 条（默认 200），发送后合并而不是整表替换。
- 无对应开发任务，记录本日志。未另派 Planner/Tester。

## 2026-09-15 对话声称已补录但投递表没有对应行

- 用户截图：对话写「已补录顺丰/海康、共 5 岗」，「我的投递」只有合合、万兴、微派三行。库内确认后两岗从未写入。
- 根因：表只读数据库。「加入到我的投递」未列入写表触发词；海康链接读失败后不写空行（AC-004），贴了 JD 后模型仍等「请补录」并口头说已写入。
- 补写触发词与正文解析；有公司/岗位/JD 时读失败也落库；无岗位信息的失败链接仍不写。未另派 Tester。
- 复发：用户再说「把顺丰的岗加入到投递表中」仍无行。日志显示本轮拿历史海康链接去跑考查点，用户在等待中退出，写表未执行。已改为当前句无新链接时先落库、不沿用旧链接；并把顺丰/海康两行补进当前求职者投递表。
- 再复发：创想三维飞书链接读到 19 字后 qwen-max 超时；用户贴 JD 并问「补录进去了吗」，模型口头成功仍未写表。改为识别链接宿主公司名、JD 正文和「补录」后先落库再调模型；已补 Bambu Lab 行。投递表切回时重新拉取。

## 2026-09-15 心理辅导 Agent 改为闺蜜人设

- 用户提供新正文：好闺蜜语气、「诡秘」谐音称呼、五种模式、幽默分级、拒绝强行积极。已覆盖写入 `counseling.md`。
- 对用户仍不露出 Agent 名；保留 `心情变好` 与 `set_counseling_state`。用户侧示例不再写「投递 Agent」。
- 另填 `recent_event`（最近更新投递）、`current_emotion`、`ready_for_action`、`session_ended`。共享七项变量块原文不变。未另派 Planner/Tester。

## 2026-09-15 投递表写入意图与九项状态

- 用户确认：「我投递了这个岗位」就必须写入投递表，不要等「请补录」；没说进度则追问。状态下拉增加简历筛选中、简历挂、待测评、已测评、一面、二面、三面，并保留等待面试、已挂。
- 编排层 `_ensure_apply_and_status` 在主体漏调工具时补写。一面/二面/三面/等待面试可出题；简历挂与已挂为 rejected。PRD AC-058、AC-059。未另派 Planner/Tester。

## 2026-09-15 对话带最近轮次，以支持接话与追问

- 用户确认：需要上下文记忆，否则无法做类似面试里的追问。PRD 增补 AC-057。
- API-006 将最近 `llm_history_max_messages` 条用户/助手气泡插入主体与子 Agent 请求；每条截断 `llm_history_max_chars`。
- 仍不做完整模拟面试场次；讲解/辅导可就上一句追问 1～3 句。未另派 Planner/Tester。

## 2026-09-15 先限回复长度，再收主体 prompt

- 用户确认：先限制对外回复长度，再收短主体 prompt。PRD 增补 AC-056。
- 主体 `orchestrator.md` 改为短指令（保留系统调用约定、每日五题 3+2、共享上下文块原文）。`## 当前快照` 只带时间/辅导/解析/投递计数。
- 主体调用 `max_tokens=llm_reply_max_tokens`（默认 640）；落库前 `_clip_user_reply` 按 `llm_reply_max_chars`（默认 500）截断。子 Agent JSON 出题不套此上限。
- 未另派 Planner/Tester。

## 2026-09-15 小凹流式输出与思考提示

- 用户确认：全部小凹对用户说的话改为流式出字；文字出现前必须有缓冲提示「小凹正在思考...」，不能空白干等。
- PRD 增补 AC-055；tech-spec API-006 首事件 `thinking`，可见回复走百炼 `stream=true`；ui-style / D-002 同步。
- 前端发送后立刻展示思考提示；Mock 与真实 SSE 均先 `thinking` 再 `delta`。
- 未另派 Planner/Tester（沿用已确认 D-002-B，补齐实现缺口）。

## 2026-09-15 心理辅导 Agent 正文

- 用户提供心理辅导正文：语气、不幼稚化、回应顺序、不同状态、缩小行动、尊重结束、专业边界。已写入 `counseling.md`。
- 未说心情变好不关掉辅导标记；暂停本轮不提五题。对用户不露出 Agent 名。新增填入 `{{session_ended}}`。共享七项变量仍保留。未另派 Tester。

## 2026-09-15 面试 Agent 正文

- 用户提供面试正文：岗位画像、每日五题（默认 3+2）、简历深挖/业务场景、单题结构、点评与复盘。已写入 `questions.md`；考查点与点评分文件。
- 调度出题仍只出五题 JSON。不做完整模拟面试/连环追问；「可能追问」写在题目或点评里。不编造简历事实。共享变量七项仍由系统填入。未另派 Tester。

## 2026-09-15 知识库 Agent 正文

- 用户提供知识库正文：检索优先级、来源可信度、岗位整理、简历深挖/业务场景支持、上传材料与更新规则、交接摘要。已写入 `knowledge_search.md`；收录与已挂评估仍分文件。
- 公开检索仍只出可核对 JSON；不对小红书域名发 GET；不把个人面经写成官方规则；不编造来源。共享变量七项仍由系统填入。未另派 Tester。

## 2026-09-15 投递催促 Agent 正文

- 用户提供投递催促正文：投递字段、跟进判断、跟进文案、优先级权重、每日五题归属与考查方向、输出分区。已写入 `nudge.md`。
- 整点催促仍按已确认 `tone_level` 1–4；跟进文案不得声称已发送；不生成完整题目；共享变量七项仍由系统填入。未另派 Tester。

## 2026-09-15 主体 Agent 正文再修订

- 用户更新主体正文：每日五题含义（默认 3+2，可 2+3 / 4+1；岗位 4＋1 / 3＋2 / 3＋1＋1）、调度四步、行动真实性、任务 JSON。已写入 `orchestrator.md`。
- 对外仍是小凹。已确认约束保留：不做完整模拟面试/连环追问；不对小红书域名发 GET；跟进文案只是草稿。共享变量七项仍由系统填入。未另派 Tester。

## 2026-09-15 共享变量按用户清单对齐

- 用户确认所有 Agent 共用：`{{current_date}}`、`{{user_profile}}`、`{{resume}}`、`{{job_list}}`、`{{calendar}}`、`{{uploaded_documents}}`、`{{knowledge_base}}`。十份提示词使用同一共享上下文块。
- `user_profile` 只填背景/求职方向/年限/城市/偏好，不再贴整份简历；最新简历只在 `{{resume}}`。年限、城市、偏好、可用时段、截图未建档时写「（暂无）」，不编造。未另派 Planner/Tester。

## 2026-09-15 每日改为五题

- 用户确认出题日合计五题。已更新 PRD REQ-005 / AC-010–012、018、020 与 tech-spec 出题约束。
- 默认 3 道简历深挖 + 2 道业务场景；校验允许 2+3 或 4+1，必须两种题型都有。规则模板与 Mock 同步为五题。
- 自验：`test_scheduler.py` + `test_agent.py` + `test_prompt_context.py` 28 passed；`test_question_sets.py` 补 Key 后按五题结构验收。未另派 Tester。历史 `tasks.json` 文案仍写三题，以 PRD 为准。

## 2026-09-15 主体 Agent 提示词修订

- 用户更新主体正文：统一入口、意图分类、专业 Agent 调用规则、每日题调度、行动真实性、任务 JSON。已写入 `orchestrator.md`。
- 当时仍按旧 PRD 三题执行；同日用户确认改为每天五题，见上方条目。不做完整模拟面试；知识库不对小红书域名发 GET。新增共享变量 `{{resume}}`。未另派 Tester。

## 2026-09-15 主体 Agent 提示词

- 用户提供主体 Agent 正文：识别意图、规划、分派、汇总；对用户不暴露内部分工。已写入 `backend/src/prompts/orchestrator.md`。
- 保留小凹对外身份，以及已确认的工具名、情绪优先、岗位链接顺序、禁止完整模拟面试等系统调用约定。当前上下文按用户四项填入，并保留已上传材料与知识库两项共享变量。未另派 Tester。

## 2026-09-15 全 Agent 共享提示词变量

- 用户要求先给所有 Agent 配置：`{{current_date}}`、`{{user_profile}}`、`{{job_list}}`、`{{calendar}}`、`{{uploaded_documents}}`、`{{knowledge_base}}`。各角色正文尚未另行替换。
- 实现：`backend/src/services/prompt_context.py` 运行时填入；十份 `backend/src/prompts/*.md` 均含占位；编排/子 Agent/出题/催促/已挂评估/面经检索调用处替换。工作年限、城市未单独建档，填「（暂无）」并禁止编造。未另派 Planner/Tester（需求与验收不变的实现细节）。
- 自验：`test_prompt_context.py` + `test_agent.py` 共 18 passed（含共享变量替换与编排/子 Agent 既有用例）。未另派 Tester。

## 2026-09-14 用户休息，overnight 续跑

- 用户授权：休息期间继续 automatic，醒后验收 Mock 门禁。
- T-012 / T-013 / T-014 passed（前端阶段 / Mock）。user_gate=awaiting_user，已停新派发。
- T-015 / T-016 / T-017 passed。T-018 Developer 已交回，status=testing，Tester 暂缓至门禁放行。
- 用户醒后：验收 http://127.0.0.1:5199/ Mock，并在 backend/.env 填齐百炼五套模型与 Key（不要把密钥发到对话）。
- 2026-09-14 用户反馈首条对话贴在卡片上方、输入框上留白过大。已改 `.chat-thread` 为 `justify-content: flex-end`，首条贴在输入区上方。门禁仍 awaiting_user。
- 2026-09-14 用户确认「界面可以」。user_confirmation 已记。占位检查：百炼 Key、base_url、五套模型名均非空且五套互不相同；未读出密钥。仍等用户确认「百炼五套配置已填」后才能放行门禁。
- 2026-09-14 用户确认「百炼五套配置已填」。user_gate=passed。恢复 T-018 Tester，不重跑已稳定前端。真实连通性由后续联调验收，不靠读取 .env 宣布通过。
- 2026-09-14 T-018 Tester 08ad740b-9336-4651-8917-72da09e44a58 PASS（后端阶段）。报告 .sdd/test-reports/test-T-018.md。已派 T-019 Developer。
- 2026-09-14 T-019 PASS。AC-003 曾 FAIL（真实分派未 save_exam_points），定点修复后复验通过。报告 .sdd/test-reports/test-T-019.md。已沉淀「真实子 Agent 分派后编排层要补 Mock 路径的写库步骤」。续派 T-020。
- 2026-09-14 T-020 PASS（AC-037 真实检索成功仍 BLOCKED）。AC-039/040 曾 FAIL 已修。报告 .sdd/test-reports/test-T-020.md。已沉淀「出题 JSON 降级模板必须编入已有面经摘录」。续派 T-021。
- 2026-09-14 T-021 PASS。AC-031/011 曾 FAIL（主体只回菜单），编排层补派后复验通过。报告 .sdd/test-reports/test-T-021.md。续派 T-022 刷新导航。
- 2026-09-14 T-022 PASS。报告 .sdd/test-reports/test-T-022.md。本轮 T-012–T-022 已全部 passed。AC-037 真实检索成功入库仍 BLOCKED（已发起 enable_search 但未抽出可用正文）。
- 2026-09-14 用户确认上线后求职者自备百炼 Key：换设备不必再贴；须能更换。进入对话后「初次见面」变为虚拟头像，点头像下方任务栏为「我的简历」「我的key」「退出」。PRD 已改为 Draft（REQ-001 增补、新增 REQ-011 / AC-049–054）。待用户确认本轮 PRD 后再改 tech-spec 与界面。
- 2026-09-14 用户确认本轮 PRD。PRD status=Confirmed。tech-spec 改为 Draft。Solution Designer `1d85495e-c4ba-45a9-9fd6-6731d55d04b1` 已交回草案；阻塞项 D-007/D-008/D-009 待用户选择。
- 2026-09-14 用户确认 D-007-C、D-008-B、D-009-B。tech-spec status=Confirmed。阶段 B：ui-style Draft + 原型已加 Key 行与头像任务栏，待用户确认界面。
- 2026-09-14 用户确认界面。ui-style Confirmed。Planner `9689b06c-09ed-47ba-86cf-4fdc46d9a1b5` 写入 T-023–T-031。已并行派 Developer：T-023 `109575b0-552f-4c1d-a8e6-a97134d65571`，T-026 `4dc156f0-38bd-472e-b3bb-15988077ffa7`。
- 2026-09-14 用户确认推进模式为 automatic。execution_mode 本已是 automatic，不改。T-023/T-026 仍在实现，完成后自动验收并续派。
- 2026-09-14 T-023 Developer `109575b0-552f-4c1d-a8e6-a97134d65571` 交回，status=testing。已派 Tester。T-026 仍 in_progress。
- 2026-09-14 T-023 Tester `e086e746-2af9-482e-8663-0c2b99cda200` FAIL TC-03（对话页不见「这次没连上」）。报告 `.sdd/test-reports/test-T-023.md`。status=fixing，retry_count=1；write_scope 补 `ChatPage.tsx`。
- 2026-09-14 T-023 Developer `927ea669-26ac-4497-86a4-680ece04320d` 交回 ChatPage 展示 entryNotice。status=testing，待复验。经验候选暂不落盘。
- 2026-09-14 T-023 Tester `d33aeb3a-6609-4f2f-ad38-80b0dd444b18` 复验 PASS（前端阶段/Mock）。报告 `.sdd/test-reports/test-T-023.md`。已沉淀「跳转前写入的 Context 提示必须在目的页渲染」。续派 T-024。
- 2026-09-14 T-026 Developer `4dc156f0-38bd-472e-b3bb-15988077ffa7` 交回（pytest 28 passed，httpx mock）。status=testing。并行：T-024 Developer `eee2cd3b-21df-4850-90e8-e0d5efe2400d`，T-026 Tester `3467e3af-b6cc-47ed-ae5f-da7e0e011021`。
- 2026-09-14 T-026 Tester `3467e3af-b6cc-47ed-ae5f-da7e0e011021` PASS（后端阶段/httpx mock）。报告 `.sdd/test-reports/test-T-026.md`。无新增经验。续派 T-027 Developer `d7105915-dc3b-4a63-a0a2-588543602bae`。T-024 仍 in_progress。
- 2026-09-14 T-024 Developer `eee2cd3b-21df-4850-90e8-e0d5efe2400d` 交回头像任务栏 Mock。T-027 Developer `d7105915-dc3b-4a63-a0a2-588543602bae` 交回 API-016/017/018。Tester：T-024 `b9ba0a6b-36d0-4305-a931-d31dd1fccd96`，T-027 `e0bcd7ff-23e6-41e5-aad4-5e7186a82a74`。
- 2026-09-14 T-027 Tester `e0bcd7ff-23e6-41e5-aad4-5e7186a82a74` PASS（后端阶段/httpx mock）。报告 `.sdd/test-reports/test-T-027.md`。无新增经验。续派 T-028 Developer `721d243e-2830-4d6a-b96d-8854df1aeebc`。T-024 仍 testing。
- 2026-09-14 T-024 Tester `b9ba0a6b-36d0-4305-a931-d31dd1fccd96` PASS（前端阶段/Mock）。报告 `.sdd/test-reports/test-T-024.md`。无新增经验。续派 T-025 Developer `d7be5605-a6f2-474d-a921-02b6c69622e1`。T-028 仍 in_progress。
- 2026-09-14 T-025 Developer `d7be5605-a6f2-474d-a921-02b6c69622e1` 交回 Mock 收口。Tester `aa64f8e5-e018-4fbc-80a4-246426591da8` 验收中。T-028 仍 in_progress。
- 2026-09-14 T-028 Developer `721d243e-2830-4d6a-b96d-8854df1aeebc` 交回。Tester `35f9c17d-1a01-45dc-9d8a-3646cf272c21` 验收中。T-025 仍 testing。
- 2026-09-14 T-028 Tester `35f9c17d-1a01-45dc-9d8a-3646cf272c21` PASS（后端阶段/httpx mock）。报告 `.sdd/test-reports/test-T-028.md`。无新增经验。不派 T-029（等 T-025 门禁）。T-025 仍 testing。
- 2026-09-14 T-025 Tester `aa64f8e5-e018-4fbc-80a4-246426591da8` 因 Cursor 月度额度耗尽中断。已写 `.sdd/test-reports/t025-accept.mjs`，未执行、无报告。status 保持 testing。不派 T-029。恢复额度后重派 Tester。
- 2026-09-14 T-025 Tester `9efe6d4c-7720-482d-80c5-e698679e5d55` PASS（前端阶段/Mock）。报告 `.sdd/test-reports/test-T-025.md`。任务 passed，`user_gate=awaiting_user`。停新派发（含 T-029）。已沉淀「已登录访问欢迎路由必须持续重定向」「浏览器 Mock 会话表随 opaque token 落盘且禁止写 Key」。交用户验收 Mock：http://127.0.0.1:5199/ 。占位检查：百炼 Key、base_url、五套模型名均非空且五套互不相同；未读出密钥。SMTP 本轮不作为门禁必需。上一轮 T-014 界面确认不替代本轮 Key+头像走查。
- 2026-09-14 用户确认 Mock「没有问题」，并确认配置仍可用。user_gate=passed。不重跑已稳定前端。续派 T-029（关闭 Mock、真实验收 AC-001/002/017/021/049）。不得用运营 llm_api_key 代填求职者 Key。T-007 保持 passed。
- 2026-09-14 T-029 Developer `0df07b85-ef3b-46ce-9c68-276d2998c751` 交回。自验：`test_onboarding_integration.py` 8 passed；页面走通 AC-001/002/017/021/049（占位 Key + 本地探活，未扣费）。status=testing，待独立 Tester。未派 T-030。
- 2026-09-14 T-029 Tester `47acf415-df61-4cdd-a9d4-58f22d3f9f88` PASS（真实 5199→8099；探活本地 mock）。报告 `.sdd/test-reports/test-T-029.md`。AC-001/002/017/021/049 通过；真实百炼 401 BLOCKED。不新增经验。续派 T-030 Developer `976f2f83-225e-4062-983f-9c6b1c72d6fa`。
- 2026-09-14 T-030 Developer `976f2f83-225e-4062-983f-9c6b1c72d6fa` 交回。status=testing。已派 Tester `e6222aee-2213-4fa3-9427-3ba2755fb1de`。未派 T-031。
- 2026-09-14 T-030 Tester `e6222aee-2213-4fa3-9427-3ba2755fb1de` BLOCKED（无代码缺陷）。报告 `.sdd/test-reports/test-T-030.md`。AC-050/053 与局部 051/052/054 已过；考查点对照、真实换 Key、真实 401、SMTP 邮件未授权。status=blocked。不新增经验。不派 T-031。恢复：求职者真实 Key（页面粘贴，勿发到对话）后恢复 testing；SMTP 配好后再验邮件。
- 2026-09-14 用户选择本轮先收口（选项 2）：接受上述未验项，继续 T-031。T-030 恢复 testing，已派 Tester `fca1b95e-7b91-4ca7-81ab-5a107d40947a` 按任务允许的 BLOCKED 与 T-020 先例重述总结果，不重跑已稳定页面。
- 2026-09-14 T-030 Tester `fca1b95e-7b91-4ca7-81ab-5a107d40947a` PASS（考查点对照 / 真实换 Key / 真实 401 / SMTP 邮件仍 BLOCKED）。报告 `.sdd/test-reports/test-T-030.md`。不新增经验。续派 T-031 Developer `3d66e91b-094f-4fea-aa31-65e55b89664e`。
- 2026-09-14 T-031 Developer `3d66e91b-094f-4fea-aa31-65e55b89664e` 交回导航刷新。status=testing。已派 Tester `b3817e33-118e-4afa-9278-5551a9eecf6b`。
- 2026-09-14 T-031 Tester `b3817e33-118e-4afa-9278-5551a9eecf6b` PASS。报告 `.sdd/test-reports/test-T-031.md`。DEL-001～005 通过。无新增经验。本轮任务均 passed；T-030 考查点对照 / 真实换 Key / 真实 401 / SMTP 邮件仍未验，不宣称项目全部完成。
- 2026-09-15 用户改对话页布局（无新任务）：底栏与顶栏左右齐平；输入框默认单行插入符居中，字多增高至约三行（96px）后框内滚动。根因：对话主列连同底栏锁在 840/760 内，输入 `max-height:48px` 裁切长文本。已改 `ChatPage.tsx` / `appShell.css` / `ui-style.md` / 原型。Chrome 1440×900 Mock `http://127.0.0.1:5175/chat` 测量：底栏与 `.tabs-right` 右缘差 0px；空输入 48px/`line-height:48px`；长文本 96px 封顶。未另派 Tester（无任务 ID）。不新增经验（一次性布局收口）。
- 2026-09-15 用户改对话气泡：消息区左右 24px 对称；小凹左灰气泡、用户右黑气泡（不再写「你：」）；字号 14px。已改 `ChatPage.tsx`、`docs/ui-style.md`、`docs/prototypes/ui-prototype.html`。Chrome Mock 5175 测量左右内边距均为 24、分侧、14px。未另派 Tester。不新增经验。
- 2026-09-15 用户要求对话白卡片与「我的面经」文件区同宽同高同位置。对话主列改为 1080px，页头与面经页头对齐，白卡片与 `.sheet` 重合（Chrome 1440×900 差 0.2px）。心情/Mock 底栏改为固定在页底，不占卡片高度。已改 `ChatPage.tsx`、`appShell.css`、`ui-style.md`、原型。未另派 Tester。不新增经验。
- 2026-09-15 用户改对话页：logo 56px；去掉消息区顶部渐隐；用户气泡改为淡粉 `#F6D5DC`、深色字。已改 `ChatPage.tsx`、`ui-style.md`、原型。Chrome Mock 5175 已核。未另派 Tester。不新增经验。
- 2026-09-15 用户要求保持面经白卡片尺寸：logo 仍 56px 但向上探出，进度文字与 logo 下沿齐平；用户气泡改回与小凹相同的灰色 Pill。Chrome 卡片差 0.2px，下沿对齐差 0。已改 `ChatPage.tsx`、`ui-style.md`、原型。未另派 Tester。不新增经验。
