"""Bounded production question contract and AST semantic gate (no model or gold)."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo

from sqlglot import exp, parse_one

VERSION = 'industrial-query-v1'
PROMPT_VERSION = 'industrial-sql-v1'
NOTE = '自建合成数据；批次数仅统计已有测量的批次并去重。温度聚合按测量计算，K 换算为摄氏度，未知单位不参与聚合；不代表工艺合格或报告真值。'
PROMPT = '''工业生产查询约束：仅单层 SELECT，无 WITH/子查询/UNION。
查询零件目录使用 parts；测量统计和明细使用 measurement_observations，一行一条测量。
已有测量批次数=count(DISTINCT batch_id)；测量数=count(*)；平均/最高/最低温度使用 temperature_c，禁止聚合原始 value。
temperature_c 为原 degC 或 K-273.15，未知单位 NULL。单位无法归一化用 temperature_c IS NULL。
生产年月严格按 INDUSTRIAL_FRAME 中 produced_at 的 >= start 和 < end，以 Asia/Shanghai 为时区；不能按 measured_at 过滤。
WHERE 必须包含 frame 中所有实体/工序/保温指标条件，不能追加没有请求的条件；part_id、batch_id、operation_no 均是字符串。
每个零件统计时 GROUP BY part_id，可按问题要求排序。SELECT 只投影所问字段。
parts 仅能通过 part_id 唯一键作维度 JOIN；不要重复 JOIN 事实行。不要判断报告存在、审批、版本适用或工艺超限。
不能回答的问题返回 clarification。'''


def frame_for(question):
    q = question.replace(' ', '')
    if re.search(r'超限|超标|合格|报告|质检|工艺|版本|审批|一致|冲突', q):
        return None, '该问题需要工艺/报告原文，请选择「工业合成演示核验」。工业生产问数只统计数据库测量。'
    if re.search(r'测量时间|测量日期|今年|去年|本月|上个月|季度|超过|低于|大于|小于|每个批次|按批次', q):
        return None, '请使用明确的生产年月、单个批次或零件目录查询；测量时间、阈值和按批次均值口径暂未接入。'
    parts = sorted(set(re.findall(r'(?<![A-Z0-9])A\d{3}(?![A-Z0-9])', q.upper())))
    batches = sorted(set(re.findall(r'(?<![A-Z0-9])B\d{3}(?![A-Z0-9])', q.upper())))
    operations = sorted(set(re.findall(r'工序(?:号)?(\d+)', q)))
    if len(parts)>1 or len(batches)>1 or len(operations)>1:
        return None, '请一次指定一个零件、批次和工序；按各零件分组可不指定零件编号。'
    directory = bool(re.search(r'零件目录|材料', q)) and not any((batches, operations))
    frame = {'contract_version':VERSION,'intent':'directory' if directory else 'measurements',
             'part_id':parts[0] if parts else None,'batch_id':batches[0] if batches else None,
             'operation_no':operations[0] if operations else None,'time_filters':[],
             'metric':None if directory else 'holding_temperature',
             'group_by_part':bool(re.search(r'每个零件|各零件|按零件分组',q)),
             'unknown_units':bool(re.search(r'无法归一化|未知单位|单位未知|不明单位',q))}
    if directory:
        return frame, None
    months = set(re.findall(r'(20\d{2})年(\d{1,2})月',q))
    years = set(re.findall(r'(20\d{2})年',q))
    if len(months)>1 or len(years)>1 or not operations:
        return None, '请指定一个工序号和明确的生产年月（或年份），也可查询单个批次。'
    if not months and not years and not batches:
        return None, '请指定生产年月或年份，例如 2026年9月；单个批次查询可不指定月份。'
    try:
        if months:
            year,month=map(int,next(iter(months)))
            start=datetime(year,month,1,tzinfo=ZoneInfo('Asia/Shanghai'))
            end=datetime(year+(month==12),month%12+1,1,tzinfo=ZoneInfo('Asia/Shanghai'))
        elif years:
            year=int(next(iter(years)));start=datetime(year,1,1,tzinfo=ZoneInfo('Asia/Shanghai'));end=datetime(year+1,1,1,tzinfo=ZoneInfo('Asia/Shanghai'))
        else:start=end=None
    except ValueError:
        return None, '生产年月不合法，请输入有效年份和月份。'
    if start:frame['time_filters']=[{'field_id':'measurement_observations.produced_at','from':start.isoformat(),'to':end.isoformat(),'timezone':'Asia/Shanghai'}]
    if re.search(r'平均|均值',q):frame['intent']='average'
    elif re.search(r'最高|最大',q):frame['intent']='maximum'
    elif re.search(r'最低|最小',q):frame['intent']='minimum'
    elif re.search(r'多少|计数|数量|批次数|测量数',q):
        frame['intent']='batch_count' if re.search(r'批次数|多少个.*批次|批次.*多少',q) else 'measurement_count'
    return frame,None


def _unwrap(node):
    while isinstance(node,(exp.Cast,exp.Paren)):node=node.this
    return node


def _datetime(value):
    value=datetime.fromisoformat(value)
    return value.replace(tzinfo=ZoneInfo('Asia/Shanghai')) if value.tzinfo is None else value


def findings(sql, frame):
    tree=parse_one(sql,read='postgres')
    if not isinstance(tree,exp.Select) or len(list(tree.find_all(exp.Select)))!=1 or tree.args.get('with_') or tree.args.get('having'):
        return ['INDUSTRIAL_FLAT_SELECT_REQUIRED']
    if tree.args.get('distinct') or any(isinstance(n,(exp.Filter,exp.Window,exp.Case)) for n in tree.walk()):
        return ['INDUSTRIAL_PROJECTION_MISMATCH']
    tables=list(tree.find_all(exp.Table));names=[t.name for t in tables]
    if frame['intent']=='directory':
        if names!=['parts'] or tree.args.get('group') or any(isinstance(n,exp.AggFunc) for n in tree.walk()):
            return ['INDUSTRIAL_DIRECTORY_GRAIN']
        base=tables[0]
    else:
        if names.count('measurement_observations')!=1 or len(names)>2 or names.count('parts')>1:
            return ['INDUSTRIAL_MEASUREMENT_GRAIN']
        base=next((t for t in tables if t.name=='measurement_observations'),None)
        if base is None:return ['INDUSTRIAL_MEASUREMENT_GRAIN']
        if tree.args.get('offset'):return ['INDUSTRIAL_UNREQUESTED_FILTER']
    aliases={t.alias_or_name:t.name for t in tables}
    def column(node):
        while isinstance(node,exp.Paren):node=node.this
        if not isinstance(node,exp.Column):return None
        # Unqualified duplicated part_id becomes a DB error; it is not accepted as a scope check.
        if node.table and aliases.get(node.table)!=base.name:return None
        return node.name
    expected={k:v for k,v in frame.items() if k in ('part_id','batch_id','operation_no','metric') and v is not None}
    expected_checks={(k,'eq',str(v)) for k,v in expected.items()}
    for t in frame['time_filters']:
        expected_checks.update({('produced_at','gte',_datetime(t['from'])),('produced_at','lt',_datetime(t['to']))})
    actual=set();where=tree.args.get('where')
    def atoms(node):
        node=_unwrap(node)
        if isinstance(node,exp.And):return atoms(node.left)+atoms(node.right)
        return [node]
    for condition in atoms(where.this) if where else []:
        if isinstance(condition,(exp.EQ,exp.GTE,exp.LT)):
            key=column(condition.left);right=_unwrap(condition.right)
            if key is None or not isinstance(right,exp.Literal):return ['INDUSTRIAL_UNREQUESTED_FILTER']
            op='eq' if isinstance(condition,exp.EQ) else 'gte' if isinstance(condition,exp.GTE) else 'lt'
            value=str(right.this)
            if key=='produced_at':
                try:value=_datetime(value)
                except ValueError:return ['INDUSTRIAL_TIME_FILTER_MISMATCH']
            item=(key,op,value)
            if item not in expected_checks:return ['INDUSTRIAL_UNREQUESTED_FILTER']
            actual.add(item)
        elif isinstance(condition,exp.Is) and column(condition.this)=='temperature_c' and isinstance(condition.expression,exp.Null) and frame['unknown_units']:
            actual.add(('temperature_c','is_null',None))
        else:return ['INDUSTRIAL_UNREQUESTED_FILTER']
    if frame['unknown_units']:expected_checks.add(('temperature_c','is_null',None))
    if actual!=expected_checks:return ['INDUSTRIAL_SCOPE_FILTER_MISSING']
    group=tree.args.get('group')
    group_columns=[column(c) for c in group.expressions] if group else []
    if group_columns!=(['part_id'] if frame['group_by_part'] else []):return ['INDUSTRIAL_GROUP_GRAIN_MISMATCH']
    aggregates=list(tree.find_all(exp.AggFunc));intent=frame['intent']
    if intent in {'batch_count','measurement_count'}:
        if len(aggregates)!=1 or not isinstance(aggregates[0],exp.Count):return ['INDUSTRIAL_COUNT_GRAIN_MISMATCH']
        arg=aggregates[0].this
        if intent=='batch_count':
            if not isinstance(arg,exp.Distinct) or len(arg.expressions)!=1 or column(arg.expressions[0])!='batch_id':return ['INDUSTRIAL_BATCH_DISTINCT_REQUIRED']
        elif not (isinstance(arg,exp.Star) or column(arg)=='measurement_id'):return ['INDUSTRIAL_COUNT_GRAIN_MISMATCH']
    elif intent in {'average','maximum','minimum'}:
        kind={'average':exp.Avg,'maximum':exp.Max,'minimum':exp.Min}[intent]
        if len(aggregates)!=1 or not isinstance(aggregates[0],kind) or column(aggregates[0].this)!='temperature_c':return ['INDUSTRIAL_NORMALIZED_TEMPERATURE_REQUIRED']
    elif aggregates:return ['INDUSTRIAL_UNREQUESTED_AGGREGATE']
    projections=[p.this if isinstance(p,exp.Alias) else p for p in tree.expressions]
    if aggregates:
        metrics=[p for p in projections if column(p)!='part_id']
        if len(projections)!=(2 if frame['group_by_part'] else 1) or len(metrics)!=1:
            return ['INDUSTRIAL_PROJECTION_MISMATCH']
        projected=metrics[0]
        if isinstance(projected,exp.Round) and intent in {'average','maximum','minimum'}:
            projected=projected.this
        if projected!=aggregates[0]:return ['INDUSTRIAL_PROJECTION_MISMATCH']
    elif any(not isinstance(p,exp.Column) for p in projections):
        return ['INDUSTRIAL_PROJECTION_MISMATCH']
    if intent=='measurements' and 'measurement_id' not in [column(p) for p in projections]:
        return ['INDUSTRIAL_MEASUREMENT_ID_REQUIRED']
    # An approved dimension join must preserve all observations, not silently filter them.
    if tree.args.get('joins'):
        for join in tree.args['joins']:
            on=join.args.get('on')
            if join.args.get('side') not in (None,'LEFT') or not isinstance(on,exp.EQ) or not all(isinstance(n,exp.Column) and n.name=='part_id' for n in (on.left,on.right)):
                return ['INDUSTRIAL_DIMENSION_JOIN_MISMATCH']
    return []
