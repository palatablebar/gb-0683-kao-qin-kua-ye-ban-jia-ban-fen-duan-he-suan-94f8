import base64
from flask import request,render_template,jsonify
from app.errors import DomainError
from app.config import TITLE,MODULES,SHORT
from app.workflow import run,load
from app import store

def payload():
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
    if 'csv' in data and not isinstance(data['csv'],str):raise DomainError('CSV','CSV内容必须是文本')
    sample=data.get('sample',request.args.get('sample','dirty.csv'))
    content=data.get('csv')
    return (content.encode() if isinstance(content,str) else load(sample)),data.get('parameters',{})

def api(stage):
    content,parameters=payload()
    # 预览阶段也带上已归档停线回执，保证页面与最新批次口径一致
    if stage>=4:
        parameters=dict(parameters or {});parameters.setdefault('_stoppage_receipts',store.list_receipts())
    return jsonify(run(content,parameters,stage))

def page(stage,template):
    latest=store.history();state=store.get(latest[0]['id']) if latest else run(load('dirty.csv'),{})
    source=load(request.args['sample']) if 'sample' in request.args else base64.b64decode(state['source_b64']) if state.get('source_b64') else load('dirty.csv')
    params=dict(state.get('manifest',{}).get('parameters',{}))
    if stage>=4:params['_stoppage_receipts']=store.list_receipts()
    data=run(source,params,stage)
    return render_template(template,title=TITLE,short=SHORT,modules=MODULES,current=stage,data=data,run=state,history=latest)
