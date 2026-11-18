#!/usr/bin/env python3
"""Whole runner rounds (lib/drover-run.sh) with fakes on PATH for gh, claude
and pgrep (NFR-7). The board is a JSON file the fake gh serves; nothing reaches GitHub,
and no real worker or dispatcher on this machine is ever matched or signalled."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

KIT = Path(__file__).resolve().parents[1]
IDLE_ROUND_CALLS = 3  # measured at d0b7d5a; see docs/baseline.md
# The skills the readiness check needs for every lane (see test_skill_dispatch.py).
NAMES = ['drover', 'drover-build', 'drover-qa', 'drover-review', 'drover-collect', 'visual', 'git-sync',
         'ui-refine-loop', 'pr-security-review', 'tdd', 'implement', 'codebase-design', 'code-review',
         'diagnosing-bugs', 'verification-before-completion', 'testing-strategy']

FAKE_GH = r'''
import glob, json, os, re, subprocess, sys, time
args = sys.argv[1:]
manifest = ''.join(open(p).read() for p in glob.glob('.drover/runs/*.md'))
phase = 'round' if 'initial Ready count' in manifest else 'startup'
with open(os.environ['CALL_LOG'], 'a') as f:
    f.write(json.dumps({'phase': phase, 'args': args}) + '\n')
board = json.load(open(os.environ['BOARD']))
cols = ['Backlog', 'Ready', 'Building', 'QA', 'Review', 'Blocked', 'Done']
def card(n):
    return next((c for c in board if c['number'] == n), {})
if args[:2] == ['api', 'rate_limit']:
    print('{"resources":{"graphql":{"remaining":5000,"reset":0},"core":{"remaining":5000,"reset":0}}}')
elif args[:2] == ['api', 'graphql'] and '--paginate' in args:
    nodes = [{'id': 'item%d' % c['number'], 'fieldValueByName': {'name': c['status']},
              'content': {'__typename': 'Issue', 'number': c['number'], 'title': 'fixture',
                          'url': 'https://github.com/o/r/issues/%d' % c['number'],
                          'repository': {'nameWithOwner': 'o/r'},
                          'labels': {'nodes': [{'name': l} for l in c.get('labels', [])]}}} for c in board]
    print(json.dumps([{'data': {'repositoryOwner': {'projectV2': {'items': {
        'totalCount': len(nodes), 'nodes': nodes, 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}}]))
elif args[:2] == ['api', 'graphql']:
    print('{"data":{"rateLimit":{"limit":5000,"remaining":5000,"used":0,"resetAt":"2030-01-01T00:00:00Z"}}}')
elif args[:2] == ['issue', 'view']:
    c = card(int(args[2]))
    fields = args[args.index('--json') + 1] if '--json' in args else ''
    if fields == 'state':
        print(c.get('state', 'OPEN'))
    elif fields == 'stateReason,comments':  # run the runner's own --jq over gh's shape
        issue = {'stateReason': c.get('reason', 'COMPLETED'), 'comments': [{'body': b} for b in c.get('comments', [])]}
        sys.stdout.write(subprocess.run(['jq', '-c', args[args.index('--jq') + 1]], input=json.dumps(issue),
                                        capture_output=True, text=True, check=True).stdout)
    elif fields == 'labels':
        print(json.dumps({'labels': [{'name': l} for l in c.get('labels', [])]}))
    else:
        print(json.dumps({'number': int(args[2]), 'title': 'fixture', 'body': ''}))
elif args[:2] == ['project', 'field-list']:
    print(json.dumps({'fields': [{'id': 'F', 'name': 'Status',
                                  'options': [{'id': 'opt_' + s, 'name': s} for s in cols]}]}))
elif args[:2] == ['project', 'view']:
    print('{"id":"P"}')
elif args[:2] == ['pr', 'list'] and 'merged' in args:
    base = args[args.index('--base') + 1]
    print(json.dumps([{'headRefName': c['merged']} for c in board
                      if c.get('merged') and c.get('merged_base', 'main') == base]))
elif args[0] == 'api' and re.fullmatch(r'repos/\{owner\}/\{repo\}/issues/\d+/comments', args[1]):
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    print(json.dumps([[{'id': i + 1, 'body': b, 'user': {'login': 'bot'}, 'created_at': now, 'updated_at': now}
                       for i, b in enumerate(card(int(args[1].split('/')[4])).get('reports', []))]]))
elif args[:2] == ['pr', 'list']:
    m = re.search(r'issue-(\d+)-', ' '.join(args))
    print(os.environ.get('PR_FOR_' + m.group(1), '') if m else '')
elif args[:2] == ['project', 'item-edit']:  # the board remembers column moves
    item, option = args[args.index('--id') + 1], args[args.index('--single-select-option-id') + 1]
    for c in board:
        if 'item%d' % c['number'] == item:
            c['status'] = option[len('opt_'):]
    json.dump(board, open(os.environ['BOARD'], 'w'))
elif args[:2] in (['issue', 'edit'], ['issue', 'comment'], ['pr', 'comment']):
    pass
else:
    sys.exit('unexpected gh call: %r' % args)
'''

# A live worker: records its pid, then runs until signalled (60 s cap).
# FAKE_CLAUDE=exit makes it a worker that ends at once without touching the board;
# FAKE_CLAUDE=handoff moves its card to Blocked, then keeps running.
FAKE_CLAUDE = r'''
import json, os, signal, sys, time
from pathlib import Path
d = Path(os.environ['WORKER_DIR']); d.mkdir(exist_ok=True)
(d / ('argv-%d' % os.getpid())).write_text('\0'.join(sys.argv[1:]))
(d / ('env-%d' % os.getpid())).write_text('DROVER_ROLE=%s DROVER_ISSUE=%s' % (os.environ.get('DROVER_ROLE'), os.environ.get('DROVER_ISSUE')))
(d / ('stdin-%d' % os.getpid())).write_text(str(os.path.samestat(os.fstat(0), os.stat(os.devnull))))
(d / str(os.getpid())).write_text(' '.join(sys.argv[1:]))
if os.environ.get('FAKE_CLAUDE') == 'exit':
    sys.exit(0)
if os.environ.get('FAKE_CLAUDE') == 'handoff':
    board = json.load(open(os.environ['BOARD']))
    for c in board:
        if c['number'] == int(os.environ['DROVER_ISSUE']):
            c['status'] = 'Blocked'
    json.dump(board, open(os.environ['BOARD'], 'w'))
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
end = time.time() + 60
while time.time() < end:
    time.sleep(.05)
'''


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith('Z')


class RunnerRounds(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root, self.home, self.bin = [self.base / n for n in ['repo', 'home', 'bin']]
        for p in [self.root, self.home, self.bin]:
            p.mkdir()
        self.calls_path = self.base / 'calls.jsonl'
        self.board_path = self.base / 'board.json'
        self.workers = self.base / 'workers'
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        DROVER_SKILLS_HOME=str(self.home), SB_GITHUB_RETRY_DELAY='0',
                        SB_GITHUB_HALT_FILE=str(self.base / 'halt.json'), CALL_LOG=str(self.calls_path),
                        BOARD=str(self.board_path), WORKER_DIR=str(self.workers),
                        GIT_AUTHOR_NAME='f', GIT_AUTHOR_EMAIL='f@example.com',
                        GIT_COMMITTER_NAME='f', GIT_COMMITTER_EMAIL='f@example.com')
        for name, source in [('gh', FAKE_GH), ('claude', FAKE_CLAUDE), ('pgrep', 'import sys; sys.exit(1)')]:
            path = self.bin / name
            path.write_text('#!/usr/bin/env python3\n' + source)
            path.chmod(0o755)
        self.git('init', '-q', '-b', 'main')
        self.git('commit', '-q', '--allow-empty', '-m', 'base')
        for name in NAMES:
            skill = self.root / '.claude/skills' / name / 'SKILL.md'
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text('fixture skill instructions')
        self.state = self.root / '.claude/drover'
        (self.root / '.drover').mkdir()
        self.addCleanup(self.stop_workers)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.root, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def board(self, *cards):
        self.board_path.write_text(json.dumps(list(cards)))

    def run_board(self, cycles, **extra):
        # no_progress_cycles=N stops the run after N rounds with no new Done card.
        (self.root / '.drover/config.json').write_text(json.dumps(dict(
            project=dict(owner='o', number=1), base_branch='main', notifications=dict(bot_identity='bot'),
            verify_commands=['true'], human_approves_merge=True, tick_seconds=0, no_progress_cycles=cycles,
            **extra)))
        result = subprocess.run(['bash', str(KIT / 'lib/drover-run.sh')], cwd=self.root,
                                env=self.env, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def stop(self):
        result = subprocess.run(['bash', str(KIT / 'lib/drover-stop.sh')], cwd=self.root,
                                env=self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def calls(self, phase=None):
        if not self.calls_path.exists():
            return []
        rows = [json.loads(line) for line in self.calls_path.read_text().splitlines()]
        return [r['args'] for r in rows if phase in (None, r['phase'])]

    def worker_pids(self, expect=0):
        deadline = time.monotonic() + 5  # a dispatched fake worker may still be starting
        while expect and len(self.worker_pids()) < expect and time.monotonic() < deadline:
            time.sleep(.02)
        return self._pids()

    def _pids(self):
        return sorted(int(p.name) for p in self.workers.iterdir() if p.name.isdigit()) if self.workers.exists() else []

    def lock(self, issue):
        path = self.state / 'inflight' / str(issue)
        return path.read_text() if path.exists() else None

    def wait_dead(self, pid):
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(alive(pid), 'fixture worker %d did not exit' % pid)

    def stop_workers(self):
        for pid in self.worker_pids():
            if alive(pid):
                os.kill(pid, signal.SIGTERM)
                self.wait_dead(pid)

    # FR-WRK-1: a worker's lock is removed only when the worker is not alive.
    def test_live_worker_lock_survives_a_round(self):
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=2)  # round 1 dispatches, round 2 sees the lock
        [pid] = self.worker_pids(expect=1)
        self.assertTrue(alive(pid), 'the fake worker should still be running')
        self.assertIn('HANDLE=process:%d\n' % pid, self.lock(7) or '', 'live worker lost its lock')
        manifest = self.manifest()
        self.assertNotIn('reaped stale lock', manifest)
        self.assertEqual(manifest.count('dispatch lane=build issue=#7'), 1, manifest)
        self.assertNotIn(['issue', 'edit', '7', '--remove-assignee', 'bot'], self.calls())

        # Once the worker is gone, the next round does reap it.
        os.kill(pid, signal.SIGTERM)
        self.wait_dead(pid)
        self.board()
        self.run_board(cycles=1)
        self.assertIsNone(self.lock(7))
        self.assertIn(['issue', 'edit', '7', '--remove-assignee', 'bot'], self.calls())

    def manifest(self):
        return next((self.root / '.drover/runs').glob('*-repo.md')).read_text()

    def moves(self, column):
        return [c[c.index('--id') + 1] for c in self.calls()
                if c[:2] == ['project', 'item-edit'] and c[-1] == 'opt_' + column]

    def done_moves(self):
        return self.moves('Done')

    # FR-BRD-3: the runner never moves an open card to Done; the merge gate
    # (test-merge-gate.sh) is what proves a merge before the reviewer moves it.
    # A closed card reaches Done only with a merged issue-<N>-* PR into the base, or a
    # not-planned close with a comment from a person; any other close goes to Blocked [needs-you].
    def test_done_only_after_merge(self):
        self.env['FAKE_CLAUDE'] = 'exit'  # the reviewer ends without merging
        self.board({'number': 6, 'status': 'Review', 'state': 'CLOSED'},
                   {'number': 9, 'status': 'Review', 'state': 'CLOSED', 'merged': 'issue-9-fix'},
                   {'number': 1, 'status': 'Review', 'state': 'CLOSED'},  # issue-12- is not issue-1-
                   {'number': 12, 'status': 'Done', 'state': 'CLOSED', 'merged': 'issue-12-fix'},
                   {'number': 13, 'status': 'Review', 'state': 'CLOSED', 'merged': 'issue-13-fix',
                    'merged_base': 'dev'},  # merged, but not into the base
                   {'number': 10, 'status': 'Review', 'state': 'CLOSED', 'reason': 'NOT_PLANNED',
                    'comments': ['[orchestrator] [report] stopped', 'duplicate of #3']},
                   {'number': 11, 'status': 'QA', 'state': 'CLOSED', 'reason': 'NOT_PLANNED'},
                   {'number': 14, 'status': 'Review', 'state': 'CLOSED', 'reason': 'NOT_PLANNED',
                    'comments': ['[orchestrator] [report] stopped · build lane', '[reviewer] [report] bounced']},
                   {'number': 5, 'status': 'Review'})
        self.run_board(cycles=1)
        self.assertNotIn('item5', self.done_moves(), 'an unmerged open card reached Done')
        self.assertEqual(sorted(self.done_moves()), ['item10', 'item9'])
        self.assertEqual(sorted(self.moves('Blocked')), ['item1', 'item11', 'item13', 'item14', 'item6'])
        notes = {c[2]: c[c.index('--body') + 1] for c in self.calls() if c[:2] == ['issue', 'comment']}
        self.assertEqual(sorted(notes), ['1', '11', '13', '14', '6'])
        self.assertTrue(notes['6'].startswith('[orchestrator] [blocker] blocked [needs-you] '), notes['6'])
        self.assertIn('\nblocked-by:  -', notes['6'])
        self.assertTrue(self.worker_pids(expect=1), 'the open card got a reviewer')
        self.assertTrue(all('issue #5 ' in (self.workers / str(p)).read_text() for p in self.worker_pids()))

    # FR-RUN-2: roles.<role>.max_workers workers of one role run side by side, no more.
    def test_two_builders_run_in_parallel(self):
        self.board({'number': 7, 'status': 'Ready'}, {'number': 8, 'status': 'Ready'},
                   {'number': 9, 'status': 'Ready'})
        self.run_board(cycles=1, roles=dict(build=dict(max_workers=2)))
        pids = self.worker_pids(expect=2)
        self.assertEqual(len(pids), 2, pids)
        self.assertTrue(all(alive(p) for p in pids), 'both builders should run at once')
        issues = sorted((self.workers / ('env-%d' % p)).read_text() for p in pids)
        self.assertEqual(issues, ['DROVER_ROLE=build DROVER_ISSUE=7', 'DROVER_ROLE=build DROVER_ISSUE=8'])
        for issue in (7, 8):
            self.assertRegex(self.lock(issue) or '', r'^HANDLE=process:\d+\nLANE=build\nSTARTED=\S+\nCOLUMN=Ready\n$')
        self.assertIsNone(self.lock(9), 'a third builder started past max_workers 2')
        self.assertIn('max_workers=4 (build=2 qa=1 review=1)', self.manifest())

    # FR-RUN-13: two failed dispatches in a row on one card halt the run with the log tail.
    def test_two_failed_dispatches_halt(self):
        self.env['FAKE_CLAUDE'] = 'exit'  # ends without moving its card or reporting
        self.board({'number': 5, 'status': 'Review'})
        out = self.run_board(cycles=30)
        manifest = self.manifest()
        self.assertEqual(manifest.count('dispatch lane=review issue=#5 pid='), 2, manifest)
        self.assertIn('failed dispatch lane=review issue=#5', manifest)
        self.assertIn('halt — failed dispatch: #5 failed 2 dispatches in a row', out)
        self.assertIn('| === dispatch lane=review issue=#5 at ', out)  # the log tail
        self.assertNotIn('RUNAWAY', manifest)

    # FR-WRK-10: a worker whose card left its lane runs one more tick, then is stopped.
    def test_worker_gets_one_tick_after_its_card_leaves(self):
        self.env['FAKE_CLAUDE'] = 'handoff'  # moves its card to Blocked and keeps running
        self.board({'number': 7, 'status': 'Ready'})
        out = self.run_board(cycles=30)
        [pid] = self.worker_pids(expect=1)
        self.wait_dead(pid)
        lines = self.manifest().splitlines()
        marked = next(i for i, l in enumerate(lines) if 'one more tick to finish its handoff' in l)
        stopped = next(i for i, l in enumerate(lines) if 'zombie build worker on #7 (pid=%d)' % pid in l)
        self.assertEqual(sum('] tick — Ready=' in l for l in lines[marked:stopped]), 1, '\n'.join(lines))
        self.assertIsNone(self.lock(7))
        self.assertIn('exiting cleanly', out)

    def started_build(self, issue, pr):
        # A card mid-build: issue branch pushed, its worktree, an open PR, a live worker.
        origin = self.base / 'origin.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(origin)], check=True)
        self.git('remote', 'add', 'origin', str(origin))
        branch = 'issue-%d-fix' % issue
        self.git('checkout', '-q', '-b', branch)
        self.git('commit', '-q', '--allow-empty', '-m', 'wip on #%d' % issue)
        self.git('push', '-q', 'origin', branch)
        self.git('checkout', '-q', 'main')
        self.git('worktree', 'add', '-q', '.drover/worktrees/issue-%d-build' % issue, branch)
        self.env['PR_FOR_%d' % issue] = str(pr)
        self.board({'number': issue, 'status': 'Ready'})
        self.run_board(cycles=1)
        [pid] = self.worker_pids(expect=1)
        self.assertTrue(alive(pid))
        self.board({'number': issue, 'status': 'Building'})  # the builder moved its card
        return branch, pid

    # FR-RUN-10: stop comments on issue and PR, releases the claim, stops the worker,
    # keeps branch, PR and worktree.
    def test_stop(self):
        branch, pid = self.started_build(12, pr=34)
        self.calls_path.unlink()
        self.stop()
        self.wait_dead(pid)
        calls = self.calls()
        comments = [c for c in calls if c[:2] in (['issue', 'comment'], ['pr', 'comment'])]
        self.assertEqual([c[:3] for c in comments], [['issue', 'comment', '12'], ['pr', 'comment', '34']])
        self.assertTrue(all('stopped' in c[c.index('--body') + 1] for c in comments))
        self.assertIn(['issue', 'edit', '12', '--remove-assignee', 'bot'], calls)
        self.assertIsNone(self.lock(12))
        self.assertFalse([c for c in calls if c[:2] in (['pr', 'close'], ['pr', 'merge'], ['issue', 'close'])])
        self.assertTrue(self.git('branch', '--list', branch).strip())
        self.assertTrue(self.git('ls-remote', '--heads', 'origin', branch).strip())
        self.assertTrue((self.root / '.drover/worktrees/issue-12-build').is_dir())

    # FR-RUN-11: run after a stop sends the card back to a builder on the same branch.
    def test_resume_after_stop(self):
        branch, first = self.started_build(12, pr=34)
        self.stop()
        self.wait_dead(first)
        self.calls_path.unlink()
        self.run_board(cycles=1)
        calls = self.calls()
        self.assertIn(['project', 'item-edit', '--id', 'item12', '--project-id', 'P', '--field-id', 'F',
                       '--single-select-option-id', 'opt_Ready'], calls)
        [note] = [c for c in calls if c[:3] == ['issue', 'comment', '12']]
        self.assertIn('branch %s kept' % branch, note[note.index('--body') + 1])
        self.assertTrue(self.git('ls-remote', '--heads', 'origin', branch).strip(), 'branch was dropped')
        self.assertFalse([c for c in calls if c[:2] in (['pr', 'create'], ['pr', 'close'])])
        [second] = [p for p in self.worker_pids(expect=2) if p != first]
        self.assertIn('Run drover-build on issue #12 ', (self.workers / str(second)).read_text())

    # FR-HRN-4: the worker starts with the harness's permissions and the checked skills.
    def test_worker_starts_with_harness_permissions(self):
        self.board({'number': 8, 'status': 'Ready', 'labels': ['bug']})
        self.run_board(cycles=1, permissions=dict(allow_commands=['rtk']))
        [pid] = self.worker_pids(expect=1)
        argv = (self.workers / str(pid)).read_text()
        harness = json.loads((KIT / 'harnesses/claude.json').read_text())['permissions']
        for flag in (harness['allow_flag'], harness['mode_flag'], harness['deny_flag']):
            self.assertIn(' %s ' % flag, argv)
        self.assertNotIn('--add-dir', argv)
        self.assertIn('Bash(drover:*)', argv)
        self.assertIn('Bash(true:*)', argv)  # the project's verify command
        home = '//' + str(KIT).lstrip('/') + '/**'
        args = (self.workers / ('argv-%d' % pid)).read_text().split('\0')
        allow = args[args.index(harness['allow_flag']) + 1].split(',')
        deny = args[args.index(harness['deny_flag']) + 1].split(',')
        self.assertIn('Read(%s)' % home, allow)  # the install folder is readable ...
        self.assertIn('Edit(%s)' % home, deny)  # ... and never edited (Edit covers every file tool)
        for tool in ('Write', 'MultiEdit', 'NotebookEdit'):  # Claude rejects path rules on these
            self.assertNotIn('%s(%s)' % (tool, home), deny)
        self.assertIn('Bash(rtk:*)', allow)  # permissions.allow_commands
        self.assertEqual((self.workers / ('stdin-%d' % pid)).read_text(), 'True', 'worker stdin is not /dev/null')
        self.assertIn('Bash(gh pr merge:*)', deny)
        self.assertEqual((self.workers / ('env-%d' % pid)).read_text(), 'DROVER_ROLE=build DROVER_ISSUE=8')
        self.assertIn('Skills for this card', argv)
        self.assertIn('mattpocock-skills:diagnosing-bugs=' + str(self.root / '.claude/skills/diagnosing-bugs/SKILL.md'), argv)
        self.assertNotIn('drover-skills-ready', argv)  # no readiness command left for the worker

    # permissions.allow_commands is schema-checked: one command word each.
    def test_allow_commands_rejects_more_than_a_word(self):
        cfg = self.base / 'cfg.json'
        cfg.write_text(json.dumps({'project': {'owner': 'o', 'number': 1},
                                   'permissions': {'allow_commands': ['rtk', 'rm -rf']}}))
        r = subprocess.run(['python3', str(KIT / 'lib/drover-config.py'), 'validate', str(cfg)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 65, r.stderr)
        self.assertIn('permissions.allow_commands[1]', r.stderr)

    # NFR-6 measurement: GitHub calls in one round with nothing to do. Prints the
    # call list; asserts only the count measured at the fork point (docs/baseline.md).
    def test_idle_round_github_calls(self):
        self.board({'number': 1, 'status': 'Backlog'}, {'number': 2, 'status': 'Blocked'},
                   {'number': 3, 'status': 'Done'})
        out = self.run_board(cycles=1)
        self.assertIn('exiting cleanly', out)
        self.assertEqual(self.worker_pids(), [])
        startup, round_ = self.calls('startup'), self.calls('round')
        for name, calls in [('startup', startup), ('idle round', round_)]:
            print('\n%s: %d gh calls' % (name, len(calls)), file=sys.stderr)
            for c in calls:
                print('  gh ' + ' '.join(a.split('\n')[0] for a in c), file=sys.stderr)
        self.assertLessEqual(len(round_), IDLE_ROUND_CALLS)


if __name__ == '__main__':
    unittest.main()
