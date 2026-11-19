# 夜班工时与工资

班次属于shift_day；计划结束时间不大于开始时间时跨到次日。打卡是带偏移的时间，先存UTC，再用Asia/Shanghai拆分实际日历日。休息区间按夜班日期归属处理，先从实际工作区间扣除。单班最多18小时，打卡须为整分钟。缺IN或OUT整班隔离，不猜补卡。

按实际日历日判断法定假日、周末，法定假日优先3倍、周末2倍、工作日8小时内1倍、超过8小时1.5倍；正常分钟按同员工同日累计，跨班片段共享阈值。参数holidays可传日期列表，默认仅2026-10-01。金额按片段Decimal计算到分，paid为实发工资，差额为应发减实发。此规则用于样例计算，不替代当地劳动法规。

## 通用采信约定

CSV按UTF-8 BOM、UTF-8、GB18030回退，保留逻辑记录结束行号；引号内换行由CSV解析。重复表头单独计数，列数不齐隔离。编码与ID按字符串保形。缺失哨兵为 `-`、`N/A`、`NULL`、`未知` 与空串；不得作为零。数值先NFKC归一、去千分位，再用Decimal。日期采用ISO；时间必须带偏移并转UTC。空文件、全空行、仅表头分别返回EMPTY_FILE、BLANK_FILE、HEADER_ONLY。

重复键为(id,version)，完全相同行去重；同键内容冲突则整组隔离，不取最后一行。行数对账：输入=采纳+隔离+空行+重复表头+完全重复。运行幂等键由输入字节、参数与规则版本计算。

同一员工/归属日/方向的最高版本若有时刻、时薪、班次、休息或实发金额冲突，则整班隔离；选中IN与OUT的时薪、班次起止与休息起止也必须完全一致。

## 停线待命（M8）

设备故障后员工被要求留岗等待的时间不产出，不能直接按正常作业发薪。停线回执是独立台账，必填列为 receipt_id、employee、shift_day、stop_start、stop_end、reason_code、responsibility、standby_rate；钟点为班次本地时区 HH:MM，早于班次起点的钟点自动跨到次日。回执同样走 UTF-8 BOM/UTF-8/GB18030 编码回退，缺列 MISSING、列数不齐 WIDTH、无对应班次 NO_SHIFT、超出班次跨度 OUT_OF_SHIFT、同班重叠 STOPPAGE_OVERLAP、同号内容冲突 RECEIPT_CONFLICT。

裁决口径：responsibility 为 pending 或空白时，即使 reason_code 已知也先按**无薪中断（INTERRUPTION，金额0.00）**挂账；employer 责任（EQUIP_FAULT、POWER_OUTAGE、MATERIAL_WAIT）改判**可付待命（STANDBY）**，按 standby_rate × 分钟 × 倍率（默认1，参数可覆盖）计到分；worker 与 shared（不可抗力）不付待命。待命与中断分钟都不挤占同日正常工时8小时阈值，正常作业分钟不被停线吞掉。

停线起止作为 M4 的切段刻线，每个片段带 kind、basis（停线依据）、receipt_id（回执号）、responsibility 与 payable_minutes（可付分钟）。M6 增加停线分钟守恒：回执事件分钟 = 可付待命分钟 + 无薪中断分钟，且每个停线片段必须挂回执号；任一不平则整批 balanced=false，M7 禁止封存与导出。

回执只追加不改写：同业务键完全相同的回执重跑跳过、不重复累计；同键内容不一致返回 409 且不覆盖原件。归档与重算在同一事务，重算失败整体回滚，latest 指回旧批次，旧版工时工资、原始回执与历史批次全部保留。封存 CSV 含每份原始回执的“停线回执”留痕行。
