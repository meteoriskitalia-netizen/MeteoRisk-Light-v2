import re, subprocess, sys, os, json, tempfile
p = sys.argv[1] if len(sys.argv)>1 else 'mri-light-1.3.0.37.html'
s = open(p, encoding='utf-8', errors='replace').read()
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", s, re.S | re.I)
tmp = tempfile.mkdtemp(prefix='mri-jscheck-')
fail=[]
for i,b in enumerate(blocks):
    fp=os.path.join(tmp,'block_%02d.js'%i)
    open(fp,'w',encoding='utf-8',newline='\n').write(b)
    r=subprocess.run(['node','--check',fp],capture_output=True,text=True)
    if r.returncode!=0: fail.append({'block':i,'err':(r.stderr or r.stdout).strip()[:300]})
print(json.dumps({'file':p,'inline_blocks':len(blocks),'passed':len(blocks)-len(fail),'failed':fail},ensure_ascii=False,indent=1))
