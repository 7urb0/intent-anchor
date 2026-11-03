"""全局测试夹具：确定性时钟 + 真实请求熔断 + 网络封禁（对齐业界做法）。"""

import socket

import pytest

from goal_harness.testing import block_real_requests


@pytest.fixture(autouse=True)
def _forbid_real_model_requests(request: pytest.FixtureRequest):
    """所有测试默认熔断真实模型请求，防 CI 误打 API（pydantic-ai 同款）。

    `@pytest.mark.live` 是唯一例外——它们的目的就是真实调用。
    """
    if request.node.get_closest_marker("live"):
        yield                       # live 测试放行
        return
    with block_real_requests():
        yield


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """兜底：默认禁止任何 socket 连接；`@pytest.mark.live` 测试放行。"""
    if request.node.get_closest_marker("live"):
        yield
        return

    def blocked(*args, **kwargs):
        raise RuntimeError("测试禁止网络访问（如需真实调用请加 @pytest.mark.live）")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    yield


@pytest.fixture
def deterministic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """固定时间源：证据层 ts 恒定，轨迹可做快照断言。

    思路同 LangGraph 的 deterministic_uuids 夹具——把不确定性钉死。
    """
    import goal_harness.goal_harness as core

    counter = {"n": 0}

    def fake_time() -> float:
        counter["n"] += 1
        return 1_700_000_000.0 + counter["n"]

    monkeypatch.setattr(core.time, "time", fake_time)
