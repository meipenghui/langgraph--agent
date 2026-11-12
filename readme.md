这里为你整理了一份结构化的 Markdown 设计文档，你可以直接复制并保存为 `ecommerce-agent-design.md` 文件，用于团队分享或作为项目 README。

***

# 🛒 电商客服 Agent 系统架构设计文档

## 1. 整体技术栈与架构定位
*   **技术选型**：FastAPI + LangGraph + PostgreSQL + Redis + RAG + MCP
*   **系统定位**：生产级高可用微服务化 Agent 调度中心，接入淘宝开放平台 API，支撑四大核心业务（售前、订单、使用、退款）。
*   **核心理念**：采用 **Workflow (LangGraph) + Agent (LLM 自主决策) + Rule (硬编码拦截)** 的混合架构，确保系统的灵活性与业务确定性。

---

## 2. 存储与基础设施设计

### 2.1 存储介质职责划分
*   **PostgreSQL (PG)**：
    *   **持久化业务数据**：用户长短期记忆数据（结合 `pgvector` 存储向量）、Agent 状态快照（LangGraph Checkpointer）、人工工单记录、知识库元数据。
*   **Redis**：
    *   **高速缓存与并发控制**：高频商品信息缓存、热点 FAQ 缓存。
    *   **分布式锁**：防止同一用户并发发起多次退款（防重放）。
    *   **限流与熔断**：API 调用频率限制、Token 消耗预警。

### 2.2 LangGraph 全局状态（State）设计
全局 `State` 必须在整个图中流转，建议包含以下核心字段：
*   `messages`: 聊天历史记录。
*   `user_id` / `order_id`: 核心实体标识。
*   `intent`: 当前识别的意图（售前 / 订单 / 使用 / 退款）。
*   `refund_status`: 退款流程流转状态。
*   `need_human_intervention`: 布尔值，触发人工介入（HITL）的标志位。

---

## 3. 核心业务编排设计（LangGraph 图结构）

采用 **“主图（路由） + 子图（业务模块）”** 的设计模式：

### 3.1 路由节点（Router Node）- 主图入口
*   **逻辑**：调用轻量级模型（如 Qwen-Turbo），结合 Redis 中的当前会话上下文，进行意图识别。
*   **流转**：通过条件边（Conditional Edges）将任务分发给对应的子图。

### 3.2 售前与使用质询（RAG 子图）
*   **流程**：`意图确认 -> 查询改写 (Query Rewrite) -> 混合检索 (RAG) -> 结果重排 (Reranker) -> LLM 生成回复`。
*   **降级策略**：若检索置信度低，触发兜底话术，并在 State 中标记请求转人工。

### 3.3 订单状态查询（MCP 工具子图）
*   **模式**：典型的 ReAct 循环。
*   **流程**：LLM 提取参数 \(\rightarrow\) 调用 MCP 工具（淘宝订单 API） \(\rightarrow\) 获取 JSON 结果 \(\rightarrow\) LLM 将 JSON 转换为自然语言回复。
*   **解耦优势**：复杂的淘宝 API 签名与鉴权封装在独立的 MCP Server 中，Agent 仅负责传参。

### 3.4 退款工作流（长链路 + HITL 子图）
*   **流程**：
    1.  收集退款原因与订单信息。
    2.  调用 MCP（查询淘宝退款状态）确认是否满足条件。
    3.  **规则拦截（Rule）**：如金额 > 500 元，强制中断。
    4.  满足条件：调用 MCP（同意退款 API）完成闭环。
    5.  触发拦截：图执行暂停（`interrupt`），状态存入 PG，调用 MCP 通知工具（企微/飞书）通知人工。
    6.  人工审核通过 \(\rightarrow\) Webhook 唤醒图 \(\rightarrow\) 继续调用退款 API。

---

## 4. MCP 服务端设计（工具集群）

设计独立的 MCP Server 来封装外部能力：
*   **鉴权模块**：统一处理淘宝 OAuth2.0 授权、Token 自动刷新。
*   **标准化工具暴露**：
    *   `get_order_status(user_id)`
    *   `apply_refund(order_id, reason)`
    *   `get_product_info(item_id)`
*   **安全隔离**：MCP Server 内部需二次校验权限（如校验 `order_id` 是否属于当前 `user_id`），防止模型越权操作。

---

## 5. 记忆与上下文工程（Memory & RAG）

*   **短期记忆**：利用 Redis 挂载最近 N 轮对话，或使用 LangGraph 自带的短时 Checkpointer。
*   **长期记忆**：PostgreSQL 记录用户画像与偏好（如“VIP 客户”、“偏好中文回复”）。
*   **知识库检索（RAG）**：
    *   商品说明书长文本进行合理分段（Chunking）。
    *   采用 **混合检索**：`BM25 (关键词匹配) + 向量 (语义匹配)`。
    *   重排（Rerank）后仅向模型注入 Top-3 片段，降低模型理解难度。

---

## 6. 可靠性设计与工程化保障（Guardrails）

1.  **并发控制（Redis 锁）**：防止同一用户短时间内重复触发退款申请。
2.  **Token 与成本熔断**：单次会话消耗 Token 超阈值，强制走向 `END` 节点并友好提示。
3.  **Saga 事务补偿**：若淘宝侧退款成功，但本地 PG 记录失败，需有后台定时任务重新拉取淘宝状态进行对账。
4.  **防注入攻击**：RAG 召回内容强制包裹在 `<context>` 标签中，通过硬编码规则过滤用户输入的恶意越权指令。
5.  **服务降级**：外部淘宝接口报错或超时时，Agent 停止自主重试（防死亡螺旋），直接降级为安抚话术并转人工。

---

## 7. 推荐的项目目录结构

```text
ecommerce-agent/
├── app/
│   ├── main.py                 # FastAPI 入口与路由挂载
│   ├── api/                    # API 接口 (Webhook, 聊天对话接口)
│   ├── core/                   # 核心配置 (Redis 连接池, PG 连接池, 日志)
│   ├── graph/                  # LangGraph 核心代码
│   │   ├── state.py            # 定义 AgentState (TypedDict)
│   │   ├── main_graph.py       # 主图编排与路由逻辑
│   │   ├── subgraphs/          # 四大业务子图
│   │   │   ├── presale.py      # 售前
│   │   │   ├── order.py        # 订单
│   │   │   ├── usage.py        # 使用质询
│   │   │   └── refund.py       # 退款
│   │   └── nodes/              # 各节点具体实现 (LLM调用, 规则拦截)
│   ├── mcp_client/             # 连接 MCP Server 的客户端代码
│   └── rag/                    # 知识库构建与检索逻辑 (Embedding, Rerank)
├── mcp_server/                 # 独立部署的 MCP 服务 (淘宝 API 封装)
│   ├── server.py               # MCP 服务主入口
│   └── taobao_api.py           # 淘宝 API 签名与请求逻辑
├── docker-compose.yml          # 一键拉起 PG, Redis, FastAPI, MCP 环境
└── README.md
```

**🚀 落地建议**：
第一阶段先实现 FastAPI + LangGraph 路由 + 独立的 MCP 淘宝订单查询服务，跑通只读的 ReAct 循环；第二阶段叠加 RAG 知识库；第三阶段再攻克高风险的退款 HITL 人工审批流。

## 8. HITL 退款审批后端

退款金额超过 500 元时，`refund` 子图会通过 LangGraph `interrupt()` 挂起，主图使用
`MemorySaver` 保存中断状态。后端提供以下接口：

- `POST /chat`：触发对话；响应包含本次会话的 `thread_id`。
- `GET /pending_approvals`：查询当前等待人工审批的会话。
- `POST /approve`：使用 `user_id`、`thread_id`、`decision` 审批；通过后调用
  MCP `apply_refund`，拒绝后更新为 `rejected`。

完整启动、触发中断、审批通过/拒绝及状态恢复验证步骤见
`docs/HITL_TESTING.md`。
