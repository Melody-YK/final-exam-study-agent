# 对话与学习记忆

课程问答和单题问 AI 共用服务端持久化、上下文预算、课程权限与脱敏日志，但使用不同的教学策略。前端不负责拼接可信历史，也不能通过请求覆盖服务端保存的上下文。

## 会话作用域

| 类型 | 唯一作用域 | 主要用途 |
|---|---|---|
| `course_qa` | 用户 + 课程 + 会话 | 基于当前有效课程资料连续提问、追问和查看引用 |
| `practice_tutor` | 用户 + 课程 + 练习场次 + 题目 | 针对一道题请求提示、澄清、例子、检查和解析 |

课程问答会话由用户创建或在第一次提问时创建。单题会话按练习场次和题目复用，同一道题重复打开不会创建第二个会话。所有读取和写入都会重新校验用户、课程、题目与来源状态。

## 上下文规则

- 当前消息始终作为独立结构化输入，优先于历史消息和上一轮策略。
- 课程问答只选取引用依赖仍然有效的完整历史问答，最近上下文上限为 `6000` 字符；更早内容保存为不超过 `2000` 字符的主题摘要。
- 单题辅导最多向模型提供最近 `12` 个完整回合，估算预算为 `3000` token；接口最多返回最近 `200` 条消息，并用 `has_earlier_messages` 表示更早记录。
- Provider 失败时不保存半个回合。单题请求使用稳定 `turn_id`，同一回合重试不会重复写入消息。
- 摘要只记录较早对话的主题，不保存模型隐藏推理、Provider 原始响应或 chain-of-thought。

## 课程问答

查询规划器识别 `new_question`、`follow_up`、`comparison`、`summary` 和 `clarification`，并将含指代或省略的追问改写为可独立检索的问题。模型规划不可用、超时或输出不符合 JSON 契约时，系统使用确定性中文回退规则。

一次提问最多执行 3 次检索：先检索独立问题；证据不足时，在同一个补救阶段尝试最多两个互补查询。一旦证据充分就停止，不会递归启动新的 Agent。多轮结果按 chunk 去重并稳定融合，最多保留 8 条候选，引用仍必须通过课程权限、活动 revision、内容哈希和 `CitationValidator` 校验。

`QueryResponse` 暴露以下可解释字段：

- `query_intent`：当前问题类型。
- `standalone_question`：追问改写后的独立问题。
- `retrieval_rounds`：每轮查询、索引状态和候选数量。
- `retrieval_diagnostic`：`initial_sufficient`、`repair_succeeded`、`index_unavailable`、`no_candidates` 或 `low_relevance`。

历史来源删除或活动索引变化后，旧回答不会继续作为可靠回答上下文。

如果三轮检索仍没有足够证据，系统会先判断问题是否依赖本课程的特定事实。涉及课件、讲义、教师要求、考试范围、评分、页码或原文时继续拒答，并说明需要课程来源；一般概念、比较、举例和学习方法问题则可以调用通识回答模型。此类回答仍然保存到会话中，但 `answer_basis` 为 `ai_general_knowledge`，不生成课程引用，也不会写入课程资料证据依赖。页面会明确标注“AI 通识回答，未找到可验证的课程来源”。

## 单题问 AI

当前意图包括 `hint`、`clarify`、`example`、`answer_check`、`solution`、`reflection`、`source` 和 `open_question`。明确要求提示或只发送答案尝试时仍使用 `hint`；无法归入前七类但包含开放式问题的表达使用 `open_question`，例如“这个知识点有什么用？”或“为什么会这样？”。作答前使用 `hint` 模式，不能暴露正确选项、判断值或最终计算结果；概念解释、方法提示和平行例子仍可提供。提交答案后切换为 `review` 模式，可以比较作答并给出完整解析。

题目状态、已提交答案、当前意图、当前消息、历史回合、来源和相关学习记忆分别传给 Provider，不依赖一段混合提示词猜测当前状态。

## 学习记忆

学习记忆按用户和课程隔离，类型为：

- `preference`：用户明确表达的长期解释偏好。
- `confirmed_misconception`：用户明确确认的误解。
- `learning_goal`：用户明确说明的学习目标。

用户可以在课程问答页新增、编辑和删除记忆。系统只从“我喜欢……”“以后请……”“我的目标是……”“我容易把……”等明确的用户陈述中提取候选，不把普通请求、助手推断或模型结论直接保存为长期记忆。掌握度与作答记录继续使用独立的确定性结构化数据。

## HTTP API

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/v1/courses/{course_id}/conversations` | 创建课程问答会话 |
| `GET` | `/api/v1/courses/{course_id}/conversations` | 列出课程问答会话 |
| `GET` | `/api/v1/conversations/{conversation_id}/queries` | 读取会话问答记录 |
| `POST` | `/api/v1/courses/{course_id}/queries` | 提交课程问题 |
| `GET` | `/api/v1/queries/{query_id}/events` | 读取包含 `retrieval.planned` 的 SSE 进度 |
| `GET` | `/api/v1/practice-sessions/{session_id}/questions/{question_id}/tutor` | 恢复单题辅导记录 |
| `POST` | `/api/v1/practice-sessions/{session_id}/questions/{question_id}/tutor` | 使用 `message` 和 `turn_id` 发送单题消息 |
| `GET` | `/api/v1/courses/{course_id}/learner-memories` | 列出课程学习记忆 |
| `POST` | `/api/v1/courses/{course_id}/learner-memories` | 手动新增学习记忆 |
| `PUT` | `/api/v1/learner-memories/{memory_id}` | 修改学习记忆 |
| `DELETE` | `/api/v1/learner-memories/{memory_id}` | 删除学习记忆 |

OpenAPI 源文件位于 `packages/contracts/openapi/openapi.json`，Web 类型由该契约生成，不应手工维护重复接口形状。

## 运行与隐私

升级已有本地数据库后再启动新 API：

```bash
uv run python -c 'import asyncio; from study_agent.infrastructure.db.migrations import upgrade_database; asyncio.run(upgrade_database("postgresql+asyncpg://study_agent@127.0.0.1:54329/study_agent"))'
make api
make web
```

当前对话迁移顺序为 `20260804_0015 -> 0016 -> 0017 -> 0018`。对话日志只允许记录作用域 ID、意图、诊断、轮次、数量、状态和耗时，不记录课程正文、问题正文、回答正文、正确答案或 Provider 原始响应。
