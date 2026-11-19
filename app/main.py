import importlib
from flask import Flask,jsonify,request,redirect,Response
from app.config import MODULES,STOPPAGE_SAMPLE
from app.errors import DomainError
from app.workflow import run,load
from app import store
from app.web import payload
from app.modules.m7_archive.export_out import bundle
from app.modules.m8_stoppage.reader import read as read_receipts

def create_app():
    app=Flask(__name__);app.config['MAX_CONTENT_LENGTH']=2_000_000
    for entry in MODULES:app.register_blueprint(importlib.import_module('app.modules.'+entry[0]+'.handler').bp)
    @app.errorhandler(DomainError)
    def domain_error(error):return jsonify(code=error.code,message=error.message),error.status
    @app.errorhandler(404)
    def missing(error):return jsonify(code='NOT_FOUND',message='入口不存在'),404
    @app.get('/')
    def index():return redirect('/'+MODULES[0][0])
    @app.get('/healthz')
    def health():return jsonify(status='ok')
    @app.post('/api/runs')
    def submit():
        content,params=payload();state=run(content,params);return jsonify(store.save(state['run_id'],state))
    @app.get('/api/runs')
    def list_runs():return jsonify(store.history())
    @app.get('/api/runs/<run_id>')
    def get_run(run_id):return jsonify(store.get(run_id))
    @app.post('/api/runs/<run_id>/review')
    def review(run_id):
        data=request.get_json(silent=True) or {}
        if not isinstance(data.get('version'),int):raise DomainError('VERSION','请提交复核版本')
        return jsonify(store.review(run_id,data['version'],data.get('decision')))
    @app.get('/api/archive/<run_id>/export.csv')
    def download(run_id):
        state=store.get(run_id)
        if not state.get('reconcile',{}).get('balanced'):raise DomainError('UNBALANCED','对账未平，禁止封存导出')
        return Response(bundle(state),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=evidence.csv'})

    @app.get('/api/stoppage/receipts')
    def receipts():
        return jsonify(store.list_receipts())

    @app.post('/api/stoppage/file')
    def file_stoppage():
        # 停线回执补录：先解析与校验，再追加归档，最后整批重算。
        # 任一步失败都不更新 latest，旧版工时工资与原始回执全部保留。
        data=request.get_json(silent=True) or {}
        if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
        raw_text=data.get('csv')
        if isinstance(raw_text,str):
            raw=raw_text.encode()
        else:
            sample=data.get('sample',STOPPAGE_SAMPLE)
            if sample not in ('stoppage.csv',):raise DomainError('SAMPLE','回执样例名称无效')
            from pathlib import Path
            raw=(Path(__file__).resolve().parents[1]/'sample_data'/sample).read_bytes()
        parsed=read_receipts(raw)
        # 同号回执内容与已归档原件不一致 -> 直接 409，禁止覆盖，旧版结果不动
        ledger={(r['receipt_id'].strip(),r['employee'].strip(),r['shift_day'].strip(),
                 r['stop_start'].strip(),r['stop_end'].strip()):r
                for r in store.list_receipts()}
        for r in parsed['receipts']:
            key=(r['receipt_id'].strip(),r['employee'].strip(),r['shift_day'].strip(),
                 r['stop_start'].strip(),r['stop_end'].strip())
            old=ledger.get(key)
            if old and (old.get('reason_code'),old.get('responsibility'),old.get('standby_rate'))!= \
               (r.get('reason_code'),r.get('responsibility'),r.get('standby_rate')):
                raise DomainError('RECEIPT_CONFLICT','回执 %s 已归档且内容不一致，禁止覆盖旧原件' % key[0],409)
        # 用当前最新批次的打卡数据做一次试算，确认回执可裁决、对账能平
        latest=store.history()
        current=store.get(latest[0]['id']) if latest else None
        source=__import__('base64').b64decode(current['source_b64']) if current and current.get('source_b64') else load('dirty.csv')
        base_params=dict(current.get('manifest',{}).get('parameters',{})) if current else {}
        base_params.pop('_stoppage_receipts',None)
        # 同号重跑先按业务键去重，避免把同一份回执计成两段重叠停线
        from app.modules.m8_stoppage.rules import dedup_receipts
        trial_receipts=dedup_receipts(store.list_receipts()+[dict(r,line=r['_line']) for r in parsed['receipts']])
        trial=run(source,dict(base_params,_stoppage_receipts=trial_receipts))
        if not trial['reconcile']['balanced']:
            raise DomainError('UNBALANCED','重算后对账未平，已保留旧版结果，回执未归档',409)
        # 仅归档通过裁决的回执；被隔离回执回显给用户但不进持久台账
        accepted_ids={e['receipt_id'] for e in trial['stoppage_events']}
        valid=[r for r in parsed['receipts'] if r['receipt_id'].strip() in accepted_ids]
        previous_id=current['run_id'] if current else None

        def recalc(receipts):
            return run(source,dict(base_params,_stoppage_receipts=receipts))

        try:
            saved=store.filing_transaction(valid,recalc,previous_id)
        except DomainError:
            raise
        except Exception:
            raise DomainError('RECALC_FAILED','重算失败，已保留旧版结果与原始回执',409)
        stoppage_only=[q for q in trial['quarantine']
                       if q['code'] in ('RECEIPT_CONFLICT','NO_SHIFT','OUT_OF_SHIFT','STOPPAGE_OVERLAP')]
        saved['stoppage_filing']['quarantine']=parsed['quarantine']+stoppage_only
        return jsonify(saved)

    with app.app_context():
        if not store.history():
            state=run(load('dirty.csv'),{});store.save(state['run_id'],state)
    return app

app=create_app()
if __name__=='__main__':app.run(host='0.0.0.0',port=8000)
