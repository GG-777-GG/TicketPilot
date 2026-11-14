# 知识库模块目录说明

这次只调整文件位置和导入路径，业务规则、数据库表及 API 地址不变。

```text
knowledge/
├── api/           HTTP 路由、请求/响应 Schema、FastAPI 依赖
├── application/   入库、检索、更新、版本、管理和任务等用例编排
├── domain/        原始文档、常量、清洗、分块、权限规则和处理流水线
├── persistence/   SQLAlchemy ORM 模型、文档与任务 Repository
├── workers/       后台任务执行器及两个命令行入口
├── parsers/       TXT、Markdown、PDF、DOCX 文件解析器
└── embeddings/    向量模型接口及 Ollama 实现
```

## 先从哪里看

- 上传或创建文档：`api/router.py` → `application/uploads.py` 或 `application/ingestion.py` → `domain/pipeline.py` → `persistence/repository.py`。
- 知识检索：`api/router.py` → `application/retrieval.py` → `persistence/repository.py`。
- 异步入库：`application/jobs.py` 保存任务 → `workers/worker.py` 领取并处理 → `application/ingestion.py` → `persistence/job_repository.py`。
- 过期任务恢复：`workers/recovery_cli.py` → `application/jobs.py` → `persistence/job_repository.py`。

`domain/` 不依赖 HTTP 和数据库；`persistence/` 负责数据库操作；`application/` 负责业务流程与事务；`api/` 和 `workers/` 是两种调用入口。`parsers/` 和 `embeddings/` 是可替换的外部能力适配器。

## 常用命令

```powershell
# 处理最多 5 条排队任务（需要 Ollama 和数据库）
uv run python -m ticketpilot.knowledge.workers.worker_cli --max-jobs 5

# 持续领取任务；本轮未领取到任务时等待 3 秒
uv run python -m ticketpilot.knowledge.workers.worker_cli --watch

# 指定持续模式的空闲等待间隔，单位为秒，必须为正整数
uv run python -m ticketpilot.knowledge.workers.worker_cli --watch --poll-interval 5

# 检查并恢复最多 100 条租约过期任务（只需要数据库）
uv run python -m ticketpilot.knowledge.workers.recovery_cli --limit 100

# 验证知识库模块
uv run pytest -q tests/unit/knowledge tests/integration/knowledge
```

不要直接移动 ORM 模型或改数据库表名；Alembic 在 `migrations/env.py` 中导入 `persistence.models`，以便继续识别知识库表。

`--watch` 每个进程逐条处理任务。任务返回 succeeded 或 failed 后继续领取，未领取到任务才等待；每轮会话在等待前关闭。该模式不能与指定任务 ID 或 `--max-jobs` 同时使用，`--poll-interval` 只能与 `--watch` 一起使用。

按 Ctrl+C 停止持续模式。处理中断可能留下 running 任务，需由已有的租约恢复机制重新排队。它是终端中持续运行的进程，不提供开机自启或崩溃自动重启；未处理的系统异常会使进程退出。failed 任务不会自动重试，需要通过重试接口重新排队。
