# -*- coding: utf-8 -*-
"""
multi_factor.py — 期货单品种多因子分析
================================================
用户 2026-09-19 要求: "在网上找一些现在比较流行的因子加进去做一个多因子分析"。

本模块在已算好的 tech / basis / term 之上, 叠加持仓变化(OI 历史),
组合成一套面向商品/股指期货的"多因子打分":

  因子1  趋势动量  (weight 0.33)  来源 tech: 均线排列 + 趋势 + 突破 + MACD
  因子2  期限结构  (weight 0.28)  来源 term: 展期收益 roll_long_pct (Back 利多 / Contango 利空)
  因子3  基差位置  (weight 0.22)  来源 basis: 近一年基差分位 prc_hist (高位=现货紧张=利多)
  因子4  持仓变化  (weight 0.17)  来源 kline 持仓序列: 近 20 日持仓变化率(增仓顺势=强化)

每个因子输出 -100 ~ +100 的得分(正=偏多, 负=偏空), 加权得到 composite,
再给出 bias(偏多/偏空/中性)与 strength(|composite|)。

波动率(atr_pct)不作为方向因子, 仅作"波动机会"展示字段。

输出: result['multi_factor'] = {
  'updated': str, 'note': str,
  'items': { code: {cn, f_trend, f_term, f_basis, f_oi, composite, bias,
                     strength, atr_pct, breakout, term_struct, basis_prc,
                     oi_chg_pct, note} }
}
"""
import os
import json
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, 'data')
KLINE_CACHE = os.path.join(DATA_DIR, 'kline_cache.json')

W_TREND = 0.33
W_TERM = 0.28
W_BASIS = 0.22
W_OI = 0.17


def _clamp(x, lo=-100.0, hi=100.0):
    return max(lo, min(hi, x))


def _load_oi_cache():
    """读取 tech_indicators 的 K 线缓存, 取每个品种最近持仓序列。"""
    if not os.path.exists(KLINE_CACHE):
        return {}
    try:
        with open(KLINE_CACHE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _oi_chg_pct(cache, code, lookback=20):
    """近 lookback 根 K 线的持仓量变化率(%), 无数据返回 None。"""
    rec = cache.get(code)
    if not rec or not isinstance(rec, dict):
        return None
    bars = rec.get('bars')
    if not isinstance(bars, list) or len(bars) < lookback + 1:
        return None
    try:
        cur = bars[-1].get('p')
        old = bars[-(lookback + 1)].get('p')
        if not cur or not old:
            return None
        if old <= 0:
            return None
        return (cur - old) / old * 100.0
    except Exception:
        return None


def _trend_score(t):
    """趋势动量因子: 均线排列 + 趋势 + 突破 + MACD。"""
    s = 0.0
    ma = t.get('ma_arr')
    if ma == '多头排列':
        s += 35
    elif ma == '空头排列':
        s -= 35
    trend = t.get('trend')
    if trend == '上升趋势':
        s += 15
    elif trend == '下降趋势':
        s -= 15
    elif trend == '偏强震荡':
        s += 8
    elif trend == '偏弱震荡':
        s -= 8
    bo = t.get('breakout')
    if bo == 'near_high':
        s += 20
    elif bo == 'near_low':
        s -= 20
    m = t.get('macd') or {}
    sig = m.get('signal')
    if sig == '金叉':
        s += 12
    elif sig == '多头':
        s += 6
    elif sig == '死叉':
        s -= 12
    elif sig == '空头':
        s -= 6
    return _clamp(s)


def _term_score(item):
    """期限结构因子: 展期收益(多头展期年化)。Back(正)利多, Contango(负)利空。"""
    if not item:
        return 0.0
    roll = item.get('roll_long_pct')
    if roll is None:
        return 0.0
    s = _clamp(roll * 3.0)            # roll ±20% -> ±60
    # 远月流动性稀薄 / 极端月差 / 临近交割 / 自然人吃不到完整展期 -> 降权
    conf = 1.0
    if item.get('thin') or item.get('extreme') or item.get('near') or item.get('near_delivery'):
        conf = 0.5
    return _clamp(s * conf)


def _basis_score(bitem):
    """基差因子: 近一年基差分位。高(现货相对贵/供应偏紧)利多。"""
    if not bitem:
        return 0.0
    if bitem.get('unit_warn'):
        return 0.0
    prc = bitem.get('prc_hist')
    if prc is None:
        return 0.0
    return _clamp((prc - 50.0) * 2.0)


def _bias_of(comp):
    if comp >= 25:
        return '偏多'
    if comp >= 10:
        return '偏多(温和)'
    if comp <= -25:
        return '偏空'
    if comp <= -10:
        return '偏空(温和)'
    return '中性'


def _note(cn, f_trend, f_term, f_basis, f_oi, bias):
    """给新手的一句话因子解读(口语化、可朗读)。"""
    parts = []
    if f_trend >= 25:
        parts.append('价格趋势向上、均线多头排列')
    elif f_trend <= -25:
        parts.append('价格趋势向下、均线空头排列')
    elif f_trend >= 10:
        parts.append('技术面偏强')
    elif f_trend <= -10:
        parts.append('技术面偏弱')
    if f_term >= 20:
        parts.append('期限结构呈 Back(反向市场)、多头移仓有展期收益')
    elif f_term <= -20:
        parts.append('期限结构呈 Contango(正向市场)、多头移仓有成本')
    if f_basis >= 25:
        parts.append('基差处于近一年高位(现货相对偏强)')
    elif f_basis <= -25:
        parts.append('基差处于近一年低位(现货相对偏弱)')
    if f_oi >= 20:
        parts.append('持仓近 20 日明显增加、资金顺势流入')
    elif f_oi <= -20:
        parts.append('持仓近 20 日明显回落')
    if not parts:
        parts.append('各因子信号相互中和、方向不清晰')
    return '%s:%s' % (cn, '；'.join(parts)) + ('。' if parts else '')


def build(tech, basis, term, verbose=True):
    """
    tech : result['tech']   {code: tech_dict}
    basis: result['basis']  {'items': {code: bitem}}
    term : result['term']   {code: item}
    返回 {'updated','note','items':{code:{...}}}
    """
    t0 = time.time()
    tech = tech or {}
    basis_items = ((basis or {}).get('items') or {}) if isinstance(basis, dict) else {}
    term = term or {}
    oi_cache = _load_oi_cache()

    items = {}
    for code, t in tech.items():
        if not isinstance(t, dict):
            continue
        cn = t.get('cn', code)
        f_trend = _trend_score(t)
        f_term = _term_score(term.get(code))
        f_basis = _basis_score(basis_items.get(code))
        oi_chg = _oi_chg_pct(oi_cache, code)
        f_oi = _clamp((oi_chg or 0.0) * 4.0)
        # 趋势中性时, 单独的持仓变化方向意义不大 -> 减半
        if abs(f_trend) < 10:
            f_oi *= 0.5

        composite = (f_trend * W_TREND + f_term * W_TERM +
                     f_basis * W_BASIS + f_oi * W_OI)
        composite = round(composite, 1)
        bias = _bias_of(composite)

        items[code] = {
            'code': code,
            'cn': cn,
            'f_trend': round(f_trend, 1),
            'f_term': round(f_term, 1),
            'f_basis': round(f_basis, 1),
            'f_oi': round(f_oi, 1),
            'composite': composite,
            'bias': bias,
            'strength': round(abs(composite), 1),
            'atr_pct': t.get('atr_pct'),
            'breakout': t.get('breakout', ''),
            'term_struct': (term.get(code) or {}).get('struct', ''),
            'basis_prc': (basis_items.get(code) or {}).get('prc_hist'),
            'oi_chg_pct': round(oi_chg, 1) if oi_chg is not None else None,
            'note': _note(cn, f_trend, f_term, f_basis, f_oi, bias),
        }

    out = {
        'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'note': ('多因子打分 = 趋势动量 %.0f%% + 期限结构 %.0f%% + 基差位置 %.0f%% + 持仓变化 %.0f%%;'
                 ' 各因子 -100~+100(正偏多/负偏空), 加权得综合分与多空倾向。'
                 '因子为量化参考, 不构成交易依据。'
                 % (W_TREND * 100, W_TERM * 100, W_BASIS * 100, W_OI * 100)),
        'items': items,
    }
    if verbose:
        n_bull = sum(1 for v in items.values() if v['composite'] >= 10)
        n_bear = sum(1 for v in items.values() if v['composite'] <= -10)
        print('[OK] multi_factor: %d 品种 (偏多 %d / 偏空 %d / 中性 %d), 耗时 %.1fs'
              % (len(items), n_bull, n_bear, len(items) - n_bull - n_bear,
                 time.time() - t0))
    return out


if __name__ == '__main__':
    # 本地自检: 需要 tech/basis/term 已生成(data/ 下)。无则只打印空结构。
    try:
        import tech_indicators
        tech = tech_indicators.load_cache_build() if hasattr(tech_indicators, 'load_cache_build') else {}
    except Exception:
        tech = {}
    print('multi_factor self-check skipped (需 refresh 全量数据)')
