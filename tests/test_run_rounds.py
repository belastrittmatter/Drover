#!/usr/bin/env python3
"""Whole runner rounds (scripts/super-board-run.sh) with fakes on PATH for gh, claude
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
# The skills the readiness check needs for every lane (see test_skill_dispatch.py).
NAMES = ['super-board', 'super-build', 'super-qa', 'super-review', 'super-collect', 'visual', 'git-sync',
         'ui-refine-loop', 'pr-security-review', 'tdd', 'implement', 'codebase-design', 'code-review',
         'diagnosing-bugs', 'verification-before-completion', 'testing-strategy']

FAKE_GH = r'''
import glob, json, os, re, sys
args = sys.argv[1:]
manifest = ''.join(open(p).read() for p in glob.glob('docs/super-board/runs/*-fixture.md'))
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
    elif fields == 'labels':
        print(json.dumps({'labels': [{'name': l} for l in c.get('labels', [])]}))
    else:
        print(json.dumps({'number': int(args[2]), 'title': 'fixture', 'body': ''}))
elif args[:2] == ['project', 'field-list']:
    print(json.dumps({'fields': [{'id': 'F', 'name': 'Status',
                                  'options': [{'id': 'opt_' + s, 'name': s} for s in cols]}]}))
elif args[:2] == ['project', 'view']:
    print('{"id":"P"}')
elif args[:2] == ['pr', 'list']:
    m = re.search(r'issue-(\d+)-', ' '.join(args))
    print(os.environ.get('PR_FOR_' + m.group(1), '') if m else '')
elif args[:2] in (['issue', 'edit'], ['issue', 'comment'], ['pr', 'comment'], ['project', 'item-edit']):
    pass
else:
    sys.exit('unexpected gh call: %r' % args)
'''

# A live worker: records its pid, then runs until signalled (60 s cap).
# FAKE_CLAUDE=exit makes it a worker that ends at once without touching the board.
FAKE_CLAUDE = r'''
import os, signal, sys, time
from pathlib import Path
d = Path(os.environ['WORKER_DIR']); d.mkdir(exist_ok=True)
(d / str(os.getpid())).write_text(' '.join(sys.argv[1:]))
if os.environ.get('FAKE_CLAUDE') == 'exit':
    sys.exit(0)
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
                        SUPER_BOARD_SKILLS_HOME=str(self.home), SB_GITHUB_RETRY_DELAY='0',
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
        self.state = self.root / '.claude/super-board'
        (self.state / 'configs').mkdir(parents=True)
        self.addCleanup(self.stop_workers)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.root, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def board(self, *cards):
        self.board_path.write_text(json.dumps(list(cards)))

    def run_board(self, cycles):
        # no_progress_cycles=N stops the run after N rounds with no new Done card.
        (self.state / 'configs/fixture.json').write_text(json.dumps(dict(
            project=dict(owner='o', number=1), base_branch='main', worker_backend='claude-p',
            max_workers=3, bot_identity='bot', verify_commands=['true'], human_approves_merge=True,
            tick_seconds=0, no_progress_cycles=cycles)))
        result = subprocess.run(['bash', str(KIT / 'scripts/super-board-run.sh'), 'fixture'], cwd=self.root,
                                env=self.env, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def stop(self):
        result = subprocess.run(['bash', str(KIT / 'scripts/super-board-stop.sh'), 'fixture'], cwd=self.root,
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
        return sorted(int(p.name) for p in self.workers.iterdir()) if self.workers.exists() else []

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
        self.assertIn('PID=%d\n' % pid, self.lock(7) or '', 'live worker lost its lock')
        manifest = next((self.root / 'docs/super-board/runs').glob('*-fixture.md')).read_text()
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


if __name__ == '__main__':
    unittest.main()
