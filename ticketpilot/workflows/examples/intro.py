"""
文件职责：演示 LangGraph 条件路由和真实只读知识检索。
使用方式：uv run python -m ticketpilot.workflows.examples.intro。
作用与配合：复用已有知识工具、数据库会话和 Ollama 向量模型。
边界：工单分支仍只生成计划；尚未接入正式问答接口，
也尚未调用对话模型生成答案或执行证据校验。
"""

# TypedDict 用来描述字典有哪些字段，以及各字段的类型。
from typing import Literal, TypedDict

# StateGraph 负责组装工作流。
# START、END 分别表示流程入口和结束位置。
from langgraph.graph import END, START, StateGraph

# LangGraph 向节点提供运行上下文。
from langgraph.runtime import Runtime

# 复用已有的工具运行依赖容器。
from ticketpilot.agent.dispatcher import AgentToolContext

# 知识检索的参数模型与结果模型。
from ticketpilot.agent.schemas import (
    SearchKnowledgeToolArguments,
    SearchKnowledgeToolResult,
)

# 已经实现的知识检索工具。
from ticketpilot.agent.tools.knowledge import search_knowledge

# 创建数据库会话。
from ticketpilot.core.database import SessionLocal

# 使用项目配置中的 Ollama 向量模型。
from ticketpilot.knowledge.embeddings.ollama import (
    OllamaEmbeddingProvider,
)

# 工作流调用配置的类型，thread_id 放在这里。
from langchain_core.runnables import RunnableConfig

# 用内存保存工作流检查点。
from langgraph.checkpoint.memory import InMemorySaver

# 负责把状态转换成可保存的数据，再还原回来。
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

# build_workflow() 编译后返回的工作流类型。
from langgraph.graph.state import CompiledStateGraph


class QuestionState(TypedDict):
    """描述本次流程共享的数据。"""

    # 用户最初提供的问题。
    question: str

    # 去掉两端空白后的问题。
    normalized_question: str

    # 下一步准备执行的检索计划。
    search_plan: str

    # 尚未执行检索时为 None；执行后保存完整工具结果。
    search_result: SearchKnowledgeToolResult | None


def normalize_question(
        state: QuestionState,
) -> dict[str, str]:
    """读取原始问题，去掉两端空白并检查是否为空。"""

    question = state["question"].strip()

    if not question:
        raise ValueError("问题不能为空")

    # 只返回本节点需要更新的字段。
    return {
        "normalized_question": question,
    }


def prepare_search(
        state: QuestionState,
) -> dict[str, str]:
    """读取整理后的问题，生成下一步检索计划。"""

    question = state["normalized_question"]

    return {
        "search_plan": (
            f"使用知识检索工具查找“{question}”的相关文档。"
        ),
    }


def choose_route(
        state: QuestionState,
) -> Literal["knowledge", "ticket"]:
    """读取整理后的问题，返回代表处理方向的字符串。"""

    question = state["normalized_question"]

    # 对字符串使用 in，表示检查是否包含指定内容。
    if "工单" in question:
        return "ticket"

    # 上面的条件不成立时，进入知识检索方向。
    return "knowledge"


def prepare_ticket_search(
        state: QuestionState,
) -> dict[str, str]:
    """读取问题，生成工单查询计划；此时尚未执行真实查询。"""

    question = state["normalized_question"]

    # 与知识检索节点一样，把生成的计划写入共享状态。
    return {
        "search_plan": (
            f"准备查询工单信息，用户问题是：“{question}”。"
        ),
    }


def retrieve_knowledge_node(
        state: QuestionState,
        runtime: Runtime[AgentToolContext],
) -> dict[str, SearchKnowledgeToolResult]:
    """读取问题和运行依赖，调用知识工具并返回检索结果。"""

    # 取得本次运行使用的数据库会话、模型及检索范围。
    context = runtime.context

    # Pydantic 模型会实际验证参数：
    # 例如问题不能为空、top_k 不能超过允许范围。
    arguments = SearchKnowledgeToolArguments(
        query=state["normalized_question"],
        top_k=3,
    )

    # 调用已有工具，内部会生成问题向量并查询知识库。
    tool_result = search_knowledge(
        session=context.session,
        embedding_provider=context.embedding_provider,
        arguments=arguments,
        allowed_visibilities=context.allowed_visibilities,
    )

    # 把完整工具结果写入流程状态。
    return {
        "search_result": tool_result,
    }


def build_memory_saver() -> InMemorySaver:
    """创建内存保存器，并允许还原项目使用的三种结果类型。"""

    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            # 完整检索结果。
            (
                "ticketpilot.agent.schemas",
                "SearchKnowledgeToolResult",
            ),
            # 检索结果中的单个知识片段。
            (
                "ticketpilot.agent.schemas",
                "KnowledgeToolMatch",
            ),
            # 片段的来源类型，例如 manual、file。
            (
                "ticketpilot.knowledge.domain.documents",
                "KnowledgeSourceType",
            ),
        ],
    )

    return InMemorySaver(serde=serializer)


def build_workflow(
        checkpointer: InMemorySaver | None = None,
) -> CompiledStateGraph:
    """注册处理节点，并用条件边选择知识检索或工单查询。"""

    builder = StateGraph(
        QuestionState,
        # 声明本流程接受哪种运行上下文。
        context_schema=AgentToolContext,
    )

    # 注册三个真正执行处理工作的节点。
    builder.add_node(
        "normalize_question",
        normalize_question,
    )
    builder.add_node(
        "prepare_search",
        prepare_search,
    )
    builder.add_node(
        "prepare_ticket_search",
        prepare_ticket_search,
    )
    builder.add_node(
        "retrieve_knowledge",
        retrieve_knowledge_node,
    )

    # 无论用户问什么，都先整理问题。
    builder.add_edge(START, "normalize_question")

    # 整理问题完成后，根据 choose_route 的返回值选择下一步。
    builder.add_conditional_edges(
        "normalize_question",  # 从哪个节点之后开始判断。
        choose_route,  # 负责判断的函数，传函数本身。
        {
            # 左边是判断函数的返回值，右边是注册的节点名称。
            "knowledge": "prepare_search",
            "ticket": "prepare_ticket_search",
        },
    )

    # 知识分支生成计划后，继续执行真实检索。
    builder.add_edge("prepare_search", "retrieve_knowledge")
    builder.add_edge("retrieve_knowledge", END)
    builder.add_edge("prepare_ticket_search", END)

    # 编译时绑定保存器，运行期间由框架保存检查点。
    return builder.compile(checkpointer=checkpointer)


def run_with_progress(
        workflow: CompiledStateGraph,
        initial_state: QuestionState,
        context: AgentToolContext,
        config: RunnableConfig,
) -> QuestionState:
    """使用指定会话运行流程，打印进度并返回最终状态。"""

    # 开始时还没有收到任何状态快照。
    final_state: QuestionState | None = None

    # 遍历过程中，工作流会逐步执行并提供输出。
    for part in workflow.stream(
            initial_state,
            config=config,
            context=context,
            # 同时观察节点更新和完整状态。
            stream_mode=["updates", "values"],
            # 使用统一的 type / data 输出结构。
            version="v2",
    ):
        if part["type"] == "updates":
            # data 是“节点名称 → 本节点更新内容”的字典。
            for node_name, changes in part["data"].items():
                print(
                    "已完成节点：",
                    node_name,
                    flush=True,
                )
                print(
                    "更新字段：",
                    ", ".join(changes.keys()),
                    flush=True,
                )

        elif part["type"] == "values":
            # 直接保存框架提供的完整状态。
            final_state = part["data"]

    # 对当前这个正常运行的图，应当能收到状态快照。
    if final_state is None:
        raise RuntimeError("工作流没有返回状态")

    return final_state


def main() -> None:
    """提供真实运行依赖，观察知识检索结果及工单计划。"""

    # 整个演示过程共用一个保存器和工作流。
    checkpointer = build_memory_saver()
    workflow = build_workflow(checkpointer=checkpointer)

    # 从项目配置读取模型名称、Ollama 地址等信息。
    embedding_provider = OllamaEmbeddingProvider()

    questions = [
        "根据《前端联调演示规则》，换货申请应在签收后多少个自然日内提交？",
        "帮我查询工单的处理进度",
    ]

    for number, question in enumerate(questions, start=1):
        # 给两个演示问题分配不同的逻辑会话。
        config: RunnableConfig = {
            "configurable": {
                "thread_id": f"demo-{number}",
            },
        }
        initial_state: QuestionState = {
            "question": question,
            "normalized_question": "",
            "search_plan": "",
            "search_result": None,
        }

        # 每次运行使用独立会话；离开 with 后自动关闭。
        with SessionLocal() as session:
            # 先创建运行上下文，把数据库会话和模型装进去。
            context = AgentToolContext(
                session=session,
                embedding_provider=embedding_provider,
                allowed_visibilities=("internal",),
            )

            # 再把上下文交给工作流，只执行一次。
            result = run_with_progress(
                workflow=workflow,
                initial_state=initial_state,
                context=context,
                config=config,
            )

        # 按 thread_id 读取该会话最新保存的状态。
        snapshot = workflow.get_state(config)

        print(
            "会话编号：",
            config["configurable"]["thread_id"],
        )
        print(
            "保存的问题：",
            snapshot.values["normalized_question"],
        )
        print(
            "待执行节点：",
            snapshot.next,
        )

        print("整理后的问题：", result["normalized_question"])
        print("下一步计划：", result["search_plan"])

        search_result = result["search_result"]

        if search_result is None:
            print("本分支未执行知识检索。")
        else:
            print("检索状态：", search_result.message)

            # matches 是工具返回的知识片段列表。
            for match in search_result.matches:
                print(
                    "来源：",
                    match.document_title,
                    f"v{match.document_version}",
                )
                print("片段原文：", match.content)

        print("-" * 40)


if __name__ == "__main__":
    main()
