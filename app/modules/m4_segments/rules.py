from datetime import datetime,timedelta
from app.modules.m3_versions.rules import schedule,ZONE
from app.errors import DomainError

def _basis_for(left, right, stoppages):
    """返回落在 [left,right) 内的停线依据；半开区间，跨段不重复计分钟。"""
    for e in stoppages:
        if e['start'] <= left and right <= e['end']:
            return e
    return None

def split_shift(shift, stoppages=None):
    stoppages = stoppages or []
    if shift['in'] is None or shift['out'] is None:raise DomainError('MISSING','班次缺少进出打卡')
    a=shift['in'];b=shift['out'];start=datetime.fromisoformat(a['at']).astimezone(ZONE);end=datetime.fromisoformat(b['at']).astimezone(ZONE)
    planned_start,planned_end,bs,be=schedule(a)
    if start>=end or end-start>timedelta(hours=18):raise DomainError('RANGE','打卡时长超出18小时')
    if start<planned_start-timedelta(hours=6) or end>planned_end+timedelta(hours=6):raise DomainError('RANGE','打卡超出班次范围')
    if start.second or end.second or start.microsecond or end.microsecond:raise DomainError('RANGE','打卡精度必须为整分钟')
    boundaries={start,end}
    for d in (bs,be):
        if start<d<end:boundaries.add(d)
    midnight=start.replace(hour=0,minute=0,second=0)+timedelta(days=1)
    while midnight<end:boundaries.add(midnight);midnight+=timedelta(days=1)
    # 停线起止作为新的切段刻线，使每个班次片段都带明确的停线依据
    shift_stops=[e for e in stoppages if e['employee']==shift['employee'] and e['shift_day']==shift['day']]
    for e in shift_stops:
        for d in (e['start'],e['end']):
            d=datetime.fromisoformat(d) if isinstance(d,str) else d
            if start<d<end:boundaries.add(d)
    points=sorted(boundaries);segments=[]
    for left,right in zip(points,points[1:]):
        if bs<=left<be:continue
        basis=_basis_for(left,right,shift_stops)
        minutes=int((right-left).total_seconds()//60)
        segments.append({'id':shift['id'],'employee':shift['employee'],'shift_day':shift['day'],
            'calendar_day':left.date().isoformat(),'start':left.isoformat(),'end':right.isoformat(),
            'minutes':minutes,'rate':str(a['hourly_rate']),'line':a['_line'],
            'kind':basis['reason_code'] if basis else 'WORK',
            'basis':basis['reason_label'] if basis else '正常作业',
            'responsibility':basis['responsibility_label'] if basis else '',
            'receipt_id':basis['receipt_id'] if basis else '',
            'payable_minutes':minutes if (basis and basis['payable']) else 0,
            'unpaid_minutes':minutes if basis else 0,
            'standby_rate':basis['standby_rate'] if basis else str(a['hourly_rate']),
            'multiplier_hint':basis['multiplier'] if basis else ''})
    return segments

def apply(state):
    out=[];bad=set()
    stoppages=state.get('stoppage_events',[])
    for shift in state['shifts']:
        try:out.extend(split_shift(shift,stoppages))
        except DomainError as e:
            for r in shift['sources']:bad.add(r['_line']);state['quarantine'].append({'line':r['_line'],'id':r['id'],'code':e.code,'raw':r['_raw']})
    state['rows']=[r for r in state['rows'] if r['_line'] not in bad];state['segments']=out;return state
