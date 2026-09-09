# -*- coding: utf-8 -*-
"""
期限结构与展期收益(Term Structure / Roll Yield)
------------------------------------------------
为什么做这个:
  同一品种不同月份合约的价差(期限结构)是商品期货里最"硬"的一类可验证收益来源。
  - Backwardation(近月贵于远月): 多头每次移仓都能以更低价格买到下月合约 -> 多头展期正收益
  - Contango(远月贵于近月):     多头移仓要付出升水成本                  -> 空头展期正收益
  这个收益不依赖"看对方向", 只依赖结构本身, 且盘面上直接可观测, 可回溯, 可证伪。

计算口径:
  1. 枚举品种当前月起未来 LOOKAHEAD 个月的全部合约, 批量取新浪合约级行情;
  2. 保留"活跃"合约(成交量 >= 该品种最大成交 2% 且持仓 > 0), 剔除虚挂/僵尸合约;
  3. C1 = 活跃合约中持仓量最大者(即主力); C2/C3 = 月份晚于 C1 的下一个/再下一个活跃合约;
     —— 注意: 不能用"月份最小的活跃合约"当 C1, 近月合约进入交割月后只剩产业交割博弈,
        自然人已清仓, 价格被仓单与交割品级压歪(实测甲醇 2610 相对 2611 折价 7.25%, 年化 -87%,
        明显失真)。改用主力合约为锚后, 数值回到 ±30% 的合理区间。
     商品品种剔除已进入交割月的合约(ym <= 当前月); 金融期货保留(现金交割, 当月合约流动性最好)。
  4. 月间价差 gap% = (P_C2 - P_C1) / P_C1 * 100
     年化斜率 ann% = gap% * 12 / Δmonths   (Δmonths = 两合约月份差)
  5. 多头展期年化 roll_long% = -ann%
     (ann > 0 即 Contango, 多头移仓吃亏; ann < 0 即 Back, 多头移仓占便宜)

数据质量:
  - 远月合约常因流动性稀薄而报价失真, 对 C2/C3 持仓量明显偏小的打 thin 标记, 前端提示"仅供参考";
  - 只保留至少能取到 C1/C2 两个活跃合约的品种;
  - 近月合约若已进入交割月, 打 near_delivery 标记(自然人不得进入交割月)。

输出写入 data/term_structure.json, 并由 fetch_quotes 并入 quotes.json 的 'term' 字段。
"""
import os
import json
import time
import datetime

from main_contract import _http_get, _month_candidates, _extract, COMMODITY_CODES, INDEX_CODES, DATA_DIR

CACHE_FILE = os.path.join(DATA_DIR, 'term_structure.json')
CACHE_TTL = 1800          # 30 分钟(期限结构变化慢, 不必每 15 分钟重算)
LOOKAHEAD = 8             # 向前枚举月份数(足够覆盖 C1~C3)
BATCH = 100
ACTIVE_VOL_RATIO = 0.02   # 活跃合约成交量门槛(占该品种最大成交比)
THIN_OI_RATIO = 0.20      # 远月持仓 < 近月的该比例 -> 判为流动性稀薄


def _ym(code):
    """从合约代码 'RB2701' 解析出可比大小的年月数值 2026*12+9"""
    try:
        yy = int(code[-4:-2])
        mm = int(code[-2:])
        return (2000 + yy) * 12 + mm
    except Exception:
        return None


def _month_gap(c1, c2):
    a, b = _ym(c1), _ym(c2)
    if not a or not b:
        return None
    return b - a


def build(verbose=True):
    now = datetime.datetime.now()
    cur_ym = now.year * 12 + now.month

    varieties = []
    for _cat, lst in COMMODITY_CODES.items():
        for code, cn, _name in lst:
            varieties.append((code, code.rstrip('0'), cn, 'cmd'))
    for _cat, lst in INDEX_CODES.items():
        for code, cn, _name in lst:
            varieties.append((code, code.rstrip('0'), cn, 'idx'))

    months = _month_candidates()[:LOOKAHEAD]
    cand, all_codes = {}, []
    for code0, prefix, cn, kind in varieties:
        lst = ['%s%02d%02d' % (prefix, yy, mm) for yy, mm in months]
        cand[code0] = (lst, kind, cn)
        all_codes.extend(lst)

    raw = {}
    for i in range(0, len(all_codes), BATCH):
        try:
            raw.update(_http_get(all_codes[i:i + BATCH]))
        except Exception as e:
            if verbose:
                print('[WARN] term batch %d fail: %s' % (i // BATCH, e))

    out = {}
    for code0, (lst, kind, cn) in cand.items():
        rows = []
        for c in lst:
            parts = raw.get(c)
            if not parts:
                continue
            v = _extract(parts, kind)
            if not v:
                continue
            last, oi, vol, _prev = v
            ym = _ym(c)
            if not ym:
                continue
            rows.append({'code': c, 'last': last, 'oi': oi, 'vol': vol, 'ym': ym})
        if len(rows) < 2:
            continue
        max_vol = max(r['vol'] for r in rows)
        active = [r for r in rows if (r['vol'] >= max_vol * ACTIVE_VOL_RATIO and r['oi'] > 0)] or rows
        if kind == 'cmd':
            # 商品: 剔除已进入交割月的合约(自然人已清仓, 价格只反映交割博弈)
            active = [r for r in active if r['ym'] > cur_ym] or active
        active.sort(key=lambda r: (-r['oi'], r['ym']))
        c1 = active[0]
        rest = sorted([r for r in active if r['ym'] > c1['ym']], key=lambda r: r['ym'])
        if not rest:
            continue
        c2 = rest[0]
        c3 = rest[1] if len(rest) > 1 else None

        gap = _month_gap(c1['code'], c2['code'])
        if not gap or gap <= 0:
            continue
        gap_pct = (c2['last'] - c1['last']) / c1['last'] * 100
        ann = gap_pct * 12.0 / gap

        item = {
            'cn': cn,
            'kind': kind,
            'c1': c1['code'], 'c1_last': round(c1['last'], 2), 'c1_oi': int(c1['oi']), 'c1_vol': int(c1['vol']),
            'c2': c2['code'], 'c2_last': round(c2['last'], 2), 'c2_oi': int(c2['oi']), 'c2_vol': int(c2['vol']),
            'months': gap,
            'gap_pct': round(gap_pct, 2),
            'ann_pct': round(ann, 2),
            'roll_long_pct': round(-ann, 2),
            'thin': bool(c1['oi'] > 0 and (c2['oi'] < c1['oi'] * THIN_OI_RATIO
                                           or c2['vol'] < c1['vol'] * 0.10)),
            'near_delivery': bool(_ym(c1['code']) <= cur_ym),
            # 近月主力距交割月 <= 1 个月: 自然人须在交割月前清仓,
            # 这段"多头展期收益"自然人吃不到完整区间, 必须提示(否则是把纸面收益当到手收益)
            'near': bool(kind == 'cmd' and _ym(c1['code']) - cur_ym <= 1),
        }
        if c3:
            gap2 = _month_gap(c1['code'], c3['code'])
            if gap2 and gap2 > 0:
                item['c3'] = c3['code']
                item['c3_last'] = round(c3['last'], 2)
                item['c3_oi'] = int(c3['oi'])
                item['ann_far_pct'] = round((c3['last'] - c1['last']) / c1['last'] * 100 * 12.0 / gap2, 2)
        if ann >= 1:
            item['struct'] = 'Contango'
        elif ann <= -1:
            item['struct'] = 'Back'
        else:
            item['struct'] = '平坦'
        out[code0] = item

    if verbose:
        n_back = sum(1 for v in out.values() if v['struct'] == 'Back')
        n_cont = sum(1 for v in out.values() if v['struct'] == 'Contango')
        print('[OK] term_structure: %d 品种 (Back %d / Contango %d / 平坦 %d)'
              % (len(out), n_back, n_cont, len(out) - n_back - n_cont))

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({'_ts': time.time(),
                   '_time': now.strftime('%Y-%m-%d %H:%M:%S'),
                   'data': out},
                  open(CACHE_FILE, 'w', encoding='utf-8'), ensure_ascii=False)
        _append_history(now.strftime('%Y-%m-%d'), out)
    except Exception as e:
        if verbose:
            print('[WARN] term cache write fail: %s' % e)
    return out


def _append_history(day, data):
    """按天追加一条快照到 data/term_history.jsonl, 为将来的"展期收益历史分位"积累样本。
    同一天只保留最后一条(覆盖写法: 读回全部 -> 去重 -> 重写)。"""
    hist_file = os.path.join(DATA_DIR, 'term_history.jsonl')
    lines = []
    if os.path.exists(hist_file):
        try:
            for ln in open(hist_file, encoding='utf-8'):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                if rec.get('d') != day:
                    lines.append(rec)
        except Exception:
            pass
    slim = {k: {'ann': v['ann_pct'], 'gap': v['gap_pct'], 'c1': v['c1'], 'c2': v['c2']}
            for k, v in data.items()}
    lines.append({'d': day, 'v': slim})
    lines.sort(key=lambda r: r['d'])
    lines = lines[-500:]          # 最多保留 500 个交易日
    tmp = hist_file + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in lines:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, hist_file)


def load(force=False, verbose=False):
    if not force:
        try:
            if os.path.exists(CACHE_FILE):
                c = json.load(open(CACHE_FILE, encoding='utf-8'))
                if time.time() - c.get('_ts', 0) < CACHE_TTL and c.get('data'):
                    return c['data']
        except Exception:
            pass
    return build(verbose=verbose)


if __name__ == '__main__':
    d = build(verbose=True)
    rows = sorted(d.items(), key=lambda kv: kv[1]['ann_pct'])
    print('\n--- 最强 Back(多头展期收益最高, 前 12) ---')
    for k, v in rows[:12]:
        print('%-6s %-8s C1=%-8s C2=%-8s 月差 %6.2f%%  年化 %7.2f%%  多头展期 %+7.2f%%  %s%s'
              % (k, v['cn'], v['c1'], v['c2'], v['gap_pct'], v['ann_pct'], v['roll_long_pct'],
                 v['struct'], ' [远月薄]' if v['thin'] else ''))
    print('\n--- 最强 Contango(空头展期收益最高, 前 12) ---')
    for k, v in rows[::-1][:12]:
        print('%-6s %-8s C1=%-8s C2=%-8s 月差 %6.2f%%  年化 %7.2f%%  多头展期 %+7.2f%%  %s%s'
              % (k, v['cn'], v['c1'], v['c2'], v['gap_pct'], v['ann_pct'], v['roll_long_pct'],
                 v['struct'], ' [远月薄]' if v['thin'] else ''))
