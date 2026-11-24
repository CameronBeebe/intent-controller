"""The opt-in actor receives the corrective loop in real prompt assembly, not just docs."""
import pytest

from tests.agent.test_intent_attractor import harness


@pytest.mark.parametrize('enabled', [True, False])
def test_corrective_intent_guidance_is_gated_in_assembled_prompt(harness, enabled):
    from agent.intent_feedback import INTENT_GUIDANCE
    from agent.intent_protocol import surface_tools
    from agent.system_prompt import build_system_prompt, build_system_prompt_parts

    agent = harness.agent
    agent._intent_guidance_enabled = enabled
    parts = build_system_prompt_parts(agent)
    prompt = build_system_prompt(agent)
    names = {tool['function']['name'] for tool in surface_tools(agent, [])}
    assert ('intent_control' in names) is enabled
    if not enabled:
        assert INTENT_GUIDANCE not in prompt
        assert '## Intent as telos' not in prompt
        return

    assert INTENT_GUIDANCE in parts['stable']
    assert INTENT_GUIDANCE in prompt
    for instruction in (
        'estimate current state from evidence',
        'actionable remaining discrepancies',
        'observe the result and correct',
        'verified completion',
        'genuine blocker',
        'user stop',
        'not activity for its own sake',
        'without weakening intent or shifting burden to the user',
        'Kolmogorov complexity is an ideal, not a computable metric',
        'manual fields/pins',
        'provenance and revision fences',
        'Jev advice cannot veto interpretation',
        'not permission',
    ):
        assert instruction in prompt
    # Evolving working state is delivered append-only, never rendered into this stable block.
    agent._intent_state = {'revision': 99, 'fields': {'outcome': 'a changed working interpretation'}}
    assert build_system_prompt_parts(agent)['stable'] == parts['stable']
    assert 'a changed working interpretation' not in prompt
    assert not harness.payloads and not harness.fidelity_payloads
