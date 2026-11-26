#!/usr/bin/env python3
"""FactoryBench from the runner (metrics.md FR-MET-4..12): attempt events through a fake
`factorybench` that records its calls, the OTLP env per worker, and silence without it.
Fakes: tests/runner_fixture.py."""
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner_fixture import FAKE_FACTORYBENCH, KIT, RunnerCase  # noqa: E402


def block(tag, by):
    return ('[builder] [blocker] blocked %s fixture\nCard:        #1\nReason tag:  %s\nWhy blocked: x\n'
            'blocked-by:  %s' % (tag, tag, by))


class FactoryBench(RunnerCase):
    def factorybench(self):
        self.tool('factorybench', FAKE_FACTORYBENCH)
        self.env.update(FB_LOG=str(self.base / 'fb.jsonl'), FACTORYBENCH_URL='http://fb.invalid')

    def events(self):
        path = self.base / 'fb.jsonl'
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    ORDER = ['started', 'resumed', 'waiting', 'pr', 'gate', 'send_back', 'finished']

    def emits(self, count):
        # Emits run in the background (fire and forget): wait for them, then order them by the
        # event time they carry and the lifecycle, as FactoryBench does.
        self.wait_for(lambda: len([e for e in self.events() if e[1] != '--flush']) >= count)
        rows = [e for e in self.events() if e[0] == 'emit' and e[1] != '--flush']
        self.assertTrue(all(e[2] == '--attempt' and e[4] == '--key' and e[6] == '--json' and e[8] == '--ts' for e in rows), rows)
        return sorted(rows, key=lambda e: (e[9], self.ORDER.index(e[5].split(':')[0]), e[5]))

    # FR-MET-4..12: one attempt per card with its lanes, sent through `factorybench emit`.
    def test_factorybench_attempt(self):
        self.factorybench()
        self.env['FAKE_CLAUDE'] = 'advance'  # each worker moves its card one column and exits
        self.board({'number': 7, 'status': 'Ready', 'pr': 'https://github.com/o/r/pull/70'})
        out = self.run_board(cycles=60, metrics=dict(commit_email='drover@example.com'))
        self.assertIn('exiting cleanly', out)
        emits = self.emits(6)
        self.assertTrue(self.wait_for(lambda: ['emit', '--flush'] in self.events()), 'the spool is flushed at start')
        self.assertEqual(sorted((e[1], e[5]) for e in emits), sorted([
            ('attempt.started', 'started'), ('pr.linked', 'pr'), ('gate', 'gate:qa:1'),
            ('gate', 'gate:merge:1'), ('gate', 'gate:review:1'), ('attempt.finished', 'finished')]))
        self.assertEqual((emits[0][5], emits[1][5], emits[2][5], emits[-1][5]), ('started', 'pr', 'gate:qa:1', 'finished'))
        self.assertEqual(len({e[3] for e in emits}), 1, 'one attempt for the card')
        started = json.loads(emits[0][7])
        self.assertEqual(started['work_item'], {'repo': 'o/r', 'issue': 7})
        self.assertRegex(started['factory_config_version'], r'^drover@[0-9a-f]+\+claude-code@2\.1\.400\+cfg:[0-9a-f]{8}$')
        self.assertIn('feature', started['labels'])
        self.assertNotIn('role', started)
        self.assertEqual(json.loads(emits[1][7]), {'pr_url': 'https://github.com/o/r/pull/70'})
        self.assertEqual(json.loads(emits[2][7]), {'name': 'qa', 'passed': True, 'reason': 'QA passed'})
        self.assertEqual(json.loads(emits[-1][7]), {'status': 'completed'})
        attempt = emits[0][3]
        phases = []
        for pid in self.worker_pids():
            env = json.loads((self.workers / ('otel-%d' % pid)).read_text())
            self.assertEqual(env['OTEL_EXPORTER_OTLP_LOGS_ENDPOINT'], 'http://fb.invalid/v1/logs')
            self.assertEqual(env['CLAUDE_CODE_ENABLE_TELEMETRY'], '1')
            self.assertEqual(env['GIT_AUTHOR_EMAIL'], 'drover@example.com')
            self.assertFalse({'OTEL_LOG_USER_PROMPTS', 'OTEL_LOG_TOOL_DETAILS', 'OTEL_LOG_RAW_API_BODIES'} & set(env))
            phases.append(env['OTEL_RESOURCE_ATTRIBUTES'])
        self.assertEqual(sorted(phases), sorted('factorybench.attempt=%s,factorybench.phase=%s,factorybench.step=%s' % (attempt, p, l)
                                                for p, l in (('implement', 'build'), ('verify', 'qa'), ('review', 'review'))))
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
        emits = self.emits(3)
        self.assertEqual([(e[1], e[5]) for e in emits], [
            ('attempt.started', 'started'), ('human.waiting', 'waiting:1'), ('human.resumed', 'resumed:1')])
        self.assertEqual(json.loads(emits[1][7]), {'reason': 'stopped'})
        self.assertEqual(len({e[3] for e in emits}), 1)

    # FR-MET-5: Blocked waits the attempt with its reason tag, leaving Blocked resumes it, Backlog
    # throws it away; a PR closed unmerged ends the attempt as failed too.
    def test_factorybench_blocked_backlog_and_closed_pr(self):
        self.factorybench()
        self.env['FAKE_CLAUDE'] = 'handoff'  # the builder moves its card to Blocked
        self.board({'number': 7, 'status': 'Ready'})
        self.run_board(cycles=4)
        self.stop_workers()
        board = self.read_board()
        board[7].update(status='Blocked', comments=[block('[blocked:creds]', '-')])
        self.board(*board.values())
        self.run_board(cycles=1)
        board[7].update(status='Review', comments=[])
        self.board(*board.values())
        self.env['FAKE_CLAUDE'] = 'exit'
        self.run_board(cycles=1)
        self.stop_workers()
        board[7].update(status='Backlog')
        self.board(*board.values())
        self.run_board(cycles=1)
        emits = self.emits(4)
        self.assertEqual([(e[1], e[5]) for e in emits], [
            ('attempt.started', 'started'), ('human.waiting', 'waiting:1'), ('human.resumed', 'resumed:1'),
            ('attempt.finished', 'finished')])
        self.assertIn(json.loads(emits[1][7])['reason'], ('blocked', '[blocked:creds]'))
        self.assertEqual(json.loads(emits[3][7]), {'status': 'failed', 'reason': 'moved back to Backlog'})

        (self.base / 'fb.jsonl').unlink()
        self.env['FAKE_CLAUDE'] = 'advance'
        self.board({'number': 8, 'status': 'Ready', 'pr': 'https://github.com/o/r/pull/80', 'pr_closed': True})
        self.run_board(cycles=60)
        self.wait_for(lambda: any(e[1] == 'attempt.finished' for e in self.events()))
        finished = [json.loads(e[7]) for e in self.events() if e[1] == 'attempt.finished']
        self.assertEqual(finished[0], {'status': 'failed', 'reason': 'PR closed unmerged'})

    # FR-MET-5/9: a bounce is a failed gate plus a send_back with a fixed key per round.
    def test_factorybench_send_back(self):
        self.factorybench()
        run = lambda *a: subprocess.run([sys.executable, str(KIT / 'lib/drover-metrics.py'), *a], cwd=self.root,
                                        env=self.env, capture_output=True, text=True, check=True).stdout
        attempt = run('claim', '7', 'o/r', 'feature', '', 'v').strip()
        run('send-back', '7', 'qa', 'build', 'bounced to Ready')
        run('send-back', '7', 'review', 'qa', 'bounced to QA')
        backs = [e for e in self.emits(3) if e[1] == 'send_back']
        self.assertEqual([(e[3], e[5]) for e in backs], [(attempt, 'send_back:1'), (attempt, 'send_back:2')])
        self.assertEqual(json.loads(backs[0][7]), {'from': 'qa', 'to': 'build', 'kind': 'fix-attempt', 'reason': 'bounced to Ready'})
        self.assertEqual(json.loads(backs[1][7])['to'], 'qa')

    # FR-MET-4: without FACTORYBENCH_URL nothing is sent and nothing is said about it.
    def test_factorybench_silent_without_url(self):
        self.factorybench()
        del self.env['FACTORYBENCH_URL']
        self.env['FAKE_CLAUDE'] = 'advance'
        self.board({'number': 7, 'status': 'Ready'})
        out = self.run_board(cycles=60)
        time.sleep(.3)  # anything sent would be a background process by now
        self.assertEqual(self.events(), [])
        self.assertFalse((self.root / '.drover/runs/attempts.json').exists())
        self.assertNotIn('factorybench', (out + self.manifest()).lower())
        for pid in self.worker_pids():
            self.assertNotIn('OTEL_RESOURCE_ATTRIBUTES', (self.workers / ('otel-%d' % pid)).read_text())


if __name__ == '__main__':
    unittest.main()
