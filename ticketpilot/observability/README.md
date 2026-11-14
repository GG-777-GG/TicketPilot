# 请求关联与追踪

当前已实现请求编号、HTTP SERVER Span、Agent/模型/工具/MCP Client子Span及审计关联；MCP Server内部Span与跨进程上下文尚未接入。

| 文件 | 责任 |
|---|---|
| context.py | 通过ContextVar读取请求编号；with作用域进入设置、退出恢复 |
| middleware.py | HTTP入口生成UUID及SERVER Span，响应回传X-Request-ID/X-Trace-ID；提供未捕获500的固定响应 |
| tracing.py | 创建每应用Provider（可注入Exporter），读取有效当前Span的32位Trace编号 |
| spans.py | 当前应用Tracer作用域、固定步骤Span、工具名白名单及异常脱敏 |

## 调用过程

HTTP请求 → request_context → 认证/Router/Service → 审计Repository → HTTP响应 → 恢复原上下文。

- 每次HTTP请求由服务器生成新UUID，不采用客户端自报的X-Request-ID。
- 同步路由由AnyIO工作线程取得调用方上下文，异步路由直接读取当前上下文。
- 请求结束、异常或取消后，ContextVar通过reset(token)恢复原值；嵌套作用域不会清空外层编号。
- 响应头回传X-Request-ID，正文与业务状态保持原逻辑。未捕获异常在外层处理时从request.state取原编号，固定返回500正文，不把原异常文本交给客户端。
- 中间件只改响应开始消息中的头，不重组或缓存响应正文分块；响应已开始后的错误不能再改成一份新500响应。
- 审计Repository在event.request_id为空时补当前编号；可信后端显式提供的编号保留。独立失败审计的新Session仍处于同一请求上下文。
- 普通脚本没有HTTP上下文时request_id可空；旧事件不补录。
- 编号不参与认证、角色或业务幂等判断。跨请求业务仍以operation_id关联。
- 非HTTP消息原样转发；独立MCP子进程不会因本模块自动得到请求上下文。

## OpenTelemetry生命周期与边界

API/SDK固定1.45.2。应用lifespan启动时构建Provider并放在app.state，所有请求复用；关闭时force_flush（5000ms）再shutdown，即使刷新抛异常也尝试关闭及清理state。导入模块不创建Provider，不设置进程全局Provider；create_app可注入测试Exporter。

默认Console写stderr，SimpleSpanProcessor同步导出，ALWAYS_ON便于本地演示，暂非生产采样/队列方案。SDK有效Trace编号与导出成功是不同保证；未运行生命周期的独立ASGI测试允许仅有请求编号。

每个HTTP请求建立新根SERVER Span，当前不提取客户端traceparent，也不跨进程传递。属性仅包括方法、路由模板、状态码和ticketpilot.request_id；不记录原始URL、查询串、请求头、正文、原始异常或堆栈。普通4xx不自动标服务器Span为ERROR；5xx、未捕获异常、取消使用固定错误类别。关闭自动exception recording避免异常正文进入Span。

中间件用tracer_context向当前执行上下文提供同一Provider的业务Tracer，AnyIO同步线程及asyncio.run中的MCP Client沿用上下文。HTTP聊天入口包装agent.run，Agent循环只在实际model.decide时建立同名Span，每次Dispatcher调用包装tool.execute；知识模式后端强制检索不伪造模型步骤。Client公共异步适配器包装整个连接、发现、调用和结果校验，原私有传输实现保持业务规则。

```text
POST /v1/agent/chat             SERVER
└── agent.run                  INTERNAL
    ├── model.decide           INTERNAL
    ├── tool.execute           INTERNAL
    │   └── mcp.client         CLIENT
    └── model.decide           INTERNAL
```

同一请求共享trace_id，各步骤有不同span_id及parent_id。步骤抛错标固定operation_failed/cancelled并原样传播；步骤ERROR与HTTP 4xx的SERVER状态是不同边界，不互相替代。工具属性只允许三种既有工具名，其余unknown；原耗时打印的未知工具名同样脱敏。不记录参数、模型输入、结果或原异常事件。当前mcp.client仅表示父进程客户端整体耗时，不能据此声称Server或数据库内部Span已接入。可选证据校验器等步骤尚未单独细分Span。

上下文退出后外层500处理器从request.state取得原trace_id。响应已开始后的异常保持原已发送状态，同时Span可标记失败，不重新发送500。审计Repository在event.trace_id为空时读取当前有效Span，显式可信编号保留，无Span时仍为空；不是生成UUID来替代。

## Swagger验收

1. 在现有Swagger执行一次创建审批申请，记录响应头中的x-request-id。
2. 用管理身份按新approval_id查询审计记录，事件的request_id应等于创建请求的响应头，而不是本次查询的响应头。
3. 再做一次会产生审计的独立操作，应得到新的响应编号，相应事件保存新编号。
4. 当前HTTP响应还会带x-trace-id。新事件的trace_id应等于产生它的操作响应头，并可在后端Console Span输出中找到同一个编号（Console通常带0x前缀）。查询操作本身有另一条Trace。
5. 在聊天auto模式查询实际工单，Console应出现agent.run、model.decide、tool.execute、mcp.client；对应Span共享trace_id，parent_id形成上述关系。Span结束时即导出，父Span可能最后输出，不按树形顺序打印。

本批无须新迁移。若后端没有热更新，重启原后端进程后刷新Swagger。

## 测试

`tests/unit/observability/test_request_context.py`14项：默认/指定UUID、嵌套、异常、参数错误、异步重叠、取消、AnyIO工作线程、非HTTP透传、正文分块和中间件异常恢复。

`tests/integration/api/test_request_correlation_api.py`16项：真实HTTP同步/异步路由、服务端生成与拒绝自报编号、处理过及未捕获错误、真实并发线程池、成功/拒绝/存储失败审计与响应头关联、显式可信编号保留。测试库与测试路由均隔离。

新增`tests/unit/observability/test_tracing.py`7项与`tests/integration/api/test_http_tracing_api.py`13项：真实SDK及内存导出器验证Provider/资源/采样、上下文父子恢复、stderr、生命周期清理、同步/异步HTTP Span、错误分类、并发、脱敏、不接收远端父上下文、取消和响应开始后异常。

子Span新增`test_operation_spans.py`14项；既有`test_agent_mcp_api.py`新增5项、`test_agent_approval_api.py`新增3项。验证无配置透传、Tracer恢复、父子关系、错误/取消、未知工具脱敏、真实stdio MCP六Span链路、非法参数不启动Client、关闭申请审计关联及后端强制知识检索。真实SDK/HTTP/stdio，模型及向量服务为替身。
