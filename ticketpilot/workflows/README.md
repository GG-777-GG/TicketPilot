# 工作流目录说明

`workflows` 负责业务步骤的顺序、暂停、检查点和恢复。`agent` 保留模型适配、工具调用循环、工具分发和证据校验；`approvals` 保留审批记录、权限规则、事务和幂等执行。

## 正式关闭工单流程

```text
ticketpilot/workflows/
├── ticket_closure/
│   ├── state.py        # TicketClosureState 和本次运行的 Context
│   ├── nodes.py        # 查询申请、等待审批、调用正式执行服务
│   ├── graph.py        # 注册节点、连线和选择分支
│   ├── checkpoints.py  # 打开/关闭 SQLite 检查点连接
│   ├── service.py      # 面向 HTTP 的流程进度查询
│   └── operations.py   # 启动、恢复、重试及进程内互斥
└── examples/
    ├── intro.py        # 入门图、只读知识检索与状态观察
    ├── approval.py     # 独立的模拟审批、暂停和恢复
    └── idempotency.py  # 独立的幂等计数器实验
```

当前调用关系：

```text
approvals/router.py
    → workflows/ticket_closure/service.py
        → approvals/service.py              # 先检查当前申请可见范围
        → ticket_closure/checkpoints.py
            → ticket_closure/graph.py
                → ticket_closure/nodes.py
                    → approvals/service.py  # 读取审批
                    → approvals/execution.py # 权限、事务、幂等和关闭
```

`state.py` 由节点、图和调用方共同使用。Context 中的 Session 和当前用户在每次运行/恢复时重新提供，不保存到检查点。

`ticket_closure/operations.py` 负责start/resume/retry的权限与前置条件，重复start返回已有进度。它通过进程内Lock非阻塞串行化工作流写操作；当前本地部署必须使用单worker，不代表多进程或分布式锁。实际业务效果仍由审批Service的条件更新及幂等结果记录保护。

图的三个节点名称保持原样，以便继续读取已有检查点：

```text
START → read_approval
            ├─ pending  → wait_for_approval → 恢复后回到 read_approval
            ├─ approved → execute_ticket_closure → END
            └─ 其他状态 → END
```

图只编排步骤。实际关闭和执行结果由 `approvals/execution.py` 在同一业务事务中提交；节点失败后重试仍沿用原申请的 `operation_id`。

## 当前接入范围

- 已有：`GET /v1/approvals/{approval_id}/workflow`，检查权限后只读查询流程进度。
- 已有：正式图的等待、复查、执行、失败恢复，以及临时 SQLite 上的新进程恢复测试。
- 已有：`POST /v1/approvals/{approval_id}/workflow/run`，请求action为start/resume/retry；恢复/重试要求主管或管理员。
- 已有：Agent受控申请工具调用start，Context执行开关为False；原始用户明确句式与模型参数必须一致，成功后由后端直接返回申请编号。实际模型选工具待人工验收。
- 审批前端按11月20日面试版计划暂缓，管理流程用Swagger演示。
- 当前 `POST /v1/approvals/{approval_id}/execute` 仍直接调用执行 Service。
- 检查点文件默认在 `data/ticket_closure_checkpoints.sqlite`，本次目录整理不移动或改写运行数据。

响应模型仍在 `approvals/schemas.py`，与审批 HTTP 接口的公开数据保持在一起。

聊天仍使用 `agent/loop.py` 的既有工具调用循环，只有关闭申请工具连接这条独立业务图。申请工具没有审核或关闭权限；管理用户通过后续工作流HTTP请求提供有效身份并继续执行。

## 教学示例

新的模拟审批命令入口：

```powershell
uv run python -m ticketpilot.workflows.examples.approval --help
```

入门和幂等实验入口分别为 `ticketpilot.workflows.examples.intro`、`ticketpilot.workflows.examples.idempotency`。示例保留原有行为：入门图会使用配置的知识库服务，模拟审批和幂等实验会写独立演示文件。

对应测试移动到 `tests/integration/workflows/`；HTTP 测试仍放在 `tests/integration/api/`。

本次移除旧 `ticketpilot.agent.workflow_*`、`closure_*` 和 `idempotency_demo` 入口，调用方使用以上新路径。
