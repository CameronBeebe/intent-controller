"""Standalone launch credentials are opt-in, scoped and never persisted."""
import json
import os
from pathlib import Path

import pytest

from agent import secret_scope as ss
from evals.intent_v1.live_auth import resolve_credentials
from hermes_constants import get_hermes_home_override


@pytest.mark.parametrize('profile_keys', [False, True])
def test_authorized_launch_keys_fill_only_missing_profile_keys(tmp_path, monkeypatch, profile_keys):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'launch'))
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-v1-synthetic-launch')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'synthetic-launch-judge')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-or-v1-unrelated')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'synthetic-unrelated')
    home = tmp_path / 'selected'
    home.mkdir()
    if profile_keys:
        (home / '.env').write_text('OPENROUTER_API_KEY=sk-or-v1-profile\nTYPESAFE_API_KEY=profile-judge\n')
    before = {p.name: p.read_bytes() for p in home.iterdir()}
    environment = dict(os.environ)
    original_home = get_hermes_home_override()
    token = ss.set_secret_scope(None)
    try:
        credentials, report = resolve_credentials(home, allow_launch_env=True)
        assert credentials == ({'actor': 'sk-or-v1-profile', 'judge': 'profile-judge'} if profile_keys else
                               {'actor': 'sk-or-v1-synthetic-launch', 'judge': 'synthetic-launch-judge'})
        assert ss.current_secret_scope() is None
        assert report['launch_env_keys_imported'] == ([] if profile_keys else
                                                    ['OPENROUTER_API_KEY', 'TYPESAFE_API_KEY'])
        assert get_hermes_home_override() == original_home
        assert dict(os.environ) == environment
        assert {p.name: p.read_bytes() for p in home.iterdir()} == before
        assert all(value not in json.dumps(report) for value in credentials.values())
    finally:
        ss.reset_secret_scope(token)


@pytest.mark.parametrize('scope', [None, {}, {'TYPESAFE_API_KEY': 'bound-judge'}])
def test_launch_keys_do_not_bypass_scope_or_implicit_authorization(tmp_path, monkeypatch, scope):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'launch'))
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-v1-synthetic-launch')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'synthetic-launch-judge')
    home = tmp_path / 'selected'
    home.mkdir()
    token = ss.set_secret_scope(scope, profile_home=str(home))
    try:
        credentials, _ = resolve_credentials(home, allow_launch_env=scope is not None)
        assert credentials == {'actor': '', 'judge': (scope or {}).get('TYPESAFE_API_KEY', '')}
        assert ss.current_secret_scope() is scope
    finally:
        ss.reset_secret_scope(token)


@pytest.mark.parametrize('multiplex', [False, True])
def test_unrelated_launch_keys_and_multiplex_env_stay_excluded(tmp_path, monkeypatch, multiplex):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-or-v1-unrelated')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'synthetic-judge')
    token = ss.set_secret_scope(None)
    context = ss.set_multiplex_context(multiplex)
    try:
        credentials, report = resolve_credentials(tmp_path, allow_launch_env=True)
        assert credentials == {'actor': '', 'judge': '' if multiplex else 'synthetic-judge'}
        assert report['launch_env_keys_imported'] == ([] if multiplex else ['TYPESAFE_API_KEY'])
        assert ss.is_multiplex_active() is multiplex
    finally:
        ss.reset_multiplex_context(context)
        ss.reset_secret_scope(token)
