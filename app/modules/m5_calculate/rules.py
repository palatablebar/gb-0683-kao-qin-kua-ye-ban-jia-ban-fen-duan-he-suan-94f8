from datetime import date
from decimal import Decimal,ROUND_HALF_UP
from collections import defaultdict

def price(segments,holidays):
    normal_used=defaultdict(int);out=[]
    for s in sorted(segments,key=lambda r:(r['start'],r['employee'])):
        d=date.fromisoformat(s['calendar_day']);key=(s['employee'],s['calendar_day']);minutes=s['minutes'];rate=Decimal(str(s['rate']))
        kind=s.get('kind','WORK')
        if kind!='WORK':
            # 停线段：企业责任且可付 -> 待命工资；责任未确认/其他责任 -> 无薪中断，保留零金额证据
            if s.get('payable_minutes'):
                multiplier=Decimal(str(s.get('multiplier_hint') or '1'))
                standby_rate=Decimal(str(s.get('standby_rate') or s['rate']))
                item=dict(s);item.update(minutes=minutes,multiplier=str(multiplier),code='STANDBY',
                    amount=str((standby_rate*minutes*multiplier/60).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)))
                out.append(item)
            else:
                item=dict(s);item.update(minutes=minutes,multiplier='0',code='INTERRUPTION',amount='0.00')
                out.append(item)
            # 待命/中断均不挤占同日正常工时8小时阈值
            continue
        if s['calendar_day'] in holidays:parts=[(minutes,Decimal(3),'HOLIDAY')]
        elif d.weekday()>=5:parts=[(minutes,Decimal(2),'REST')]
        else:
            regular=min(minutes,max(0,480-normal_used[key]));normal_used[key]+=minutes
            parts=[(regular,Decimal(1),'REGULAR'),(minutes-regular,Decimal('1.5'),'OVERTIME')]
        for count,multiplier,code in parts:
            if count:
                item=dict(s);item.update(minutes=count,multiplier=str(multiplier),code=code,amount=str((rate*count*multiplier/60).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)));out.append(item)
    return out

def apply(state):
    holidays=set(state['parameters'].get('holidays',['2026-10-01']))
    calculations=price(state['segments'],holidays)
    total=sum((Decimal(r['amount']) for r in calculations),Decimal(0));paid=sum((Decimal(str(r['paid'])) for r in state['rows']),Decimal(0))
    stoppage=state.get('stoppage_events',[])
    standby_minutes=sum(r['minutes'] for r in calculations if r['code']=='STANDBY')
    interruption_minutes=sum(r['minutes'] for r in calculations if r['code']=='INTERRUPTION')
    state['calculations']=calculations
    state['business']={'total':str(total),'paid':str(paid),'difference':str(total-paid),
        'minutes':sum(r['minutes'] for r in calculations if r['code']!='INTERRUPTION'),
        'work_minutes':sum(r['minutes'] for r in calculations if r['code'] in ('REGULAR','OVERTIME','HOLIDAY','REST')),
        'standby_minutes':standby_minutes,'interruption_minutes':interruption_minutes,
        'stoppage_events':len(stoppage),'unit':'CNY'}
    return state
