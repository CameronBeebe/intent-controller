"""Two independent writers cannot both adopt the same intent revision."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from agent.intent_attractor import IntentStore
from persistence_assembly import open_persistence, profile_scope


def test_two_handles_fence_creation_and_revision_update(tmp_path, monkeypatch):
    scope = profile_scope(home=tmp_path)
    with open_persistence(scope) as a, open_persistence(scope) as b, ThreadPoolExecutor(2) as pool:
        stores = [IntentStore(handle, 'same-session') for handle in (a, b)]
        for revision in (0, 1):
            ready = Barrier(2)
            get = a.state_meta.get

            def concurrent_read(key):
                value = get(key)
                ready.wait(timeout=5)  # Both writers observed the same old value.
                return value

            with monkeypatch.context() as m:
                m.setattr(a.state_meta, 'get', concurrent_read)
                m.setattr(b.state_meta, 'get', concurrent_read)
                winners = list(pool.map(lambda i: stores[i].save({'revision': revision + 1, 'author': i}, revision), (0, 1)))
            assert winners.count(True) == 1
            assert stores[0].read() == stores[1].read() == {
                'revision': revision + 1, 'author': winners.index(True)}
