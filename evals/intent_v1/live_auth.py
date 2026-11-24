"""Read-only credential preflight; no inference, refresh, login or auth copying."""
from pathlib import Path
from typing import Any


DEFAULT_MODEL = 'anthropic/claude-haiku-5.5'
MODELS = (DEFAULT_MODEL, 'openai/gpt-6-luna')
JUDGE_MODEL = 'jev-1.14.0'
ACTOR_BASE = 'https://openrouter.ai/api/v1'
JUDGE_BASE = 'https://api.typesafe.ai/v1'


def resolve_credentials(home, *, allow_launch_env=False):
    import os
    from agent.secret_scope import (
        build_profile_secret_scope, set_secret_scope, reset_secret_scope, get_secret,
        current_secret_scope, current_secret_scope_home, serves_routed_profile,
        set_multiplex_context, reset_multiplex_context,
    )
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_cli.auth import read_credential_pool, looks_like_openrouter_key
    home = Path(home).resolve()
    bound = current_secret_scope()
    if bound is not None and current_secret_scope_home() != str(home):
        raise ValueError('credential home must match the existing secret scope')
    # Explicit standalone permission only: never import a gateway/routed task's
    # ambient credentials, nor widen an existing scope (even an empty one).
    launch = ({name: os.environ[name] for name in ('OPENROUTER_API_KEY', 'TYPESAFE_API_KEY')
               if os.environ.get(name)}
              if allow_launch_env and bound is None and not serves_routed_profile() else {})
    home_token = set_hermes_home_override(home)
    token = context_token = None
    try:
        scope = dict(bound) if bound is not None else build_profile_secret_scope(home)
        imported = [name for name in launch if name not in scope]
        for name in imported:
            scope[name] = launch[name]
        token = set_secret_scope(scope, profile_home=str(home))
        # No fallback to unrelated launch secrets, even when home == launch home.
        context_token = set_multiplex_context(True)
        # Avoid load_pool/resolve_runtime_provider: they may seed/persist pools or
        # rotate OAuth. Read the canonical profile-aware store (root fallback included).
        pool: Any = read_credential_pool('openrouter')
        key = get_secret('OPENROUTER_API_KEY') or ''
        source = (('authorized launch OPENROUTER_API_KEY' if 'OPENROUTER_API_KEY' in imported
                   else 'profile-scoped OPENROUTER_API_KEY') if key else None)
        if not looks_like_openrouter_key(key):
            key = ''
            for entry in sorted(pool, key=lambda e: e.get('priority', 0)):
                candidate = entry.get('access_token', '')
                if looks_like_openrouter_key(candidate):
                    key, source = candidate, 'read-only credential_pool:openrouter'
                    break
        if not key:
            legacy = get_secret('OPENAI_API_KEY') or ''
            if looks_like_openrouter_key(legacy) and not get_secret('OPENAI_BASE_URL'):
                key, source = legacy, 'legacy OpenRouter-shaped OPENAI_API_KEY'
        judge = get_secret('TYPESAFE_API_KEY') or ''
        return {'actor': key, 'judge': judge}, {
            'home': str(home), 'actor_source': source, 'pool_entries_inspected': len(pool),
            'launch_env_keys_imported': imported,
            'pool_note': 'Env-backed metadata/fingerprints are not usable credentials.',
            'external_sources': 'Only already-hydrated sources read; no external secret commands executed.'}
    finally:
        if context_token is not None:
            reset_multiplex_context(context_token)
        if token is not None:
            reset_secret_scope(token)
        reset_hermes_home_override(home_token)


def preflight(home, model=DEFAULT_MODEL, *, get=None, allow_launch_env=False):
    import httpx
    get = get or httpx.get
    credentials, report = resolve_credentials(home, allow_launch_env=allow_launch_env)
    report.update(mode='live-preflight-zero-inference', actor_model=model, judge_model=JUDGE_MODEL,
                  actor_route=ACTOR_BASE, judge_route=JUDGE_BASE, ready=False)
    for role, url in (('actor', ACTOR_BASE + '/key'), ('judge', JUDGE_BASE + '/models')):
        status: dict[str, Any] = {'credential_present': bool(credentials[role]), 'authenticated': False}
        if credentials[role]:
            try:
                response = get(url, headers={'Authorization': 'Bearer ' + credentials[role]},
                               timeout=15, follow_redirects=False, trust_env=False)
                status.update(http_status=response.status_code, authenticated=response.status_code == 200)
                if response.status_code == 200:
                    data = response.json()
                    if role == 'actor':
                        status['limit_remaining'] = data.get('data', {}).get('limit_remaining')
                    else:
                        status['listed_models'] = [m['name'] for m in data.get('models', [])]
                        status['pinned_model_note'] = 'Catalog may list aliases only; pinned version from docs.typesafe.ai/models.'
            except Exception as exc:
                status['error_type'] = type(exc).__name__
        report[role] = status
    try:
        response = get(ACTOR_BASE + '/models', timeout=20, follow_redirects=False, trust_env=False)
        if response.status_code != 200:
            raise ValueError('catalog unavailable')
        row = next(m for m in response.json()['data'] if m['id'] == model)
        if 'tools' not in row['supported_parameters']:
            raise ValueError('tools unsupported')
        report['catalog'] = {k: row.get(k) for k in ('id', 'canonical_slug', 'pricing', 'context_length', 'supported_parameters', 'reasoning')}

    except Exception as exc:
        report['catalog_error_type'] = type(exc).__name__
    report['ready'] = bool(report['actor']['authenticated'] and report['judge']['authenticated'] and report.get('catalog'))
    report['required_action'] = ([] if report['ready'] else [
        'Supply a usable OpenRouter credential in the selected profile if actor auth is missing/invalid.',
        'Supply TYPESAFE_API_KEY through the selected profile secret scope if judge auth is missing/invalid.',
        'A running dev gateway may have inherited process-only keys; do not copy its auth or scrape process secrets.'])
    return report, credentials
