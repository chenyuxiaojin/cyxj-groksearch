import time
import grok_search.key_pool as kp


def setup_function():
    # 清理模块级状态，避免测试间串扰
    kp._state["keys"] = None
    kp._state["cycle"] = None
    kp._cooldown.clear()


def test_round_robin_cycles():
    keys = ["k1", "k2", "k3"]
    picks = [kp.pick_tavily_key(keys) for _ in range(4)]
    assert picks[0] != picks[1]
    assert set(picks[:3]) == {"k1", "k2", "k3"}
    assert picks[3] == picks[0]


def test_failover_sticks_to_first():
    keys = ["k1", "k2"]
    assert kp.pick_failover_key(keys) == "k1"
    assert kp.pick_failover_key(keys) == "k1"


def test_mark_failed_removes_from_pool():
    keys = ["k1", "k2"]
    kp.mark_key_failed("k1", cooldown_seconds=60)
    assert kp.pick_failover_key(keys) == "k2"
    for _ in range(4):
        assert kp.pick_tavily_key(keys) == "k2"


def test_all_cooldown_returns_none():
    keys = ["k1"]
    kp.mark_key_failed("k1", cooldown_seconds=60)
    assert kp.pick_tavily_key(keys) is None
    assert kp.pick_failover_key(keys) is None


def test_cooldown_status_reports():
    keys = ["k1", "k2"]
    kp.mark_key_failed("k1", cooldown_seconds=60)
    status = kp.cooldown_status(keys)
    assert status["total"] == 2
    assert status["active"] == 1
    assert len(status["cooling_down"]) == 1
