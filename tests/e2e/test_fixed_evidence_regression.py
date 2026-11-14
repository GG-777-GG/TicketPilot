"""固定的证据校验回归集：使用正式校验器，不复制生产判断逻辑。

这里只测给定证据后的可回答性，不代表完整 RAG 正确率或权限安全性。
普通测试默认跳过，真实运行需显式启用环境变量。
"""

import json
import os
from time import perf_counter

import pytest

from tests.e2e.test_evidence_answerability_eval import EVIDENCE_CASES
from ticketpilot.agent.verification import OllamaEvidenceVerifier
from ticketpilot.core.config import settings


# 复用已有的 22 道题，避免把原来的实验数据再抄一份。
# 新增学习笔记换问法与传真干扰；标准标签只存在于测试中。
FIXED_CASES = EVIDENCE_CASES + [
    pytest.param(
        "完成新功能的第一步是什么？",
        "先写业务流程和职责边界,明确输入、输出、正常与异常路径。",
        True,
        id="study-first-step",
    ),
    pytest.param(
        "动手开发前应先想清楚哪些事情？",
        "先写业务流程和职责边界,明确输入、输出、正常与异常路径。",
        True,
        id="study-first-step-paraphrase",
    ),
    pytest.param(
        "维修人员的手机号码是多少？",
        "维修合同。乙方传真：0757-86688195。",
        False,
        id="fax-is-not-mobile",
    ),
]


@pytest.fixture(scope="module")
def score_rows(tmp_path_factory):
    """汇总每题结果；报告保存在 pytest 临时目录，不写入业务数据库。"""
    if os.environ.get("TICKETPILOT_RUN_LIVE_EVAL") != "1":
        pytest.skip("设置 TICKETPILOT_RUN_LIVE_EVAL=1 后运行")
    rows = []
    yield rows
    report = {
        "scope": "evidence_verifier_only",
        "model": settings.ollama_model,
        "cases": rows,
    }
    # 异常也占分母，不能把服务失败剔除后虚增通过率。
    for label, expected in (("answerable", True), ("unanswerable", False)):
        group = [row for row in rows if row["expected"] is expected]
        report[label] = {
            "passed": sum(row["passed"] for row in group),
            "total": len(group),
        }
    report["errors"] = sum(row["error"] is not None for row in rows)
    report["total_seconds"] = sum(row["seconds"] for row in rows)
    path = tmp_path_factory.mktemp("evidence-eval") / "results.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n证据校验评测报告：{path}", flush=True)
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False), flush=True)


@pytest.mark.e2e
@pytest.mark.ollama
@pytest.mark.parametrize(("question", "evidence", "expected"), FIXED_CASES)
def test_fixed_evidence_regression(
    question: str,  # 当前题目，不对模型透露预期标签。
    evidence: str,  # 固定原文，排除数据库内容变化带来的干扰。
    expected: bool,  # 人工标注：原文是否包含所问事实。
    score_rows,  # 本轮评测共享的记录列表。
    request,  # pytest 提供的当前用例信息，用于记录稳定的用例 ID。
) -> None:
    started = perf_counter()
    row = {
        "id": request.node.callspec.id,
        "expected": expected,
        "actual": None,
        "passed": False,
        "error": None,
    }
    try:
        row["actual"] = OllamaEvidenceVerifier().verify(
            question=question,
            evidence=(evidence,),
        )
        row["passed"] = row["actual"] is expected
    except Exception as error:
        # 记录异常类型后重新抛出，不能把服务错误当作正常拒答。
        row["error"] = type(error).__name__
        raise
    finally:
        row["seconds"] = round(perf_counter() - started, 3)
        score_rows.append(row)
    assert row["passed"], f"预期={expected}，实际={row['actual']}"
