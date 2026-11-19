from decimal import Decimal

STOPPAGE_CODES=('RECEIPT_CONFLICT','NO_SHIFT','OUT_OF_SHIFT','STOPPAGE_OVERLAP')

def balance(state):
    # 停线回执是独立台账，其隔离不计入打卡 CSV 的行数守恒
    punch_quarantine=[q for q in state['quarantine'] if q['code'] not in STOPPAGE_CODES]
    accepted=len(state['rows']);isolated=len(punch_quarantine)
    accounted=accepted+isolated+state['blank_count']+state['header_count']+state['duplicate_count']
    row_balanced=accounted==state['input_count']

    # 停线分钟守恒：回执事件分钟 = 可付待命分钟 + 无薪中断分钟，且每段都能在切段里找到依据
    events=state.get('stoppage_events',[])
    event_minutes=0
    for e in events:
        start=e['start'];end=e['end']
        if isinstance(start,str):
            from datetime import datetime
            start=datetime.fromisoformat(start);end=datetime.fromisoformat(end)
        event_minutes+=int((end-start).total_seconds()//60)
    business=state.get('business',{})
    standby=business.get('standby_minutes',0);interruption=business.get('interruption_minutes',0)
    stoppage_minutes=standby+interruption
    # 每个停线片段必须携带回执号，防止无依据累计
    tagged=all(r.get('receipt_id') for r in state.get('segments',[]) if r.get('kind')!='WORK')
    stoppage_balanced=(event_minutes==stoppage_minutes) and tagged
    stoppage_quarantine=len([q for q in state['quarantine'] if q['code'] in STOPPAGE_CODES])

    return {'input':state['input_count'],'accepted':accepted,'isolated':isolated,
        'blank':state['blank_count'],'headers':state['header_count'],'duplicates':state['duplicate_count'],
        'accounted':accounted,'balanced':row_balanced and stoppage_balanced,
        'stoppage':{'events':len(events),'event_minutes':event_minutes,
            'standby_minutes':standby,'interruption_minutes':interruption,
            'tagged':tagged,'balanced':stoppage_balanced,'quarantine':stoppage_quarantine},
        'business':business}
