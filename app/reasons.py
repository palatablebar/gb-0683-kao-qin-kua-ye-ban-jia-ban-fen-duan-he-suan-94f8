# 停线原因与责任裁决表。
# 设备故障后员工被要求留岗等待：责任未确认前先按无薪中断挂账；
# 回执补录确认企业侧责任后，同一段等待改判为可付待命工时并整批重算。

# reason_code -> 责任归属
RESPONSIBILITY_BY_REASON={
    'EQUIP_FAULT': 'employer',      # 设备故障，企业承担
    'POWER_OUTAGE': 'employer',     # 停电等公用设施中断，企业承担
    'MATERIAL_WAIT': 'employer',    # 待料，企业承担
    'FORCE_MAJEURE': 'shared',      # 不可抗力，企业不付待命工资
    'WORKER_CAUSE': 'worker',       # 员工原因，员工承担
}

# 责任归属 -> 是否支付待命工资
PAYABLE_BY_RESPONSIBILITY={
    'employer': True,
    'shared': False,
    'worker': False,
}

REASON_LABEL={
    'EQUIP_FAULT': '设备故障',
    'POWER_OUTAGE': '停电中断',
    'MATERIAL_WAIT': '待料停线',
    'FORCE_MAJEURE': '不可抗力',
    'WORKER_CAUSE': '员工原因',
}

RESPONSIBILITY_LABEL={
    'employer': '企业责任',
    'shared': '不可抗力',
    'worker': '员工责任',
    'pending': '责任待确认',
}

# 回执上允许出现的责任取值；空白或 pending 表示尚未确认
VALID_RESPONSIBILITIES={'employer', 'shared', 'worker', 'pending', ''}


def adjudicate(reason_code, responsibility):
    """返回 (责任归属, 是否可付待命)。责任未确认一律无薪。"""
    reason = (reason_code or '').strip()
    resp = (responsibility or '').strip().lower()
    if resp not in VALID_RESPONSIBILITIES:
        resp = RESPONSIBILITY_BY_REASON.get(reason, 'pending')
    if resp in ('', 'pending'):
        # 即使原因已知，只要回执未确认责任，仍按无薪中断挂账
        return 'pending', False
    if resp not in PAYABLE_BY_RESPONSIBILITY:
        return 'pending', False
    return resp, PAYABLE_BY_RESPONSIBILITY[resp]
