"""可被多个 Web 模块复用的 FastAPI 依赖类型。"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ticketpilot.core.database import get_session




# 路由写 ``session: DatabaseSession`` 时，FastAPI 会为该请求创建 Session。
DatabaseSession = Annotated[Session, Depends(get_session)]
