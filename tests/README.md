# TicketPilot 测试结构

测试采用“测试层级 + 业务模块”组织方式。

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

### integration

- 验证多个组件能否正确协作。
- Repository 测试使用临时数据库。
- API 测试通过 FastAPI `TestClient` 验证路由、依赖和响应。
- SQLite 适合验证通用 SQLAlchemy 行为，但不能代替 pgvector 测试。

### migrations

- 从空数据库升级到最新版本。
- 验证 downgrade 能够回滚。
- 验证 Alembic 结构与 ORM metadata 一致。

### e2e（后续增加）

- 使用真实 PostgreSQL、pgvector 和 Ollama。
- 验证完整的文档入库、向量检索和 Agent 回答流程。
- 默认快速测试不会运行依赖真实外部服务的用例。

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
