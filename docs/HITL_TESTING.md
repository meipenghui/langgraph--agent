# 退款 HITL 后端闭环测试指南

本文验证退款金额超过 500 元时的真实人工审批（Human-in-the-loop）流程：

1. `/chat` 触发退意图并命中退款子图。
2. 退款金额超过 500 元时调用 `interrupt()`，LangGraph 图挂起。
3. `/pending_approvals` 查询等待审批的会话。
4. `/approve` 使用 `Command(resume=...)` 恢复图执行。
5. 审批通过后调用 MCP `apply_refund`；审批拒绝后更新为 rejected。

## 1. 启动服务

在项目根目录执行（建议先关闭旧的 Uvicorn 进程）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

确认服务可用：

```powershell
Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/health'
```

预期返回：

```json
{status:ok}
```

## 2. 触发高额退款中断

`user_003` 的订单号为 `TB202611120003`，金额为 `699.00` 元，超过 500 元阈值。

PowerShell 请求体请先转成 UTF-8 字节，避免中文在真实终端中被错误编码：

```powershell
$payload = @{
    message  = '帮我退款 user_003'
    user_id  = 'user_003'
} | ConvertTo-Json -Compress
$bytes = [Text.Encoding]::UTF8.GetBytes($payload)

$chat = Invoke-RestMethod `
    -Method Post `
    -Uri 'http://127.0.0.1:8000/chat' `
    -ContentType 'application/json; charset=utf-8' `
    -Body $bytes

$chat | ConvertTo-Json -Depth 10
$threadId = $chat.thread_id
```

预期响应中的关键字段：

```json
{
  thread_id: thread_...,
  user_id: user_003,
  intent: refund,
  order_id: TB202611120003,
  order_amount: 699.0,
  refund_status: pending_human_review,
  need_human_intervention: true
}
```

服务端日志应出现：

```text
[refund] pending human approval for order_id=TB202611120003 amount=699.0
[refund] interrupting graph for approval order_id=TB202611120003
```

这表示图已经通过 `interrupt()` 挂起，而不是仅修改了一个普通状态字段。

## 3. 查询等待审批列表

```powershell
$pending = Invoke-RestMethod `
    -Method Get `
    -Uri 'http://127.0.0.1:8000/pending_approvals'

$pending | ConvertTo-Json -Depth 10
```

应能在列表中看到刚返回的 `$threadId`，并且 `refund_status` 为
`pending_human_review`。

## 4. 审批通过并验证恢复

```powershell
$approvalBody = @{
    user_id   = 'user_003'
    thread_id = $threadId
    decision  = 'approve'
} | ConvertTo-Json -Compress
$approvalBytes = [Text.Encoding]::UTF8.GetBytes($approvalBody)

$approval = Invoke-RestMethod `
    -Method Post `
    -Uri 'http://127.0.0.1:8000/approve' `
    -ContentType 'application/json; charset=utf-8' `
    -Body $approvalBytes

$approval | ConvertTo-Json -Depth 10
```

预期：

- `decision` 为 `approve`。
- `refund_status` 为 `completed`。
- `need_human_intervention` 为 `false`。
- `reply` 是自然语言，例如“订单 TB202611120003 退款申请已提交成功……”。
- 服务端日志出现 `[refund] calling MCP apply_refund ...`。

再次查询待审批列表，该 `thread_id` 应已消失：

```powershell
$pendingAfterApprove = Invoke-RestMethod `
    -Method Get `
    -Uri 'http://127.0.0.1:8000/pending_approvals'
$pendingAfterApprove | ConvertTo-Json -Depth 10
```

## 5. 审批拒绝流程

拒绝流程必须使用一个新的 `thread_id`，重新触发一次中断：

```powershell
$rejectChatBody = @{
    message = '帮我退订 user_003'
    user_id = 'user_003'
} | ConvertTo-Json -Compress
$rejectChatBytes = [Text.Encoding]::UTF8.GetBytes($rejectChatBody)

$rejectChat = Invoke-RestMethod `
    -Method Post `
    -Uri 'http://127.0.0.1:8000/chat' `
    -ContentType 'application/json; charset=utf-8' `
    -Body $rejectChatBytes

$rejectThreadId = $rejectChat.thread_id

$rejectBody = @{
    user_id   = 'user_003'
    thread_id = $rejectThreadId
    decision  = 'reject'
} | ConvertTo-Json -Compress
$rejectBytes = [Text.Encoding]::UTF8.GetBytes($rejectBody)

$rejectResult = Invoke-RestMethod `
    -Method Post `
    -Uri 'http://127.0.0.1:8000/approve' `
    -ContentType 'application/json; charset=utf-8' `
    -Body $rejectBytes

$rejectResult | ConvertTo-Json -Depth 10
```

预期：

- `refund_status` 为 `rejected`。
- `need_human_intervention` 为 `false`。
- 不会调用 MCP `apply_refund`。
- 服务端日志出现模拟用户通知：
  `[refund] rejected and user notified user_id=user_003`。

## 6. 验证 500 元以下自动退款

`user_001` 的订单金额为 `299.00` 元，应直接自动退款，不进入人工审批：

```powershell
$autoBody = @{
    message = '帮我退款 user_001'
    user_id = 'user_001'
} | ConvertTo-Json -Compress
$autoBytes = [Text.Encoding]::UTF8.GetBytes($autoBody)

$autoResult = Invoke-RestMethod `
    -Method Post `
    -Uri 'http://127.0.0.1:8000/chat' `
    -ContentType 'application/json; charset=utf-8' `
    -Body $autoBytes

$autoResult | ConvertTo-Json -Depth 10
```

预期：

- `intent` 为 `refund`。
- `refund_status` 为 `completed`。
- `need_human_intervention` 为 `false`。
- `order_id` 为 `TB202611120001`。
- 服务端日志出现 MCP `apply_refund` 调用，且待审批列表中没有该线程。

## 7. 异常校验

以下命令需在上面的高额退款审批通过之后执行。`$threadId` 对应已完成的
`user_003` 线程。

```powershell
# 1. 用户与挂起线程不匹配：403
$wrongUserBody = @{
    user_id   = 'user_001'
    thread_id = $threadId
    decision  = 'approve'
} | ConvertTo-Json -Compress
$wrongUserBytes = [Text.Encoding]::UTF8.GetBytes($wrongUserBody)

try {
    Invoke-RestMethod `
        -Method Post `
        -Uri 'http://127.0.0.1:8000/approve' `
        -ContentType 'application/json; charset=utf-8' `
        -Body $wrongUserBytes
} catch {
    wrong_user_status=$($_.Exception.Response.StatusCode.value__)
}

# 2. 已完成的线程再次审批：409
$duplicateBody = @{
    user_id   = 'user_003'
    thread_id = $threadId
    decision  = 'approve'
} | ConvertTo-Json -Compress
$duplicateBytes = [Text.Encoding]::UTF8.GetBytes($duplicateBody)

try {
    Invoke-RestMethod `
        -Method Post `
        -Uri 'http://127.0.0.1:8000/approve' `
        -ContentType 'application/json; charset=utf-8' `
        -Body $duplicateBytes
} catch {
    duplicate_status=$($_.Exception.Response.StatusCode.value__)
}

# 3. 不存在的线程：404
$missingBody = @{
    user_id   = 'user_003'
    thread_id = 'thread-does-not-exist'
    decision  = 'approve'
} | ConvertTo-Json -Compress
$missingBytes = [Text.Encoding]::UTF8.GetBytes($missingBody)

try {
    Invoke-RestMethod `
        -Method Post `
        -Uri 'http://127.0.0.1:8000/approve' `
        -ContentType 'application/json; charset=utf-8' `
        -Body $missingBytes
} catch {
    missing_status=$($_.Exception.Response.StatusCode.value__)
}
```

预期依次输出 `wrong_user_status=403`、`duplicate_status=409`、
`missing_status=404`。

`/approve` 会依次校验线程存在性、`user_id` 是否一致，以及线程是否仍处于中断状态。

## 8. 自动化回归测试

无需真实调用 DeepSeek 和 MCP 子进程，可用 mock MCP 跑完整 API 闭环：

```powershell
.\.venv\Scripts\python.exe -m tests.test_hitl_flow
```

成功输出末尾为：

```text
HITL_FLOW_OK
```

该测试覆盖高额退款中断、审批通过、审批拒绝和低额自动退款。

## 9. 当前存储限制

当前使用 `MemorySaver`：

- 挂起状态保存在当前 Python 进程内。
- 重启 Uvicorn 后，所有挂起状态会丢失。
- 多 worker 部署时，各 worker 不共享挂起状态。

生产环境应替换为 PostgreSQL Checkpointer（如 `AsyncPostgresSaver`）或 Redis
Checkpointer，并让 `/pending_approvals` 从持久化存储读取，而不是只依赖进程内字典。
