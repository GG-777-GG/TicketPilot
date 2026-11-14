"""验证 Agent 依赖按配置决定是否提供证据校验器。"""

import pytest

from ticketpilot.agent import dependencies as agent_dependencies


class FakeEvidenceVerifier:
    """测试专用校验器，不连接 Ollama。"""

    def verify(self, *, question: str, evidence: tuple[str, ...]) -> bool:
        return True


@pytest.mark.parametrize("enabled", [False, True])
def test_evidence_verifier_dependency_respects_feature_flag(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    """关闭时不创建对象；开启时才创建一个可传给 Loop 的对象。"""
    monkeypatch.setattr(
        agent_dependencies.settings,
        "enable_evidence_verification",
        enabled,
    )
    created: list[FakeEvidenceVerifier] = []

    def fake_verifier_factory() -> FakeEvidenceVerifier:
        verifier = FakeEvidenceVerifier()
        created.append(verifier)
        return verifier

    monkeypatch.setattr(
        agent_dependencies,
        "OllamaEvidenceVerifier",
        fake_verifier_factory,
        raising=False,
    )

    result = agent_dependencies.get_evidence_verifier()

    if enabled:
        assert result is created[0]
        assert len(created) == 1
    else:
        assert result is None
        assert created == []
