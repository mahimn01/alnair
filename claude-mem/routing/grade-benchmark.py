#!/usr/bin/env python3
import importlib.util,itertools,json,math,random,sys,time
from pathlib import Path
kind,path=sys.argv[1:];sp=importlib.util.spec_from_file_location('solution',path);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
rng=random.Random(520055);passed=0;total=0;failures=[]
def check(args,expected):
 global passed,total
 total+=1
 try:got=m.solve(*args)
 except Exception as e:got=type(e).__name__+': '+str(e)
 if got==expected:passed+=1
 elif len(failures)<5:failures.append({'args':args,'expected':expected,'got':got})
def reachable(n,edges,root):
 seen={root}
 while True:
  nxt=seen|{v for u,v,*_ in edges if u in seen}
  if nxt==seen:return seen
  seen=nxt
if kind=='arborescence':
 for _ in range(80):
  n=rng.randrange(2,6);root=rng.randrange(n);edges=[(rng.randrange(n),rng.randrange(n),rng.randrange(-5,10)) for i in range(rng.randrange(1,10))]
  incoming=[[e for e in edges if e[1]==v and e[0]!=v] for v in range(n) if v!=root];best=None
  for chosen in itertools.product(*incoming):
   if len(reachable(n,chosen,root))==n:
    cost=sum(e[2] for e in chosen);best=cost if best is None else min(best,cost)
  check((n,edges,root),best)
 check((100,[(u,v,1) for u in range(100) for v in range(100) if u!=v],0),99)
elif kind=='circulation':
 for _ in range(80):
  n=rng.randrange(2,5);edges=[]
  for i in range(rng.randrange(1,8)):
   lo=rng.randrange(2);edges.append((rng.randrange(n),rng.randrange(n),lo,rng.randrange(lo,3),rng.randrange(-4,5)))
  best=None
  for flows in itertools.product(*(range(lo,hi+1) for u,v,lo,hi,c in edges)):
   bal=[0]*n;cost=0
   for (u,v,lo,hi,c),f in zip(edges,flows):bal[u]-=f;bal[v]+=f;cost+=c*f
   if not any(bal):best=cost if best is None else min(best,cost)
  check((n,edges),best)
 check((4,[(0,1,0,30,-3),(1,0,0,30,1),(2,3,2,5,1),(3,2,0,5,1)]),-56)
elif kind=='steiner':
 for _ in range(80):
  n=rng.randrange(2,6);root=rng.randrange(n);terms=rng.sample(range(n),rng.randrange(1,min(3,n)+1));edges=[(rng.randrange(n),rng.randrange(n),rng.randrange(8)) for i in range(rng.randrange(1,11))];best=None
  for mask in range(1<<len(edges)):
   chosen=[e for j,e in enumerate(edges) if mask>>j&1]
   if set(terms)<=reachable(n,chosen,root):
    cost=sum(e[2] for e in chosen);best=cost if best is None else min(best,cost)
  check((n,edges,root,terms),best)
 check((80,[(i,i+1,1) for i in range(79)],0,[10,20,30,40,50,60,70,79]),79)
elif kind=='extensions':
 for _ in range(60):
  n=rng.randrange(1,8);edges=[(rng.randrange(n),rng.randrange(n)) for i in range(rng.randrange(10))];answer=0
  for perm in itertools.permutations(range(n)):
   pos={v:i for i,v in enumerate(perm)}
   answer+=all(pos[u]<pos[v] for u,v in edges)
  check((n,edges),answer)
 check((18,[]),math.factorial(18));check((22,[(i,i+1) for i in range(21)]),1)
else:raise ValueError(kind)
print(json.dumps({'passed':passed,'total':total,'correct':passed==total,'failures':failures}));sys.exit(0 if passed==total else 1)
