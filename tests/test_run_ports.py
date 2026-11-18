#!/usr/bin/env python3
"""Runner rounds for what stage 6b ported from the workflow backend into lib/drover-run.sh:
pre-flight (FR-RUN-5/6), classifier (FR-RUN-7/15), dependencies (FR-BRD-5/7/8, replacing
test-wave-plan.sh), usage pause (FR-RUN-8), manifest lines (FR-RUN-16) and FactoryBench
(metrics.md FR-MET-4..12). Fakes: tests/runner_fixture.py."""
import json
from pathlib import Path
import re
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner_fixture import FAKE_FACTORYBENCH, RunnerCase, alive  # noqa: E402

H1, H2 = 'a' * 40, 'b' * 40


def block(tag, by):
    return ('[builder] [blocker] blocked %s fixture\nCard:        #1\nReason tag:  %s\nWhy blocked: x\n'
            'blocked-by:  %s' % (tag, tag, by))


class Ports(RunnerCase):
    def dispatched(self):
        return sorted(int(m) for m in re.findall(r'dispatch lane=\w+ issue=#(\d+) pid=', self.manifest()))

    def worker_args(self, issue):
        for pid in self.worker_pids():
            if (self.workers / ('env-%d' % pid)).read_text().endswith('DROVER_ISSUE=%d' % issue):
                return (self.workers / ('argv-%d' % pid)).read_text().split('\0')
        self.fail('no worker for #%d' % issue)

    # FR-RUN-5/6: one agent per batch of up to 5 Ready build cards, before any claim. Hold and
    # sequence-behind go to Blocked with the Block template; a sequence with nothing to wait on
    # builds with a conflict note; no verdict is never built.
    def test_preflight_verdicts(self):
        self.env['FAKE_PREFLIGHT'] = json.dumps({
            '2': {'verdict': 'hold', 'tag': 'done', 'detail': 'duplicate of merged PR #31'},
            '3': {'verdict': 'sequence', 'tag': 'overlap', 'blockedBy': [50], 'detail': 'PR #41 touches login.ts'},
            '4': {'verdict': 'sequence', 'tag': 'overlap', 'blockedBy': [], 'detail': 'PR #44 touches api.ts'},
            '5': 'none'})
        self.env['OPEN_EXTRA'] = '50'
        self.board(*[{'number': n, 'status': 'Ready'} for n in range(1, 8)],
                   {'number': 9, 'status': 'Review'})
        self.run_board(cycles=1, roles=dict(build=dict(max_workers=3)))
        pre = self.agents('preflight')
        self.assertEqual([re.search(r'--issues (\S+)', a['prompt']).group(1) for a in pre], ['1,2,3,4,5', '6,7'])
        self.assertTrue(all(a['model'] == 'sonnet' for a in pre))
        self.assertNotIn('#9', ' '.join(a['prompt'] for a in pre), 'a Review card was pre-flighted')
        self.assertEqual(self.dispatched(), [1, 4, 6, 9])
        order = re.findall(r'dispatch lane=\w+ issue=#(\d+) pid=', self.manifest())
        self.assertEqual(order[0], '9', 'FR-RUN-3: Review is dispatched before Ready')
        board = self.read_board()
        self.assertEqual([board[n]['status'] for n in (2, 3, 5, 7)], ['Blocked', 'Blocked', 'Ready', 'Ready'])
        [hold] = self.comments(2)
        self.assertTrue(hold.startswith('[orchestrator] [blocker] blocked [blocked:duplicate] duplicate of merged PR #31\n'), hold)
        self.assertTrue(hold.endswith('\nblocked-by:  -'), hold)
        [seq] = self.comments(3)
        self.assertIn('Reason tag:  [blocked:sequenced]', seq)
        self.assertTrue(seq.endswith('\nblocked-by:  50'), seq)
        self.assertTrue(any('expected conflict' in c for c in self.comments(4)))
        self.assertEqual(self.comments(5), [])
        self.assertIn('preflight batch #1 #2 #3 #4 #5: proceed #1, hold #2 (duplicate of merged PR #31), '
                      'sequence #3 (PR #41 touches login.ts), proceed #4, skipped #5 (no verdict)', self.manifest())

    # Pre-flight never runs for cards that build nothing: a `qa` card goes Ready -> QA Tester.
    def test_qa_card_skips_preflight(self):
        self.board({'number': 1, 'status': 'Ready', 'labels': ['qa']})
        self.run_board(cycles=1)
        self.assertEqual(self.agents('preflight'), [])
        self.assertIn('dispatch lane=qa issue=#1 pid=', self.manifest())

    # A halted pre-flight (required GitHub read failed) stops the run and builds nothing.
    def test_preflight_halt(self):
        self.env['FAKE_PREFLIGHT'] = json.dumps({'1': {'verdict': 'halted', 'detail': 'read failed'}})
        self.board({'number': 1, 'status': 'Ready'})
        self.run_board(cycles=1, rc=79)
        self.assertEqual(self.dispatched(), [])

    # FR-RUN-7/15: the classifier types an untyped card, adds missing risk labels and says why in
    # one comment; it never overrides a type label, never adds qa, never removes a label; no
    # comment when it adds nothing. Complexity picks the model tier.
    def test_classifier_labels_and_model(self):
        self.env['FAKE_CLASSIFY'] = json.dumps({
            '8': {'kind': 'bug', 'complexity': 'high', 'risk': ['auth', 'qa', 'payments', 'auth'], 'why': 'touches login'},
            '9': {'kind': 'feature', 'complexity': 'low', 'risk': [], 'why': 'small'},
            '10': {'kind': 'qa', 'complexity': 'medium', 'risk': ['security'], 'why': 'reads secrets'}})
        self.board({'number': 8, 'status': 'Ready'}, {'number': 9, 'status': 'Ready', 'labels': ['bug']},
                   {'number': 10, 'status': 'Ready', 'labels': ['feature', 'security']})
        self.run_board(cycles=1, roles=dict(build=dict(max_workers=3)))
        self.assertEqual(self.dispatched(), [8, 9, 10])
        cls = self.agents('classify')
        self.assertEqual(len(cls), 3)
        self.assertTrue(all(a['model'] == 'haiku' for a in cls))
        self.assertIn('label says "bug"', next(a['prompt'] for a in cls if 'issue #9 ' in a['prompt']))
        self.assertIn('never qa', next(a['prompt'] for a in cls if 'issue #8 ' in a['prompt']))
        edits = [c for c in self.calls() if c[:2] == ['issue', 'edit'] and ('--add-label' in c or '--remove-label' in c)]
        self.assertEqual(edits, [['issue', 'edit', '8', '--add-label', 'bug,auth,payments']])
        self.assertEqual(self.comments(8), ['[orchestrator] [classify] added label bug, auth, payments\n'
                                            'Did: classified as bug, complexity high\nWhy: touches login'])
        self.assertEqual(self.comments(9) + self.comments(10), [])
        model = lambda n: (lambda a: a[a.index('--model') + 1] if '--model' in a else None)(self.worker_args(n))
        # Medium tier ladder: low sonnet, medium opus, high the harness default (no flag).
        self.assertEqual([model(8), model(9), model(10)], [None, 'sonnet', 'opus'])
        # The label routing it added reaches the skills: bug -> diagnosing-bugs.
        self.assertIn('diagnosing-bugs=', ' '.join(self.worker_args(8)))

    # A label the repo lacks: no comment, and the card is still built.
    def test_classifier_label_failure_still_builds(self):
        self.env['LABEL_FAILS'] = '1'
        self.board({'number': 8, 'status': 'Ready'})
        out = self.run_board(cycles=1)
        self.assertEqual(self.dispatched(), [8])
        self.assertEqual(self.comments(8), [])
        self.assertIn('could not add label feature', out)

    # FR-BRD-7 and FR-BRD-5 (was test-wave-plan.sh 1, 3-6, 14): only free Ready cards are
    # claimed; an unreadable line is flagged once and never built; a card the graph does not know
    # is not free; a Blocked card whose blockers closed goes back to Ready, and builds next tick.
    def test_blockers_and_sweep(self):
        self.board({'number': 12, 'status': 'Ready'},
                   {'number': 14, 'status': 'Ready', 'body': '## Blocked by\n- #12 the base\n'},
                   {'number': 15, 'status': 'Ready', 'body': '## Blocked by\n- None, but #26 must merge first\n'},
                   {'number': 13, 'status': 'Ready', 'ingraph': False},
                   {'number': 16, 'status': 'Blocked', 'comments': [block('[blocked:sequenced]', '9')]},
                   {'number': 17, 'status': 'Blocked', 'comments': [block('[blocked:sequenced]', '12')]},
                   {'number': 18, 'status': 'Blocked', 'comments': [block('[blocked:creds]', '-')]})
        self.run_board(cycles=2, roles=dict(build=dict(max_workers=5)))
        self.assertEqual(self.dispatched(), [12, 16])
        manifest = self.manifest()
        self.assertEqual(manifest.count('unblocked #'), 1, manifest)
        self.assertIn('unblocked #16: blocker #9 closed, back to Ready', manifest)
        lines = manifest.splitlines()
        swept = next(i for i, l in enumerate(lines) if 'unblocked #16' in l)
        built = next(i for i, l in enumerate(lines) if 'dispatch lane=build issue=#16 ' in l)
        # The sweep runs before its tick's summary line: built one tick later = two lines between.
        self.assertEqual(sum('] tick — Ready=' in l for l in lines[swept:built]), 2, 'swept and built in one tick')
        board = self.read_board()
        self.assertEqual([board[n]['status'] for n in (14, 15, 13, 17, 18)], ['Ready', 'Ready', 'Ready', 'Blocked', 'Blocked'])
        [flag] = self.comments(15)
        self.assertIn('names an issue', flag)
        self.assertEqual(self.comments(14) + self.comments(13), [])
        self.assertIn('blocker closed', self.comments(16)[0])

    # FR-BRD-8 (was test-wave-plan.sh 18-19): an owner's `done` on the current PR head moves the
    # card back to Review; a head that moved while Blocked goes to Review for a fresh request.
    def test_approval_resume_and_head_change(self):
        def request(pr, head):
            return ('[reviewer] [blocker] blocked [needs-you] merge needs the owner\nReason tag:  [needs-you]\n'
                    'blocked-by:  -\napproval-request: ' + json.dumps({'repo': 'o/r', 'pr': pr, 'head': head,
                                                                      'scope': 'c' * 64}, sort_keys=True))
        def thread(pr, head, done):
            owner = {'login': 'owner', 'type': 'User'}
            rows = [{'id': 1, 'body': request(pr, head), 'user': owner,
                     'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z'}]
            if done:
                rows.append({'id': 2, 'body': 'done', 'user': owner,
                             'created_at': '2026-01-01T00:05:00Z', 'updated_at': '2026-01-01T00:05:00Z'})
            return rows
        self.board({'number': 20, 'status': 'Blocked', 'comments': [request(40, H1)],
                    'needs': {'pr': 40, 'current': H1, 'comments': thread(40, H1, True)}},
                   {'number': 21, 'status': 'Blocked', 'comments': [request(41, H1)],
                    'needs': {'pr': 41, 'current': H2, 'comments': thread(41, H1, True)}},
                   {'number': 22, 'status': 'Blocked', 'comments': [request(42, H1)],
                    'needs': {'pr': 42, 'current': H1, 'comments': thread(42, H1, False)}})
        out = self.run_board(cycles=1)
        board = self.read_board()
        self.assertEqual([board[n]['status'] for n in (20, 21, 22)], ['Review', 'Review', 'Blocked'])
        manifest = self.manifest()
        self.assertIn('approval resume #20: owner approved head aaaaaaa, back to Review', manifest)
        self.assertNotIn('approval resume #21', manifest)
        self.assertIn('approval refresh #21', out)
        self.assertIn('fresh approval request', self.comments(21)[0])
        self.assertEqual(self.comments(20) + self.comments(22), [])
        self.assertEqual(self.dispatched(), [], 'a moved card waits for the next tick')

    # FR-RUN-8: at or above usage_pause_pct no new worker starts; the run resumes when usage drops,
    # and paused ticks never count toward the runaway halt.
    def test_usage_pause_and_resume(self):
        usage = self.base / 'usage.json'
        now = int(time.time())
        usage.write_text(json.dumps({'recorded_at': now, 'rate_limits': {
            'five_hour': {'used_percentage': 97, 'resets_at': now + 3600}}}))
        self.env.update(SB_USAGE_FILE=str(usage), USAGE_FILE=str(usage), USAGE_DROP_AFTER='4')
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=1)
        lines = self.manifest().splitlines()
        paused = [i for i, l in enumerate(lines) if 'usage pause: 97% >= 95%, no new workers' in l]
        resumed = [i for i, l in enumerate(lines) if 'usage resume: 40%' in l]
        built = [i for i, l in enumerate(lines) if 'dispatch lane=build issue=#7 pid=' in l]
        self.assertEqual((len(paused), len(resumed), len(built)), (1, 1, 1), '\n'.join(lines))
        self.assertLess(paused[0], resumed[0])
        self.assertLess(resumed[0], built[0])
        self.assertGreaterEqual(sum('] tick — Ready=' in l for l in lines[paused[0]:resumed[0]]), 2)
        self.assertNotIn('RUNAWAY', '\n'.join(lines[:built[0]]))

    # A probe with no reading is said once per run, and never pauses.
    def test_usage_unknown_said_once(self):
        self.board({'number': 7, 'status': 'Ready'}, {'number': 8, 'status': 'Review'})
        out = self.run_board(cycles=2)
        self.assertEqual(out.count('usage: no reading from the usage probe'), 1, out)
        self.assertEqual(self.dispatched(), [7, 8])
        self.assertNotIn('usage', self.manifest().replace('usage: no reading', ''))

    # Variant was removed in v3.0.0 (was test-wave-plan.sh 11-12); a config with no GitHub repo
    # to read dependencies from stops before any claim (was 15).
    def test_refusals(self):
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=1, rc=65, variant='qa-only')
        self.run_board(cycles=1, rc=64, repo=dict(remote=None))
        self.assertEqual(self.worker_pids(), [])

    def factorybench(self):
        self.tool('factorybench', FAKE_FACTORYBENCH)
        self.env.update(FB_LOG=str(self.base / 'fb.jsonl'), FACTORYBENCH_URL='http://fb.invalid')

    def events(self):
        path = self.base / 'fb.jsonl'
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    # FR-MET-4..12: one attempt per card with its lanes, sent through `factorybench emit`.
    def test_factorybench_attempt(self):
        self.factorybench()
        self.env['FAKE_CLAUDE'] = 'advance'  # each worker moves its card one column and exits
        self.board({'number': 7, 'status': 'Ready', 'pr': 'https://github.com/o/r/pull/70'})
        out = self.run_board(cycles=60, metrics=dict(commit_email='drover@example.com'))
        self.assertIn('exiting cleanly', out)
        events = self.events()
        self.assertEqual(events[0], ['emit', '--flush'])
        emits = [e for e in events[1:]]
        self.assertTrue(all(e[0] == 'emit' and e[2] == '--attempt' and e[4] == '--key' and e[6] == '--json' for e in emits))
        self.assertEqual([(e[1], e[5]) for e in emits], [
            ('attempt.started', 'started'), ('pr.linked', 'pr'), ('gate', 'gate:qa:1'),
            ('gate', 'gate:review:1'), ('gate', 'gate:merge:1'), ('attempt.finished', 'finished')])
        self.assertEqual(len({e[3] for e in emits}), 1, 'one attempt for the card')
        started = json.loads(emits[0][7])
        self.assertEqual(started['work_item'], {'repo': 'o/r', 'issue': 7})
        self.assertRegex(started['factory_config_version'], r'^drover@[0-9a-f]+\+claude-code@2\.1\.400\+cfg:[0-9a-f]{8}$')
        self.assertIn('feature', started['labels'])
        self.assertNotIn('role', started)
        self.assertEqual(json.loads(emits[1][7]), {'pr_url': 'https://github.com/o/r/pull/70'})
        self.assertEqual(json.loads(emits[2][7]), {'name': 'qa', 'passed': True, 'reason': 'QA passed'})
        self.assertEqual(json.loads(emits[5][7]), {'status': 'completed'})
        attempt = emits[0][3]
        phases = []
        for pid in self.worker_pids():
            env = json.loads((self.workers / ('otel-%d' % pid)).read_text())
            self.assertEqual(env['OTEL_EXPORTER_OTLP_LOGS_ENDPOINT'], 'http://fb.invalid/v1/logs')
            self.assertEqual(env['CLAUDE_CODE_ENABLE_TELEMETRY'], '1')
            self.assertEqual(env['GIT_AUTHOR_EMAIL'], 'drover@example.com')
            self.assertFalse({'OTEL_LOG_USER_PROMPTS', 'OTEL_LOG_TOOL_DETAILS', 'OTEL_LOG_RAW_API_BODIES'} & set(env))
            phases.append(env['OTEL_RESOURCE_ATTRIBUTES'])
        self.assertEqual(sorted(phases), sorted('factorybench.attempt=%s,factorybench.phase=%s' % (attempt, p)
                                                for p in ('implement', 'verify', 'review')))
        self.assertEqual(json.loads((self.root / '.drover/runs/attempts.json').read_text()), {})

    # FR-MET-5: a stop with the card in flight waits the attempt; the next claim resumes it.
    def test_factorybench_stop_and_resume(self):
        self.factorybench()
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=1)
        [first] = self.worker_pids(expect=1)
        self.stop()
        self.wait_dead(first)
        self.run_board(cycles=1)
        emits = [e for e in self.events() if e[0] == 'emit' and e[1] != '--flush']
        self.assertEqual([(e[1], e[5]) for e in emits], [
            ('attempt.started', 'started'), ('human.waiting', 'waiting:1'), ('human.resumed', 'resumed:1')])
        self.assertEqual(json.loads(emits[1][7]), {'reason': 'stopped'})
        self.assertEqual(len({e[3] for e in emits}), 1)

    # FR-MET-4: without FACTORYBENCH_URL nothing is sent and nothing is said about it.
    def test_factorybench_silent_without_url(self):
        self.factorybench()
        del self.env['FACTORYBENCH_URL']
        self.env['FAKE_CLAUDE'] = 'advance'
        self.board({'number': 7, 'status': 'Ready'})
        out = self.run_board(cycles=60)
        self.assertEqual(self.events(), [])
        self.assertFalse((self.root / '.drover/runs/attempts.json').exists())
        self.assertNotIn('factorybench', (out + self.manifest()).lower())
        for pid in self.worker_pids():
            self.assertNotIn('OTEL_RESOURCE_ATTRIBUTES', (self.workers / ('otel-%d' % pid)).read_text())


if __name__ == '__main__':
    unittest.main()
