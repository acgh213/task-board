"""Register the 4 coder clone agents for the Task Board."""
import requests
import json

BASE = 'http://127.0.0.1:8893/api'

clones = [
    {
        'name': 'coder-alpha',
        'display_name': 'Coder Alpha',
        'model': 'deepseek-v4-flash',
        'role': 'worker',
        'skills': 'python,flask,api,backend',
        'preferred_projects': 'task-board',
        'max_concurrent': 2,
    },
    {
        'name': 'coder-beta',
        'display_name': 'Coder Beta',
        'model': 'deepseek-v4-flash',
        'role': 'worker',
        'skills': 'python,sqlalchemy,database,migrations',
        'preferred_projects': 'task-board',
        'max_concurrent': 2,
    },
    {
        'name': 'coder-gamma',
        'display_name': 'Coder Gamma',
        'model': 'gpt-5-nano',
        'role': 'worker',
        'skills': 'python,testing,pytest,validation',
        'preferred_projects': 'task-board',
        'max_concurrent': 2,
    },
    {
        'name': 'coder-delta',
        'display_name': 'Coder Delta',
        'model': 'deepseek-v4-flash',
        'role': 'worker',
        'skills': 'python,docker,systemd,deployment',
        'preferred_projects': 'task-board',
        'max_concurrent': 2,
    },
]

for clone in clones:
    resp = requests.post(f'{BASE}/agents', json=clone)
    print(f"{clone['name']}: {resp.status_code} - {resp.json().get('name', resp.json().get('error', ''))}")
