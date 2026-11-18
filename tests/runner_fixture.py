"""Shared fixture for whole runner rounds (lib/drover-run.sh): fakes on PATH for gh, claude,
pgrep and factorybench (NFR-7). The board is a JSON file the fake gh serves; nothing reaches
GitHub, and no real worker or dispatcher on this machine is ever matched or signalled.

A board card is a dict: number, status, and optionally labels, state, reason, comments (issue
comments for the closed-card check and the dependency graph), ingraph (False: missing from it), reports (role reports), body,
merged / merged_base (a merged PR head), pr (an open PR url), needs (approval fixtures).
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

KIT = Path(__file__).resolve().parents[1]
# The skills the readiness check needs for every lane (see test_skill_dispatch.py).
NAMES = ['drover', 'drover-build', 'drover-qa', 'drover-review', 'drover-collect', 'visual', 'git-sync',
         'ui-refine-loop', 'pr-security-review', 'tdd', 'implement', 'codebase-design', 'code-review',
         'diagnosing-bugs', 'verification-before-completion', 'testing-strategy']
BODY = '## Acceptance Criteria\n- it works\n\n## Blocked by\n- None.\n'

FAKE_GH = r'''
import glob, json, os, re, subprocess, sys, time
args = sys.argv[1:]
manifest = ''.join(open(p).read() for p in glob.glob('.drover/runs/*.md'))
phase = 'round' if 'initial Ready count' in manifest else 'startup'
with open(os.environ['CALL_LOG'], 'a') as f:
    f.write(json.dumps({'phase': phase, 'args': args}) + '\n')
board = json.load(open(os.environ['BOARD']))
cols = ['Backlog', 'Ready', 'Building', 'QA', 'Review', 'Blocked', 'Done']
BODY = %r
joined = ' '.join(args)
def card(n):
    return next((c for c in board if c['number'] == n), {})
def save():
    json.dump(board, open(os.environ['BOARD'], 'w'))
if os.environ.get('USAGE_DROP_AFTER') and args[:2] == ['api', 'rate_limit']:
    # After N rate-limit reads (one per fresh round) usage drops below the pause threshold;
    # from USAGE_BLIND_AT reads until then, the snapshot is gone (the probe goes blind).
    count = os.environ['USAGE_FILE'] + '.reads'
    n = int(open(count).read()) + 1 if os.path.exists(count) else 1
    open(count, 'w').write(str(n))
    if n >= int(os.environ['USAGE_DROP_AFTER']):
        json.dump({'recorded_at': int(time.time()), 'rate_limits': {'five_hour': {
            'used_percentage': 40, 'resets_at': int(time.time()) + 3600}}}, open(os.environ['USAGE_FILE'], 'w'))
    elif n >= int(os.environ.get('USAGE_BLIND_AT', '999999')) and os.path.exists(os.environ['USAGE_FILE']):
        os.remove(os.environ['USAGE_FILE'])
if args[:2] == ['api', 'rate_limit']:
    print('{"resources":{"graphql":{"remaining":5000,"reset":0},"core":{"remaining":5000,"reset":0}}}')
elif args[:2] == ['api', 'graphql'] and 'issues(states:OPEN' in joined:
    # The dependency graph's open issues: open board cards plus OPEN_EXTRA issues off the board.
    extra = [{'number': int(n)} for n in os.environ.get('OPEN_EXTRA', '').split(',') if n]
    nodes = [{'number': c['number'], 'title': 'fixture', 'body': c.get('body', BODY), 'state': 'OPEN',
              'labels': {'nodes': [{'name': l} for l in c.get('labels', [])]},
              'comments': {'nodes': [{'body': b} for b in c.get('comments', [])]}}
             for c in board + extra if c.get('state', 'OPEN') == 'OPEN' and c.get('ingraph', True)]
    print(json.dumps([{'data': {'repository': {'issues': {
        'nodes': nodes, 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}]))
elif args[:2] == ['api', 'graphql'] and 'pullRequest(number:' in joined:
    pr = int(next(a for a in args if a.startswith('pr='))[3:])
    c = next(c for c in board if (c.get('needs') or {}).get('pr') == pr)
    print(json.dumps([{'data': {'repository': {'defaultBranchRef': {'name': 'main'}, 'pullRequest': {
        'headRefOid': c['needs']['current'], 'state': 'OPEN', 'baseRefName': 'main', 'body': 'Closes #%%d' %% c['number'],
        'closingIssuesReferences': {'pageInfo': {'hasNextPage': False, 'endCursor': None},
                                    'nodes': [{'number': c['number'], 'repository': {'nameWithOwner': 'o/r'}}]}}}}}]))
elif args[:2] == ['api', 'graphql'] and '--paginate' in args:
    nodes = [{'id': 'item%%d' %% c['number'], 'fieldValueByName': {'name': c['status']},
              'content': {'__typename': 'Issue', 'number': c['number'], 'title': 'fixture',
                          'url': 'https://github.com/o/r/issues/%%d' %% c['number'],
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
        print(json.dumps({'number': int(args[2]), 'title': 'fixture', 'body': c.get('body', BODY)}))
elif args[:2] == ['project', 'field-list']:
    print(json.dumps({'fields': [{'id': 'F', 'name': 'Status',
                                  'options': [{'id': 'opt_' + s, 'name': s} for s in cols]}]}))
elif args[:2] == ['project', 'view']:
    print('{"id":"P"}')
elif args[:2] == ['pr', 'list'] and 'merged' in args:
    base = args[args.index('--base') + 1]
    print(json.dumps([{'headRefName': c['merged']} for c in board
                      if c.get('merged') and c.get('merged_base', 'main') == base]))
elif args[:2] == ['pr', 'list'] and 'url,mergedAt' in args:
    print(json.dumps([{'url': c['pr'], 'mergedAt': None} for c in board if c.get('pr') and c.get('pr_closed')]))
elif args[:2] == ['pr', 'list'] and 'headRefName,url' in args:
    print(json.dumps([{'headRefName': 'issue-%%d-fix' %% c['number'], 'url': c['pr']} for c in board if c.get('pr')]))
elif args[0] == 'api' and re.fullmatch(r'repos/o/r/collaborators/[^/]+/permission', args[-1]):
    print(json.dumps({'permission': 'write'}))
elif args[0] == 'api' and re.fullmatch(r'repos/(\{owner\}/\{repo\}|o/r)/issues/\d+/comments(\?.*)?', [a for a in args if a.startswith('repos/')][0]):
    path = [a for a in args if a.startswith('repos/')][0]
    n = int(path.split('/')[4].split('?')[0])
    c = card(n)
    if (c.get('needs') or {}).get('comments') is not None:
        print(json.dumps([c['needs']['comments']]))
    else:
        now = time.strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ', time.gmtime())
        print(json.dumps([[{'id': i + 1, 'body': b, 'user': {'login': 'bot'}, 'created_at': now, 'updated_at': now}
                           for i, b in enumerate(c.get('reports', []))]]))
elif args[:2] == ['pr', 'list']:
    m = re.search(r'issue-(\d+)-', joined)
    print(os.environ.get('PR_FOR_' + m.group(1), '') if m else '')
elif args[:2] == ['project', 'item-edit'] and os.environ.get('ITEM_EDIT_FAILS'):
    sys.exit('item-edit failed')
elif args[:2] == ['project', 'item-edit']:  # the board remembers column moves
    item, option = args[args.index('--id') + 1], args[args.index('--single-select-option-id') + 1]
    for c in board:
        if 'item%%d' %% c['number'] == item:
            c['status'] = option[len('opt_'):]
    save()
elif args[:2] == ['issue', 'edit'] and '--add-label' in args:
    if os.environ.get('LABEL_FAILS'):
        sys.exit('could not add label')
    c = card(int(args[2]))
    c['labels'] = c.get('labels', []) + args[args.index('--add-label') + 1].split(',')
    save()
elif args[:2] in (['issue', 'edit'], ['issue', 'comment'], ['pr', 'comment']):
    pass
else:
    sys.exit('unexpected gh call: %%r' %% args)
''' % BODY

# A live worker: records its pid, then runs until signalled (60 s cap).
# FAKE_CLAUDE=exit makes it a worker that ends at once without touching the board;
# FAKE_CLAUDE=handoff moves its card to Blocked, then keeps running; FAKE_CLAUDE=strand
# moves its card to Building, then ends. The pre-flight and classifier agents answer at once
# from FAKE_PREFLIGHT ({"<n>": verdict}; default proceed) and FAKE_CLASSIFY ({"<n>": answer}),
# and are recorded in WORKER_DIR/agents.jsonl, not as workers.
FAKE_CLAUDE = r'''
import json, os, re, signal, sys, time
from pathlib import Path
d = Path(os.environ['WORKER_DIR']); d.mkdir(exist_ok=True)
if sys.argv[1:] == ['--version']:
    print('2.1.400 (Claude Code)')
    sys.exit(0)
role = os.environ.get('DROVER_ROLE')
if role in ('preflight', 'classify'):
    # FAKE_AGENT=garbage answers with no usable JSON, =hang never answers (until signalled),
    # =<role>-garbage or =<role>-hang only for that role. The reply is wrapped the way
    # `claude -p --output-format json` wraps it: one envelope whose `result` holds the text.
    prompt = sys.argv[2]
    model = sys.argv[sys.argv.index('--model') + 1] if '--model' in sys.argv else ''
    with open(d / 'agents.jsonl', 'a') as f:
        f.write(json.dumps({'role': role, 'prompt': prompt, 'model': model, 'argv': sys.argv[1:]}) + '\n')
    mode = os.environ.get('FAKE_AGENT', '')
    mode = mode.split('-', 1)[1] if mode.startswith(role + '-') else ('' if '-' in mode else mode)
    if mode == 'hang':
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        (d / ('agent-hang-%d' % os.getpid())).write_text(role)
        time.sleep(60)
        sys.exit(0)
    if role == 'preflight':
        issues = re.search(r'--issues ([0-9,]+)', prompt).group(1).split(',')
        script = json.loads(os.environ.get('FAKE_PREFLIGHT', '{}'))
        out = [dict({'number': int(n)}, **script.get(n, {'verdict': 'proceed', 'tag': 'clean', 'detail': 'clean'}))
               for n in issues if script.get(n) != 'none']
        answer = json.dumps({'verdicts': out})
    else:
        n = re.search(r'issue #(\d+)', prompt).group(1)
        answer = json.dumps(json.loads(os.environ.get('FAKE_CLASSIFY', '{}')).get(
            n, {'kind': 'feature', 'complexity': 'medium', 'risk': [], 'why': 'fixture'}))
    if mode == 'garbage':
        answer = '{"verdicts": [{"number": "x", "verdict": 7, "detail": {"a": 1}}], "complexity": 9'
    text = 'Here is my answer, as asked: {not json}\n' + answer + '\nDone.'
    if '--output-format' in sys.argv and sys.argv[sys.argv.index('--output-format') + 1] == 'json':
        print(json.dumps({'type': 'result', 'is_error': False, 'result': text}))
    else:
        print(text)
    sys.exit(0)
(d / ('argv-%d' % os.getpid())).write_text('\0'.join(sys.argv[1:]))
(d / ('env-%d' % os.getpid())).write_text('DROVER_ROLE=%s DROVER_ISSUE=%s' % (os.environ.get('DROVER_ROLE'), os.environ.get('DROVER_ISSUE')))
(d / ('otel-%d' % os.getpid())).write_text(json.dumps({k: v for k, v in os.environ.items()
                                                      if k.startswith(('OTEL_', 'CLAUDE_CODE_', 'GIT_AUTHOR_EMAIL'))}))
(d / ('stdin-%d' % os.getpid())).write_text(str(os.path.samestat(os.fstat(0), os.stat(os.devnull))))
(d / str(os.getpid())).write_text(' '.join(sys.argv[1:]))
if os.environ.get('FAKE_CLAUDE') == 'exit':
    sys.exit(0)
if os.environ.get('FAKE_CLAUDE') in ('handoff', 'strand', 'advance'):
    board = json.load(open(os.environ['BOARD']))
    nxt = {'build': 'QA', 'qa': 'Review', 'review': 'Done'}[os.environ['DROVER_ROLE']]
    for c in board:
        if c['number'] == int(os.environ['DROVER_ISSUE']):
            c['status'] = {'handoff': 'Blocked', 'strand': 'Building', 'advance': nxt}[os.environ['FAKE_CLAUDE']]
            if c['status'] == 'Done':
                c['state'] = 'CLOSED'
    json.dump(board, open(os.environ['BOARD'], 'w'))
    if os.environ['FAKE_CLAUDE'] != 'handoff':
        sys.exit(0)
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
end = time.time() + 60
while time.time() < end:
    time.sleep(.05)
'''

# Records every call; never contacts anything (FactoryBench's CLI, for FR-MET-4..12).
FAKE_FACTORYBENCH = r'''
import json, os, sys
with open(os.environ['FB_LOG'], 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\n')
'''


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith('Z')


class RunnerCase(unittest.TestCase):
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
                        SB_USAGE_FILE=str(self.base / 'no-usage.json'),
                        GIT_AUTHOR_NAME='f', GIT_AUTHOR_EMAIL='f@example.com',
                        GIT_COMMITTER_NAME='f', GIT_COMMITTER_EMAIL='f@example.com')
        for k in ('FACTORYBENCH_URL', 'FACTORYBENCH_TOKEN'):
            self.env.pop(k, None)
        # No drover code may search the process table: a pgrep call fails the test.
        pgrep = "import os, sys; open(os.environ['PGREP_CALLED'], 'w').write(' '.join(sys.argv)); sys.exit(1)"
        self.env['PGREP_CALLED'] = str(self.base / 'pgrep-called')
        self.addCleanup(lambda: self.assertFalse((self.base / 'pgrep-called').exists(), 'pgrep was called'))
        for name, source in [('gh', FAKE_GH), ('claude', FAKE_CLAUDE), ('pgrep', pgrep)]:
            self.tool(name, source)
        self.git('init', '-q', '-b', 'main')
        self.git('commit', '-q', '--allow-empty', '-m', 'base')
        for name in NAMES:
            skill = self.root / '.claude/skills' / name / 'SKILL.md'
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text('fixture skill instructions')
        self.state = self.root / '.claude/drover'
        (self.root / '.drover').mkdir()
        self.addCleanup(self.stop_workers)

    def tool(self, name, source):
        path = self.bin / name
        path.write_text('#!/usr/bin/env python3\n' + source)
        path.chmod(0o755)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.root, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def board(self, *cards):
        self.board_path.write_text(json.dumps(list(cards)))

    def read_board(self):
        return {c['number']: c for c in json.loads(self.board_path.read_text())}

    def run_board(self, cycles, rc=0, **extra):
        # no_progress_cycles=N stops the run after N rounds with no new Done card.
        cfg = dict(project=dict(owner='o', number=1), base_branch='main', notifications=dict(bot_identity='bot'),
                   verify_commands=['true'], human_approves_merge=True, tick_seconds=0, no_progress_cycles=cycles,
                   repo=dict(remote='https://github.com/o/r'))
        cfg.update(extra)
        (self.root / '.drover/config.json').write_text(json.dumps(cfg))
        result = subprocess.run(['bash', str(KIT / 'lib/drover-run.sh')], cwd=self.root,
                                env=self.env, text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, rc, result.stdout + result.stderr)
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

    def agents(self, role=None):
        path = self.workers / 'agents.jsonl'
        rows = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
        return [r for r in rows if role in (None, r['role'])]

    def wait_for(self, check, seconds=10):
        # For effects that land in the background (FactoryBench emits are fire and forget).
        deadline = time.monotonic() + seconds
        while not check() and time.monotonic() < deadline:
            time.sleep(.05)
        return check()

    def comments(self, issue):
        return [c[c.index('--body') + 1] for c in self.calls() if c[:3] == ['issue', 'comment', str(issue)]]

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

    def manifest(self):
        return next((self.root / '.drover/runs').glob('*-repo.md')).read_text()

    def moves(self, column):
        return [c[c.index('--id') + 1] for c in self.calls()
                if c[:2] == ['project', 'item-edit'] and c[-1] == 'opt_' + column]

    def done_moves(self):
        return self.moves('Done')
