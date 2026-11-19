from collections import defaultdict
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from app.reasons import adjudicate, REASON_LABEL, RESPONSIBILITY_LABEL
from app.config import DEFAULT_STANDBY_MULTIPLIER

ZONE = ZoneInfo('Asia/Shanghai')


def dedup_receipts(receipts):
    """按回执业务键去重：已归档回执与新上传完全相同的回执只保留一份，
    保证同一停线回执重跑不会被当成两段重叠停线重复累计。"""
    seen = {}
    order = []
    for r in receipts:
        key = (str(r.get('receipt_id', '')).strip(), str(r.get('employee', '')).strip(),
               str(r.get('shift_day', '')).strip(), str(r.get('stop_start', '')).strip(),
               str(r.get('stop_end', '')).strip())
        if key not in seen:
            seen[key] = r
            order.append(key)
    return [seen[k] for k in order]


def _local_window(shift_day, hhmm_start, hhmm_end, shift_start=None):
    """把停线起止（班次本地时区 HH:MM）解析为带时区时刻，跨午夜自动顺延。"""
    day = date.fromisoformat(shift_day)
    start = datetime.combine(day, time.fromisoformat(hhmm_start), ZONE)
    end = datetime.combine(day, time.fromisoformat(hhmm_end), ZONE)
    # 夜班跨午夜：早于班次起点的钟点归入次日
    if shift_start is not None and start < shift_start:
        start += timedelta(days=1)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _shift_span(shift):
    a = shift['in']
    start = datetime.fromisoformat(a['at']).astimezone(ZONE)
    end = datetime.fromisoformat(shift['out']['at']).astimezone(ZONE)
    return start, end


def validate_and_adjudicate(receipts, shifts, quarantine):
    """校验回执并裁决责任，返回可执行停线事件。不合格回执进 quarantine 不丢记录。"""
    shift_index = {(s['employee'], s['day']): s for s in shifts}
    # 同一 receipt_id 出现不同内容视为回执冲突，整组隔离（同内容重复在 reader 已去重）
    by_receipt = defaultdict(list)
    for r in receipts:
        by_receipt[r['receipt_id'].strip()].append(r)
    conflicts = {rid for rid, rows in by_receipt.items()
                 if len({(x['employee'], x['shift_day'], x['stop_start'], x['stop_end'],
                          x['reason_code'], x['responsibility'], x['standby_rate']) for x in rows}) > 1}
    grouped = defaultdict(list)
    rejected_lines = set()
    for r in receipts:
        rid = r['receipt_id'].strip()
        if rid in conflicts:
            quarantine.append({'line': r['_line'], 'id': rid, 'code': 'RECEIPT_CONFLICT', 'raw': r['_raw']})
            rejected_lines.add(r['_line'])
            continue
        shift = shift_index.get((r['employee'].strip(), r['shift_day'].strip()))
        if shift is None:
            quarantine.append({'line': r['_line'], 'id': rid, 'code': 'NO_SHIFT', 'raw': r['_raw']})
            rejected_lines.add(r['_line'])
            continue
        try:
            shift_start, shift_end = _shift_span(shift)
            stop_start, stop_end = _local_window(r['shift_day'].strip(),
                                                 r['stop_start'].strip(), r['stop_end'].strip(), shift_start)
        except (ValueError, TypeError):
            quarantine.append({'line': r['_line'], 'id': rid, 'code': 'RANGE', 'raw': r['_raw']})
            rejected_lines.add(r['_line'])
            continue
        if not (shift_start <= stop_start < stop_end <= shift_end):
            quarantine.append({'line': r['_line'], 'id': rid, 'code': 'OUT_OF_SHIFT', 'raw': r['_raw']})
            rejected_lines.add(r['_line'])
            continue
        responsibility, payable = adjudicate(r['reason_code'], r['responsibility'])
        rate = r['standby_rate'] or str(shift['in']['hourly_rate'])
        grouped[(r['employee'].strip(), r['shift_day'].strip())].append({
            'receipt_id': rid,
            'employee': r['employee'].strip(),
            'shift_day': r['shift_day'].strip(),
            'start': stop_start,
            'end': stop_end,
            'reason_code': r['reason_code'].strip(),
            'reason_label': REASON_LABEL.get(r['reason_code'].strip(), r['reason_code'].strip()),
            'responsibility': responsibility,
            'responsibility_label': RESPONSIBILITY_LABEL[responsibility],
            'payable': payable,
            'standby_rate': rate,
            'multiplier': DEFAULT_STANDBY_MULTIPLIER,
            'line': r['_line'],
        })

    events = []
    for key, items in sorted(grouped.items()):
        items.sort(key=lambda e: (e['start'], e['receipt_id']))
        # 仅同一班次内的停线区间重叠才隔离，避免跨班次误判
        for earlier, later in zip(items, items[1:]):
            if later['start'] < earlier['end']:
                for e in (earlier, later):
                    if e['line'] not in rejected_lines:
                        quarantine.append({'line': e['line'], 'id': e['receipt_id'],
                                           'code': 'STOPPAGE_OVERLAP', 'raw': next(
                                               r['_raw'] for r in receipts if r['_line'] == e['line'])})
                        rejected_lines.add(e['line'])
        events.extend(e for e in items if e['line'] not in rejected_lines)
    return events


def apply(state):
    """把已归档停线回执（state['stoppage_receipts']，字典行）裁决为停线事件。"""
    receipts = dedup_receipts(state.get('stoppage_receipts', []))
    # 归档行是纯字典，补回 reader 的内部字段形态
    normalized = []
    for r in receipts:
        item = dict(r)
        item.setdefault('_line', r.get('line', 0))
        item['_raw'] = r.get('raw', dict(r))
        normalized.append(item)
    events = validate_and_adjudicate(normalized, state['shifts'], state['quarantine'])
    state['stoppage_events'] = events
    return state
