# TicketPilot 部署与镜像验收

本文记录可复现的镜像构建及隔离验收。完整应用Compose、前端运行镜像与Worker编排还在后续阶段；当前compose.yaml仍是既有数据库配置。

## 后端镜像

Dockerfile使用Python 3.12.16 slim-bookworm、uv 0.11.32，按uv.lock安装运行依赖，不安装pytest等开发依赖。后端以UID/GID 10001运行，数据目录为/app/data，单worker与当前工作流进程内互斥设计一致。

根.dockerignore排除本机虚拟环境、.env、业务数据、测试和临时文件；Dockerfile仅复制后端与迁移所需文件。构建不会执行数据库迁移。

在项目根目录、Docker Desktop运行Linux容器时构建：

```powershell
docker build --progress=plain -t ticketpilot-backend:interview-check .
```

`DEBIAN_MIRROR`默认https://deb.debian.org/debian，`DEBIAN_SECURITY_MIRROR`默认https://security.debian.org/debian-security；保留Debian包签名校验，下载有超时和重试限制。

### 本机代理/VPN下的构建网络

2026-11-14实际排查：镜像仓库可拉取，但deb.debian.org解析失败；官方安全源和中科大Debian镜像可通过HTTPS访问。随后files.pythonhosted.org在构建中解析失败，而宿主机可解析。

仅在相同故障下使用下面的构建参数。CDN地址从宿主机实时取得，不把临时IP写进Dockerfile，不修改Docker全局DNS或现有代理。

```powershell
$packageCdnAddress = ([System.Net.Dns]::GetHostAddresses("files.pythonhosted.org") |
    Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } |
    Select-Object -First 1).IPAddressToString

docker build --progress=plain --build-arg DEBIAN_MIRROR=https://mirrors.ustc.edu.cn/debian --add-host "files.pythonhosted.org:$packageCdnAddress" -t ticketpilot-backend:interview-check .
```

中科大镜像只用于主源，安全更新仍走官方源；HTTPS校验保持启用。[镜像站说明](https://mirrors.ustc.edu.cn/help/debian.html)

## 隔离验证

构建完成后运行：

```powershell
uv run python scripts/check_backend_image.py --image ticketpilot-backend:interview-check
```

脚本只创建唯一命名的临时容器：不发布宿主机端口、不挂载目录或数据卷、禁用外部网络。固定使用sqlite:////tmp/ticketpilot-image-check.sqlite，完成后删除测试容器。

检查内容：

- Linux虚拟环境、UID/GID 10001、data目录写入权限及凭据/本机环境未复制。
- Linux安装依赖兼容，OpenCV、ONNX Runtime、PDFium和RapidOCR可导入，OCR模型可离线初始化。
- 镜像包含Alembic配置和迁移，可把独立临时SQLite升级到当前head。
- 默认启动命令提供/health和OpenAPI，回传合法X-Request-ID和真实X-Trace-ID。

这里的SQLite迁移仅验证镜像打包及隔离启动，不等同于PostgreSQL/pgvector、真实Ollama或完整Compose验收。现有数据库容器与数据卷不参与本脚本。

## 已验证记录

2026-11-14，Windows Docker Desktop Linux引擎，使用上述本机网络参数构建成功：

- 标签：ticketpilot-backend:interview-check，平台linux/amd64，运行用户ticketpilot。
- 镜像ID：sha256:489d4d20c3af57bd2e5beed6b69a2918b90edbddc4f1b11e62e9339afffa1378。
- Docker报告Size为339697362字节；此为当前Docker镜像元数据，不代表跨环境性能或节省比例。
- BuildKit静态检查：Check complete, no warnings found。
- 114个运行依赖安装完成；隔离容器内依赖兼容、原生库、UID/GID10001和离线RapidOCR初始化通过。
- 镜像内迁移在临时SQLite升级到当前head；健康接口、OpenAPI、请求编号与真实Trace编号通过。
- 验证脚本输出Backend image check passed；任务创建的验证及网络诊断容器均已清理。

本批未重跑历史836项相关pytest组合；以上为实际Linux镜像构建与运行验证，不把两种证据合并成新的测试数量。

## 独立Compose演示环境准备

compose.demo.yaml是已运行的独立后端演示栈，项目名ticketpilot-demo，后端映射127.0.0.1:18000。数据库仅在Compose内网使用，新卷名为demo_postgres_data和demo_app_data（实际由项目名前缀隔离），不引用原compose.yaml的业务卷。

首次创建本机凭据：

```powershell
uv run python scripts/init_demo_env.py
```

脚本以独占创建方式生成.env.demo中的DEMO_POSTGRES_PASSWORD和DEMO_JWT_SECRET_KEY，值不打印，已有文件保持原样。文件由.gitignore和.dockerignore排除，不修改既有.env。数据库密码使用URL安全的随机十六进制字符串。

先做配置校验：

```powershell
docker compose --env-file .env.demo -f compose.demo.yaml config --quiet
```

quiet仅校验，不展示展开后的凭据，也不启动服务。启动顺序为database健康→migrate成功退出→backend启动；数据库就绪不代表业务表已经迁移。新环境不自动带入旧用户或文档，前端/Worker接入与演示数据初始化在后续阶段处理。

启动及检查状态：

```powershell
docker compose --env-file .env.demo -f compose.demo.yaml up -d backend
docker compose --env-file .env.demo -f compose.demo.yaml ps --all
```

入口为http://127.0.0.1:18000/docs，backend和database应healthy，migrate应Exited(0)。复用镜像默认单worker CMD，init负责辅助回收子进程。

当前DNS环境中，host.docker.internal未自动解析，backend使用extra_hosts映射host-gateway；这是容器配置，不改变Windows Ollama监听范围或Docker全局DNS。Ollama仍在宿主机运行。

进一步的隔离PostgreSQL验证：

```powershell
uv run python scripts/check_demo_postgres.py
```

脚本确认固定项目和演示数据库目标，再创建tp_check_加随机UUID的临时PostgreSQL库。只修改docker exec子进程的数据库环境，应用迁移后跑真实JWT HTTP审批/权限拒绝/工作流恢复/幂等/审计及实际stdio MCP六Span链路；模型和向量服务为替身，检查点位于临时目录，结束删除临时库。演示业务行和运行中后端的配置保持原样，密码/JWT不输出。

2026-11-14实际结果：PostgreSQL18.6、pgvector0.8.6，迁移到head、卷UID/GID10001、后端重建后测试探针保留并清理、已发布健康/Swagger及请求/Trace响应头均通过。host-gateway映射后Ollama0.40.14版本及配置所需模型元数据可读，未调用真实推理。临时库验证返回所有标记true且temporary_database_removed=true，补目标保护后复验同样通过，临时库计数0。演示服务保持运行；未操作原业务栈/卷，完整前端与Worker编排仍待完成。


## 演示登录账号

独立后端运行后，在项目根目录执行：

```powershell
uv run python scripts/bootstrap_demo_accounts.py
```

脚本只接受ticketpilot-demo容器和ticketpilot_demo数据库。生成两个账号：

| 邮箱 | 角色 |
|---|---|
| agent.demo@example.com | agent（客服） |
| supervisor.demo@example.com | supervisor（主管） |

随机密码保存在本机.env.demo.accounts，Git与Docker构建上下文均排除该文件；数据库仅存密码哈希。自行在编辑器打开该文件取用，勿将内容粘贴到聊天或提交。先在http://127.0.0.1:18000/docs调用POST /v1/auth/login，再将返回令牌用于Authorize；GET /v1/auth/me可检查身份。普通注册仍只能创建客服，主管角色仅由本机初始化脚本指定。

重复运行不会重置密码、角色或启用状态；已有账号与本机凭据不一致时停止，两个账号的创建在同一事务中回滚。请保留本机密码文件，删除它后重新生成的密码不能用于已有账号。

2026-11-14实测：两角色实际HTTP登录与身份正确；客服审计查询403、主管200；重复初始化零写入，密码文件未变。模拟第二账号冲突，首个新探针账号的插入随事务回滚，查询确认无残留。这一批有意向独立演示库保存两个用户，不修改原业务栈。


## 前端容器验收（2026-11-14）

学习者完成frontend/Dockerfile多阶段构建、frontend/nginx.conf及Compose frontend服务，容器提供127.0.0.1:18080入口。Compose config --quiet与容器内nginx -t通过。实际HTTP检查/login和/tickets返回React入口、构建JS可读、缺失/assets资源404；两演示角色通过/v1/代理登录并读取auth/me身份正确，令牌及密码未输出。页面交互验收由学习者操作，未将HTTP检查表述为全浏览器自动化。Worker尚待下一批手写接入。


## Worker启动与待修复代理（2026-11-14）

2026-11-14Worker启动核对：学习者已手写worker服务及backend显式上传目录。Compose静态校验通过，Worker处于Up且启动标记存在，API/Worker挂载同一ticketpilot-demo_demo_app_data到/app/data。只读演示库任务状态统计为空，尚无真实文件入库成功证据。后端因配置更新重建后，前端/v1/auth/me实测502：Nginx错误中的旧上游172.21.0.3:8000，当前backend为172.21.0.5。上一版静态proxy_pass缺少动态DNS更新，需要学习者改为共享zone+Docker resolver+upstream server resolve，再重建前端验收入库。当前Nginx1.30.6支持该语法；尚未应用修复，不能宣称问题已解决。理解题：为什么改完宿主机nginx.conf后，执行容器restart不会自动使用新配置？


## 动态DNS修复核对（2026-11-14）

2026-11-14动态DNS修复录入核对：学习者已添加ticketpilot_api共享zone、Docker resolver与server resolve，并重建frontend。nginx -t通过，实际18080/v1/auth/me由502恢复为未携带令牌时预期的401；未主动重建backend验证下一次IP切换。Compose五服务状态正常（migrate退出0、database/backend健康、frontend/worker运行）。只读确认演示库入库任务0、文档0、片段0，因此尚无真实入库/问答成功证据。下一批学习者在18080环境上传演示TXT并验收有依据/部分缺失/无依据三类问答，避免误用5173旧开发环境；不宣称固定题评测已完成。理解题：为什么文档ready不能保证任意问题都有答案？


## 正确演示入口的真实入库（2026-11-14）

2026-11-14正确演示入口入库完成：学习者确认此前误用旧网址，改用18080。老师只读演示库确认1条文件入库任务succeeded、attempt_count=1，1篇文档、1个知识片段。该结果证明已有真实入库成功，不再只是Worker启动证据；本次未读取正文、未执行问答推理，不宣称三题RAG验收已通过。下一批由学习者在18080知识库问答模式验证有依据、无依据和部分依据三题，保留来源与失败情况。理解题：为什么检索到相关片段，不等于片段能支持问题中的每一个事实？
