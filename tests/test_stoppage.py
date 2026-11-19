import csv, io
from app.workflow import run, load
from app.reasons import adjudicate
from app.modules.m8_stoppage.reader import read as read_receipts
from app.modules.m8_stoppage.rules import dedup_receipts


def sample_receipts():
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / 'sample_data' / 'stoppage.csv').read_bytes()


def filed_state():
    parsed = read_receipts(sample_receipts())
    receipts = [dict(r, line=r['_line']) for r in parsed['receipts']]
    return run(load('dirty.csv'), {'_stoppage_receipts': receipts})


# ---------- 裁决规则 ----------

def test_pending_responsibility_is_unpaid():
    # 设备故障但责任未确认：先作无薪中断，不得直接发待命工资
    assert adjudicate('EQUIP_FAULT', 'pending') == ('pending', False)
    assert adjudicate('EQUIP_FAULT', '') == ('pending', False)


def test_employer_fault_is_payable_standby():
    assert adjudicate('EQUIP_FAULT', 'employer') == ('employer', True)
    assert adjudicate('POWER_OUTAGE', 'employer') == ('employer', True)


def test_worker_and_force_majeure_unpaid():
    assert adjudicate('WORKER_CAUSE', 'worker') == ('worker', False)
    assert adjudicate('FORCE_MAJEURE', 'shared') == ('shared', False)


# ---------- 回执采信 ----------

def test_receipt_reader_parse_and_width_quarantine():
    parsed = read_receipts(sample_receipts())
    assert len(parsed['receipts']) == 5
    good = (b'receipt_id,employee,shift_day,stop_start,stop_end,reason_code,responsibility,standby_rate,note\n'
            b'R1,E001,2026-10-01,23:00,23:30,EQUIP_FAULT,employer,20,x\nbroken\n')
    out = read_receipts(good)
    assert any(q['code'] == 'WIDTH' for q in out['quarantine'])


def test_receipt_missing_field_quarantine():
    raw = (b'receipt_id,employee,shift_day,stop_start,stop_end,reason_code,responsibility,standby_rate,note\n'
           b'R1,E001,2026-10-01,23:00,23:30,-,employer,20,x\n')
    out = read_receipts(raw)
    assert out['quarantine'][0]['code'] == 'MISSING' and not out['receipts']


def test_receipt_exact_duplicate_collapses():
    raw = sample_receipts() + sample_receipts()
    out = read_receipts(raw)
    # 完全相同回执去重，不产生重复事件
    assert len(out['receipts']) == 5 and out['duplicate_count'] == 5


# ---------- 裁决结果与切段 ----------

def test_four_events_adjudicated_and_unknown_shift_quarantined():
    state = filed_state()
    ids = {e['receipt_id']: e for e in state['stoppage_events']}
    assert set(ids) == {'R2026100101', 'R2026100102', 'R2026100103', 'R2026100104'}
    assert ids['R2026100101']['payable'] is True
    assert ids['R2026100103']['payable'] is False
    assert any(q['code'] == 'NO_SHIFT' and q['id'] == 'R2026100105' for q in state['quarantine'])


def test_every_stoppage_segment_carries_receipt_and_basis():
    state = filed_state()
    stops = [s for s in state['segments'] if s['kind'] != 'WORK']
    assert stops and all(s['receipt_id'] and s['basis'] for s in stops)


def test_payable_minutes_only_for_employer_events():
    state = filed_state()
    e1 = [s for s in state['segments'] if s['employee'] == 'E001' and s['kind'] != 'WORK']
    assert sum(s['payable_minutes'] for s in e1) == 60
    e2 = [s for s in state['segments'] if s['employee'] == 'E002' and s['kind'] != 'WORK']
    assert sum(s['payable_minutes'] for s in e2) == 0 and sum(s['minutes'] for s in e2) == 45


def test_stoppage_does_not_eat_normal_minutes():
    state = filed_state()
    # E001 实到420分钟，其中60分钟停线，正常作业仍为360分钟，不被吞掉
    work = sum(c['minutes'] for c in state['calculations']
               if c['employee'] == 'E001' and c['code'] in ('REGULAR', 'OVERTIME', 'HOLIDAY', 'REST'))
    assert work == 360


def test_standby_priced_and_interruption_zero():
    state = filed_state()
    standby = [c for c in state['calculations'] if c['code'] == 'STANDBY']
    interrupt = [c for c in state['calculations'] if c['code'] == 'INTERRUPTION']
    assert sum(c['minutes'] for c in standby) == 60
    assert all(c['amount'] != '0.00' for c in standby)
    assert sum(c['minutes'] for c in interrupt) == 75
    assert all(c['amount'] == '0.00' for c in interrupt)


def test_standby_does_not_consume_daily_regular_threshold():
    state = filed_state()
    # E001 正常片段仍按 2h 假日 + 4h 正常切，待命60分钟不挤占8小时阈值
    e1_regular = [c for c in state['calculations'] if c['employee'] == 'E001' and c['code'] == 'REGULAR']
    assert sum(c['minutes'] for c in e1_regular) == 270


def test_reconcile_stoppage_minute_conservation():
    state = filed_state()
    sp = state['reconcile']['stoppage']
    assert sp['balanced'] is True
    assert sp['event_minutes'] == sp['standby_minutes'] + sp['interruption_minutes'] == 135
    assert state['reconcile']['balanced'] is True


def test_overlapping_stoppage_quarantined_not_double_counted():
    parsed = read_receipts(sample_receipts())
    receipts = [dict(r, line=r['_line']) for r in parsed['receipts']]
    receipts.append({'receipt_id': 'RX', 'employee': 'E004', 'shift_day': '2026-10-01',
                     'stop_start': '03:00', 'stop_end': '03:40', 'reason_code': 'EQUIP_FAULT',
                     'responsibility': 'employer', 'standby_rate': '20', '_line': 901,
                     '_raw': {}})
    receipts.append({'receipt_id': 'RY', 'employee': 'E004', 'shift_day': '2026-10-01',
                     'stop_start': '03:30', 'stop_end': '04:00', 'reason_code': 'EQUIP_FAULT',
                     'responsibility': 'employer', 'standby_rate': '20', '_line': 902,
                     '_raw': {}})
    state = run(load('dirty.csv'), {'_stoppage_receipts': receipts})
    assert any(q['code'] == 'STOPPAGE_OVERLAP' for q in state['quarantine'])
    assert not any(e['receipt_id'] in ('RX', 'RY') for e in state['stoppage_events'])


def test_dedup_keeps_single_copy_of_same_receipt():
    parsed = read_receipts(sample_receipts())
    rows = [dict(r, line=r['_line']) for r in parsed['receipts']]
    assert len(dedup_receipts(rows + rows)) == 5


def test_out_of_shift_receipt_quarantined():
    raw = ("receipt_id,employee,shift_day,stop_start,stop_end,reason_code,responsibility,standby_rate,note\n"
           "RBAD,E001,2026-10-01,12:00,12:30,EQUIP_FAULT,employer,20,白天空窗不在班次\n").encode()
    out = read_receipts(raw)
    state = run(load('dirty.csv'), {'_stoppage_receipts': [dict(r, line=r['_line']) for r in out['receipts']]})
    assert any(q['code'] == 'OUT_OF_SHIFT' for q in state['quarantine'])


# ---------- 归档、重算与封存（Flask 客户端） ----------

def test_file_endpoint_adjudicates_and_recalcs(client):
    r = client.post('/api/stoppage/file', json={'sample': 'stoppage.csv'})
    assert r.status_code == 200
    s = r.get_json()
    assert s['stoppage_filing']['inserted'] == 4
    assert s['business']['standby_minutes'] == 60 and s['business']['interruption_minutes'] == 75
    assert s['reconcile']['balanced'] is True


def test_same_receipt_rerun_is_idempotent(client):
    a = client.post('/api/stoppage/file', json={'sample': 'stoppage.csv'}).get_json()
    b = client.post('/api/stoppage/file', json={'sample': 'stoppage.csv'}).get_json()
    assert b['stoppage_filing']['inserted'] == 0 and b['stoppage_filing']['skipped'] == 4
    assert a['run_id'] == b['run_id']
    assert b['business']['standby_minutes'] == 60  # 重跑不重复累计


def test_conflicting_receipt_rejected_409_and_ledger_untouched(client):
    client.post('/api/stoppage/file', json={'sample': 'stoppage.csv'})
    before = len(client.get('/api/stoppage/receipts').get_json())
    latest_before = client.get('/api/runs').get_json()[0]['id']
    conf = ("receipt_id,employee,shift_day,stop_start,stop_end,reason_code,responsibility,standby_rate,note\n"
            "R2026100101,E001,2026-10-01,23:00,23:30,EQUIP_FAULT,worker,20,改判\n")
    r = client.post('/api/stoppage/file', json={'csv': conf})
    assert r.status_code == 409 and r.get_json()['code'] == 'RECEIPT_CONFLICT'
    assert len(client.get('/api/stoppage/receipts').get_json()) == before
    assert client.get('/api/runs').get_json()[0]['id'] == latest_before


def test_recalc_failure_rolls_back_and_keeps_old_run(client, monkeypatch):
    first = client.post('/api/stoppage/file', json={'sample': 'stoppage.csv'}).get_json()
    import app.main as main_mod
    real = main_mod.run
    calls = {'n': 0}

    def flaky(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('recalc blew up')
        return real(*args, **kwargs)

    monkeypatch.setattr(main_mod, 'run', flaky)
    new_rcpt = ("receipt_id,employee,shift_day,stop_start,stop_end,reason_code,responsibility,standby_rate,note\n"
                "RX1,E004,2026-10-01,03:00,03:15,MATERIAL_WAIT,employer,20,待料\n")
    r = client.post('/api/stoppage/file', json={'csv': new_rcpt})
    assert r.status_code == 409 and r.get_json()['code'] == 'RECALC_FAILED'
    # 旧版结果仍是 latest，新回执随事务回滚，记录不丢
    assert client.get('/api/runs').get_json()[0]['id'] == first['run_id']
    assert len(client.get('/api/stoppage/receipts').get_json()) == 4


def test_old_run_and_raw_receipts_survive_recalc(client):
    base = client.get('/api/runs').get_json()[0]['id']
    client.post('/api/stoppage/file', json={'sample': 'stoppage.csv'})
    history = [h['id'] for h in client.get('/api/runs').get_json()]
    assert base in history  # 旧批次留档
    receipts = client.get('/api/stoppage/receipts').get_json()
    assert {r['receipt_id'] for r in receipts} >= {'R2026100101', 'R2026100103'}


def test_archive_export_blocked_when_unbalanced(client):
    client.post('/api/stoppage/file', json={'sample': 'stoppage.csv'})
    run_id = client.get('/api/runs').get_json()[0]['id']
    # 平衡批次允许导出，且封存文件含原始回执
    ok = client.get('/api/archive/%s/export.csv' % run_id)
    assert ok.status_code == 200 and '停线回执' in ok.data.decode('utf-8-sig')
    # 手工破坏对账后导出必须被拒
    from app import store
    with store.connection() as db:
        row = db.execute('SELECT body FROM runs WHERE id=?', (run_id,)).fetchone()
        import json
        body = json.loads(row['body'])
        body['reconcile']['balanced'] = False
        db.execute('UPDATE runs SET body=? WHERE id=?', (json.dumps(body, ensure_ascii=False), run_id))
    blocked = client.get('/api/archive/%s/export.csv' % run_id)
    assert blocked.status_code == 422 and blocked.get_json()['code'] == 'UNBALANCED'


def test_m8_page_and_api_available(client):
    assert client.get('/m8_stoppage').status_code == 200
    assert client.get('/api/m8_stoppage').status_code == 200


def test_export_rows_include_each_receipt_once(client):
    s = client.post('/api/stoppage/file', json={'sample': 'stoppage.csv'}).get_json()
    txt = client.get('/api/archive/%s/export.csv' % s['run_id']).data.decode('utf-8-sig')
    rows = list(csv.reader(io.StringIO(txt)))
    receipt_rows = [r for r in rows if r[0] == '停线回执']
    ids = [r[3] for r in receipt_rows]
    assert len(ids) == len(set(ids)) == 4  # 每份回执仅留痕一次
