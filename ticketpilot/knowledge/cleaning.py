"""
实现第一阶段的基础文档清洗。

文件职责：
- 规范化文档标题和正文。
- 删除会干扰检索的不可见字符。
- 统一空格、换行和空白段落。
- 为清洗后的正文计算 SHA-256 内容指纹。
- 保留清洗前的原始正文。

正常流程：
RawDocument
→ Unicode 规范化
→ 换行符规范化
→ 删除控制字符
→ 空白规范化
→ 验证清洗结果
→ 计算内容指纹
→ CleanedDocument

异常流程：
- 清洗后标题为空，抛出 DocumentCleaningError。
- 清洗后正文为空，抛出 DocumentCleaningError。
- 异常发生时不会生成 CleanedDocument，也不会写入数据库。

第一阶段暂不处理：
- PDF 重复页眉和页脚
- OCR 错别字
- HTML 广告和导航栏
- 表格结构
- 代码块缩进
- 页码与原文位置映射

这些功能会在第二阶段加入基于 DocumentBlock 的清洗流水线。

完整调用链：
API Schema
→ RawDocument
→ clean_document()
→ CleanedDocument
→ 文本切块器
→ Embedding 模型
→ PostgreSQL
"""

# hashlib 提供稳定的 SHA-256 内容指纹算法。
from hashlib import sha256

# re 用来执行正则表达式文本清洗。
import re

# unicodedata 用来统一全角、半角等 Unicode 表示。
import unicodedata

# 导入清洗前和清洗后的内部文档对象。
from ticketpilot.knowledge.documents import (
    CleanedDocument,
    RawDocument,
)


# 当前基础清洗规则的版本。
# 以后清洗规则发生变化时，需要修改这个版本。
CLEANER_VERSION = "basic-v1"


# 匹配不应该进入知识库的控制字符和不可见字符。
#
# 保留了换行符 \n，因为段落结构对后续切块很重要。
_CONTROL_CHARACTERS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"
    r"\u200b\u200c\u200d\ufeff]"
)


# 匹配同一行中连续出现的普通空格和制表符。
_HORIZONTAL_WHITESPACE = re.compile(
    r"[ \t]+"
)


# 匹配三个或更多连续换行。
# 最终最多保留一个空白行，也就是两个换行符。
_EXCESSIVE_NEWLINES = re.compile(
    r"\n{3,}"
)


# 表示文档经过清洗后仍然不符合入库要求。
class DocumentCleaningError(ValueError):
    """文档没有可以进入知识库的有效内容。"""


# 对一段普通文本执行基础规范化。
def _normalize_text(
    value: str,
) -> str:
    # 使用 NFKC 统一部分全角、半角和兼容字符。
    normalized_value = unicodedata.normalize(
        "NFKC",
        value,
    )

    # 把 Windows 的 \r\n 和旧系统的 \r
    # 全部转换成统一的 Linux 风格 \n。
    normalized_value = normalized_value.replace(
        "\r\n",
        "\n",
    ).replace(
        "\r",
        "\n",
    )

    # 删除空字符、零宽字符等不可见控制字符。
    normalized_value = _CONTROL_CHARACTERS.sub(
        "",
        normalized_value,
    )

    # 一行一行处理，避免直接破坏段落换行。
    normalized_lines = []

    # 遍历正文中的每一行。
    for line in normalized_value.split(
        "\n"
    ):
        # 把一行中连续的空格和 Tab 合并成一个空格。
        normalized_line = (
            _HORIZONTAL_WHITESPACE.sub(
                " ",
                line,
            )
        )

        # 删除这一行开头和结尾的空格。
        normalized_lines.append(
            normalized_line.strip()
        )

    # 使用统一的换行符重新组合所有行。
    normalized_value = "\n".join(
        normalized_lines
    )

    # 三个或更多连续换行压缩成两个换行。
    # 这样会保留段落之间的一个空白行。
    normalized_value = (
        _EXCESSIVE_NEWLINES.sub(
            "\n\n",
            normalized_value,
        )
    )

    # 删除整篇文本最前面和最后面的空白。
    return normalized_value.strip()


# 清洗文档标题。
def _normalize_title(
    title: str,
) -> str:
    # 先复用普通文本的基础规范化。
    normalized_title = _normalize_text(
        title
    )

    # 标题不应该包含换行，所以把多行连接成一行。
    normalized_title = " ".join(
        normalized_title.splitlines()
    )

    # 再次合并连接过程中可能产生的连续空格。
    return _HORIZONTAL_WHITESPACE.sub(
        " ",
        normalized_title,
    ).strip()


# 清洗一篇原始文档并返回新的清洗结果。
def clean_document(
    document: RawDocument,
) -> CleanedDocument:
    """清洗原始文档并生成稳定的内容指纹。"""

    # 清洗文档标题。
    cleaned_title = _normalize_title(
        document.title
    )

    # 标题清洗后为空，说明文档无法正常展示和引用。
    if not cleaned_title:
        raise DocumentCleaningError(
            "文档标题不能为空",
        )

    # 清洗文档正文。
    cleaned_content = _normalize_text(
        document.content
    )

    # 正文清洗后为空，说明没有可以切块和检索的内容。
    if not cleaned_content:
        raise DocumentCleaningError(
            "文档正文不能为空",
        )

    # 将清洗后的正文编码成 UTF-8 字节。
    cleaned_content_bytes = (
        cleaned_content.encode(
            "utf-8"
        )
    )

    # 计算清洗后正文的 SHA-256 指纹。
    # hexdigest() 返回便于存储和比较的十六进制字符串。
    content_hash = sha256(
        cleaned_content_bytes
    ).hexdigest()

    # 不修改原来的 RawDocument，
    # 而是返回一个新的 CleanedDocument。
    return CleanedDocument(
        # 使用清洗后的标题。
        title=cleaned_title,

        # 原始正文保持不变，方便以后重新清洗。
        raw_content=document.content,

        # 后续切块使用清洗后的正文。
        cleaned_content=cleaned_content,

        # 保留原来的文档来源类型。
        source_type=document.source_type,

        # 保留文件名或网页地址等来源信息。
        source_uri=document.source_uri,

        # 保存本次计算的内容指纹。
        content_hash=content_hash,

        # 记录生成该结果的清洗器版本。
        cleaner_version=CLEANER_VERSION,
    )