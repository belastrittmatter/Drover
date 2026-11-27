#!/usr/bin/env python3
"""Whole runner rounds (lib/drover-run.sh) with fakes on PATH for gh, claude and pgrep
(tests/runner_fixture.py, NFR-7). Nothing reaches GitHub, and no real worker or dispatcher
on this machine is ever matched or signalled."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner_fixture import KIT, RunnerCase, alive  # noqa: E402

# 3 measured at d0b7d5a (docs/baseline.md), plus the dependency-graph read a board with a
# Blocked card now costs (FR-BRD-5). NFR-6 caps an idle round at 5.
IDLE_ROUND_CALLS = 4


class RunnerRounds(RunnerCase):
    # FR-WRK-1: a worker's lock is removed only when the worker is not alive.
    def test_production_guard_reads_merge_policy(self):
        # A deploy-on-main repo: auto-merge refuses (exit 75); merge_policy "human" starts.
        (self.root / 'vercel.json').write_text('{}')
        self.board()
        out = self.run_board(1, rc=75, human_approves_merge=False)
        self.assertIn('would auto-merge to production main', out)
        out = self.run_board(1, human_approves_merge=False, merge_policy=dict(default='human'))
        self.assertNotIn('would auto-merge', out)

    def test_live_worker_lock_survives_a_round(self):
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=2)  # round 1 dispatches, round 2 sees the lock
        [pid] = self.worker_pids(expect=1)
        self.assertTrue(alive(pid), 'the fake worker should still be running')
        self.assertRegex(self.lock(7) or '', r'^HANDLE=process:%d:\S+\n' % pid, 'live worker lost its lock')
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
            self.assertRegex(self.lock(issue) or '', r'^HANDLE=process:\d+:\S+\nLANE=build\nSTARTED=\S+\nCOLUMN=Ready\n$')
        self.assertIsNone(self.lock(9), 'a third builder started past max_workers 2')
        self.assertIn('max_workers=4 (build=2 qa=1 review=1)', self.manifest())

    # FR-RUN-13: two failed dispatches in a row on one card halt the run with the log tail.
    def test_two_failed_dispatches_halt(self):
        self.env['FAKE_CLAUDE'] = 'exit'  # ends without moving its card or reporting
        self.board({'number': 5, 'status': 'Review'})
        out = self.run_board(cycles=30, rc=76)
        manifest = self.manifest()
        self.assertEqual(manifest.count('dispatch lane=review issue=#5 pid='), 2, manifest)
        self.assertIn('failed dispatch lane=review issue=#5', manifest)
        self.assertIn('halt — failed dispatch: #5 failed 2 dispatches in a row', out)
        self.assertIn('| === dispatch lane=review issue=#5 at ', out)  # the log tail
        self.assertNotIn('RUNAWAY', manifest)
        # Issue #22: a worker that died without a handoff is still a stale lock.
        self.assertEqual(manifest.count('reaped stale lock + swept assignee on #5 (pid='), 2, manifest)
        self.assertNotIn('worker done #5', manifest)

    # Issue #22: a worker that handed its card on and exited logs `worker done`, not a stale lock.
    def test_handed_off_exit_logs_worker_done(self):
        self.env['FAKE_CLAUDE'] = 'advance'  # moves its card one column and exits
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=10000, max_dispatches=2)
        manifest = self.manifest()
        [pid] = [int(l.split('pid=')[1].split()[0]) for l in manifest.splitlines() if 'dispatch lane=build issue=#7 pid=' in l]
        self.assertIn('worker done #7 lane=build (pid=%d)' % pid, manifest)
        self.assertNotIn('reaped stale lock', manifest)

    # FR-RUN-13: a worker that posted its role's report did its job, even with the card
    # where it was (here: a reviewer waiting on the owner's merge approval).
    def test_report_without_a_move_is_not_a_failed_dispatch(self):
        self.env['FAKE_CLAUDE'] = 'exit'
        self.board({'number': 5, 'status': 'Review',
                    'reports': ['[reviewer] [report] awaiting owner · merge needs approval']})
        # Ends on a state, not a tick count: the run stops at the second dispatch, however long a
        # loaded machine takes the first worker to exit (no_progress_cycles is only a ceiling).
        self.run_board(cycles=10000, max_dispatches=2)
        manifest = self.manifest()
        self.assertGreaterEqual(manifest.count('dispatch lane=review issue=#5 pid='), 2, manifest)
        self.assertNotIn('failed dispatch', manifest)

    # FR-RUN-13: a builder that moves its card to Building and dies strands it where no lane
    # looks. It counts as failed, the card goes back to Ready, and a second time halts.
    def test_builder_dying_in_building_is_a_failed_dispatch(self):
        self.env['FAKE_CLAUDE'] = 'strand'
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=30, rc=76)
        manifest = self.manifest()
        self.assertEqual(manifest.count("failed dispatch lane=build issue=#7 — worker exited, card in 'Building'"), 2, manifest)
        self.assertIn('stranded #7: Building → Ready', manifest)
        self.assertEqual(manifest.count('dispatch lane=build issue=#7 pid='), 2, manifest)

    def fake_lock(self, path, handle, lane='build'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('HANDLE=%s\nLANE=%s\nSTARTED=2026-01-01T00:00:00Z\n' % (handle, lane))

    # A pid the OS gave to another process since the lock was written reads as dead and is
    # never signalled: worker locks and runner.lock alike.
    def test_recycled_pid_is_dead_and_never_signalled(self):
        other = subprocess.Popen(['sleep', '60'])
        self.addCleanup(lambda: (other.kill(), other.wait()))
        recycled = 'process:%d:Mon_Jan_1_00:00:00_2001' % other.pid
        self.fake_lock(self.state / 'inflight/7', recycled)
        self.fake_lock(self.state / 'runner.lock', recycled)
        self.board({'number': 1, 'status': 'Backlog'})
        out = self.run_board(cycles=1)  # neither lock stops the run from starting
        self.assertIn('reaped stale lock + swept assignee on #7 (pid=%d)' % other.pid, out)
        self.assertIsNone(self.lock(7))
        self.fake_lock(self.state / 'inflight/8', recycled)
        self.fake_lock(self.state / 'runner.lock', recycled)
        self.stop()
        time.sleep(.2)
        self.assertIsNone(other.poll(), 'a recycled pid was signalled')

    # An older PID= lock has no start time: it blocks a run start but is never signalled.
    def test_old_pid_lock_refuses_start_and_is_not_signalled(self):
        other = subprocess.Popen(['sleep', '60'])
        self.addCleanup(lambda: (other.kill(), other.wait()))
        (self.state / 'inflight').mkdir(parents=True)
        (self.state / 'inflight/7').write_text('PID=%d\nLANE=build\n' % other.pid)
        self.board({'number': 1, 'status': 'Backlog'})
        out = self.run_board(cycles=1, rc=73)
        self.assertIn('older PID= lock (pid %d)' % other.pid, out)
        self.assertIn('not signalled', self.stop())
        time.sleep(.2)
        self.assertIsNone(other.poll(), 'an old PID= lock was signalled')
        self.assertTrue((self.state / 'inflight/7').exists())

    # FR-WRK-10: a worker whose card left its lane runs one more tick, then is stopped.
    def test_worker_gets_one_tick_after_its_card_leaves(self):
        self.env['FAKE_CLAUDE'] = 'handoff'  # moves its card to Blocked and keeps running
        self.board({'number': 7, 'status': 'Ready'})
        out = self.run_board(cycles=30)
        [pid] = self.worker_pids(expect=1)
        self.wait_dead(pid)
        lines = self.manifest().splitlines()
        marked = next(i for i, l in enumerate(lines) if "left its lane (now 'Blocked'); one more tick" in l)
        self.assertNotIn('card moved to', lines[marked])  # FR-MET-1: not an outcome line
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
        [note] = [c for c in calls if c[:3] == ['issue', 'comment', '12'] and 'stranded' in c[-1]]
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

    # FR-RUN-17: a real run leaves state.json with its in-flight card, marked stopped on exit.
    def test_run_state_json_after_a_run(self):
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=2)
        [pid] = self.worker_pids(expect=1)
        state = json.loads((self.root / '.drover/runs/state.json').read_text())
        self.assertIs(state.get('stopped'), True, state)
        self.assertNotIn('pid', state)
        self.assertGreaterEqual(state['tick'], 1)
        [card] = state['cards']
        self.assertEqual((card['issue'], card['lane']), (7, 'build'), card)
        self.assertTrue(card['worker'].startswith('process:%d:' % pid), card)
        self.assertRegex(card['since'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(list((self.root / '.drover/runs').glob('state.json.tmp.*')), [])


if __name__ == '__main__':
    unittest.main()
