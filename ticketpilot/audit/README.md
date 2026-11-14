# 审计模块

记录审批相关操作的身份、目标、结果和关联编号，供管理角色排查和演示。

| 文件 | 责任 |
|---|---|
| schemas.py | 写入白名单、查询条件、分页响应 |
| models.py | 历史快照表；不设置随业务生命周期变化的外键 |
| repository.py | add/flush及按目标只读查询，不提交事务 |
| service.py | 独立保存失败尝试；校验管理查询权限 |
| router.py | GET查询入口，转换稳定HTTP错误 |

## 事务与覆盖范围

- 成功事件由创建、审批决定和执行Service放入业务事务一起提交。
- 失败尝试先回滚原事务，再通过新Session尽力保存；数据库不可用时不保证保存成功。
- 普通拒绝申请是`approval.rejected / succeeded`；无权限尝试是`approval.denied / denied`。
- 重复执行保留原成功效果，记录`execution.replayed / replayed`。
- 工作流外层记录自己的失败；执行异常的`_failure_audit_attempted`属性阻止同一调用链重复记录。该属性仅表示尝试，不表示落库成功，也不跨请求去重。
- 节点在业务提交后失败时，可以同时存在成功执行和工作流失败事件；失败不代表已提交业务被撤销。
- 401、认证依赖的停用账号拒绝、422及普通只读查询未新增审计。
- 这是应用层审计基础，不是数据库防篡改平台；旧操作不会自动补录。

## 查询接口

`GET /v1/audit-events`，仅活跃的`supervisor`或`admin`可以查询。

| 参数 | 用途 |
|---|---|
| approval_id | 审批编号，UUID，可选 |
| operation_id | 操作编号，UUID，可选 |
| limit | 每页1–100条，默认50 |
| offset | 跳过条数，默认0 |

至少提供一个编号；两个都提供时取交集。合法目标没有事件时返回空页，不要求原审批对象存在。

返回`items / limit / offset / has_more`。内部多取一行判断下一页，不计算总数。按`occurred_at`再按`id`升序；同时间的编号排序提供确定顺序，不代表随机UUID能还原更精细的发生先后。翻页查询实时数据，不提供跨页数据库快照保证。

不提供HTTP新增、修改或删除事件的接口。查询不补查业务对象，也不会自动写入一条查询审计。

## Swagger验收

1. 在现有后端`/docs`中用主管或管理员账号授权。
2. 通过正式审批接口新建申请，复制响应中的`id`和`operation_id`。
3. 展开`audit`下的`GET /v1/audit-events`，填写`approval_id`，其他编号留空；应读到`approval.requested`。
4. 批准并执行申请，再重复执行一次；按该`operation_id`查询，应包含requested、approved、closed、replayed四类事件。
5. 以普通客服尝试审批或执行会返回403；切回管理身份按`approval_id`查询，可查看拒绝事件。早期失败可能没有`operation_id`，因此不能仅用操作编号查完整拒绝尝试。
6. 可用`limit=2, offset=0`查看第一页，再用`offset=2`翻页。若旧审批没有事件，先完成一次新操作，旧数据不补录。

HTTP请求编号及真实Trace现已接入：事件未显式提供request_id/trace_id时，Repository从当前请求及有效Span上下文补齐；旧事件和无上下文脚本仍可为空。事件编号应与产生它的操作响应头X-Request-ID/X-Trace-ID匹配，不能与本次查询的响应头混淆。聊天已有HTTP→Agent→模型/工具→MCP Client子Span，关闭申请工具产生的审计也关联同一Trace；MCP Server内部追踪与跨进程传播尚待接入。详情见[请求关联说明](../observability/README.md)。

## 测试

新查询入口30项测试位于`tests/integration/api/test_audit_query_api.py`，验证真实JWT、角色、目标过滤、分页、只读SQL、历史身份快照、稳定错误与正式审批链路。所有自动测试使用隔离数据库，不查询真实业务数据。
