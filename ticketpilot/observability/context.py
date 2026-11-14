"""
文件职责：保存和读取当前执行上下文中的请求编号。

作用与配合：
- 请求入口通过request_context建立作用域。
- 业务服务通过get_request_id读取编号。
- 退出作用域时恢复之前的上下文值。

边界：
- 不解析HTTP请求。
- 不保存用户身份、令牌或业务正文。
- 不生成OpenTelemetry的trace_id。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID, uuid4


# 对象定义在模块级，但其中的值属于当前执行上下文。
# 没有建立请求上下文时，读取结果为None。
_request_id: ContextVar[UUID | None] = ContextVar(
    "ticketpilot_request_id",
    default=None,
)


def get_request_id() -> UUID | None:
    """读取当前上下文的请求编号，不存在时返回None。"""

    return _request_id.get()


@contextmanager
def request_context(
    request_id: UUID | None = None,
) -> Iterator[UUID]:
    """在with范围内设置请求编号，退出时恢复之前的值。"""

    # 允许可信后端提供UUID，方便测试或复用已有上下文。
    if request_id is not None and not isinstance(request_id, UUID):
        raise TypeError("请求编号必须是UUID")

    current_id = (
        request_id
        if request_id is not None
        else uuid4()
    )

    # set返回恢复凭据，记录本次设置前的上下文值。
    token = _request_id.set(current_id)

    try:
        # 将编号交给外层with代码块。
        yield current_id

    finally:
        # 正常退出或发生异常，都恢复原来的上下文。
        _request_id.reset(token)