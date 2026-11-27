#!/usr/bin/env python3
"""Runner rounds for what stage 6b ported from the workflow backend into lib/drover-run.sh:
pre-flight (FR-RUN-5/6), classifier (FR-RUN-7/15), dependencies (FR-BRD-5/7/8, replacing
test-wave-plan.sh), usage pause (FR-RUN-8), and manifest lines (FR-RUN-16);
FactoryBench is in test_run_factorybench.py. Fakes: tests/runner_fixture.py."""
import json
from pathlib import Path
import re
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner_fixture import KIT, RunnerCase  # noqa: E402

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

    # roles.md FR-REV-13: a build records the model its worker started on; the reviewer of that
    # card starts on roles.review.model (sonnet), or alt_model (opus) when that equals the
    # builder's, and its prompt names both models.
    def test_reviewer_model_differs_from_builder(self):
        model = lambda n: (lambda a: a[a.index('--model') + 1] if '--model' in a else None)(self.worker_args(n))
        self.env['FAKE_CLASSIFY'] = json.dumps({'8': {'kind': 'feature', 'complexity': 'medium', 'risk': [], 'why': 'x'},
                                                '9': {'kind': 'feature', 'complexity': 'low', 'risk': [], 'why': 'x'}})
        self.board({'number': 8, 'status': 'Ready'}, {'number': 9, 'status': 'Ready'})
        self.run_board(cycles=1, roles=dict(build=dict(max_workers=2)))
        models = self.root / '.drover/runs/models'
        self.assertEqual([model(8), model(9)], ['opus', 'sonnet'])
        self.assertEqual([(models / n).read_text() for n in ('8', '9')], ['opus\n', 'sonnet\n'])
        self.stop_workers()
        for p in self.state.glob('inflight/*'):
            p.unlink()
        for p in self.workers.glob('*'):
            p.unlink()
        (models / '7').write_text('\n')  # built on the harness default
        self.board({'number': 7, 'status': 'Review'}, {'number': 8, 'status': 'Review'},
                   {'number': 9, 'status': 'Review'}, {'number': 6, 'status': 'Review', 'labels': ['qa']})
        self.run_board(cycles=1, roles=dict(review=dict(max_workers=4)))
        self.assertEqual([model(6), model(7), model(8), model(9)], ['sonnet', 'sonnet', 'sonnet', 'opus'])
        prompt = lambda n: self.worker_args(n)[1]
        self.assertIn('Models: builder opus · reviewer sonnet.', prompt(8))
        self.assertIn('Models: builder sonnet · reviewer opus.', prompt(9))
        self.assertIn('Models: builder harness-default · reviewer sonnet.', prompt(7))
        self.assertIn('Models: builder unknown · reviewer sonnet.', prompt(6))
        # Configured: review.model equals the builder's -> alt_model; both equal -> config error.
        self.stop_workers()
        for p in list(self.state.glob('inflight/*')) + list(self.workers.glob('*')):
            p.unlink()
        self.board({'number': 9, 'status': 'Review'})
        self.run_board(cycles=1, roles=dict(review=dict(model='sonnet', alt_model='haiku')))
        self.assertEqual(model(9), 'haiku')
        self.stop_workers()
        for p in list(self.state.glob('inflight/*')) + list(self.workers.glob('*')):
            p.unlink()
        claims = lambda: len([c for c in self.calls() if c[:2] == ['issue', 'edit'] and '--add-assignee' in c])
        before = claims()
        out = self.run_board(cycles=1, rc=65, roles=dict(review=dict(model='sonnet', alt_model='sonnet')))
        self.assertIn('review model: config error, halt before claim', out)
        self.assertEqual(claims(), before, 'no claim before the halt')

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

    # Review fix: any routing type label counts as typed (ux, refactor, docs ...): the classifier
    # grades complexity and risk only, and never adds feature or bug to it.
    def test_classifier_routing_labels_are_typed(self):
        self.env['FAKE_CLASSIFY'] = json.dumps({
            '5': {'kind': 'feature', 'complexity': 'low', 'risk': ['auth'], 'why': 'login page copy'},
            '6': {'kind': 'bug', 'complexity': 'low', 'risk': [], 'why': 'docs'}})
        self.board({'number': 5, 'status': 'Ready', 'labels': ['ux']}, {'number': 6, 'status': 'Ready', 'labels': ['docs']})
        self.run_board(cycles=1, roles=dict(build=dict(max_workers=2)))
        edits = [c for c in self.calls() if c[:2] == ['issue', 'edit'] and '--add-label' in c]
        self.assertEqual(edits, [['issue', 'edit', '5', '--add-label', 'auth']])
        prompts = {a['prompt'].split('issue #')[1].split(' ')[0]: a['prompt'] for a in self.agents('classify')}
        self.assertIn('label says "ux"', prompts['5'])
        self.assertIn('label says "docs"', prompts['6'])

    # NFR-6: Ready cards the graph lists as blocked cost no per-card read; the round stays in budget.
    def test_blocked_ready_cards_stay_within_the_idle_budget(self):
        self.env['OPEN_EXTRA'] = '50'
        blocked = '## Acceptance Criteria\n- it works\n\n## Blocked by\n- #50 the base\n'
        self.board(*[{'number': n, 'status': 'Ready', 'body': blocked} for n in (1, 2, 3, 4)],
                   {'number': 9, 'status': 'Blocked', 'comments': [block('[blocked:creds]', '-')]})
        self.run_board(cycles=1)
        round_ = self.calls('round')
        self.assertLessEqual(len(round_), 5, round_)
        self.assertFalse([c for c in round_ if c[:2] == ['issue', 'view']], round_)
        self.assertEqual(self.dispatched(), [])
        self.assertEqual(self.agents(), [])

    # Review fix: a pause holds while the probe goes blind until the window resets or usage drops.
    def test_usage_pause_holds_while_blind(self):
        usage = self.base / 'usage.json'
        now = int(time.time())
        usage.write_text(json.dumps({'recorded_at': now, 'rate_limits': {
            'five_hour': {'used_percentage': 97, 'resets_at': now + 3600}}}))
        self.env.update(SB_USAGE_FILE=str(usage), USAGE_FILE=str(usage), USAGE_BLIND_AT='3', USAGE_DROP_AFTER='6')
        self.board({'number': 7, 'status': 'Ready'})
        out = self.run_board(cycles=1)
        manifest = self.manifest()
        self.assertEqual(manifest.count('usage pause: 97% >= 95%'), 1, manifest)
        self.assertNotIn('usage resume: 0%', manifest)
        lines = manifest.splitlines()
        resumed = next(i for i, l in enumerate(lines) if 'usage resume: 40%' in l)
        built = next(i for i, l in enumerate(lines) if 'dispatch lane=build issue=#7 ' in l)
        self.assertLess(resumed, built)
        self.assertGreaterEqual(sum('] tick — Ready=' in l for l in lines[:resumed]), 4)
        self.assertNotIn('running without a usage pause', out)

    # Review fix 4: an agent that never answers is stopped after the timeout, and its card is not
    # built; the agent lock goes with it.
    def test_agent_timeout(self):
        self.env.update(FAKE_AGENT='preflight-hang', DROVER_AGENT_TIMEOUT='3')
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=1)
        # Under load the fake agent may not get far enough to say it hangs before the timeout;
        # every one that did is polled until it is gone (wait_dead: 5 s ceiling).
        for p in self.workers.glob('agent-hang-*') if self.workers.exists() else []:
            self.wait_dead(int(p.name.split('-')[-1]))
        self.assertIn('skipped #7 (no verdict)', self.manifest())
        self.assertEqual(self.dispatched(), [])
        self.assertIsNone(self.lock('agent'))

    # Review fix 4: TERM to the runner while it waits on an agent stops the agent too.
    def test_runner_term_stops_its_agent(self):
        import subprocess
        self.env.update(FAKE_AGENT='preflight-hang', DROVER_AGENT_TIMEOUT='60')
        self.board({'number': 7, 'status': 'Ready'})
        (self.root / '.drover/config.json').write_text(json.dumps(dict(
            project=dict(owner='o', number=1), base_branch='main', notifications=dict(bot_identity='bot'),
            verify_commands=['true'], human_approves_merge=True, tick_seconds=0, no_progress_cycles=5,
            repo=dict(remote='https://github.com/o/r'))))
        runner = subprocess.Popen(['bash', str(KIT / 'lib/drover-run.sh')], cwd=self.root, env=self.env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: runner.poll() is None and runner.kill())
        self.assertTrue(self.wait_for(lambda: list(self.workers.glob('agent-hang-*')) if self.workers.exists() else []))
        agent = int(next(self.workers.glob('agent-hang-*')).name.split('-')[-1])
        runner.terminate()
        self.assertEqual(runner.wait(timeout=15), 143)
        self.wait_dead(agent)
        self.assertIsNone(self.lock('agent'))

    # Review fixes 4 and 13: no usable pre-flight answer backs off two ticks, and after three it is
    # held [blocked:unclear]; prose and bad JSON never break the runner.
    def test_preflight_garbage_backs_off_then_holds(self):
        self.env['FAKE_AGENT'] = 'preflight-garbage'
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=12)
        pre = self.agents('preflight')
        self.assertEqual(len(pre), 3, pre)
        self.assertTrue(all('--output-format' in a['argv'] for a in pre))
        manifest = self.manifest()
        self.assertEqual(manifest.count('skipped #7 (no verdict)'), 2, manifest)
        self.assertIn('skipped #7 (no verdict 3 times)', manifest)
        self.assertEqual(self.read_board()[7]['status'], 'Blocked')
        [hold] = self.comments(7)
        self.assertIn('Reason tag:  [blocked:unclear]', hold)
        self.assertEqual(self.dispatched(), [])

    # Review fix 10: a failed classification is retried on the next tick, at most twice; then the
    # card is claimed unclassified.
    def test_classifier_retries_then_claims(self):
        self.env['FAKE_AGENT'] = 'classify-garbage'
        self.board({'number': 7, 'status': 'Ready'})
        out = self.run_board(cycles=4)
        self.assertEqual(len(self.agents('classify')), 3)
        self.assertEqual(len(self.agents('preflight')), 1, 'the clearance holds across the retries')
        self.assertIn('try 1 of 3', out)
        self.assertIn('try 2 of 3', out)
        self.assertIn('claimed unclassified', out)
        self.assertEqual(self.dispatched(), [7])
        self.assertNotIn('--model', self.worker_args(7))

    # Review fix 6: the Block comment goes first; a move GitHub refuses halts the run (79) and no
    # "hold" line claims a move that did not happen.
    def test_preflight_block_write_fails(self):
        self.env['FAKE_PREFLIGHT'] = json.dumps({'7': {'verdict': 'hold', 'tag': 'unclear', 'detail': 'no AC'}})
        self.env['ITEM_EDIT_FAILS'] = '1'
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=1, rc=79)
        self.assertIn('Reason tag:  [blocked:unclear]', self.comments(7)[0])
        self.assertNotIn('hold #7', self.manifest())
        self.assertEqual(self.read_board()[7]['status'], 'Ready')

    # Review fix 7: an unreadable line on a Blocked card is flagged once, and a refresh drops the
    # stale needs-you:done label.
    def test_blocked_flag_once_across_ticks(self):
        self.board({'number': 3, 'status': 'Blocked', 'body': '## Blocked by\n- None, but #26 must merge first\n'},
                   {'number': 4, 'status': 'Ready'})
        self.run_board(cycles=3)
        self.assertEqual(len([c for c in self.comments(3) if 'dependency line unreadable' in c]), 1)
        self.assertEqual(self.read_board()[3]['status'], 'Blocked')


class Helpers(unittest.TestCase):
    """The clearance and backoff bookkeeping, through the runner's own functions."""
    def run_lib(self, script):
        import subprocess
        body = 'set -euo pipefail\nexport SB_LIB_ONLY=1\n. "$1/lib/drover-run.sh"\n' + script
        return subprocess.run(['bash', '-c', body, 'helpers', str(KIT)], capture_output=True, text=True)

    def test_clearance_reuse_expiry_and_leaving_ready(self):
        r = self.run_lib(r'''
PROJECT_ITEMS_JSON='{"items":[{"status":"Ready","content":{"number":1}},{"status":"Ready","content":{"number":2}}]}'
PF_TTL=3; TICK=5; PF_CLEARED=" 1:5 2:5 "
pf_cleared 1 && echo reuse
TICK=7; pf_cleared 2 && echo still
TICK=8; pf_cleared 2 || echo expired
case "$PF_CLEARED" in *" 2:"*) echo kept ;; *) echo dropped ;; esac
TICK=6; PF_CLEARED=" 1:5 "
PROJECT_ITEMS_JSON='{"items":[{"status":"Building","content":{"number":1}}]}'
pf_cleared 1 || echo left
PF_SKIPS=" 4:1:9 "; TICK=8; pf_waiting 4 && echo waiting; TICK=9; pf_waiting 4 || echo retry
''')
        self.assertEqual(r.stdout.split(), ['reuse', 'still', 'expired', 'dropped', 'left', 'waiting', 'retry'], r.stderr)


if __name__ == '__main__':
    unittest.main()
