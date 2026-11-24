"""Actual provider costs from existing response journals; no reservation ledger."""
import json
from decimal import Decimal
from pathlib import Path


class LiveStop(BaseException):
    """Bypass retries on transport, storage, auth or accounting integrity failure."""


class ResourceStop(LiveStop):
    """Censored/incomplete cell, not an infrastructure failure."""


def money(value):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError('invalid provider cost/budget')
    return number


def actual_spend(root):
    """Read retained live cells, dedupe provider IDs, never turn unknown into $0.

    The root is the existing authorized experiment artifact tree, reused across
    runs. Copied reports are deduplicated by provider response ID. No writes.
    """
    known, unknown = {}, set()
    for plan_path in sorted(Path(root).rglob('cell.json')):
        plan = json.loads(plan_path.read_text())
        if 'actor_budget_usd' not in plan:  # scripted offline cells are not bills
            continue
        cell = plan_path.parent
        responses = cell / 'actor_responses.jsonl'
        indexes = set()
        if responses.exists():
            for line in responses.read_text().splitlines():
                row = json.loads(line)
                indexes.add(row['index'])
                data = row.get('response') or {}
                usage = data.get('usage') or {}
                key = data.get('id') or f'{cell}:{row["index"]}'
                if usage.get('cost') is None:
                    unknown.add(key)
                    continue
                if not isinstance(data.get('id'), str) or not data['id']:
                    raise LiveStop('provider cost without response ID')
                cost = money(usage['cost'])
                if key in known and known[key] != cost:
                    raise LiveStop('conflicting provider costs for response ID')
                known[key] = cost
        requests = cell / 'actor_requests.jsonl'
        if requests.exists():
            unknown.update(f'{cell}:{row["index"]}' for line in requests.read_text().splitlines()
                           if (row := json.loads(line))['index'] not in indexes)
    unknown.difference_update(known)
    return {'provider_reported_usd': str(sum(known.values(), Decimal(0))) if known else None,
            'known_calls': len(known), 'unknown_calls': len(unknown)}


def check_spend(root, cap):
    cap = money(cap)
    if not 0 < cap <= 5:
        raise ValueError('actor budget must be positive and at most $5')
    try:
        spend = actual_spend(root)
    except Exception as exc:
        # Do not let the agent's ordinary exception/retry ladder retry corrupted
        # accounting or storage reads. A missing cost is different from bad data.
        raise LiveStop('actor accounting/storage integrity failure') from exc
    if spend['provider_reported_usd'] is not None and money(spend['provider_reported_usd']) >= cap:
        raise ResourceStop('actor actual spending limit reached')
    return spend
