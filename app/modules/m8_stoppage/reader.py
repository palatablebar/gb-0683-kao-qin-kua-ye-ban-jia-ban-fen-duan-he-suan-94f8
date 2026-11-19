import csv, io
from app.config import STOPPAGE_REQUIRED
from app.errors import DomainError
from app.modules.m1_ingest.decode import decode
from app.modules.m2_mapping.coerce import number

SENTINEL_COLS={'receipt_id', 'employee', 'shift_day', 'stop_start', 'stop_end', 'reason_code'}


def read(raw):
    """读取停线回执 CSV，沿用打卡采信的编码回退与逻辑行号约定。"""
    text, encoding = decode(raw)
    if not text:
        raise DomainError('EMPTY_FILE', '回执文件为空')
    if not text.strip():
        raise DomainError('BLANK_FILE', '回执仅含空行')
    reader = csv.reader(io.StringIO(text, newline=''))
    try:
        header = [s.strip().lstrip('﻿') for s in next(reader)]
    except (StopIteration, csv.Error):
        raise DomainError('HEADER', '无法读取回执表头')
    if len(set(header)) != len(header) or not set(STOPPAGE_REQUIRED) <= set(header):
        raise DomainError('HEADER', '回执表头缺失或重复')
    out = {'receipts': [], 'quarantine': [], 'input_count': 0,
           'blank_count': 0, 'header_count': 0, 'duplicate_count': 0, 'encoding': encoding}
    seen = set()
    try:
        for row in reader:
            out['input_count'] += 1
            line = reader.line_num
            if not row or not any(s.strip() for s in row):
                out['blank_count'] += 1
                continue
            if row == header:
                out['header_count'] += 1
                continue
            if len(row) != len(header):
                out['quarantine'].append({'line': line, 'id': '', 'code': 'WIDTH', 'raw': row})
                continue
            item = dict(zip(header, row))
            item['_line'] = line
            item['_raw'] = dict(item)
            missing = any(str(item.get(c, '')).strip() in ('', '-', 'N/A', 'NULL', '未知') for c in SENTINEL_COLS)
            if missing:
                out['quarantine'].append({'line': line, 'id': item.get('receipt_id', ''), 'code': 'MISSING', 'raw': item['_raw']})
                continue
            # 待命倍率允许缺省，缺省由规则层补默认值
            rate_text = str(item.get('standby_rate', '')).strip()
            if rate_text in ('', '-', 'N/A', 'NULL', '未知'):
                item['standby_rate'] = ''
            else:
                try:
                    item['standby_rate'] = str(number(rate_text))
                except DomainError:
                    out['quarantine'].append({'line': line, 'id': item.get('receipt_id', ''), 'code': 'NUMBER', 'raw': item['_raw']})
                    continue
            dedup_key = tuple(sorted((k, str(v)) for k, v in item.items() if not k.startswith('_')))
            if dedup_key in seen:
                out['duplicate_count'] += 1
                continue
            seen.add(dedup_key)
            out['receipts'].append(item)
    except csv.Error as exc:
        raise DomainError('CSV', str(exc))
    if out['input_count'] == 0:
        raise DomainError('HEADER_ONLY', '回执仅含表头')
    return out
