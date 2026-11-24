"""Stdlib report tests; synthetic records here test parsing, not model efficacy."""
import json
from pathlib import Path
import tempfile
import unittest


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def save(self, name, value):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding='utf-8')

    def test_evaluator_is_separate_from_feature_jev_and_original_verdict(self):
        from evals.intent_v1 import report
        self.save('plan.json', {'mode': 'live', 'cells': [{'case': 'topic_swap', 'arm': 'B'}]})
        self.save('topic_swap-B/result.json', {'mode': 'live', 'passed': False,
            'harness_valid': True, 'behavioral_contract_passed': False, 'judge_calls': 0,
            'evaluator_calls': 1, 'evaluator': {'verdict': 'fail', 'version': 'semantic-test',
                                             'provenance': 'evaluator_provenance.json'}})
        self.save('topic_swap-B/evaluator_responses.jsonl', {'index': 1, 'role': 'evaluator',
            'response': {'usage': {'input_tokens': 123, 'output_tokens': 0}}, 'wall_seconds': .1})
        data = report.load_run(self.root)
        self.assertEqual(data['aggregate']['evaluator_verdicts'], {'fail': 1})
        self.assertEqual(data['aggregate']['calls']['evaluator']['sum'], 1)
        self.assertEqual(data['aggregate']['metrics']['evaluator']['input_tokens']['sum'], 123)
        self.assertEqual(data['cells'][0]['harness'], True)
        self.assertEqual(data['cells'][0]['result']['passed'], False)
        self.assertEqual(data['cells'][0]['result']['evaluator']['provenance'], 'evaluator_provenance.json')

    def test_loader_keeps_missing_failed_and_unlisted_cells(self):
        from evals.intent_v1 import report
        self.save('plan.json', {'mode': 'scripted-offline', 'cells': [
            {'case': 'case', 'arm': 'A'}, {'case': 'case', 'arm': 'B'}]})
        self.save('case-A/result.json', {'passed': True, 'behavioral_contract_passed': False,
                                       'actor_calls': 3, 'usage': None})
        self.save('extra-C/result.json', {'passed': False, 'infrastructure_invalid': True})
        data = report.load_run(self.root)
        self.assertEqual(len(data['cells']), 3)
        self.assertEqual(data['aggregate']['contracts'], {'passed': 0, 'failed': 1, 'unknown': 2})
        self.assertEqual(data['aggregate']['harness'], {'passed': 1, 'failed': 1, 'unknown': 1})
        self.assertIsNone(data['cells'][0]['metrics']['actor']['input_tokens'])
        self.assertTrue(next(c for c in data['cells'] if c['name'] == 'case-B')['warnings'])
        self.assertEqual(data['aggregate']['count'], 3)
        self.assertEqual(data['aggregate']['calls']['actor'], {'sum': 3, 'known': 1, 'missing': 2})
        self.assertEqual(data['aggregate']['arms']['A']['contracts']['failed'], 1)

    def test_live_halt_counts_attempted_separately_from_planned(self):
        from evals.intent_v1 import report
        self.save('plan.json', {'mode': 'live', 'cells': [
            {'case': 'overhead', 'arm': arm} for arm in 'ABC']})
        self.save('summary.json', {'mode': 'live', 'count': 1, 'planned_count': 3,
            'cells': [{'cell': 'overhead-A', 'passed': False, 'exit_code': 1}]})
        self.save('overhead-A/result.json', {'passed': False, 'behavioral_contract_passed': False})
        data = report.load_run(self.root)
        self.assertEqual(data['execution'], {'planned': 3, 'attempted': 1, 'stopped': 2, 'unattempted': 2})
        self.assertFalse(any('disagrees' in w for w in data['warnings']))
        self.assertEqual([c['execution_status'] for c in data['cells']], ['attempted', 'stopped', 'stopped'])
        self.assertEqual(data['cells'][1]['stop_reason'], 'Halted after failed cell overhead-A; not attempted')
        self.assertEqual(data['aggregate']['contracts'], {'passed': 0, 'failed': 1, 'unknown': 2})
        self.save('summary.json', {'mode': 'live', 'count': 2, 'planned_count': 3,
            'cells': [{'cell': 'overhead-A', 'passed': False, 'exit_code': 1}]})
        self.assertTrue(any('disagrees' in w for w in report.load_run(self.root)['warnings']))

    def test_renderer_escapes_script_breakout_and_has_no_remote_dependencies(self):
        from html.parser import HTMLParser
        from evals.intent_v1 import report
        attack = '</script><script>globalThis.pwned=1</script><img src=x onerror=alert(1)>'
        self.save('plan.json', {'mode': 'scripted-offline', 'cells': [{'case': 'x', 'arm': 'A'}]})
        self.save('x-A/result.json', {'final_answers': [attack]})
        html = report.render_html(report.load_run(self.root))
        self.assertNotIn(attack, html)
        class Parser(HTMLParser):
            tags = []
            def handle_starttag(self, tag, attrs):
                self.tags.append((tag, dict(attrs)))
        parser = Parser()
        parser.feed(html)
        self.assertEqual(len([t for t, a in parser.tags if t == 'script']), 2)
        self.assertFalse(any('src' in a for t, a in parser.tags))
        self.assertIn('connect-src', html)
        self.assertIn('Compare', html)
        self.assertIn('data-report', html)

    def test_partial_jsonl_and_live_unknown_metrics_preserved(self):
        from evals.intent_v1 import report
        self.save('plan.json', {'mode': 'live-smoke', 'cells': [{'case': 'x', 'arm': 'C'}]})
        self.save('x-C/result.json', {'mode': 'live-smoke', 'metrics': {'actor': {
            'input_tokens': 12, 'output_tokens': 0, 'latency_ms': 1.5, 'cost_usd': None}}})
        (self.root / 'x-C/actor_requests.jsonl').write_text('{"index": 1}\ninvalid\n{"index": 3}\n')
        data = report.load_run(self.root)
        self.assertEqual(len(data['cells'][0]['artifacts']['actor_requests.jsonl']), 2)
        self.assertTrue(any('malformed' in w for w in data['cells'][0]['warnings']))
        self.assertEqual(data['aggregate']['metrics']['actor']['output_tokens']['sum'], 0)
        self.assertIsNone(data['aggregate']['metrics']['jev']['input_tokens']['sum'])


    def test_renderer_csp_hash_and_data_roundtrip(self):
        import base64
        import hashlib
        import re
        from evals.intent_v1 import report
        payload = {'hostile': '</script> & \u2028 __REPORT_SCRIPT__', 'cells': []}
        html = report.render_html(payload)
        match = re.search(r'<script id="data-report" type="application/json">(.*?)</script>', html, re.S)
        assert match is not None
        self.assertEqual(json.loads(match.group(1)), payload)
        match = re.search(r'</script><script>(.*?)</script>', html, re.S)
        assert match is not None
        script = match.group(1)
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn("script-src 'sha256-" + digest + "'", html)
        self.assertNotIn('innerHTML', script)
        self.assertNotIn('fetch(', script)

    def test_scripted_metrics_never_mistaken_for_live_zeros(self):
        from evals.intent_v1 import report
        self.save('x-A/result.json', {'mode': 'scripted-offline', 'metrics': {
            'actor': dict.fromkeys(report.METRICS, 0), 'jev': dict.fromkeys(report.METRICS, 0)}})
        cell = report.load_run(self.root)['cells'][0]
        self.assertTrue(all(v is None for m in cell['metrics'].values() for v in m.values()))

    def test_negative_exit_code_is_harness_failure(self):
        from evals.intent_v1 import report
        self.save('x-A/result.json', {'passed': True})
        self.save('summary.json', {'cells': [{'cell': 'x-A', 'exit_code': -9}]})
        self.assertIs(report.load_run(self.root)['cells'][0]['harness'], False)

    def test_nonfinite_json_and_symlink_sources_are_not_embedded(self):
        from evals.intent_v1 import report
        self.save('plan.json', {'cells': [{'case': 'x', 'arm': 'A'}]})
        self.save('x-A/result.json', {'cost': float('nan')})
        secret = self.root / 'private.txt'
        secret.write_text('{"secret":"do-not-embed"}')
        (self.root / 'x-A/actor_requests.jsonl').symlink_to(secret)
        data = report.load_run(self.root)
        self.assertTrue(any('unreadable' in w for w in data['cells'][0]['warnings']))
        self.assertNotIn('do-not-embed', report.render_html(data))

    def test_live_transport_measurements_require_complete_call_coverage(self):
        from evals.intent_v1 import report
        self.save('x-C/result.json', {'mode': 'live-smoke', 'actor_calls': 1, 'judge_calls': 1})
        (self.root / 'x-C/actor_responses.jsonl').write_text(json.dumps({'index': 1,
            'response': {'usage': {'prompt_tokens': 20, 'completion_tokens': 5, 'cost': 0.002}},
            'wall_seconds': 0.25}) + '\n')
        (self.root / 'x-C/judge_responses.jsonl').write_text(json.dumps({'index': 1,
            'usage': {'input_tokens': 40, 'output_tokens': 0}, 'estimated_cost_usd': '0.00000168',
            'cost_source': 'public price estimate', 'wall_seconds': 0.01}) + '\n')
        cell = report.load_run(self.root)['cells'][0]
        self.assertEqual(cell['metrics']['actor']['input_tokens'], 20)
        self.assertEqual(cell['metrics']['actor']['latency_ms'], 250)
        self.assertIsNone(cell['metrics']['jev']['cost_usd'])
        self.assertTrue(any('estimate' in w for w in cell['warnings']))
        self.save('x-C/result.json', {'mode': 'live-smoke', 'actor_calls': 2, 'judge_calls': 1})
        self.assertIsNone(report.load_run(self.root)['cells'][0]['metrics']['actor']['input_tokens'])

if __name__ == '__main__':
    unittest.main()
