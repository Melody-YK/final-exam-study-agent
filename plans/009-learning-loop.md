# Plan 009: 将可信课程资料闭环为主动回忆与复习队列

> **执行者说明**：按本文档顺序执行。每一步都必须先完成实现，再运行该步的验证命令并确认预期结果，才能进入下一步。遇到“停止条件”时停止并报告，不要自行扩大范围。完成后更新 `plans/README.md` 中本计划的状态；除非调度者另有要求，不要提交、推送或创建 PR。
>
> **漂移检查（第一步执行）**：
> `git diff --stat a6ce61a -- packages/contracts/python/src/study_contracts services/api/alembic services/api/src/study_agent/infrastructure/db/models services/api/src/study_agent/api services/api/src/study_agent/modules apps/web/src/api apps/web/src/features apps/web/src/app/navigation.tsx tests/e2e`
> 当前主工作树已经提交并推送；执行者必须在基于该提交的干净隔离工作区执行本计划。

## 状态

- **执行状态**：DONE；基线实现、主题投影、审查和全量验证已完成；2026-08-03 完成标题展示、作答草稿恢复和学习单元重新整理增量修复
- **优先级**：P0
- **工作量**：L
- **风险**：HIGH
- **依赖**：计划 004、005、006 的资料就绪、可操作概念地图和可预测笔记行为；不依赖计划 007 的图谱算法重做
- **类别**：direction
- **计划基线**：commit `a6ce61a`，2026-08-02

### 2026-08-03 增量收口

- 学习单元标题在展示边界去除单级章节编号（如 `16.`、`2.`、`2)`），保留 `6.5.3` 这类层级编号；原始 `canonical_key`、题目来源和证据绑定不变。
- 练习会话按 `sessionId` 将当前题号和未提交选项保存到浏览器本地；重新进入或刷新页面时恢复，提交答案仍以服务端为准，完成练习后清除草稿。
- 退出练习会重新拉取会话，避免使用过期的内存快照；未提交草稿暂不跨设备同步。
- 学习单元增加显式“重新整理学习单元”动作：从文件名提取章节主题，将扁平小标题归入文档主题，隐藏子章节练习入口，并把旧投影标记为 stale/unavailable 但保留历史题目、来源和掌握度引用。该动作只重建学习单元投影，不自动重新调用题目生成 Provider。
- 收口证据：前端组件测试、标题清洗单测、Web 静态检查/构建，以及 Chromium/WebKit 桌面/移动 E2E 均通过；具体命令和结果见 AIWF `00_EVIDENCE.md` / `00_TEST_AUDIT.md`。

## 为什么做

当前系统已经能把资料变成可追溯的问答、笔记和概念导航，但用户的主要动作仍然是被动阅读和提问。缺少“主动回忆、判分、识别薄弱点、安排下一次复习”的状态链路，因此系统还不能证明学生是否真的学会。该计划把现有 Evidence、活动 Revision、课程范围和持久化任务基础扩展成一条最小可验证学习闭环：用户选择学习范围，完成一组有据题目，系统记录结果并返回下一步复习任务。

本计划刻意不把“图谱视觉效果”作为成功前提。第一版需要稳定的学习单元和来源绑定；图谱可以继续使用当前投影，后续再单独替换概念抽取和关系算法。

## 当前状态

### 已有能力

- `apps/web/src/app/navigation.tsx:3-8` 目前只有资料、问答、笔记、概念地图四个学生入口。
- `apps/web/src/features/qa/QAPage.tsx:480-645` 提供课程会话问答；问答只使用当前课程活动来源，并在 Provider 不可用时关闭提交。
- `services/api/src/study_agent/modules/answering/evidence_gate.py:41-61` 在调用模型前检查活动索引和 Evidence 分数；`services/api/src/study_agent/modules/answering/service.py:80-136` 验证引用并在资料版本变化时拒答。
- `apps/web/src/features/notes/NotesPage.tsx:49-77` 提供“考前速记、结构提纲、完整讲义”三种笔记风格；`services/api/src/study_agent/modules/notes/demo_runner.py` 和 `note_workflow.py` 提供可复用的持久化批次、租约、幂等和事件模式。
- `services/api/src/study_agent/modules/knowledge_graph/service.py:199-200` 将活动资料块即时投影成有界、只读的图谱；它不是一个能长期保存用户掌握度的稳定概念实体层。
- `services/api/src/study_agent/infrastructure/db/models` 当前有课程、资料、Revision、问答、笔记、检索和任务模型，但没有题目、作答、掌握度或复习安排模型。
- `services/api/src/study_agent/main.py:337-349` 集中注册 API router；新学习路由必须在此注册，并保持现有 Principal/course scope 约束。

### 明确缺口

本计划新增以下业务事实：

1. 稳定的课程级学习单元（`LearningUnit`），至少能表示章节和概念，并绑定当前资料证据。
2. 绑定来源 Revision 的选择题/判断题，以及题目解释和 Evidence 引用。
3. 一次有界的练习会话、用户作答和确定性评分。
4. 每个用户/课程/学习单元的掌握状态和可解释的薄弱点更新。
5. 基于简单间隔规则的待复习队列。
6. 学习工作台：开始练习、作答、查看依据、查看结果和继续下一项复习。

## 产品边界与不可变决策

- V1 题型只包括 `single_choice` 和 `true_false`。主观简答题、作文评分和语音作答不在本计划内。
- V1 每次练习最多 10 道题；用户可以选择一个章节或最多 8 个学习单元。不要实现无限题库、全课程一次性生成或后台批量预生成。
- 题目生成必须使用当前课程活动来源，并在题目记录中保存来源 Revision、chunk/source locator 和内容哈希。活动 Revision 变化、资料删除或 Evidence 不再授权后，题目不得继续出现在练习队列中。
- 生成题目可以调用真实 Chat Provider；未配置 Provider 时，题目生成必须明确不可用，不能使用运行时假模型。已有题目仍可作答，前提是其来源仍然有效。
- 题目生成和练习状态必须具备幂等、可恢复和有界并发。沿用 `NoteGenerationBatchModel` / `DemoNoteRunner` 的状态、租约、事件和幂等思想；不要直接复制 Demo 专用实现，也不要新增未定义的外部队列依赖。
- 掌握度第一版使用可解释的离散等级或有界分数，不引入 Bayesian Knowledge Tracing、机器学习模型或不可解释的个性化算法。
- 当前图谱算法、Neo4j、LLM 全量关系本体、图谱画布交互不在本计划内。学习单元可以由章节结构和受控的概念候选组成；如果需要模型提取候选，必须输出结构化结果并绑定 Evidence。
- 云端对象存储、横向扩容、生产入口代理、计费、配额和完整观测是独立的云部署计划，不在本计划内。本文只要求本地可测试的控制面和有界任务行为。

## 需要使用的命令

| 目的 | 命令 | 成功标准 |
|---|---|---|
| 契约单测 | `uv run pytest -q packages/contracts/python/tests` | 全部通过；新增学习契约测试通过 |
| API 学习单元测试 | `uv run pytest -q services/api/tests/unit/learning` | 全部通过；覆盖评分、掌握度、调度、题目校验 |
| API 学习集成测试 | `uv run pytest -q services/api/tests/integration/test_learning_loop.py` | PostgreSQL 环境下全部通过 |
| OpenAPI 契约 | `uv run pytest -q services/api/tests/contract/test_openapi.py` | 生成路由和 schema 与 OpenAPI 一致 |
| Web 单测 | `npm test --workspace @study-agent/web -- --run` | 全部通过 |
| Web 类型 | `npm run typecheck` | exit 0，无 TypeScript 错误 |
| Web lint | `npm run lint` | exit 0，无 warning/error |
| Web 构建 | `npm run build` | exit 0，产物构建成功 |
| 全量检查 | `make check` | format、Ruff、mypy、Python coverage、Web test、build 全部通过；外部依赖阻塞必须明确记录，不得伪报成功 |

OpenAPI 或 TypeScript 生成文件必须通过仓库现有命令生成：`npm run generate:api`。不要手工编辑 `packages/contracts/openapi/openapi.json` 或 `apps/web/src/api/generated/schema.ts`。

## 范围

### 允许修改或新增的文件

- `packages/contracts/python/src/study_contracts/learning_loop.py`（新增稳定枚举、请求和响应契约）
- `packages/contracts/python/src/study_contracts/__init__.py`
- `packages/contracts/python/tests/test_learning_loop.py`（新增）
- `services/api/alembic/versions/20260801_0013_learning_loop.py`（新增；执行时必须确认当前 Alembic head 未漂移）
- `services/api/src/study_agent/infrastructure/db/models/learning.py`（新增 ORM 模型）
- `services/api/src/study_agent/infrastructure/db/models/__init__.py`
- `services/api/src/study_agent/api/schemas/learning_loop.py`（新增 HTTP schema）
- `services/api/src/study_agent/api/routers/learning_loop.py`（新增学生学习路由）
- `services/api/src/study_agent/main.py`
- `services/api/src/study_agent/config.py`（新增默认关闭的练习生成 runner 配置）
- `services/api/src/study_agent/modules/learning/__init__.py`（新增）
- `services/api/src/study_agent/modules/learning/concepts.py`（学习单元候选和来源绑定）
- `services/api/src/study_agent/modules/learning/questions.py`（题目生成、结构校验、来源校验）
- `services/api/src/study_agent/modules/learning/runner.py`（有界、可恢复的练习题生成 runner）
- `services/api/src/study_agent/modules/learning/scoring.py`（客观题评分）
- `services/api/src/study_agent/modules/learning/mastery.py`（掌握度更新）
- `services/api/src/study_agent/modules/learning/scheduling.py`（复习队列规则）
- `services/api/src/study_agent/modules/learning/service.py`（Principal/course scoped 应用服务）
- `services/api/tests/unit/learning/test_concepts.py`（新增）
- `services/api/tests/unit/learning/test_questions.py`（新增）
- `services/api/tests/unit/learning/test_scoring.py`（新增）
- `services/api/tests/unit/learning/test_mastery.py`（新增）
- `services/api/tests/unit/learning/test_scheduling.py`（新增）
- `services/api/tests/integration/test_learning_loop.py`（新增）
- `services/api/tests/integration/__init__.py`（新增；避免与契约测试同名模块在全量 pytest 收集时冲突）
- `services/api/tests/contract/test_openapi.py`（仅在需要添加 schema 断言时修改）
- `packages/contracts/openapi/openapi.json`（仅由生成命令更新）
- `apps/web/src/api/client.ts`
- `apps/web/src/api/types.ts`
- `apps/web/src/api/generated/schema.ts`（仅由生成命令更新）
- `apps/web/src/app/navigation.tsx`
- `apps/web/src/features/learning/LearningPage.tsx`（新增学习工作台）
- `apps/web/src/features/learning/PracticeSession.tsx`（新增作答流程）
- `apps/web/src/features/learning/LearningSummary.tsx`（新增结果和待复习视图）
- `apps/web/src/features/learning/*.test.tsx`（新增对应组件测试）
- `apps/web/src/styles/workspace.css`（只添加学习工作台需要的样式）
- `tests/e2e/learning.spec.ts`（新增）
- `tests/e2e/mockApi.ts`（只添加学习闭环 mock）
- `docs/learning-loop.md`（新增产品/数据约束说明）
- `plans/README.md`（更新本计划状态；由执行者完成）

### 明确禁止修改的范围

- `services/worker/src/study_worker/parsers/**`、OCR、PDF、Markdown 解析器
- 现有问答证据门禁、CitationValidator、来源授权逻辑；如必须共享函数，先停止并报告，不能偷偷改变问答行为
- 当前知识图谱算法和 `KnowledgeGraphPage.tsx` 的图谱布局；图谱替换另立计划
- 云基础设施、Docker Compose 拓扑、对象存储、外部消息队列、自动扩容、入口代理和生产 Secret 管理
- 音频、视频、TTS、ASR、协作、公开题库、题目分享、闪卡导入
- 选择题/判断题以外的评分能力
- 与本计划无关的 `.env`、`.idea`、第三方声明和格式化全仓库改动

## 数据与 API 设计

### 1. 学习单元

建议 `learning_units` 至少包含：`id`、`user_id`、`course_id`、`canonical_key`、`label`、`kind`、`parent_id`、`status`、时间字段。课程内 `canonical_key` 必须唯一，避免 Revision 更新后同一概念产生多个掌握记录。`kind` V1 只允许 `section` 和 `concept`。

建议 `learning_unit_sources` 保存：`unit_id`、`document_id`、`revision_id`、`chunk_id`、`content_sha256`、来源位置和创建时间。所有跨用户或跨课程外键必须采用现有的复合 scope 约束模式，参考 `services/api/src/study_agent/infrastructure/db/models/note_workflow.py`。

### 2. 题目与生成批次

建议题目表保存：`id`、`user_id`、`course_id`、`learning_unit_id`、`source_revision_id`、`question_type`、`prompt`、`options`、`correct_answer`、`explanation`、`evidence_refs`、`difficulty`、`status`、`content_sha256`、时间字段。`options`、`evidence_refs` 等结构化字段可以使用 JSONB，但必须在 Pydantic schema 和服务层做严格校验；不要把未经校验的 Provider 原始响应直接持久化。

题目生成批次至少保存：请求范围、目标题数、状态、阶段、租约/重试信息、幂等键哈希、失败原因和生成的题目 ID。批次下使用有序 item 行记录每道题的生成状态；需要重试时保存 attempt 的 provider/model/耗时/错误码等元数据，不保存原始响应。批次状态必须区分排队、运行、成功、部分成功、失败和取消，并能在 API 重启后恢复或明确失败。

### 3. 练习会话、作答和掌握度

建议会话保存：`course_id`、用户、选定单元、目标题数、模式、状态、开始/完成时间。作答保存：`session_id`、`question_id`、用户答案、得分、是否正确、反馈、耗时和回答时间。掌握记录保存：`learning_unit_id`、尝试次数、正确次数、最近得分、掌握等级、`next_review_at` 和最后一次作答时间。

V1 掌握规则必须是纯函数并有测试。例如可使用 0-3 等级：首次正确升一级，连续错误降一级，查看提示后正确不超过一级，超过上限/下限时钳制。具体数值可以在实现前调整，但必须写入契约和测试，不允许散落在 UI 或 SQL 中。

### 4. API

建议至少提供以下 Principal scoped 路由，具体路径和命名在实现前保持一致，不要同时发布两套近似 API：

- `GET /courses/{course_id}/learning-units`：列出当前课程可用学习单元及来源状态。
- `POST /courses/{course_id}/learning-units/regenerate`：显式重新整理当前学习单元投影；保留历史引用，不删除题目、来源或掌握度。
- `POST /courses/{course_id}/practice-batches`：按单元和题数创建题目生成批次，要求 `Idempotency-Key`。
- `GET /practice-batches/{batch_id}`：读取生成进度和题目结果。
- `POST /courses/{course_id}/practice-sessions`：从有效题目创建一次练习会话。
- `GET /practice-sessions/{session_id}`：读取会话、当前题目和已答状态。
- `POST /practice-sessions/{session_id}/attempts`：提交一次客观题作答，必须幂等并返回 Evidence 绑定的解释。
- `GET /courses/{course_id}/review-queue`：返回按 `next_review_at`、薄弱程度和来源有效性排序的待复习单元。
- `GET /courses/{course_id}/learning-summary`：返回章节/概念掌握汇总、已完成会话和下一步行动。

所有路由必须使用可信 Principal 和课程 scope；客户端提供的 `course_id`、`learning_unit_id`、`question_id`、`revision_id` 只能作为查询条件，不能作为授权事实。题目、来源和掌握记录必须防止跨用户读取。

## 执行步骤

### Step 0：基线、漂移和产品决策冻结

1. 在干净分支执行顶部漂移检查，确认当前 Alembic head、OpenAPI 生成流程、模型导入和 `main.py` router 注册位置仍与“当前状态”一致。
2. 运行 `git status --short`，确认允许修改的文件没有来自其他工作流的未提交改动。
3. 在 `docs/learning-loop.md` 记录 V1 用户路径、题型边界、掌握度规则、题目来源失效规则和不做事项。
4. 使用当前公开 fixture 设计至少 3 个学习单元、6 道题和 2 个章节的验收数据；不得把私有课程资料写入仓库。

**收口条件**：

- [ ] 工作分支干净，且 `git diff --stat` 不显示本计划范围外的基线混入。
- [ ] `docs/learning-loop.md` 明确回答“什么时候题目有效、什么时候必须拒绝、掌握度如何更新、用户下一步看到什么”。
- [ ] 题型、单次题数、掌握度规则和图谱不重做的边界已经固定。

**验证**：`git status --short && git diff --check` → 只有计划允许的基线状态；无 whitespace 错误。

### Step 1：建立学习单元契约和来源绑定

1. 在 `study_contracts/learning_loop.py` 定义 `LearningUnitKind`、`LearningUnitStatus`、`QuestionType`、`PracticeBatchStatus`、`PracticeSessionStatus`、`MasteryLevel` 等枚举和响应模型。
2. 为每个 `LearningUnit` 暴露稳定 ID、canonical key、label、kind、parent、source status；不要把图谱节点 ID 直接当学习单元 ID。
3. 为学习单元来源定义严格的 `document_id`、`revision_id`、`chunk_id` 和 locator 字段，复用现有 `SourceLocator` 的 vocabulary，不另造一套页码字段。
4. 在 `services/api/src/study_agent/modules/learning/concepts.py` 实现课程范围内的单元读取和候选建立。V1 先使用已有 Revision 的 section path 与受控词项；如果使用模型提取概念，只允许模型返回结构化候选，服务层必须验证所有来源引用。
5. 为不存在的来源、非活动 Revision、未审核资料和跨课程/跨用户单元返回明确的错误码。

**收口条件**：

- [ ] 同一课程同一 `canonical_key` 不会生成多个学习单元。
- [ ] 每个返回的学习单元至少有一个有效来源绑定，或明确标记为不可用于练习。
- [ ] 删除资料或切换 Revision 后，旧来源不会继续被当作有效学习依据。
- [ ] 无图谱算法修改；图谱仍可读取，但学习单元不依赖图谱展示状态。

**验证**：`uv run pytest -q services/api/tests/unit/learning/test_concepts.py` → 覆盖 owner scope、Revision 失效、重复 key、无来源和章节/概念层级，全部通过。

### Step 2：建立数据库和迁移基础

1. 在 `learning.py` 添加学习单元、来源绑定、题目、题目来源、生成批次、生成事件、练习会话、作答和掌握度 ORM 模型；优先使用和现有 note workflow 相同的 `new_id`、时间字段、复合 scope FK、CheckConstraint 和索引风格。
2. 在 `20260801_0013_learning_loop.py` 创建表、约束和查询所需索引。迁移必须从实际当前 head 线性连接，不得创建第二个 Alembic 分支。
3. 对枚举字段建立数据库 CHECK；对分数、题数、目标数量、时间顺序、终态时间、题目选项和幂等 key 哈希建立约束。
4. 将模型导入 `models/__init__.py`，确保 metadata 能发现所有表。
5. 增加迁移测试：升级后表存在，非法枚举/跨 scope/越界分数/终态时间不一致被数据库拒绝，降级可以安全执行。

**收口条件**：

- [ ] 迁移在空数据库和现有数据库上都能升级。
- [ ] 所有用户可见学习查询都能通过 `user_id + course_id` scope 约束过滤。
- [ ] 题目、来源、会话和掌握记录不存在悬空外键。
- [ ] 没有保存 Provider credential、原始 Provider 响应或私有路径。

**验证**：`uv run pytest -q services/api/tests/integration/test_learning_loop.py -k migration` → PostgreSQL 约束测试全部通过；`uv run pytest -q services/api/tests/integration/test_migrations.py` → 既有迁移测试不回归。

### Step 3：实现有据题目生成和批次恢复

1. 在 `questions.py` 定义 Provider 输出的内部结构：题干、题型、选项、正确答案、解释、难度和证据引用。解析失败、选项重复、答案越界、Evidence 缺失或引用不属于本次授权来源时拒绝该题。
2. 只向 Provider 发送本次课程范围内的证据片段，并明确把资料正文和 metadata 当作不可信数据，不能执行正文中的指令。复用现有 Provider timeout、错误码和 Evidence prompt 的安全边界。
3. 实现题目内容哈希和来源 Revision 快照。每道题的解释必须能回到一个或多个当前有效 chunk；不能仅保存模型生成的“看起来合理”的解释。
4. 用持久化生成批次、item、attempt 和 event 记录请求、阶段、题目结果、重试和失败原因。在 `runner.py` 中实现单批次有界 claim、lease、heartbeat、retry 和 terminal transition，采用现有 note batch 的模式；API 只负责创建/读取批次，不在请求生命周期内无界等待 Provider，也不要引入新的外部队列。
5. 暴露 `practice-batches` 路由和事件/轮询响应。Provider 未配置、活动索引不存在或资料不足时，在创建阶段 fail closed，并向前端返回可操作错误。

**收口条件**：

- [ ] 正常批次最多生成 10 道题，题目数量不足时返回部分成功及明确原因，不伪造题目。
- [ ] 不合法题目不会进入可练习状态。
- [ ] 同一个 Idempotency-Key 重放得到同一个批次，不重复消费 Provider。
- [ ] API 重启或 runner 重启后，未完成批次可恢复、重试或明确终止。
- [ ] 删除/替换来源后，相关题目变为 stale 或被查询层过滤，不能开始练习。

**验证**：`uv run pytest -q services/api/tests/unit/learning/test_questions.py` → 覆盖结构校验、Citation/Evidence 校验、Provider timeout、无索引、幂等和来源失效；`uv run pytest -q services/api/tests/integration/test_learning_loop.py -k batch` → 批次状态、重启恢复和 owner scope 全部通过。

### Step 4：实现练习会话、判分、掌握度和复习调度

1. 在 `scoring.py` 实现只针对 `single_choice` 和 `true_false` 的纯函数评分。提交答案前验证题目属于会话、题目状态有效、会话属于当前 Principal，重复提交必须幂等。
2. 在 `mastery.py` 实现掌握度更新纯函数，输入为旧状态和本次 attempt，输出新等级及解释字段。规则要能说明“为什么升/降级”，并把查看提示、答案错误和已完成复测分开记录。
3. 在 `scheduling.py` 实现简单的离散复习间隔。第一版可以使用 0/1/3/7 天等固定间隔；错误或低掌握度回到当天/次日，连续正确才推进。所有日期计算使用现有 Clock/UTC 习惯，不读取浏览器本地时间作为事实。
4. 在 `service.py` 实现 Principal scoped 的会话创建、当前题目、提交 attempt、掌握度更新和 review queue。提交 attempt、更新掌握度、写入下一次复习时间必须在一个数据库事务中完成。
5. 实现 `learning-summary`，返回章节/概念掌握汇总、总题数、正确率、待复习数量和下一步 action；不得把 Provider 自由文本直接当统计字段。

**收口条件**：

- [ ] 作答结果可重复计算；同一幂等请求不会产生第二条 attempt 或重复推进复习时间。
- [ ] 跨用户、跨课程、stale 题目和已删除来源都被拒绝。
- [ ] 掌握度和复习日期变化都有可测试、可解释的规则。
- [ ] `review-queue` 只返回来源有效且到期的学习单元。
- [ ] 当前会话完成后可以直接返回下一项复习动作。

**验证**：`uv run pytest -q services/api/tests/unit/learning/test_scoring.py services/api/tests/unit/learning/test_mastery.py services/api/tests/unit/learning/test_scheduling.py` → 全部通过；`uv run pytest -q services/api/tests/integration/test_learning_loop.py -k attempt` → 事务、幂等、权限、来源失效和排序测试全部通过。

### Step 5：接入 OpenAPI 和 Web 学习工作台

1. 在 `learning_loop.py` API schema 和 router 中发布稳定响应，所有错误沿用现有 Problem Details，不在 Web 端自行推断后端状态。
2. 运行 `npm run generate:api`，再在 `apps/web/src/api/types.ts` 和 `client.ts` 添加类型和方法；不要手工改生成 schema。
3. 在 `apps/web/src/features/learning/LearningPage.tsx` 展示：学习单元选择、生成题目、批次状态、开始练习和待复习列表。Provider 不可用时保留已有题目练习能力，但禁用新题目生成并显示明确原因。
4. 在 `PracticeSession.tsx` 实现固定尺寸的题目区域、选项、提交、结果、Evidence 原文入口和继续按钮。提交后不可重复改变同一题答案；刷新页面能恢复会话状态。
5. 在 `LearningSummary.tsx` 展示本次正确率、薄弱学习单元、来源依据和下一步复习动作。不要把结果页面做成只有百分比的仪表盘。
6. 在导航中新增“练习”入口；不修改现有资料、问答、笔记和概念地图的行为。

**收口条件**：

- [ ] 新用户能从课程资料页进入练习，并在不离开页面的情况下完成一组题。
- [ ] 题目生成中、失败、部分成功、题目失效、无 Provider 和来源不可用都有可见状态。
- [ ] 每个答案反馈都能打开对应来源；来源失效时显示不可用而不是旧内容。
- [ ] 移动端和桌面端无题干、选项、提交按钮或结果区域重叠。
- [ ] 现有四个学习入口的测试和行为不回归。

**验证**：`npm test --workspace @study-agent/web -- --run` → 新增学习组件测试和已有测试全部通过；`npm run typecheck && npm run lint && npm run build` → 全部 exit 0。

### Step 6：端到端验收和学习证据

1. 在 `tests/e2e/learning.spec.ts` 使用 mock API 覆盖一条完整路径：选择单元、生成批次、开始会话、答对/答错、查看来源、完成会话、看到待复习动作。
2. 增加失败路径：Provider 不可用、批次部分成功、重复提交、题目 stale、课程无有效来源、越权访问。
3. 在 `docs/learning-loop.md` 记录 V1 限制和操作员验证方法，明确 `test-double`、no-provider 和 live-provider 的边界；不得把 mock 结果称为真实模型质量。
4. 运行全量验证，检查 generated contracts、Alembic parity、coverage 和 diff scope。

**收口条件**：

- [ ] Playwright 能完成一条完整学习闭环，并验证用户看到了下一步复习行动。
- [ ] 失败路径没有产生脏题目、重复 attempt、跨用户数据或无来源解释。
- [ ] OpenAPI、Python contract、Web generated schema 一致。
- [ ] 只修改本计划范围内文件，`git diff --check` 无问题。
- [ ] `make check` 通过；如果 PostgreSQL、Docker 或真实 Provider 阻塞，必须记录退出码和未验证项，不得宣称计划完成。

**验证**：`npm run test:e2e -- --grep "learning loop"` → 完整路径和失败路径全部通过；随后执行 `make check`。

## 测试计划

### 契约和纯函数

- `packages/contracts/python/tests/test_learning_loop.py`：枚举、边界、禁止未知字段、时间/数量/分数限制。
- `services/api/tests/unit/learning/test_concepts.py`：canonical key、层级、来源 Revision、owner scope。
- `test_questions.py`：Provider 结构、选项答案、Evidence、内容哈希、stale 判断。
- `test_scoring.py`：选择题/判断题全分支、重复提交输入、无效答案。
- `test_mastery.py`：等级上升、下降、钳制、提示后的上限和解释字段。
- `test_scheduling.py`：UTC 日期、当天/次日/3 天/7 天间隔、错误回退和边界日期。

### API、数据库和浏览器

- `services/api/tests/integration/test_learning_loop.py`：迁移、约束、批次状态、恢复、权限、事务幂等、来源删除/Revision 切换、review queue 排序。
- `services/api/tests/contract/test_openapi.py`：新增路径、枚举和响应 schema 出现在 OpenAPI，生成输出无漂移。
- `apps/web/src/features/learning/*.test.tsx`：加载、空态、生成中、失败、作答、来源失效、移动布局语义。
- `tests/e2e/learning.spec.ts`：完整用户路径和所有关键失败路径。

测试数据必须使用现有公开自制 fixture 或新增的脱敏 fixture；禁止复制私有课程内容、Provider credential、对象 key 或完整原始响应。

## 完成标准

- [x] 用户能够从一个已就绪课程进入练习，完成不超过 10 道有据题目，并看到每题的来源依据。
- [x] 每次作答都保存一次、可幂等重放，并更新对应学习单元的掌握度。
- [x] 用户完成会话后能看到至少一个明确的下一步复习动作；到期后 review queue 能再次返回该单元。
- [x] 题目永远不会引用未授权、已删除或非活动 Revision 的来源。
- [x] Provider 不可用时不会伪造新题目，已有有效题目仍可练习。
- [x] 课程、用户、题目、会话、attempt、掌握度和来源绑定均通过服务器端 scope 校验。
- [x] `make check` 通过；唯一跳过项为需要外部 Provider 凭据的 live test。
- [x] `git status --short` 只显示本计划允许的文件，且 `git diff --check` 通过。
- [x] `plans/README.md` 中本计划状态已更新。

## 停止条件

出现以下任一情况时停止并报告，不要自行换方案：

- 当前 Alembic head、模型导入、Provider 协议或 Note batch runner 与“当前状态”不一致。
- 学习单元无法获得稳定的课程级 ID，导致掌握度只能绑定临时图谱节点。
- 题目生成必须绕过现有 Evidence/Citation 授权，或需要把未经校验的 Provider 响应存入数据库。
- 现有 runner 无法安全承载 Provider 题目生成，且实现者需要新增外部队列、常驻进程或生产部署改动。
- 为了让 V1 可用，必须修改现有问答拒答、引用验证、资料删除或 Revision 激活语义。
- 任一数据库事务无法同时保证 attempt、mastery 和 review schedule 的一致性。
- 需要加入主观题评分、图谱全量重做、云扩容或登录/权限重构才能继续。
- 任一关键验证命令连续两次失败，或当前工作树出现计划范围外修改。

## 维护说明

- 后续替换图谱算法时，必须继续使用 `LearningUnit` 的稳定 `canonical_key`，不能让图谱节点 ID 成为掌握度主键。
- 后续支持 PPTX、DOCX 或 OCR 时，必须为学习单元来源增加新的 locator/质量状态，但不能绕过当前资料审核和活动 Revision 约束。
- 后续把学习生成迁移到云端队列时，要保持批次幂等、租约、重试和来源快照语义；不要把控制面状态改回仅存在浏览器内存。
- 审查题目生成时重点检查：题目是否真的能由 Evidence 支持、答案是否唯一、解释是否引用正确、旧 Revision 是否失效、Provider 原文是否被持久化。
- 主观题、复杂 SRS、考试日历、跨课程推荐、题目分享和图谱语义本体都应单独立项，不能作为本计划的隐性扩展。
