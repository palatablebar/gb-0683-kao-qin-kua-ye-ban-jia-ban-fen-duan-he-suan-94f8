import sqlite3,json,os
from pathlib import Path
from app.errors import DomainError

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    # 停线回执只追加、不改写：同号重跑靠 receipt_id 去重，绝不重复累计
    db.execute('''CREATE TABLE IF NOT EXISTS stoppage_receipts (
        receipt_id TEXT NOT NULL, employee TEXT NOT NULL, shift_day TEXT NOT NULL,
        stop_start TEXT NOT NULL, stop_end TEXT NOT NULL, reason_code TEXT NOT NULL,
        responsibility TEXT NOT NULL, standby_rate TEXT NOT NULL,
        line INTEGER NOT NULL, raw TEXT NOT NULL, filed_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (receipt_id, employee, shift_day, stop_start, stop_end))''')
    return db

def save(run_id, body):
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision']);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)

def list_receipts(db=None):
    own=db is None
    if own:db=connection()
    try:
        rows=db.execute('SELECT * FROM stoppage_receipts ORDER BY rowid').fetchall()
        out=[]
        for r in rows:
            item=json.loads(r['raw']);item.setdefault('line',r['line'])
            out.append(item)
        return out
    finally:
        if own:db.close()

def file_receipts(db, parsed):
    """在调用方提供的事务连接上幂等归档回执，返回 (新增数, 跳过数)。
    完全相同的回执重跑 -> 跳过；同业务键内容冲突 -> 409，随外层事务整体回滚。"""
    inserted=skipped=0
    for r in parsed:
        key=(r['receipt_id'].strip(),r['employee'].strip(),r['shift_day'].strip(),
             r['stop_start'].strip(),r['stop_end'].strip())
        existing=db.execute('SELECT raw FROM stoppage_receipts WHERE receipt_id=? AND employee=? AND shift_day=? AND stop_start=? AND stop_end=?',key).fetchone()
        if existing:
            old=json.loads(existing['raw'])
            if (old.get('reason_code'),old.get('responsibility'),old.get('standby_rate'))== \
               (r.get('reason_code'),r.get('responsibility'),r.get('standby_rate')):
                skipped+=1;continue
            raise DomainError('RECEIPT_CONFLICT','回执 %s 已存在且内容不一致，禁止覆盖' % key[0],409)
        db.execute('INSERT INTO stoppage_receipts(receipt_id,employee,shift_day,stop_start,stop_end,reason_code,responsibility,standby_rate,line,raw) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (key[0],key[1],key[2],key[3],key[4],r['reason_code'].strip(),r['responsibility'].strip(),
             str(r.get('standby_rate','')),r['_line'],json.dumps(dict(r,line=r['_line']),ensure_ascii=False)))
        inserted+=1
    return inserted,skipped

def filing_transaction(parsed, recalc_fn, previous_id):
    """归档+重算同一事务：任一步失败整体回滚，旧批次与原始回执不受影响。"""
    db=connection()
    try:
        def _recalc():
            # 复用事务连接，未提交的回执写入对本次重算可见
            final_receipts=list_receipts(db)
            state=recalc_fn(final_receipts)
            if not state['reconcile']['balanced']:
                raise DomainError('UNBALANCED','重算后对账未平')
            db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',
                       (state['run_id'],json.dumps(state,ensure_ascii=False)))
            db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (state['run_id'],))
            return state
        inserted,skipped=file_receipts(db,parsed)
        state=_recalc()
        db.commit()
        state['stoppage_filing']={'inserted':inserted,'skipped':skipped,'old_run_id':previous_id}
        return state
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
