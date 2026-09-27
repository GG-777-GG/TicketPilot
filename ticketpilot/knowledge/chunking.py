"""
实现第一阶段的字符级文档切块器。

文件职责：
- 把 CleanedDocument 的正文拆成多个可检索片段。
- 尽量在段落、换行或句子结尾处截断。
- 为相邻片段保留重叠内容。
- 为每个片段生成稳定的 SHA-256 指纹。
- 记录片段在清洗后正文中的开始和结束位置。

正常流程：
CleanedDocument
→ 从正文开头开始读取
→ 最多读取 800 个字符
→ 尝试寻找合适的段落或句子边界
→ 生成 TextChunk
→ 向前移动，同时保留 100 个字符的重叠
→ 重复执行直到正文结束

异常流程：
- 最大长度小于或等于 0，抛出 DocumentChunkingError。
- 重叠长度小于 0，抛出 DocumentChunkingError。
- 重叠长度大于或等于最大长度，抛出异常。
- 清洗后正文为空，抛出异常。
- 最终没有生成任何有效片段，抛出异常。

第一阶段临时实现：
- 使用字符数量控制片段大小。
- 使用简单标点和换行寻找边界。
- 不识别标题、表格、代码块和 token。

第二阶段重构：
- 改成 token 数量控制。
- 增加 DocumentBlock。
- 根据标题、段落、表格和代码结构切块。
- 增加父子分块和语义切块。

完整调用链：
CleanedDocument
→ chunk_document()
→ list[TextChunk]
→ Embedding 模型
→ KnowledgeChunkRecord
→ PostgreSQL
"""

# dataclass 用来定义只负责携带数据的片段对象。
from dataclasses import dataclass

# sha256 用来计算每个知识片段的内容指纹。
from hashlib import sha256


# 导入清洗完成后的文档对象。
from ticketpilot.knowledge.documents import (
    CleanedDocument,
)


# 第一阶段每个知识片段最多包含 800 个字符。
DEFAULT_MAX_CHARACTERS = 800


# 相邻知识片段默认重叠 100 个字符。
DEFAULT_OVERLAP_CHARACTERS = 100


# 只有边界出现在片段后半部分时才使用它。
# 避免因为开头很早出现句号而产生特别短的片段。
_MINIMUM_BOUNDARY_RATIO = 0.5


# 按优先级排列允许截断的位置。
#
# 优先使用空白段落，其次是普通换行，
# 然后是中文和英文句子结尾。
_PREFERRED_SEPARATORS = (
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    ". ",
    "! ",
    "? ",
    "; ",
)
# 当前字符级切块算法的版本。
# 切块规则改变后，需要修改这个版本。
CHUNKER_VERSION = "character-v1"

# 表示文本切块参数无效，或者没有可切分内容。
class DocumentChunkingError(ValueError):
    """文档无法生成有效的知识片段。"""


# 表示一段尚未保存到数据库的知识片段。
@dataclass(
    frozen=True,
    slots=True,
)
class TextChunk:
    """保存文本片段及其来源位置和内容指纹。"""

    # 片段在当前文档中的顺序，从 0 开始。
    chunk_index: int

    # 片段的实际文字内容。
    content: str

    # 片段内容的 SHA-256 指纹。
    content_hash: str

    # 片段在 cleaned_content 中的开始位置。
    # 这个位置包含在片段范围内。
    start_offset: int

    # 片段在 cleaned_content 中的结束位置。
    # 这个位置不包含在片段范围内。
    end_offset: int

    # 提供片段实际字符数量。
    @property
    def character_count(self) -> int:
        # 字符数量直接由片段内容长度得到。
        return len(
            self.content
        )


# 在允许的最大范围内寻找更自然的结束位置。
def _find_preferred_end(
    *,
    content: str,
    start_offset: int,
    hard_end_offset: int,
    max_characters: int,
) -> int:
    # 如果已经到达整篇正文结尾，就不需要继续寻找边界。
    if hard_end_offset >= len(
        content
    ):
        return len(
            content
        )

    # 取得当前最多允许读取的文字窗口。
    search_window = content[
        start_offset:hard_end_offset
    ]

    # 只在窗口后半部分寻找边界。
    # 例如最大长度为 800，就从第 400 个字符之后开始寻找。
    minimum_boundary_index = int(
        max_characters
        * _MINIMUM_BOUNDARY_RATIO
    )

    # 按照段落、换行、句子的优先级依次查找。
    for separator in _PREFERRED_SEPARATORS:
        # rfind() 返回该分隔符在窗口中最后一次出现的位置。
        separator_index = search_window.rfind(
            separator,
            minimum_boundary_index,
        )

        # -1 表示当前窗口中没有找到这个分隔符。
        if separator_index == -1:
            continue

        # 把窗口内部的位置转换成整篇正文中的位置。
        #
        # 加上 len(separator)，让标点符号保留在前一个片段中。
        return (
            start_offset
            + separator_index
            + len(separator)
        )

    # 找不到自然边界时，只能在最大字符位置截断。
    return hard_end_offset


# 删除片段范围开头和结尾处的空白字符。
def _trim_chunk_bounds(
    *,
    content: str,
    start_offset: int,
    end_offset: int,
) -> tuple[int, int]:
    # 如果片段开头是空格或换行，就向后移动开始位置。
    while (
        start_offset < end_offset
        and content[start_offset].isspace()
    ):
        start_offset += 1

    # 如果片段结尾是空格或换行，就向前移动结束位置。
    while (
        end_offset > start_offset
        and content[end_offset - 1].isspace()
    ):
        end_offset -= 1

    # 返回修正后的左闭右开范围。
    return (
        start_offset,
        end_offset,
    )


# 计算一段片段文字的内容指纹。
def _build_chunk_hash(
    content: str,
) -> str:
    # 先将字符串转换成 UTF-8 字节，
    # 再生成十六进制 SHA-256 指纹。
    return sha256(
        content.encode(
            "utf-8"
        )
    ).hexdigest()


# 把一篇清洗完成的文档拆成多个知识片段。
def chunk_document(
    document: CleanedDocument,
    *,
    max_characters: int = (
        DEFAULT_MAX_CHARACTERS
    ),
    overlap_characters: int = (
        DEFAULT_OVERLAP_CHARACTERS
    ),
) -> list[TextChunk]:
    """把清洗后的正文拆成带重叠的知识片段。"""

    # 最大字符数量必须是正整数。
    if max_characters <= 0:
        raise DocumentChunkingError(
            "片段最大字符数必须大于 0",
        )

    # 重叠字符数量不能是负数。
    if overlap_characters < 0:
        raise DocumentChunkingError(
            "片段重叠字符数不能小于 0",
        )

    # 重叠长度必须小于片段最大长度。
    # 否则开始位置无法正常向前移动。
    if overlap_characters >= max_characters:
        raise DocumentChunkingError(
            "片段重叠字符数必须小于最大字符数",
        )

    # 取得已经完成清洗的正文。
    content = document.cleaned_content

    # 即使有人手动构造了无效 CleanedDocument，
    # 切块器也要保护自己的业务边界。
    if not content.strip():
        raise DocumentChunkingError(
            "清洗后的文档正文不能为空",
        )

    # 保存最终生成的所有片段。
    chunks: list[TextChunk] = []

    # 从正文第 0 个字符开始处理。
    current_start_offset = 0

    # 保存正文总字符数量，避免循环中重复计算。
    content_length = len(
        content
    )

    # 只要开始位置还没有到达正文结尾，就继续切块。
    while current_start_offset < content_length:
        # 计算当前片段绝对不能超过的结束位置。
        hard_end_offset = min(
            current_start_offset
            + max_characters,
            content_length,
        )

        # 尝试在最大范围内寻找更自然的结束位置。
        preferred_end_offset = (
            _find_preferred_end(
                content=content,
                start_offset=(
                    current_start_offset
                ),
                hard_end_offset=(
                    hard_end_offset
                ),
                max_characters=(
                    max_characters
                ),
            )
        )

        # 删除当前片段范围两端的空白字符。
        (
            chunk_start_offset,
            chunk_end_offset,
        ) = _trim_chunk_bounds(
            content=content,
            start_offset=(
                current_start_offset
            ),
            end_offset=(
                preferred_end_offset
            ),
        )

        # 修正范围后仍有实际内容时才生成片段。
        if (
            chunk_start_offset
            < chunk_end_offset
        ):
            # 根据左闭右开位置提取片段正文。
            chunk_content = content[
                chunk_start_offset:
                chunk_end_offset
            ]

            # 创建一个不可变的 TextChunk。
            chunks.append(
                TextChunk(
                    # 使用当前列表长度作为从 0 开始的顺序。
                    chunk_index=len(
                        chunks
                    ),

                    # 保存片段实际内容。
                    content=chunk_content,

                    # 为片段生成独立内容指纹。
                    content_hash=(
                        _build_chunk_hash(
                            chunk_content
                        )
                    ),

                    # 保存片段在清洗后正文中的开始位置。
                    start_offset=(
                        chunk_start_offset
                    ),

                    # 保存片段结束位置。
                    end_offset=(
                        chunk_end_offset
                    ),
                )
            )

        # hard_end_offset 已经到达全文结尾，
        # 说明这是最后一个片段。
        if hard_end_offset >= content_length:
            break

        # 下一个片段从当前片段结尾向前退回指定重叠长度。
        next_start_offset = (
            chunk_end_offset
            - overlap_characters
        )

        # 无论边界和空白如何变化，
        # 下一轮都必须至少前进一个字符，避免无限循环。
        current_start_offset = max(
            next_start_offset,
            current_start_offset + 1,
        )

    # 理论上有效正文至少会生成一个片段。
    # 这里继续防御意外情况。
    if not chunks:
        raise DocumentChunkingError(
            "文档没有生成任何有效片段",
        )

    # 返回按 chunk_index 排列的全部片段。
    return chunks