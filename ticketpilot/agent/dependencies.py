# lru_cache 用来缓存已经创建的模型对象
from functools import lru_cache

# Annotated 用来组合参数类型与 FastAPI 依赖信息
from typing import Annotated

# Depends 表示对象由 FastAPI 自动提供
from fastapi import Depends

# 导入所有 Agent 模型共同遵守的接口
from ticketpilot.agent.model import AgentModel

# 导入真正连接本地 Ollama 的实现
from ticketpilot.agent.providers.ollama import OllamaModel


# 创建并缓存项目使用的 Agent 模型
@lru_cache
def get_agent_model() -> AgentModel:
    # 第一次调用时创建 OllamaModel
    # 后续调用直接返回同一个对象
    return OllamaModel()


# 定义可以直接用于 FastAPI 路由参数的模型依赖类型
AgentModelDependency = Annotated[
    # 路由函数最终得到的是符合 AgentModel 接口的对象
    AgentModel,

    # FastAPI 自动调用 get_agent_model()
    Depends(get_agent_model),
]