#!/usr/bin/env python3
"""Resolve explicit reviewer artifact paths before dispatch; read-only, no model invocation."""
import argparse
import json
import re
from pathlib import Path

FILE = re.compile(r'\.(?:md|txt|json|csv|py|js|ts|tsx|jsx|html|pdf|yaml|yml|toml|log)(?::\d+(?:-\d+)?(?:#[a-f0-9]+)?)?$', re.I)
TOKENS = re.compile(r'`([^`\n]+)`|"([^"\n]+)"|(?<![\w:/])((?:~/|/|\./|\.\./)[^\s`,;<>]+)|(?<![\w:/])([\w.-]+(?:/[\w.-]+)*\.(?:md|txt|json|csv|py|js|ts|tsx|jsx|html|pdf|yaml|yml|toml|log)(?::\d+(?:-\d+)?(?:#[a-f0-9]+)?)?)')


def resolve_prompt(prompt, base, run):
    base=Path(base).expanduser().resolve(); run=Path(run).expanduser().resolve()
    def replace(m):
        token=next(x for x in m.groups() if x is not None)
        if '://' in token or not FILE.search(token): return m.group(0)
        # Do not mistake a command/code snippet for an artifact.
        if re.match(r'^(?:cat|ls|python3?|git|rg|sed|head|tail)\s',token): return m.group(0)
        path_text=re.sub(r'(:\d+(?:-\d+)?(?:#[a-f0-9]+)?)$','',token)
        suffix=token[len(path_text):]
        p=Path(path_text).expanduser()
        candidates=[p] if p.is_absolute() else [base/p,run/p,Path.home()/'.clp/research/docs'/p]
        found=[x.resolve() for x in candidates if x.is_file()]
        if not found: raise ValueError('Reviewer artifact missing: '+path_text+' (base '+str(base)+')')
        if len(set(found))>1: raise ValueError('Reviewer artifact ambiguous: '+path_text+'; use an absolute path')
        result=str(found[0])+suffix
        return '`'+result+'`' if m.group(1) is not None else '"'+result+'"' if m.group(2) is not None else result
    return TOKENS.sub(replace,prompt)


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--run',required=True);p.add_argument('--prompt',required=True)
    a=p.parse_args()
    try:
        # The generated contract must point at real policies, not relative imaginary files.
        for name in ('personal-core.md','trading-policy.md'):
            if not (Path(a.run)/name).is_file(): raise ValueError('Reviewer policy missing: '+str(Path(a.run)/name))
        print(json.dumps({'prompt':resolve_prompt(a.prompt,a.base,a.run)}));return 0
    except ValueError as e: print(str(e),file=__import__('sys').stderr);return 2

if __name__=='__main__': raise SystemExit(main())
