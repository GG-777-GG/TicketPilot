# 导入 Protocol，用来定义模型必须提供的方法
from typing import Protocol

# 导入模型接收的消息和返回的决定类型
from ticketpilot.agent_schemas import (
    AgentMessage,
    ModelDecision,
)


# 定义所有 Agent 模型必须遵守的接口
class AgentModel(Protocol):
    # 模型根据消息历史产生下一步决定
    def decide(
        # self 表示当前模型对象
        self,

        # 接收完整的对话消息历史
        messages: list[AgentMessage],
    ) -> ModelDecision:
        # Protocol 只规定方法格式，不提供具体实现
        ...


# 定义专门用于自动测试的假模型
class FakeAgentModel:
    # 创建 Fake Model 对象
    def __init__(
        # self 表示当前 Fake Model 对象
        self,

        # 接收测试提前准备好的模型决定
        decisions: list[ModelDecision],
    ) -> None:
        # 复制决定列表，避免修改调用者原来的列表
        self._remaining_decisions = decisions.copy()

        # 保存每次调用模型时收到的消息历史
        self.received_messages: list[
            list[AgentMessage]
        ] = []

    # 按顺序返回下一项预设决定
    def decide(
        # self 表示当前 Fake Model 对象
        self,

        # 接收 Agent Loop 传入的消息历史
        messages: list[AgentMessage],
    ) -> ModelDecision:
        # 复制并记录本次收到的消息
        self.received_messages.append(
            messages.copy(),
        )

        # 如果预设决定已经全部使用完
        if not self._remaining_decisions:
            # 抛出明确错误，避免测试产生模糊结果
            raise RuntimeError(
                "Fake Agent Model 没有剩余的预设决定",
            )

        # 删除并返回列表中的第一个预设决定
        return self._remaining_decisions.pop(0)