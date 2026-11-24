"""Frequent intent refreshes update the control surface without chat noise."""
from types import SimpleNamespace
from unittest.mock import Mock


def test_repeated_updates_refresh_surface_without_diagnostic_spam():
    from agent.intent_attractor import notify
    callback, diagnostic = Mock(), Mock()
    agent = SimpleNamespace(_call_callback=callback, _emit_diagnostic_status=diagnostic)
    for _ in range(4):
        notify(agent, {'revision': 1, 'decision': 'CONTINUE_NOW'})
    assert callback.call_count == 4
    callback.assert_called_with('status_callback', 'intent.update', '', origin='intent_attractor')
    diagnostic.assert_not_called()
