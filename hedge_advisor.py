"""
hedge_advisor.py V1 — 企业套保决策(期货 vs 期权 / 现在套不套)
============================================================
理论依据(教科书结论, 非拍脑袋):
  套保恒等式: 套保后的有效价格 = 建仓期货价 F1 + 平仓时基差 B2 (基差 = 现货 - 期货)
    · 用料企业买入套保: 净采购成本 = F1 + B2 → 建仓时基差处于**高位**, 未来基差回落,
      净成本低于期货价 → 买保有优势; 基差低位建仓, 基差回升会抬高净成本 → 买保不划算。
    · 供货/库存企业卖出套保: 有效销售价 = F1 + B2 → 建仓时基差处于**低位**, 未来基差
      回升增厚销售价 → 卖保有优势; 基差高位建仓, 基差回落会侵蚀 → 卖保不划算。
  期货 vs 期权:
    · 需要刚性锁价、基差逻辑清晰、资金可覆盖保证金 → 期货
    · 想保留有利方向的收益空间、不愿追加保证金、事件前护尾部 → 期权(买权为主)
    · 波动率低 → 期权权利金便宜, 期权性价比高; 波动率高 → 期权贵, 期货更直接
  是否套保的紧迫度:
    · 双条件共振(价格分位 + 基差分位同向支持) → 积极(建议比例 70-100%)
    · 单条件支持 → 适度套保(40-60%)
    · 条件不利 → 暂缓/小比例(0-30%), 并说明等什么信号

数据输入: tech(价格分位/ATR波动率/趋势) + basis(基差/近一年基差分位) + calendar(事件)
"""
import statistics


# 事件影响标签(calendar impact=high 的事件临近时提示)
def _event_warning(calendar, days=3):
    try:
        from datetime import datetime, timedelta
        today = datetime.now().date()
        horizon = today + timedelta(days=days)
        names = []
        for e in calendar:
            if e.get('impact') != 'high':
                continue
            d = datetime.strptime(e['date'], '%Y-%m-%d').date()
            if today <= d <= horizon:
                names.append(e['name'])
        return names[:2]
    except Exception:
        return []


def build_hedge_advice(tech_map, basis_items, calendar, cn_fallback=None):
    """
    tech_map:   {code: {pct_1y, atr_pct, trend, cn, last, ...}}
    basis_items:{code: {basis, pct, prc_hist, pos180, hi180, lo180, avg180, days, contract}}
    返回 {code: {cn, price_pct, basis_prc, atr_pct, trend, contract,
                 buyer:  {action, ratio, why, urgency},
                 seller: {action, ratio, why, urgency},
                 tool:   '期货'/'期权'/'期货为主,期权护尾部',
                 tool_why: str, event_note: str}}
    """
    cn_fallback = cn_fallback or {}
    codes = [c for c in basis_items if c in tech_map]
    if not codes:
        return {}

    # 波动率横截面分位(判断期权贵/便宜用)
    atrs = [tech_map[c].get('atr_pct') for c in codes]
    atrs = [a for a in atrs if a]
    atr_med = statistics.median(atrs) if atrs else None
    atr_hi = sorted(atrs)[int(len(atrs) * 0.75)] if len(atrs) >= 8 else None

    events = _event_warning(calendar)
    out = {}

    for code in codes:
        t = tech_map[code]
        b = basis_items[code]
        price_pct = t.get('pct_1y')
        basis_prc = b.get('prc_hist')
        atr_pct = t.get('atr_pct')
        if price_pct is None or basis_prc is None:
            continue

        # ---------- 用料企业(买入套保) ----------
        if basis_prc >= 60:
            if price_pct <= 40:
                buyer = ('积极买保', 70, '价格处近1年低位(%d%%)+基差处高位(%d%%),双重有利:锁定低价,基差回落还能再降成本' % (round(price_pct), round(basis_prc)), 3)
            elif price_pct >= 60:
                buyer = ('买保优先级高', 60, '价格已处高位(%d%%),涨价风险大于基差风险,先锁大头;基差高位(%d%%)提供额外缓冲' % (round(price_pct), round(basis_prc)), 2)
            else:
                buyer = ('适度买保', 50, '价格中性(%d%%),基差高位(%d%%)是主要买保理由' % (round(price_pct), round(basis_prc)), 1)
        elif basis_prc <= 30:
            buyer = ('暂缓买保', 20, '基差处近1年低位(%d%%),买保后基差回升会抬高净成本;等基差回升至中位再建仓' % round(basis_prc), 0)
        else:
            buyer = ('分批买保', 40, '价格分位%d%%、基差分位%d%%,均中性:按采购计划分批,不追一次锁死' % (round(price_pct), round(basis_prc)), 1)

        # ---------- 供货/库存企业(卖出套保) ----------
        if basis_prc <= 30:
            if price_pct >= 60:
                seller = ('积极卖保', 70, '价格处近1年高位(%d%%)+基差处低位(%d%%),双重有利:高位锁价,基差回升还能增厚销售价' % (round(price_pct), round(basis_prc)), 3)
            elif price_pct <= 40:
                seller = ('卖保保库存', 50, '价格偏低(%d%%),卖保主要目的是稳库存价值而非锁高价;基差低位(%d%%)回升有增厚空间' % (round(price_pct), round(basis_prc)), 1)
            else:
                seller = ('适度卖保', 50, '价格中性(%d%%),基差低位(%d%%)是主要卖保理由' % (round(price_pct), round(basis_prc)), 1)
        elif basis_prc >= 60:
            seller = ('暂缓卖保', 20, '基差处近1年高位(%d%%),卖保后基差回落会侵蚀销售价;等基差回落至中位再建仓' % round(basis_prc), 0)
        else:
            seller = ('分批卖保', 40, '价格分位%d%%、基差分位%d%%,均中性:按销售计划分批,保留部分敞口' % (round(price_pct), round(basis_prc)), 1)

        # ---------- 工具选择: 期货 vs 期权 ----------
        if atr_pct and atr_hi and atr_pct >= atr_hi:
            tool = '期货为主'
            tool_why = '波动率偏高(ATR %.2f%%, 高于多数品种), 期权权利金贵, 用期货直接锁价更省成本' % atr_pct
        elif atr_pct and atr_med and atr_pct <= atr_med:
            tool = '期权可替代'
            tool_why = '波动率偏低(ATR %.2f%%), 期权权利金便宜, 用买权替代期货: 方向错了损失权利金, 方向对了保留收益空间' % atr_pct
        else:
            tool = '期货+期权组合'
            tool_why = '波动率中等(ATR %.2f%%): 期货锁主要头寸, 少量买虚值期权护极端行情' % (atr_pct or 0)

        if events:
            tool_why += ';近3日有重要数据(%s), 事件前注意保证金/跳空风险' % '、'.join(events)

        urgency = max(buyer[3], seller[3])
        out[code] = {
            'cn': tech_map[code].get('cn') or cn_fallback.get(code, code),
            'contract': b.get('contract', ''),
            'price_pct': round(price_pct, 1),
            'basis_prc': round(basis_prc, 1),
            'atr_pct': atr_pct,
            'trend': t.get('trend', ''),
            'basis': b.get('basis'),
            'buyer': {'action': buyer[0], 'ratio': buyer[1], 'why': buyer[2]},
            'seller': {'action': seller[0], 'ratio': seller[1], 'why': seller[2]},
            'tool': tool,
            'tool_why': tool_why,
            'urgency': urgency,
        }

    # 紧迫度高的排前
    out = dict(sorted(out.items(), key=lambda kv: -kv[1]['urgency']))
    return out
