#!/usr/bin/env python3
"""Fetch https://api.github.com/repos/<owner>/<name> into a key=value cache for github_repo.
Usage: fetch.py CACHE OWNER/NAME   (no token needed; GitHub allows 60 requests an hour)"""
import json
import os
import sys
import urllib.request


def main():
    cache, repo = sys.argv[1], sys.argv[2]
    req = urllib.request.Request(f'https://api.github.com/repos/{repo}',
                                 headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'deskwidgets'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        r = json.load(resp)
    lines = [f"name={r['full_name']}", f"stars={r['stargazers_count']}", f"forks={r['forks_count']}",
             f"issues={r['open_issues_count']}", f"language={r.get('language') or ''}",
             f"description={' '.join((r.get('description') or '').split())}"]
    tmp = cache + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(tmp, cache)   # the widget never sees a half-written file


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'github_repo: {e}', file=sys.stderr)
