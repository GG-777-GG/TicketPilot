"""引文匹配的真实格式差异与内容篡改回归。"""

import pytest

from ticketpilot.agent.evidence import find_original_quote


FIRST = "换货申请应在签收后11个自然日内提交。"
SECOND = "超过11个自然日,应转人工复核。"


@pytest.mark.parametrize("line_break", ["\n", "\r\n", "\r", "\n\n"])
def test_merged_sentences_return_original_contiguous_text(line_break: str) -> None:
    original = FIRST + line_break + SECOND
    assert find_original_quote(FIRST + SECOND, "标题\n" + original + "\n尾注") == original


def test_added_sentence_break_returns_original_without_break() -> None:
    original = FIRST + SECOND
    assert find_original_quote(FIRST + "\n" + SECOND, original) == original


def test_exact_quote_preserves_all_original_formatting() -> None:
    original = "申请编号 A 11\n收件时间 10:00。"
    assert find_original_quote(original, "标题\n" + original) == original


@pytest.mark.parametrize(
    ("quote", "content"),
    [
        ((FIRST + SECOND).replace("11", "15"), FIRST + "\n" + SECOND),
        (FIRST + SECOND, FIRST + "\n但该情形不适用。\n" + SECOND),
        (SECOND + FIRST, FIRST + "\n" + SECOND),
        ("超过11天应转人工复核。", "超过11天不应转人工复核。"),
        ("应转人工复核。", "应转人工复核！"),
        ("编号A11", "编号 A 11"),
        ("期限11天。", "期限1\n1天。"),
        ("编号AB", "编号A\nB"),
        ("金额1.5元", "金额1.\n5元"),
        ("\n\r\n", FIRST),
    ],
)
def test_content_changes_are_still_rejected(quote: str, content: str) -> None:
    assert find_original_quote(quote, content) is None
