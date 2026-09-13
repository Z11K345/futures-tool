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
  3. C1 = 活跃合约中成交量最大者(持仓量次之, 即主力);
     C2 = 月份晚于 C1 的活跃合约中成交量最大者(次主力, 不要求紧邻月份 ——
          10月是主力但11月无人交易时, 取真正有成交的远月);
     C3 = 月份晚于 C1 的活跃合约中成交量第二大者(用于判断曲线是否单调).
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


def _load_spot(verbose=False):
    """读取现货价(生意社现期表), 返回 {品种码: (spot, 现货日期)}。

    只在现货与期货为同一交易日时, 前端才把现货段纳入曲线展示与结构判定 ——
    否则两个不同交易日的数据混在一条链上比大小, 结论不可信(实测黄金因此
    出现"现货>近月却判 Contango"的自相矛盾)。
    """
    f = os.path.join(DATA_DIR, 'basis_days.json')
    try:
        days = json.load(open(f, encoding='utf-8'))
    except Exception:
        return {}
    if not days:
        return {}
    day = sorted(days.keys())[-1]
    rows = days.get(day) or {}
    out = {}
    name2code = {}
    for _cat, lst in COMMODITY_CODES.items():
        for code, cn, _name in lst:
            name2code[cn] = code
    # 生意社品种名与本地简称存在差异(生意社用"铜"/"铝", 本地用"沪铜"/"沪铝"),
    # 不做映射会导致这些品种的现货段整段丢失。此处补齐已知别名。
    alias = {
        '铜': 'CU0', '铝': 'AL0', '锌': 'ZN0', '铅': 'PB0', '镍': 'NI0', '锡': 'SN0',
        '黄金': 'AU0', '白银': 'AG0', '螺纹钢': 'RB0', '线材': 'WR0',
        '燃料油': 'FU0', '石油沥青': 'BU0', '天然橡胶': 'RU0', '纸浆': 'SP0',
        '不锈钢': 'SS0', '热轧卷板': 'HC0', '铁矿石': 'I0', '焦炭': 'J0', '焦煤': 'JM0',
        '豆一': 'A0', '豆粕': 'M0', '豆油': 'Y0', '棕榈油': 'P0', '玉米': 'C0',
        '玉米淀粉': 'CS0', '鸡蛋': 'JD0', '生猪': 'LH0', '白糖': 'SR0', '棉花': 'CF0',
        '棉纱': 'CY0', '苹果': 'AP0', '红枣': 'CJ0', '花生': 'PK0', '菜油': 'OI0',
        '菜粕': 'RM0', '早籼稻': 'RI0', '粳稻': 'JR0', '晚籼稻': 'LR0',
        'PTA': 'TA0', '甲醇': 'MA0', '尿素': 'UR0', '纯碱': 'SA0', '玻璃': 'FG0',
        '动力煤': 'ZC0', '硅铁': 'SF0', '锰硅': 'SM0', '短纤': 'PF0', '苯乙烯': 'EB0',
        '乙二醇': 'EG0', '液化石油气': 'PG0', '聚丙烯': 'PP0', '塑料': 'L0',
        'PVC': 'V0', '原油': 'SC0', '低硫燃料油': 'LU0', '20号胶': 'NR0',
        '国际铜': 'BC0', '氧化铝': 'AO0', '工业硅': 'SI0', '碳酸锂': 'LC0',
        '多晶硅': 'PS0', '烧碱': 'SH0', '对二甲苯': 'PX0', '瓶片': 'PR0',
        '集运指数': 'EC0', '棉纱': 'CY0',
    }
    for nm, r in rows.items():
        code = name2code.get(nm) or alias.get(nm) \
            or name2code.get((r.get('name_raw') or '').strip()) \
            or alias.get((r.get('name_raw') or '').strip())
        if not code:
            continue
        try:
            spot = float(r.get('spot'))
        except (TypeError, ValueError):
            continue
        if spot > 0:
            out[code] = (spot, day)
    if verbose:
        print('[OK] term spot: %d 品种 现货日 %s' % (len(out), day))
    return out


def build(verbose=True):
    now = datetime.datetime.now()
    cur_ym = now.year * 12 + now.month
    spot_map = _load_spot(verbose=verbose)

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
            # 新浪字段 17 = 该合约行情日期。注意夜盘跨零点: 9/11 夜盘(21:00-次日02:30)
            # 的成交记在 9/12, 但它属于 9/11 这个交易日。若直接拿字段 17 与现货日比,
            # 沪铜/黄金这类夜盘活跃品种会被误判为"不同日"。此处按夜盘归属做归正。
            fday = parts[17].strip() if len(parts) > 17 else ''
            ftime = parts[1].strip() if len(parts) > 1 else ''
            try:
                hhmm = int(ftime[:2]) * 100 + int(ftime[2:4])
            except (ValueError, IndexError):
                hhmm = 0
            # 00:00-03:00 视为前一交易日的夜盘延续
            if fday and hhmm < 300:
                try:
                    fday = (datetime.datetime.strptime(fday, '%Y-%m-%d')
                            - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                except ValueError:
                    pass
            rows.append({'code': c, 'last': last, 'oi': oi, 'vol': vol, 'ym': ym, 'd': fday})
        if len(rows) < 2:
            continue
        max_vol = max(r['vol'] for r in rows)
        active = [r for r in rows if (r['vol'] >= max_vol * ACTIVE_VOL_RATIO and r['oi'] > 0)] or rows
        if kind == 'cmd':
            # 商品: 剔除已进入交割月的合约(自然人已清仓, 价格只反映交割博弈)
            active = [r for r in active if r['ym'] > cur_ym] or active
        # 主力 C1 = 成交量最大(持仓量次之); 次主力 C2 = 月份晚于 C1 的活跃合约里成交量最大者
        # (按成交量选, 而非按月份紧邻选 —— 10月是主力但11月无人交易时, 取真正有成交的远月)
        active.sort(key=lambda r: (-r['vol'], -r['oi']))
        c1 = active[0]
        rest = [r for r in active if r['ym'] > c1['ym']]
        rest.sort(key=lambda r: (-r['vol'], -r['oi']))
        if not rest:
            continue
        c2 = rest[0]
        # C3 必须取「月份晚于 C2」的活跃合约 —— 否则链条顺序错乱(出现 C3 月份早于 C2),
        # 后面的严格单调判定就没有意义。在 C2 之后的合约里仍按成交量选最具代表性的那个。
        after_c2 = [r for r in rest if r['ym'] > c2['ym']]
        after_c2.sort(key=lambda r: (-r['vol'], -r['oi']))
        c3 = after_c2[0] if after_c2 else None

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
            # 极端月差: |月差| >= 5% 时, 年化数字(×12/K 外推)严重失真(多为临近交割的
            # 逼仓/挤仓价差, 或新上市品种流动性异常), 必须显著提示, 不能当常态展期收益看
            'extreme': bool(abs(gap_pct) >= 5.0),
        }
        if c3:
            gap2 = _month_gap(c1['code'], c3['code'])
            if gap2 and gap2 > 0:
                item['c3'] = c3['code']
                item['c3_last'] = round(c3['last'], 2)
                item['c3_oi'] = int(c3['oi'])
                item['ann_far_pct'] = round((c3['last'] - c1['last']) / c1['last'] * 100 * 12.0 / gap2, 2)
        # ---- 现货段(可选): 现货与期货须为同一交易日才纳入 ----
        # 教科书定义里的期限结构是「现货 → 近月 → 远月」的完整链条, 现货是链条起点。
        # 但不能拿隔日的现货去和今日的期货比大小, 因此加同日校验。
        spot = None
        spot_day = None
        sm = spot_map.get(code0)
        if sm:
            spot, spot_day = sm
        item['spot'] = round(spot, 4) if spot else None
        item['spot_day'] = spot_day
        # fut_day: 期货行情实际所属交易日(取自 C1 的新浪行情日期字段, 而非本机时间 ——
        # 周末/节假日跑批时本机日期会晚于最后一个交易日, 用它做同日校验会全部误判为不同日)
        item['fut_day'] = c1.get('d') or now.strftime('%Y-%m-%d')
        # 量纲校验: 现货与期货报价单位可能不同(玻璃 现货12.4元/㎡ vs 期货958元/吨;
        # 鸡蛋 现货10.62元/公斤 vs 期货3829元/500kg)。生意社给出的基差率会掩盖这种
        # 量纲差异(玻璃仅1.92%), 但现货价与期货价相差数十倍, 直接比大小毫无意义。
        # 判定标准: |现货/期货 - 1| > 5 即量纲不可比, 现货段不得纳入判定与展示。
        unit_ok = True
        if spot and c1['last']:
            ratio = spot / c1['last'] if c1['last'] else 0
            if not (0.2 <= ratio <= 5.0):
                unit_ok = False
        item['spot_unit_ok'] = unit_ok
        item['spot_same_day'] = bool(spot and spot_day and spot_day == item['fut_day'] and unit_ok)

        # 结构判断 —— 严格按期限结构的标准定义:
        #   Contango   (正向市场) = 现货价 < 近月价 < 远月价, 近低远高
        #   Back       (反向市场) = 现货价 > 近月价 > 远月价, 近高远低
        #   要求逐月(含现货段)严格单调; 不满足严格单调时, 不套用这两个术语
        # 采用严格不等号, 避免"持平"被误判为单调。
        FLAT_THRESH = 0.15   # |gap_pct| < 0.15% 视为价格实质持平, 不参与单调判定
        p1, p2 = c1['last'], c2['last']
        flat12 = abs(gap_pct) < FLAT_THRESH
        # 现货段偏差(仅同日时参与判定)
        flat_s1 = False
        if item['spot_same_day'] and spot:
            sp_pct = (p1 - spot) / spot * 100 if spot else 0
            flat_s1 = abs(sp_pct) < FLAT_THRESH
        if c3:
            p3 = c3['last']
            gap23_pct = (p3 - p2) / p2 * 100 if p2 else 0
            flat23 = abs(gap23_pct) < FLAT_THRESH
            if flat12 or flat23 or flat_s1:
                item['struct'] = '非单调'      # 含持平段, 曲线不严格单调
                item['struct_reason'] = '含持平段'
            elif item['spot_same_day'] and spot:
                # 含现货的完整四段链条: 现货 → C1 → C2 → C3
                if spot < p1 < p2 < p3:
                    item['struct'] = 'Contango'
                    item['struct_reason'] = '现货<C1<C2<C3 严格递增'
                elif spot > p1 > p2 > p3:
                    item['struct'] = 'Back'
                    item['struct_reason'] = '现货>C1>C2>C3 严格递减'
                else:
                    item['struct'] = '非单调'
                    item['struct_reason'] = '现货段不单调'
            elif p1 < p2 < p3:
                item['struct'] = 'Contango'    # 严格逐月递增
                item['struct_reason'] = '严格逐月递增'
            elif p1 > p2 > p3:
                item['struct'] = 'Back'        # 严格逐月递减
                item['struct_reason'] = '严格逐月递减'
            else:
                item['struct'] = '非单调'      # 先升后降 / 先降后升
                item['struct_reason'] = '先升后降' if (p2 > p1 and p3 < p2) else '先降后升'
        else:
            # 仅有 C1/C2, 无 C3: 无法验证单调性, 不强行定性
            if flat12:
                item['struct'] = '平坦'
                item['struct_reason'] = '两月价差近平'
            else:
                item['struct'] = '仅两月'
                item['struct_reason'] = ('近月贴水(倾向Back)' if p2 > p1 else '近月升水(倾向Contango)')
        # 合约代码标记: 让前端能直接展示 C1/C2/C3 究竟是哪几个合约
        item['c1_c2'] = f"{c1['code']}→{c2['code']}"
        item['gap_months'] = gap
        if c3:
            item['c2_c3'] = f"{c2['code']}→{c3['code']}"
        out[code0] = item

    if verbose:
        n_back = sum(1 for v in out.values() if v['struct'] == 'Back')
        n_cont = sum(1 for v in out.values() if v['struct'] == 'Contango')
        n_flat = sum(1 for v in out.values() if v['struct'] == '平坦')
        n_mix = sum(1 for v in out.values() if v['struct'] == '非单调')
        n_two = sum(1 for v in out.values() if v['struct'] == '仅两月')
        n_sd = sum(1 for v in out.values() if v.get('spot_same_day'))
        print('[OK] term_structure: %d 品种 (严格单调: Back %d / Contango %d; 未定性: 非单调 %d / 仅两月 %d / 平坦 %d; 现货同日可用 %d)'
              % (len(out), n_back, n_cont, n_mix, n_two, n_flat, n_sd))

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
