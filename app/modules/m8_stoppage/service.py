from copy import deepcopy
from app.modules.m8_stoppage.rules import apply

def execute(state):
    # 停线回执由归档接口注入 parameters._stoppage_receipts；预览无回执时为空集
    state = deepcopy(state)
    state.setdefault('stoppage_receipts', list(state.get('parameters', {}).get('_stoppage_receipts', [])))
    return apply(state)
