# 协议集成目录

当前提供 `mcp_server.py` 与 `mcp_client.py`，使用已锁定的官方Python SDK `mcp==2.3.0`。

## 只读工单 MCP Server

- 仅注册 `get_ticket_details(ticket_id: UUID)`，复用已有工单工具和公开字段模型。
- 本地使用stdio，由Client启动独立Python子进程；stdout用于协议，日志写stderr。
- 可信后端为子进程提供 `TICKETPILOT_MCP_TOKEN`。Server在每次调用时验证JWT，再查询当前账号启用状态与角色。
- 工具参数不提供角色、令牌、审核或关闭能力；这里的本地令牌传递方案不等于远程MCP OAuth。
- 数据库沿用服务器配置，代码导入不自动查询工单或启动Server。

## 当前验收边界

`tests/integration/mcp/test_ticket_server.py` 使用真实MCP Client验证工具发现、结构化结果、参数错误、身份拒绝、错误过滤及未知写工具拒绝。内存协议测试替换业务Session工厂，stdio测试使用独立子进程、临时业务数据库和测试令牌。

正式Client适配已经接入认证聊天的工单查询：Router明确启用MCP模式，Dispatcher验证UUID并为当前用户签发访问令牌，通过同步桥调用异步Client，再由独立Server重新检查当前身份并查询数据库。MCP失败返回503，不回退为本地直接查询。旧独立示例/单元测试可显式使用原本的本地模式，HTTP请求不能填写该内部开关。

Client限制固定模块与启动目录，设置整体15秒/读取5秒等待期限，验证工具存在、is_error、结构化数据和目标ID。异步上下文负责资源清理；已存在事件循环的调用方应直接await异步入口，不能使用同步桥。

`tests/unit/integrations/test_mcp_client.py`验证错误、超时、取消和清理；`tests/integration/api/test_agent_mcp_api.py`使用真实JWT、生产Client和stdio子进程验证整个HTTP链路，并禁止父进程本地查询。对话模型仍为替身，实际Ollama工具选择与用户界面验收另行确认。

运行Server命令为 `uv run python -m ticketpilot.integrations.mcp_server`；它等待Client发送协议消息，手动运行时不会显示网页。正式聊天由Client自动启动子进程并提供令牌，无需手动保持Server终端，令牌不写进源码或命令行参数。
