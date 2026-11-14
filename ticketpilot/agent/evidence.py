"""核对模型引句，并取回对应的连续原文。"""


def _fold_sentence_breaks(value: str) -> tuple[str, list[int]]:
    """只忽略句末标点后的换行，同时记录字符在原文中的位置。"""
    characters: list[str] = []
    offsets: list[int] = []
    for offset, character in enumerate(value):
        # 不删除普通空格、数字之间的换行或其他文字。
        # 不包含英文句点，避免把小数点后的换行当作句子边界。
        if (
            character in "\r\n"
            and characters
            and characters[-1] in "。！？；!?;"
        ):
            continue
        characters.append(character)
        offsets.append(offset)
    return "".join(characters), offsets


def find_original_quote(quote: str, content: str) -> str | None:
    """允许句间换行差异，成功时返回原文切片，失败时返回 None。

    文字、数字、标点和顺序仍须一致；不能跳过正文或拼接不同片段。
    返回值保留原文换行，供后续证据判断和最终回答使用。
    """
    if not quote.strip():
        return None
    if quote in content:
        return quote

    folded_quote, _ = _fold_sentence_breaks(quote)
    folded_content, offsets = _fold_sentence_breaks(content)
    start = folded_content.find(folded_quote)
    if start < 0:
        return None

    # 从匹配位置映射回原文，返回真实的连续文本。
    end = start + len(folded_quote) - 1
    return content[offsets[start]:offsets[end] + 1]
