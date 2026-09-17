# TicketPilot 架构说明

TicketPilot 当前采用“按业务功能分包的模块化单体”。它仍然是一个应用、一个部署单元，
但用户、工单、Agent 和基础设施各自在自己的目录中，避免所有代码继续堆进一个文件。

## 目录结构

```text
ticketpilot/
├── app.py                  # FastAPI 组合根：创建应用、注册路由
├── core/                   # 跨业务模块共享的基础设施
│   ├── config.py           # 环境变量和应用配置
│   ├── database.py         # Engine、SessionLocal、Base、get_session
│   └── dependencies.py     # 共用的 FastAPI Session 依赖类型
├── identity/               # 用户、登录、JWT 和角色权限
│   ├── router.py
│   ├── schemas.py
│   ├── models.py
│   ├── repository.py
│   ├── security.py
│   ├── dependencies.py
│   └── types.py
├── tickets/                # 工单业务
│   ├── router.py
│   ├── schemas.py
│   ├── models.py
│   ├── repository.py
│   └── types.py
├── agent/                  # Agent 循环、模型协议和受控工具
│   ├── loop.py
│   ├── model.py
│   ├── dispatcher.py
│   ├── schemas.py
│   ├── tools/
│   │   └── tickets.py
│   └── providers/
│       └── ollama.py       # Ollama 结构化输出适配器
└── legacy_cli/             # 早期 JSON 命令行练习，与正式 API 隔离
```

数据库迁移继续放在项目根目录的 `migrations/`，测试继续放在 `tests/`。

## 一次 HTTP 请求怎样流动

```text
客户端请求
   ↓
app.py 注册的 Router
   ↓
Pydantic Schema 校验输入
   ↓
认证/权限依赖（需要时）
   ↓
Repository 执行数据库操作
   ↓
SQLAlchemy Session → PostgreSQL / SQLite
   ↓
ORM 对象转换成 Response Schema
   ↓
HTTP 响应
```

## 依赖规则

```text
app → identity / tickets 路由
agent tools → tickets repository
identity / tickets → core
core 不反向导入业务路由
```

- `router.py` 只处理 HTTP 参数、状态码、依赖和响应转换。
- `schemas.py` 只描述 API 边界数据，不负责数据库读写。
- `models.py` 只定义 ORM 表映射。
- `repository.py` 只负责数据库查询、提交和回滚。
- `security.py` 只负责密码哈希和 JWT。
- `app.py` 只装配应用，不放业务细节。

当前工单功能主要是单表 CRUD，所以暂不创建空的 `service.py`。以后出现“一个操作需要多个
Repository、一个事务、外部调用或复杂业务规则”时，再增加 Service 层，这样分层是由真实职责
推动的，而不是为了目录数量而增加空壳。

## 常用入口

```powershell
# 运行全部测试
uv run pytest -q

# 检查迁移是否与 ORM 模型一致
uv run alembic check

# 启动 FastAPI 开发服务
uv run fastapi dev ticketpilot/app.py

# 运行早期命令行练习版
uv run python main.py
```
