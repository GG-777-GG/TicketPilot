# TicketPilot 测试结构

测试采用“测试层级 + 业务模块”组织方式。

2026-11-14第二次远端CI实际结果：用户修复并推送c0c8b44，远端main一致；run43600842541已正常创建两job，Frontend checks测试/lint/build全部success。Backend checks在Install dependencies失败，Run backend tests skipped；日志明确No interpreter found for Python 3.12.16 in managed installations or search path并提示uv内置下载清单可能需更新。固定uv0.11.32的本机可下载3.12目录不含3.12.16；该观察为Windows元数据，不宣称完整Linux安装验收。提供由学习者修改CI为python-version 3.12，并在sync前显式uv python install 3.12，允许使用工具已支持的3.12补丁版本；不改本机解释器、Dockerfile或uv.lock。内存候选YAML结构与步骤顺序检查通过，未代写配置、未提交/推送。当前远端前端CI已通过，后端测试未执行，整个CI未通过。理解题：为什么Install dependencies失败后，Run backend tests显示skipped而不是failure？

2026-11-14知识问答与CI教学：学习者对有依据/无依据/部分依据三题回复可以了，按用户反馈记录手工验收完成，老师未读取三题原始响应，不新增准确率或延迟结论。进入GitHub Actions持续集成，区别于LangGraph业务工作流；部署/CI配置由学习者手写。拟新增.github/workflows/ci.yml：push/pull_request/workflow_dispatch，两独立Ubuntu job，后端Python3.12.16、uv0.11.32、锁文件同步和6目录隔离单测，前端Node24、npm ci/test/lint/build。不使用真实数据库/Ollama/本机凭据。老师本机实测拟选后端228 passed in 3.45s，前端12项测试、lint及生产构建通过；不是GitHub/Linux CI结果，也不与历史836项叠加。尚未创建CI文件、提交或推送。理解题：为什么CI应使用临时数据库和模型替身，而不连接本机演示数据库与Ollama？

2026-11-14演示账号：新增scripts/bootstrap_demo_accounts.py，仅向ticketpilot-demo创建agent.demo@example.com客服与supervisor.demo@example.com主管。随机密码保存在Git/Docker忽略的.env.demo.accounts，库中仅存哈希；重复执行核对并保留账号，角色/密码/停用状态冲突则整批回滚。实际18000登录及auth/me通过，客服审计403、主管200；重复执行零写入、凭据文件不变，第二账号冲突时首个探针插入回滚且未残留。未操作原业务库；未重跑历史836项pytest。下一批为前端容器/Nginx与Worker编排。

2026-11-14独立Compose与真实PostgreSQL：新演示栈运行并通过迁移/健康/卷权限/重建保留/宿主机Ollama元数据连通。`scripts/check_demo_postgres.py`确认固定项目与目标后，在唯一临时PostgreSQL库应用迁移，真实JWT HTTP验证审批/权限拒绝/工作流恢复/幂等/审计和实际stdio MCP六Span；模型/向量服务为替身，未调用真实推理。临时库及检查点清理，补目标保护复验通过。未访问原业务栈或写演示业务行，未重跑历史836项pytest，不合并计数。服务保持运行供Swagger验收，详情见DEPLOYMENT.md。

2026-11-14后端镜像：`scripts/check_backend_image.py`实际通过，唯一临时容器禁用外网、不发布端口、不挂载宿主机目录/卷，验证UID/GID10001、data权限、本机凭据排除、Linux依赖兼容/原生导入/离线RapidOCR、打包Alembic迁移在临时SQLite到head、健康/OpenAPI和请求/真实Trace头；结束后验证所有本任务测试容器已清理。BuildKit静态检查无警告，实际镜像构建成功，复现见根DEPLOYMENT.md。未运行业务PostgreSQL/Ollama或完整Compose，未重跑历史836项pytest，不合并计数。

最新相关回归（2026-11-13 Agent子Span）：836 passed in 91.10s，原814项相关组加22项。真实SDK、真实HTTP和测试stdio MCP，隔离测试数据库及PostgreSQL离线DDL；模型/向量服务为替身，非全项目、真实业务或生产验收。

2026-11-13子Span：新增`tests/unit/observability/test_operation_spans.py`14项；`test_agent_mcp_api.py`新增5项（文件9项）并注入内存Exporter，验证真实stdio六Span父子关系/时序、模型/传输失败、非法UUID不启动Client与未知名脱敏；`test_agent_approval_api.py`新增3项（文件29项），验证申请审计同Trace、409步骤ERROR与SERVER状态区别、后端强制检索不伪造模型步骤。相关单元/HTTP/MCP组189 passed in 12.00s，申请API29 passed in 5.46s。默认沙盒初轮线程执行超时，扩展执行环境复验通过；全部临时库和测试子进程，模型/向量服务为替身。

最新相关回归（2026-11-13 HTTP Trace）：814 passed in 108.29s，原794项相关组加20项真实SDK/HTTP追踪测试。追加500日志trace_id关联断言后，请求关联16项再次通过（6.71s）。仅临时测试库、SDK内存/Console测试导出器、测试子进程及PostgreSQL离线DDL，非全项目/真实模型/生产验收。

2026-11-13真实HTTP Trace：新增`tests/unit/observability/test_tracing.py`7项及`tests/integration/api/test_http_tracing_api.py`13项，使用真实SDK/内存导出验证Provider配置、真实上下文、Console stderr、应用生命周期复用及关闭、SERVER Span属性/错误分类/模板、并发/脱敏/异常/取消/响应开始后错误。原请求关联16项补验证成功、拒绝和回滚后独立失败审计的trace_id对应HTTP头。新增20项与原请求关联和审计集成130 passed in 17.52s；不访问真实库或模型，不代表Agent子Span/MCP跨进程已完成。

最新相关回归（2026-11-13请求编号）：794 passed in 86.92s，原764项相关组加请求关联30项。仅隔离测试数据库、测试子进程及PostgreSQL离线DDL，非全项目或生产验收；未调用真实模型。

2026-11-13请求关联：新增`tests/unit/observability/test_request_context.py`14项与`tests/integration/api/test_request_correlation_api.py`16项，覆盖ContextVar作用域/异常/取消/任务重叠、AnyIO线程、ASGI非HTTP透传和正文分块，以及真实HTTP同步/异步上下文、响应头、401等错误/未捕获500、并发请求隔离、成功与独立失败审计关联。新增30项及原审计集成110 passed in 11.92s；无真实库或模型调用，未接OTel真实trace_id。

最新相关回归（2026-11-13只读审计查询）：764 passed in 88.14s，原734项相关组加30项查询测试。仅隔离数据库、测试子进程与PostgreSQL离线DDL，范围包括审计/审批/工作流/Agent/MCP/相关HTTP和迁移等，不等同于全项目、真实模型或生产验收。

2026-11-13只读审计查询：新增`tests/integration/api/test_audit_query_api.py`30项，真实JWT与临时文件SQLite验证主管/管理员、客服/未知角色/停用/未认证拒绝、UUID与分页边界、双过滤交集、同时间稳定排序、空页、只读SQL、删除身份后快照保留、数据库错误以及完整审批链路后查询。查询30项连同原审计组130 passed in 13.85s。未访问真实业务库或模型。

最新相关回归（2026-11-13入口边界）：734 passed in 74.13s，原715项相关组加新入口边界19项；包含最终权限异常捕获顺序修复。仅临时测试数据库、测试子进程、PostgreSQL离线DDL，不是全项目、真实模型或生产验收。

2026-11-13入口边界：新增`tests/integration/api/test_approval_boundary_audit.py`19项，真实认证HTTP、正式图和临时文件SQLite验证review/execute角色不足留痕、工作流权限/可见范围/状态/互斥锁/检查点/节点错误、目标快照与错误脱敏、Service失败去重（包含审计写入失败）、已提交关闭后节点错误不撤销原效果。补只申请执行范围拒绝测试，防止PermissionError被OSError分支错误转换。未新增401、认证停用账号、422及只读查询审计。

最新相关回归（2026-11-13审批决定/执行失败接线）：715 passed in 72.93s，上一轮687项加本批28项。范围为审计/审批、MCP、Agent、工作流、相关HTTP与迁移等，仅隔离测试数据库、测试子进程及PostgreSQL离线DDL，不等同于全项目或生产验收。

2026-11-13审批决定/执行失败：`test_creation_failure_audit.py`沿用临时文件库fixture，新增28项（文件合计43项）覆盖两路径权限与身份、合法读取后目标快照、各阶段真实写入后回滚、审计/回滚故障不覆盖原异常、无过期ORM查询、重复执行失败保留原成功效果与凭据。更新原成功审计测试：业务commit失败时独立失败事件保留，拒绝尝试不能误记为成功。审计/审批相关219 passed in 9.95s；未覆盖HTTP依赖提前拒绝或工作流外层失败审计。

最新相关回归（2026-11-13创建失败接线）：687 passed in 75.13s，包含原660项相关组、独立失败服务12项及创建失败接线15项。使用隔离测试数据库、测试子进程及PostgreSQL离线DDL，未调用真实模型或迁移业务库；不等同于全项目测试。

2026-11-13创建失败接线：新增`tests/integration/audit/test_creation_failure_audit.py`15项，验证正式create_approval的权限/不存在/冲突/存储错误、独立同库持久化、原异常保留、回滚故障跳过新事务、成功不走失败接口，以及禁止rollback后原会话重读身份。原成功审计测试中创建commit故障的预期改为保存独立失败事件。审计/审批组合191 passed in 5.56s；未表示review/execute或HTTP依赖403失败审计已接入。

2026-11-13独立失败审计：新增`tests/integration/audit/test_failed_attempt_service.py`12项，临时文件SQLite验证独立提交、业务回滚隔离、写入/提交/工厂数据库错误、回滚关闭、固定日志和非数据库错误传播；调用方示例保留原业务异常。单独12 passed in 0.43s；`tests/unit/audit tests/integration/audit tests/unit/approvals tests/integration/approvals`组合176 passed in 2.92s。正式审批失败分支与HTTP权限依赖尚未接入，本轮未重跑下方历史660项组合。

最新相关组合：2026-11-12成功事件接入后660 passed in 68.85s（新增17项，原相关643项），覆盖下述审计、审批、工作流、MCP、Agent、相关HTTP与迁移等。仅隔离测试数据库、测试子进程、PostgreSQL离线DDL，不等同于全项目或生产验收。

2026-11-12审批成功审计：新增`tests/integration/audit/test_approval_audit.py`17项，使用正式Service/Repository验证申请三角色身份、批准/拒绝决定、首次关闭及跨执行人重放、重开后不再关闭、四阶段实际flush后故障与提交故障回滚、权限/状态错误不误记成功。跨进程检查点测试的固定建表清单同步补入AuditEventRecord，保留真实子进程恢复验证。失败/无权限尝试的独立审计尚未接入，本测试不将其描述为已完成。

```powershell
uv run pytest tests/integration/audit/test_approval_audit.py tests/integration/workflows/test_closure_checkpoints.py -q
```

2026-11-12审计基础：新增`tests/unit/audit/test_audit_schemas.py`20项、`tests/integration/audit/test_audit_repository.py`8项和`tests/migrations/test_audit_migration.py`2项。验证字段白名单与UUID序列化、业务/审计共同提交和回滚、错误传播、历史快照、同操作多事件及升级/回滚保留原业务数据。与原MCP/Agent/工作流/审批/相关HTTP等合跑643 passed in 59.72s；仅临时SQLite与PostgreSQL离线DDL，不代表真实数据库迁移或业务Service已接入审计。

```powershell
uv run pytest tests/unit/audit tests/integration/audit tests/migrations/test_audit_migration.py -q
```

2026-11-12生产MCP Client：`tests/unit/integrations/test_mcp_client.py`新增18项，验证工具发现、配置、结果验证、错误、实际整体超时、取消清理、同步桥；`tests/integration/api/test_agent_mcp_api.py`新增4项，以真实JWT和文件测试库运行认证HTTP→生产Client→独立stdio Server，父进程直接查询被禁止，覆盖成功、无回退503、认证后停用与非法UUID。连同原相关组613 passed in 64.44s。对话模型为替身，不等同于真实Ollama选工具评测。

```powershell
uv run pytest tests/unit/integrations tests/integration/api/test_agent_mcp_api.py tests/integration/mcp -q
```

2026-11-12 MCP Server：`tests/integration/mcp/test_ticket_server.py`新增20项，真实SDK Client完成发现与调用，覆盖UUID/必填参数、账号身份/角色/停用、公开字段及只读、错误过滤、未注册写工具和独立stdio子进程。单独20 passed in 3.69s；安装MCP后加入原Agent/工作流/审批/HTTP等相关组591 passed in 62.85s。数据库和令牌全部隔离，生产Client及Agent路径尚未切换。

```powershell
uv run pytest tests/integration/mcp/test_ticket_server.py -q
```

2026-11-12 Agent受控申请：`tests/integration/api/test_agent_approval_api.py`新增26项，使用真实JWT、正式工具/Service/工作流和临时SQLite，模型及向量服务为替身。覆盖三角色申请且不关闭、管理HTTP批准恢复、原始用户句式与模型参数匹配、伪造字段、知识路径禁止写入、创建回滚、启动失败保留原申请和执行范围竞态保护。加入原聊天API等相关组后实际571 passed in 61.47s。未验证真实Ollama的工具选择，也未声明跨聊天请求创建幂等。

知识库生产代码的分层与入口说明见 [知识库模块目录说明](../ticketpilot/knowledge/README.md)。

工作流生产代码与新示例命令见 [工作流目录说明](../ticketpilot/workflows/README.md)。正式图、检查点与模拟审批测试位于 `tests/integration/workflows/`；模型及工具的单元测试仍位于 `tests/unit/agent/`，HTTP测试仍按接口放在 `tests/integration/api/`。

```text
tests/
├── conftest.py                 # 跨模块共享的 pytest fixture
├── factories/                  # 只构造测试对象，不隐式提交事务
├── unit/                       # 单函数或单类测试，不连接真实外部服务
│   ├── agent/
│   ├── core/
│   ├── identity/
│   ├── knowledge/
│   └── legacy_cli/
├── integration/                # 多组件协作、数据库或 FastAPI 路由测试
│   ├── workflows/              # 正式流程、文件检查点、模拟审批与恢复
│   ├── api/
│   ├── core/
│   ├── database/
│   ├── identity/
│   ├── knowledge/
│   ├── legacy_cli/
│   └── tickets/
└── migrations/                 # Alembic 升级、回滚与模型一致性测试
```

## 各层职责

### unit

- 一次主要验证一个函数或一个类。
- 数据库、Repository、Ollama 等依赖使用 fake、mock 或 monkeypatch。
- 运行速度快，失败时容易定位具体业务逻辑。

正式审批的数据模型测试：`uv run pytest tests/unit/approvals/test_approval_schemas.py -q`。覆盖 UUID 与理由校验、严格布尔值、拒绝客户端填写身份/状态字段，以及待审批响应的可空字段和 JSON 序列化；不代表身份认证或审批权限已接入。

### integration

- 验证多个组件能否正确协作。
- Repository 测试使用临时数据库。
- API 测试通过 FastAPI `TestClient` 验证路由、依赖和响应。
- SQLite 适合验证通用 SQLAlchemy 行为，但不能代替 pgvector 测试。

模拟审批的幂等恢复回归：

```powershell
uv run pytest tests/integration/workflows/test_approval_example.py -q
```

该测试在 `tmp_path` 中创建检查点与效果数据库：先提交模拟业务，再抛出节点异常，重新打开检查点并恢复，断言执行次数和操作记录均保持为 1。它还检查审批前没有执行效果、失败后审批已保存以及恢复后流程结束。测试模拟节点异常和文件重新打开，不模拟进程强制终止，不依赖真实工单数据库或 Ollama。

正式审批 ORM 与迁移回归：

```powershell
uv run pytest tests/unit/approvals tests/integration/approvals tests/integration/workflows/test_approval_example.py tests/migrations -q
```

`tests/integration/approvals/test_approval_models.py` 在启用外键的临时 SQLite 中验证默认值、合法状态、非法状态/操作、操作编号唯一、关联对象存在及 RESTRICT 删除限制。`tests/migrations/test_approval_migration.py` 验证从 `0ea19e381c10` 升到 `6cfe2a7d9b41` 后的索引、默认值与约束，以及回滚审批表时旧用户、旧工单整行保持不变；现有迁移测试另行核对 ORM 与最新迁移结构一致。

创建服务回归：`uv run pytest tests/integration/approvals/test_approval_service.py -q`。13项场景验证三种允许角色、非关闭工单申请、停用/未知角色拒绝、不存在/已关闭目标，以及查询/flush后/commit失败时回滚、保留异常链与Repository不提前提交。仅操作临时数据库，不验证HTTP和JWT。

创建接口回归：`uv run pytest tests/integration/api/test_approval_api.py -q`。17项HTTP场景使用独立应用、真实JWT验证和临时SQLite，覆盖201保存pending且工单状态不变，401缺失/无效/不存在用户的令牌，403停用/未知角色，404/409业务拒绝，422非法/自报身份字段，以及flush后故障返回固定500且无残留申请。仅覆盖创建入口，不代表审批决定或真实关闭已实现。

查询权限回归已加入同一Service/API测试文件：新增8项Service和12项HTTP场景，验证agent本人/他人范围、supervisor/admin全局查询、越权与缺失同样404、伪造查询参数不能扩权、真实JWT/停用账号、UUID错误以及存储故障。查询记录即使已在Session身份映射中也不能越权；正常读取不提交事务。

审批决定回归：同一Service/API文件新增18项服务与24项HTTP场景，覆盖批准/拒绝、角色与认证、非pending冲突、第二审批人不能覆盖首个决定，以及查询/条件更新/刷新/提交失败的回滚。`tests/integration/approvals/test_approval_concurrency.py` 用临时文件SQLite、两个独立Session和屏障，让两个请求都查询到pending后再提交相反决定，验证一成功一冲突且最终记录与成功者一致；此测试不等同于PostgreSQL真实并发验收。

执行结果表回归：`tests/integration/approvals/test_approval_execution_models.py` 验证操作编号主键去重、引用申请的唯一操作编号（不能误用申请id）、工单/执行人外键、操作检查及结果必填、删除限制，以及同一事务内工单/申请/结果一起回滚。`tests/migrations/test_approval_execution_migration.py` 验证迁移 `91b7e4c2a6d0` 升级/回滚保留旧数据，默认值和约束生效，并离线生成PostgreSQL DDL；未连接实际PostgreSQL执行该测试。

正式执行回归：新增31项执行Service、2项文件SQLite双连接竞争和18项HTTP执行用例。验证完整创建→审批→执行→成功重放闭环、执行权限、状态冲突、结果绑定、失败整体回滚、提交已生效但调用方收到错误后的重试，以及同申请并发复用结果/不同申请竞争同工单只一条成功记录。API测试每个请求单独创建Session，避免测试共享身份缓存影响跨请求读取。相关测试运行入口：

```powershell
uv run pytest tests/unit/approvals tests/integration/approvals tests/integration/api/test_approval_api.py tests/integration/workflows/test_approval_example.py tests/migrations -q
```

当前这组测试实际211 passed；数据库测试为临时SQLite，不代表已完成真实PostgreSQL并发或进程崩溃验收。

普通状态入口关闭限制回归（2026-11-11）：同一API文件新增24项测试，验证三种角色、四种原状态下直接提交closed均返回409且工单数据/审批记录不变，三种普通目标状态仍正常保存，目标缺失返回404，缺失或无效令牌返回401。原HTTP闭环另增加“已经批准也不能通过普通状态接口关闭”的断言，随后专用执行与成功重放仍正常。审批相关测试加原工单/认证API回归实际282 passed in 13.81s；全部业务写入隔离测试库。

```powershell
uv run pytest tests/unit/approvals tests/integration/approvals tests/integration/api/test_approval_api.py tests/integration/api/test_api.py tests/integration/api/test_auth_api.py tests/integration/workflows/test_approval_example.py tests/migrations -q
```

正式关闭工作流只读阶段回归（2026-11-11）：`tests/integration/workflows/test_ticket_closure.py`新增16项测试，运行真实LangGraph和审批查询服务，验证六种状态样本读取、错误输入覆盖、JSON输出、角色与归属权限、申请缺失、查询故障与后续可重试，以及工单/申请/执行表数据不变。六种状态是读取样本，不代表执行服务都能产生；本阶段不测试暂停或真实关闭。连同审批、原工单/认证API、模拟恢复及迁移回归，实际298 passed in 13.79s；不访问真实业务数据库。

暂停与恢复复查回归（同日后续）：调整原16项测试的检查点配置与pending预期，新增12项恢复场景，同文件共28项。覆盖反复伪造批准/目标仍暂停、实际批准和拒绝后的新Session读取、停用/未知角色/切换他人/主管降权、申请被删除、复查故障后从原节点继续，以及thread_id隔离。业务状态修改通过真实审批Service和隔离测试库，图仍不关闭工单；检查点使用内存，不代表跨进程恢复。全部相关回归最新实际310 passed in 15.38s。

正式执行节点回归（同日后续）：原批准只读参数由实际执行场景替代，并调整获批恢复与会话隔离预期，新增18项，同文件现45项。验证真实Service关闭目标且不修改其他工单、管理角色限制、事务写入后故障回滚再试、节点提交后报错和commit成功但报错时从原检查点恢复、原成功执行人和记录不变且关闭只调用一次、恢复时当前执行人权限、工单冲突/审批信息缺失及直接节点调用拒绝自报approved。连同原审批/工单/认证/迁移回归最新327 passed in 15.39s；只修改临时数据库，内存检查点不代表跨进程恢复。

文件检查点与跨进程恢复回归（同日后续）：`tests/integration/workflows/test_closure_checkpoints.py`新增10项，覆盖正常/异常退出关闭连接、不同文件/会话隔离、重开后仍复查，以及不同Python解释器间的批准执行/拒绝结束、提交后节点报错重试、停用/降权拒绝。业务库与检查点均为tmp_path下独立SQLite文件，子进程15秒超时；正常退出和节点异常退出后恢复已验证，未验证强制杀进程、断电、HTTP权限或真实PostgreSQL。加入该文件后的全组最新337 passed in 41.75s。

工作流查询HTTP回归（同日后续）：`tests/integration/api/test_approval_workflow_api.py`新增27项，完整应用与真实JWT，覆盖GET /v1/approvals/{approval_id}/workflow的查询范围、认证、未启动不创建文件、四阶段只读查询、检查点绑定错误、安全存储错误提示、内部异常隐藏、业务实时状态与落后检查点分别展示，以及客户端参数不能重新选择会话/路径/身份。每请求独立Session，所有文件位于tmp_path，未访问业务库或项目data检查点；启动/恢复HTTP尚未实现。加入此文件及17项配置回归后的全组实际381 passed in 44.48s。

工作流目录整理回归（同日后续）：生产代码迁至 `ticketpilot/workflows/ticket_closure/`（state、nodes、graph、checkpoints、service），独立示例迁至 `workflows/examples/`，三份集成测试同步移至 `tests/integration/workflows/`。七个原类/函数拆分前后AST一致，保留节点名及检查点格式；示例数据根目录调整为新层级的parents[3]。以下测试加Agent单元组实际495 passed in 41.74s；新模拟审批命令--help、模块语法及根目录检查通过。未移动或改写真实业务库和原检查点，未运行真实模型验收。

2026-11-12工作流写入HTTP：同一API文件新增35项（现62项），覆盖启动/恢复/失败重试、管理权限、状态条件、重复启动不重置检查点、完整HTTP批准/拒绝、提交前/后故障恢复、真实并发HTTP互斥、异常释放锁和安全响应。相关组529 passed in 44.80s；补充读取节点失败重试单项随后1 passed in 1.98s，本轮累计530个不同用例通过。只写临时SQLite与检查点，进程内Lock不代表分布式互斥，部署当前要求单worker。

```powershell
uv run pytest tests/integration/api/test_approval_workflow_api.py tests/integration/workflows tests/unit/agent tests/unit/approvals tests/integration/approvals tests/integration/api/test_approval_api.py tests/integration/api/test_api.py tests/integration/api/test_auth_api.py tests/migrations tests/unit/core/test_config.py -q
```

```powershell
uv run pytest tests/integration/api/test_approval_workflow_api.py tests/integration/workflows/test_closure_checkpoints.py tests/integration/workflows/test_ticket_closure.py tests/unit/approvals tests/integration/approvals tests/integration/api/test_approval_api.py tests/integration/api/test_api.py tests/integration/api/test_auth_api.py tests/integration/workflows/test_approval_example.py tests/migrations tests/unit/core/test_config.py -q
```

### migrations

- 从空数据库升级到最新版本。
- 验证 downgrade 能够回滚。
- 验证 Alembic 结构与 ORM metadata 一致。

### e2e（真实服务评测）

- 使用真实 PostgreSQL、pgvector 和 Ollama。
- 验证完整的文档入库、向量检索和 Agent 回答流程。
- 默认快速测试不会运行依赖真实外部服务的用例。

目前已有 `tests/e2e/test_pdf_rag_eval.py`，用于对固定的 PDF 问答做真实模型回归评测。它只读取知识库，不修改文档。运行前须确保 PostgreSQL、Ollama 已启动，且知识库中有 `pdf-test.pdf`，正文为 `TicketPilot password reset: contact administrator.`。

```powershell
$env:TICKETPILOT_RUN_LIVE_EVAL = "1"
uv run pytest -v tests/e2e/test_pdf_rag_eval.py
Remove-Item Env:TICKETPILOT_RUN_LIVE_EVAL
```

两道题分别检查“有答案时引用正确 PDF”和“文档未提供电话时不编造号码”。由于模型输出可能变化，这是一组需手动启用的回归评测，不代替快速单元测试，也不代表所有问题都已评测完毕。

## 固定回归评测集（2026-10-21）

新增两组入口，直接调用正式业务实现，不复制校验逻辑：

- `tests/e2e/test_fixed_evidence_regression.py`：25 道固定证据题，13 道可回答、12 道不可回答。复用已有 22 道实验题，增加学习笔记两种问法及传真/手机号干扰题。只评估校验器的可回答性判断，不代表完整 RAG 正确率，也不是未见数据上的泛化成绩。
- `tests/e2e/test_fixed_rag_regression.py`：3 道完整 Agent 问答，覆盖保修事实、手机号缺失、学习笔记第一步。强制检索并显式传入新校验器。依赖 ready/internal 的 `2323.docx` 和 `TicketPilot_项目学习笔记_当前进度_2026-09-21_含本章答案.pdf`，只读数据库。文档变化会影响结果，失败时应诊断，不要为了通过直接修改预期。

```powershell
$env:TICKETPILOT_RUN_LIVE_EVAL = "1"
try {
    uv run --no-cache pytest tests/e2e/test_fixed_evidence_regression.py tests/e2e/test_fixed_rag_regression.py -v -s --durations=30
} finally {
    Remove-Item Env:TICKETPILOT_RUN_LIVE_EVAL -ErrorAction SilentlyContinue
}
```

证据评测结束会输出 JSON 报告路径（pytest 临时目录）。报告包含模型名、逐题结果、耗时、两类题各自通过数和服务错误数。临时报告可能被清理，需长期比较时请另存。服务异常不计作拒答成功。完整问答使用事实原文锚点和来源断言，是严格回归标准，不是通用语义评分。

权限单独评估，不能交给模型判断：

```powershell
uv run --no-cache pytest tests/unit/knowledge/test_knowledge_access.py tests/unit/knowledge/test_knowledge_visibility_repository.py tests/unit/knowledge/test_knowledge_visibility_retrieval.py tests/integration/api/test_agent_api.py::test_agent_chat_uses_current_user_visibility tests/integration/api/test_knowledge_api.py::test_search_knowledge_uses_current_user_visibility -q
```

这组权限测试保护角色映射、SQL 过滤和 API 参数传递，使用测试替身，不代表真实 PostgreSQL 下跨角色端到端安全测试。真实库权限回归仍需独立固定数据与角色验证。

2026-10-21 首次固定证据集：`qwen3:1.7b`，25/25 通过（可回答 13/13，不可回答 12/12），服务异常 0，累计耗时 165.37 秒。此成绩只代表该轮固定题目，不保证任意问题正确。

同轮完整 Agent：3/3 通过；保修题 58.21 秒、手机号码缺失题 53.25 秒、学习笔记题 53.03 秒。当前响应速度仍需优化。校验器与权限相关快速测试合计 34 项通过，带一条已有 Starlette/httpx 弃用警告。未设置真实评测开关时，两组新增测试共 28 项均安全跳过。

## Fixture 与 Factory 的区别

`conftest.py` 中的 fixture 负责测试基础设施和生命周期：

```text
创建 Engine → 创建表 → 提供 Session → 测试结束后释放资源
```

`factories/` 中的 factory 只负责构造数据对象：

```text
build_user() → UserRecord
build_knowledge_document() → KnowledgeDocumentRecord
```

Factory 不会偷偷执行 `add()`、`flush()` 或 `commit()`，事务始终由测试明确控制。

## 常用命令

```powershell
# 全部测试
uv run pytest

# 只运行快速单元测试
uv run pytest tests/unit

# 只运行集成测试
uv run pytest tests/integration

# 只运行迁移测试
uv run pytest tests/migrations

# 只运行知识库测试
uv run pytest tests/unit/knowledge tests/integration/knowledge
```
