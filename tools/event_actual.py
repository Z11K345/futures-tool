#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
event_actual.py — 已公布重要事件「实际值」录入工具

用途
----
把联网搜索核实过的已公布事件（实际值/预期值/前值）写入 data/events.json，
并自动从 data/quotes.json（或指定快照）中回填关联品种在**公布当日**的真实涨跌幅。

为什么这么设计
--------------
实际值是唯一客观事实。多源交叉核对一致后才录入：脚本要求对每个指标
至少给出 2 家来源的一致读数，否则拒绝写入（避免把单一媒体的笔误固化进页面）。

用法
----
1) 先写一份待录入的事件草稿（JSON），例如 /tmp/ev.json：

{
  "date": "2026-09-11",
  "time": "20:30",
  "name": "美国 8 月 CPI",
  "cat": "us_macro",
  "impact": "high",
  "country": "美国",
  "metrics": [
    {"name": "CPI 同比", "actual": "3.4%", "forecast": "3.4%", "previous": "3.4%", "dir": "flat",
     "sources": ["证券时报", "新华财经", "央广网"]},
    {"name": "核心 CPI 环比", "actual": "0.3%", "forecast": "0.2%", "previous": "0.2%", "dir": "up",
     "sources": ["证券时报", "新华财经"]}
  ],
  "read": "……数据解读……",
  "market_reaction": "……公布后盘面反应……",
  "implication": "……方向启示……",
  "related": [
    {"code": "AU0", "cn": "黄金", "note": "公布后一度失守 4300 美元"},
    {"code": "AG0", "cn": "白银"}
  ]
}

2) 执行：
   python event_actual.py --spec /tmp/ev.json
   （默认读 data/quotes.json 回填涨跌幅；同一 date+name 的事件会覆盖更新）

说明
----
- 关联品种涨跌幅取快照里该品种的 pct（若快照交易日 == 事件日期，即为当日收盘涨跌）。
- 若快照日期晚于事件日期，涨跌幅标注为「快照日」口径并在 note 里写明，不做臆测。
- 未经多源核对（sources < 2）的指标一律拒绝写入。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EVENTS = os.path.join(ROOT, 'data', 'events.json')
QUOTES = os.path.join(ROOT, 'data', 'quotes.json')

MIN_SOURCES = 2


def load_quotes_snapshot(path):
    """把 quotes.json 摊平成 code -> 行情 的映射"""
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    flat = {}
    for cat, lst in (d.get('categories') or {}).items():
        for q in lst or []:
            c = q.get('code')
            if c:
                flat[c] = q
    snap_day = d.get('trading_day_cn') or d.get('trading_day') or ''
    return flat, snap_day


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--spec', required=True, help='事件草稿 JSON 路径')
    ap.add_argument('--quotes', default=QUOTES, help='行情快照路径')
    args = ap.parse_args()

    with open(args.spec, encoding='utf-8') as f:
        spec = json.load(f)

    # --- 1) 多源一致性校验 ---
    bad = []
    for m in spec.get('metrics', []):
        srcs = m.get('sources') or []
        if len(srcs) < MIN_SOURCES:
            bad.append(f"{m.get('name')}: 仅 {len(srcs)} 家来源（需 ≥{MIN_SOURCES}）")
        if not m.get('actual'):
            bad.append(f"{m.get('name')}: 缺 actual（未公布的事件请勿录入本表）")
    if bad:
        print('[拒绝写入] 以下指标未通过多源核对：')
        for b in bad:
            print('  -', b)
        print('\n提示：实际值是客观事实，请至少用 2 家权威来源交叉确认一致后再录。')
        sys.exit(2)

    # --- 2) 回填关联品种真实涨跌幅 ---
    flat, snap_day = ({}, '')
    if os.path.exists(args.quotes):
        try:
            flat, snap_day = load_quotes_snapshot(args.quotes)
        except Exception as e:
            print(f'[WARN] 读行情快照失败: {e}', file=sys.stderr)

    ev_date = spec.get('date', '')
    same_day = (snap_day == ev_date) or (snap_day.replace('0', '') == ev_date.replace('-0', '-').replace('-', '')[:0])
    filled = 0
    for r in spec.get('related', []) or []:
        code = r.get('code')
        q = flat.get(code)
        if not q:
            r['pct'] = None
            r['note'] = (r.get('note') or '') + '（快照无此品种）'
            continue
        r['pct'] = q.get('pct')
        r['last'] = q.get('last')
        r['snap_day'] = snap_day
        filled += 1
        # 只在同一天时才把它当"当日涨跌"口径
        if snap_day and snap_day != ev_date:
            if '（口径:' not in (r.get('note') or ''):
                r['note'] = (r.get('note') or '') + f'（涨跌幅为快照日 {snap_day} 口径）'

    # --- 3) 合并进 events.json ---
    doc = {'updated_at': '', 'note': '', 'events': []}
    if os.path.exists(EVENTS):
        with open(EVENTS, encoding='utf-8') as f:
            doc = json.load(f)
    doc.setdefault('events', [])

    # 以 date + name 作为唯一键，存在则覆盖
    key = (spec.get('date'), spec.get('name'))
    doc['events'] = [e for e in doc['events'] if (e.get('date'), e.get('name')) != key]
    doc['events'].append(spec)
    doc['events'].sort(key=lambda e: (e.get('date', ''), e.get('time', '')), reverse=True)

    from datetime import datetime
    doc['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    doc.setdefault('note', '已公布重要事件的实际值，经多源交叉核对后录入。仅为客观数据整理，不构成投资建议。')

    with open(EVENTS, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    print(f'[OK] 已写入 {EVENTS}')
    print(f'     事件: {spec.get("name")} ({ev_date} {spec.get("time")})')
    print(f'     指标: {len(spec.get("metrics", []))} 项（均已多源核对）')
    print(f'     关联品种: {len(spec.get("related", []))} 个，回填涨跌幅 {filled} 个')
    print(f'     当前 events.json 共 {len(doc["events"])} 条事件')


if __name__ == '__main__':
    main()
