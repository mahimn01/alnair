"""Model routing fixtures and benchmark scaffolding; no live requests."""
import json,py_compile,subprocess,sys
from pathlib import Path
import pytest
from memload import launcher as L
ASSETS=Path(__file__).resolve().parents[1]/'routing'
ROWS=json.loads((ASSETS/'labelled-prompts.json').read_text())
@pytest.mark.parametrize('gate',[False,True])
@pytest.mark.parametrize('row',ROWS,ids=[r['label']+'-'+str(i) for i,r in enumerate(ROWS)])
def test_labelled_route(row,gate):
    js='import {route} from '+json.dumps((L.LAB_ROOT/'plugin/hooks/register.js').as_uri())+';console.log(JSON.stringify(route(process.argv[1],"auto",false,process.argv[2]==="true")));'
    d=json.loads(subprocess.check_output(['node','--input-type=module','-e',js,row['prompt'],str(gate).lower()],text=True))
    assert (d.get('tier')=='extreme') == (row['label']=='extreme')
    assert d['model'] == ('claude-fable-5-2' if gate and row['label']=='extreme' else 'claude-opus-5-5' if row['label'] in {'extreme','hard'} and 'IBKR' not in row['prompt'] else 'claude-sonnet-5-5')
@pytest.mark.parametrize('task',['arborescence','circulation','steiner','extensions'])
def test_oracle_rejects_wrong_solution(tmp_path,task):
    wrong=tmp_path/'solution.py';wrong.write_text('def solve(*args):return -999999999\n')
    r=subprocess.run([sys.executable,str(ASSETS/'grade-benchmark.py'),task,str(wrong)],text=True,capture_output=True,timeout=10)
    assert r.returncode==1
    data=json.loads(r.stdout);assert not data['correct'] and data['total']>=60 and data['passed']==0
@pytest.mark.parametrize('script',['run-benchmark.py','grade-benchmark.py'])
def test_bench_scripts_compile(tmp_path,script):
    py_compile.compile(str(ASSETS/script),cfile=str(tmp_path/'output.pyc'),doraise=True)
