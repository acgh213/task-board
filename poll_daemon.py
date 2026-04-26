#!/usr/bin/env python3
"""
poll_daemon.py — Agent Poll Daemon

A standalone script that agents run to continuously check for work,
claim tasks, send heartbeats, and report results.

Usage:
    python poll_daemon.py --agent coder --interval 15

Optional:
    --api-base   Base URL for the task board API (default: http://localhost:8893/api)
    --interval   Seconds between polls (default: 15)
    --hb-interval Seconds between heartbeats when working (default: 30)
    --backoff    Idle polls before backing off to 60s (default: 3)
"""

import argparse
import json
import time
import sys
import urllib.request
import urllib.error
import logging

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('poll_daemon')


def api_request(method, url, data=None):
    """Make an HTTP request to the API and return parsed JSON."""
    if data is not None:
        body = json.dumps(data).encode('utf-8')
    else:
        body = None

    req = urllib.request.Request(url, data=body, method=method)
    req.add_header('Content-Type', 'application/json')
    req.add_header('X-ExeDev-Email', 'poll-daemon')

    try:
        with urllib.request.urlopen(req) as resp:
            return resp.getcode(), json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode('utf-8'))
        except Exception:
            err_body = {'error': str(e)}
        return e.code, err_body
    except urllib.error.URLError as e:
        return 0, {'error': f'Connection failed: {e.reason}'}


def get_pending_tasks(api_base, agent_name):
    """Fetch pending tasks matching this agent's skills."""
    url = f'{api_base}/overseer/pending-for-agent/{agent_name}'
    code, data = api_request('GET', url)
    if code == 200:
        return data.get('tasks', [])
    log.warning(f'Failed to fetch pending tasks: {data.get("error", "unknown")}')
    return []


def claim_task(api_base, task_id, agent_name):
    """Claim a specific task."""
    url = f'{api_base}/tasks/{task_id}/claim'
    code, data = api_request('POST', url, data={'agent': agent_name})
    if code == 200:
        log.info(f'Claimed task {task_id}')
        return True, data
    log.warning(f'Failed to claim task {task_id}: {data.get("error", "unknown")}')
    return False, data


def start_task(api_base, task_id, agent_name):
    """Start working on a claimed task."""
    url = f'{api_base}/tasks/{task_id}/start'
    code, data = api_request('POST', url, data={'agent': agent_name})
    if code == 200:
        log.info(f'Started task {task_id}')
        return True
    log.warning(f'Failed to start task {task_id}: {data.get("error", "unknown")}')
    return False


def send_heartbeat(api_base, task_id, agent_name):
    """Send heartbeat for a task."""
    url = f'{api_base}/tasks/{task_id}/heartbeat'
    code, data = api_request('POST', url, data={'agent': agent_name})
    if code == 200:
        return True
    return False


def submit_task(api_base, task_id, agent_name, result_text):
    """Submit completed work."""
    url = f'{api_base}/tasks/{task_id}/submit'
    code, data = api_request('POST', url, data={
        'agent': agent_name,
        'result': result_text,
    })
    if code == 200:
        log.info(f'Submitted task {task_id}')
        return True
    log.warning(f'Failed to submit task {task_id}: {data.get("error", "unknown")}')
    return False


def report_failure(api_base, task_id, agent_name, error_msg):
    """Report a task failure by escalating or releasing."""
    # Try to escalate first, then release
    url = f'{api_base}/tasks/{task_id}/escalate'
    code, data = api_request('POST', url, data={
        'target': 'needs_human',
        'reason': error_msg,
    })
    if code == 200:
        log.info(f'Escalated task {task_id} due to failure: {error_msg}')
        return True

    # Fall back to release
    url = f'{api_base}/tasks/{task_id}/release'
    code, data = api_request('POST', url)
    if code == 200:
        log.info(f'Released task {task_id} due to failure: {error_msg}')
        return True

    log.warning(f'Failed to report failure for task {task_id}: {data.get("error", "unknown")}')
    return False


def agent_heartbeat(api_base, agent_name, status='busy'):
    """Send agent-level heartbeat."""
    url = f'{api_base}/agents/heartbeat'
    code, data = api_request('POST', url, data={
        'agent': agent_name,
        'status': status,
    })
    return code == 200


def run_daemon(agent_name, api_base, interval, hb_interval, backoff):
    """Main daemon loop."""
    log.info(f'Starting poll daemon for agent "{agent_name}"')
    log.info(f'API base: {api_base}')
    log.info(f'Poll interval: {interval}s, Heartbeat interval: {hb_interval}s')

    idle_count = 0
    current_task_id = None
    working = False
    last_hb_time = 0
    current_interval = interval

    while True:
        try:
            if not working:
                # Send agent-level heartbeat
                agent_heartbeat(api_base, agent_name, 'idle')

                # Check for pending tasks matching skills
                pending = get_pending_tasks(api_base, agent_name)

                if pending:
                    idle_count = 0
                    current_interval = interval

                    # Pick the highest-priority (lowest number) task
                    best_task = min(pending, key=lambda t: (t.get('priority', 3), t.get('id', 0)))

                    log.info(f'Found pending task {best_task["id"]}: {best_task.get("title", "untitled")}')

                    # We need to assign first if not already assigned
                    task_id = best_task['id']

                    # Claim the task
                    success, task_data = claim_task(api_base, task_id, agent_name)
                    if success:
                        current_task_id = task_id
                        working = True
                        last_hb_time = time.time()

                        # Start the task
                        start_task(api_base, task_id, agent_name)

                        # Report that work has started
                        log.info(f'Now working on task {task_id}')
                    else:
                        # Task might already be claimed by someone else
                        log.debug(f'Could not claim task {task_id}, skipping')
                else:
                    idle_count += 1
                    if idle_count >= backoff:
                        current_interval = 60
                        if idle_count == backoff:
                            log.info(f'Idle for {backoff}+ polls, backing off to 60s interval')

            else:
                # Working — send heartbeats
                now = time.time()
                if now - last_hb_time >= hb_interval:
                    if send_heartbeat(api_base, current_task_id, agent_name):
                        last_hb_time = now
                        log.debug(f'Heartbeat for task {current_task_id}')
                        agent_heartbeat(api_base, agent_name, 'busy')
                    else:
                        log.warning(f'Heartbeat failed for task {current_task_id}')
                        # Maybe the task timed out or was reclaimed
                        working = False
                        current_task_id = None

                # Simulate work completion (in real usage, agent does actual work)
                # For now, just keep heartbeating — the real agent workflow is external

        except KeyboardInterrupt:
            log.info('Shutting down...')
            if current_task_id:
                report_failure(api_base, current_task_id, agent_name, 'Daemon interrupted')
            break
        except Exception as e:
            log.error(f'Unexpected error: {e}')
            working = False
            current_task_id = None

        time.sleep(current_interval)


def main():
    parser = argparse.ArgumentParser(description='Agent Poll Daemon')
    parser.add_argument('--agent', required=True, help='Agent name')
    parser.add_argument('--api-base', default='http://localhost:8893/api',
                        help='Base URL for the task board API')
    parser.add_argument('--interval', type=int, default=15,
                        help='Poll interval in seconds')
    parser.add_argument('--hb-interval', type=int, default=30,
                        help='Heartbeat interval in seconds when working')
    parser.add_argument('--backoff', type=int, default=3,
                        help='Idle polls before backing off to 60s')
    args = parser.parse_args()

    try:
        run_daemon(
            agent_name=args.agent,
            api_base=args.api_base.rstrip('/'),
            interval=args.interval,
            hb_interval=args.hb_interval,
            backoff=args.backoff,
        )
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == '__main__':
    main()
