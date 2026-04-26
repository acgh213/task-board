#!/usr/bin/env python3
"""overseer_cron.py — Runs the Overseer's duties via HTTP.

Called by systemd timer every 2 minutes. Performs:
1. Auto-assign pending tasks to best-matching agents
2. Check for timed-out tasks (lease expired)
3. Reclaim timed-out tasks (release or mark dead)
4. Summary report
"""

import json
import urllib.request
import urllib.error
import sys
from datetime import datetime

API_BASE = 'http://localhost:8893/api'
HEALTH_URL = 'http://localhost:8893/health'
HEADERS = {
    'Content-Type': 'application/json',
    'X-ExeDev-Email': 'overseer-cron',
}


def api_call(method, path, data=None):
    """Make an API call and return (status_code, response_dict)."""
    url = f'{API_BASE}{path}'
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
        except Exception:
            err = {'error': str(e)}
        return e.code, err
    except Exception as e:
        return 0, {'error': str(e)}


def check_health():
    """Verify task board is running."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def main():
    now = datetime.now().isoformat()
    report = {'timestamp': now, 'checks': []}

    # 0. Health check
    if not check_health():
        print(f'[{now}] ERROR: Task board not responding at {HEALTH_URL}')
        sys.exit(1)

    # 1. Auto-assign pending tasks
    status, data = api_call('POST', '/overseer/auto-assign')
    report['checks'].append({
        'action': 'auto-assign',
        'status': status,
        'assigned': data.get('assigned', 0),
        'skipped': data.get('skipped', 0),
    })

    # 1b. Auto-triage tasks in triage status
    status, data = api_call('POST', '/overseer/auto-triage')
    report['checks'].append({
        'action': 'auto-triage',
        'status': status,
        'accepted': data.get('accepted', 0),
        'escalated': data.get('escalated', 0),
        'skipped': data.get('skipped', 0),
    })

    # 2. Check timeouts
    status, data = api_call('POST', '/overseer/check-timeouts')
    report['checks'].append({
        'action': 'check-timeouts',
        'status': status,
        'timed_out': data.get('timed_out', 0),
    })

    # 3. Reclaim timed-out tasks
    status, data = api_call('POST', '/overseer/reclaim-timeouts')
    report['checks'].append({
        'action': 'reclaim-timeouts',
        'status': status,
        'released': data.get('released', 0),
        'dead': data.get('dead', 0),
    })

    # Summary
    total_assigned = sum(c.get('assigned', 0) for c in report['checks'])
    total_timed_out = sum(c.get('timed_out', 0) for c in report['checks'])
    total_released = sum(c.get('released', 0) for c in report['checks'])
    total_dead = sum(c.get('dead', 0) for c in report['checks'])

    print(f'[{now}] Overseer run: assigned={total_assigned} timed_out={total_timed_out} released={total_released} dead={total_dead}')

    # Print individual check results
    for check in report['checks']:
        if check['status'] != 200:
            print(f'  WARNING: {check["action"]} returned status {check["status"]}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
