import time,importlib,json,base64
from pathlib import Path
from app.config import MODULES
from app.errors import DomainError
from app.manifest import identity,manifest

def load(sample):
    if sample not in ('dirty.csv','clean.csv','legacy.csv'):raise DomainError('SAMPLE','样例名称无效')
    return (Path(__file__).resolve().parents[1]/'sample_data'/sample).read_bytes()

def run(content,parameters=None,stage=7):
    if len(content)>2_000_000:raise DomainError('SIZE','文件超过2MB',413)
    params=parameters or {}
    if not isinstance(params,dict):raise DomainError('PARAMETERS','参数必须是对象')
    start=time.monotonic();state=None
    target=min(stage,len(MODULES))
    for n,entry in enumerate(MODULES,1):
        if n>target:break
        fn=importlib.import_module('app.modules.'+entry[0]+'.service').execute
        state=fn(content,params) if n==1 else fn(state)
        # 停线待命裁决必须先于工时切段（M4），切段与计薪直接引用裁决结果
        if entry[0]=='m3_versions' and target>=4:
            state['stoppage_receipts']=list(params.get('_stoppage_receipts',[]))
            state=importlib.import_module('app.modules.m8_stoppage.service').execute(state)
    # Decimal values are serialized as strings before any storage or response.
    state=json.loads(json.dumps(state,default=str,ensure_ascii=False))
    state['run_id']=identity(content,params);state['manifest']=manifest(content,params,time.monotonic()-start)
    state['source_b64']=base64.b64encode(content).decode('ascii')
    if stage>=7:
        from app.modules.m7_archive.export_out import checksum
        state['archive']['sha256']=checksum(state)
    return state
