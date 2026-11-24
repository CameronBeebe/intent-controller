"""Characterize existing continuation boundaries; no new runtime behavior.

These deliberately passing probes document the gap before an intent-supervisor
implementation. Model transport is replaced; state management runs for real.
"""
from unittest.mock import patch

import pytest


@pytest.fixture
def goals_home(tmp_path, monkeypatch):
    from hermes_cli import goals
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    goals._DB_CACHE.clear()
    yield goals
    goals._DB_CACHE.clear()


def test_ordinary_incomplete_response_does_not_activate_goal(goals_home):
    manager = goals_home.GoalManager("intent-audit-unset")
    with patch.object(goals_home, "judge_goal") as judge:
        decision = manager.evaluate_after_turn(
            "I wrote the implementation. Next I could run the tests if you want."
        )
    assert decision["verdict"] == "inactive"
    assert decision["should_continue"] is False
    assert manager.state is None
    judge.assert_not_called()


def test_explicit_goal_activates_judging_and_continuation(goals_home):
    manager = goals_home.GoalManager("intent-audit-active")
    manager.set("Implement the feature and verify it")
    with patch.object(goals_home, "judge_goal", return_value=(
        "continue", "Tests remain", False, None, False
    )) as judge:
        decision = manager.evaluate_after_turn("Implementation exists; tests remain.")
    judge.assert_called_once()
    assert decision["should_continue"] is True
    assert "Implement the feature and verify it" in decision["continuation_prompt"]
    restored = goals_home.GoalManager("intent-audit-active")
    assert restored.is_active()
    assert restored.state.turns_used == 1


def test_draft_receives_only_objective_not_conversation(goals_home):
    with patch.object(goals_home, "_call_goal_judge_llm", return_value=(
        '{"outcome":"Verified feature","verification":"Run the test suite"}'
    )) as transport:
        contract = goals_home.draft_contract("Implement the feature", timeout=1)
    assert contract.outcome == "Verified feature"
    assert transport.call_args.args[2] == "Objective:\nImplement the feature"


def test_judge_payload_has_final_response_not_tool_history(goals_home):
    with patch.object(goals_home, "_call_goal_judge_llm", return_value=(
        '{"verdict":"continue","reason":"Verification remains"}'
    )) as transport:
        verdict, *_ = goals_home.judge_goal(
            "Implement and test", "I wrote it but have not run it", timeout=1,
            contract=goals_home.GoalContract(verification="Passing test output"),
        )
    payload = transport.call_args.args[2]
    assert verdict == "continue"
    assert "I wrote it but have not run it" in payload
    assert "Passing test output" in payload
    # The public judge boundary cannot accept these missing context surfaces.
    import inspect
    parameters = inspect.signature(goals_home.judge_goal).parameters
    assert "messages" not in parameters
    assert "tools" not in parameters
    assert "system_prompt" not in parameters


def test_initial_kickoff_omits_inline_contract(goals_home):
    from hermes_cli.goal_command import dispatch_goal_command
    manager = goals_home.GoalManager("intent-audit-kickoff")
    result = dispatch_goal_command(
        manager, "Ship API\nverify: pytest test_api.py\nboundaries: only src/api",
        authorize_gate=lambda: None,
    )
    assert manager.state.contract.verification == "pytest test_api.py"
    assert result.prompt == "Ship API"
    assert "only src/api" not in result.prompt
    assert "only src/api" in manager.next_continuation_prompt()


def test_unwait_does_not_supply_kickoff(goals_home):
    import time
    from hermes_cli.goal_command import dispatch_goal_command
    manager = goals_home.GoalManager("intent-audit-unwait")
    manager.set("Finish the work")
    manager.state.waiting_until = time.time() + 60
    result = dispatch_goal_command(manager, "unwait", authorize_gate=lambda: None)
    assert result.prompt is None
    assert manager.state.waiting_until == 0
    assert manager.is_active()


def test_wait_can_park_at_exhausted_goal_budget(goals_home):
    manager = goals_home.GoalManager("intent-audit-wait-budget", default_max_turns=1)
    manager.set("Finish the work")
    with patch.object(goals_home, "judge_goal", return_value=(
        "wait", "Background work pending", False, {"seconds": 60}, False
    )):
        decision = manager.evaluate_after_turn("Waiting for the build")
    assert manager.state.turns_used == manager.state.max_turns == 1
    assert manager.state.status == "active"
    assert decision["should_continue"] is False


@pytest.mark.parametrize("text,detected", [
    ("Let me now run the tests.", True),
    ("I will run the tests.", False),
    ("I can implement that next if you want.", False),
    ("Finished the investigation. Next steps: implement the fix.", False),
    ("Context. " * 60 + "Let me now run the tests.", False),
])
def test_trailing_action_guard_is_not_intent_completion(text, detected):
    from agent.agent_runtime_helpers import trailing_continue_intent
    assert trailing_continue_intent(text) is detected
