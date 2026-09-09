# -*- coding: utf-8 -*-
"""
期权到期日计算
--------------
到期日(最后交易日)规则按各交易所公布口径实现, 已用 2026 年 9 月期货公司公告逐条核对:

规则表(来源: 广发期货/招商期货/正信期货 2026-09 到期通知, 三家口径一致)
  上期所 常规    : 标的期货交割月前一个月的倒数第 5 个交易日
  上期所 燃料油  : 标的期货交割月前一个月的倒数第 10 个交易日
  能源中心 原油  : 标的期货交割月前一个月的倒数第 13 个交易日
  能源中心 其他  : 标的期货交割月前一个月的倒数第 5 个交易日
  大商所 常规    : 标的期货交割月前一个月的第 12 个交易日
  大商所 系列    : 标的期货交割月前两个月的第 12 个交易日
  郑商所 常规    : 标的期货交割月前一个月第 15 个日历日之前(含该日)的倒数第 3 个交易日
  郑商所 系列    : 标的期货交割月前两个月第 15 个日历日之前(含该日)的倒数第 3 个交易日
  郑商所 苹果/PX/红枣 : 标的期货交割月前两个月最后一个日历日之前(含该日)的倒数第 3 个交易日
  广期所         : 标的期货交割月前一个月的第 5 个交易日
  中金所 股指期权: 合约到期月份的第三个星期五(遇法定节假日顺延)

交易日 = 工作日 - 法定节假日(2026 年节假日按国务院办公厅国办发明电〔2025〕7 号)。
期货交易所休市安排个别年份可能长于法定假期, 故结果为推算值, 以交易所/期货公司公告为准。
"""
import os
import json
import time
from datetime import date, timedelta

try:
    from fetch_quotes import COMMODITY_CODES, INDEX_CODES, DATA_DIR
except ImportError:
    DATA_DIR = 'data'
    COMMODITY_CODES = {}
    INDEX_CODES = {}

CACHE_FILE = os.path.join(DATA_DIR, 'option_expiry.json')
CACHE_TTL = 6 * 3600

# ---------------------------------------------------------------- 节假日
# 2026 年法定节假日中"周一至周五"的休市日(周末本就非交易日,不重复列出)
HOLIDAYS = {
    # 元旦 1/1(四)-1/3(六)
    date(2026, 1, 1), date(2026, 1, 2),
    # 春节 2/15(日)-2/23(一)
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18), date(2026, 2, 19),
    date(2026, 2, 20), date(2026, 2, 23),
    # 清明 4/4(六)-4/6(一)
    date(2026, 4, 6),
    # 劳动节 5/1(五)-5/5(二)
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),
    # 端午 6/19(五)-6/21(日)
    date(2026, 6, 19),
    # 中秋 9/25(五)-9/27(日)
    date(2026, 9, 25),
    # 国庆 10/1(四)-10/7(三)
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),
    date(2026, 10, 6), date(2026, 10, 7),
}


def is_trading_day(d):
    return d.weekday() < 5 and d not in HOLIDAYS


def trading_days(y, m):
    """返回 y 年 m 月所有交易日(升序)"""
    out = []
    d = date(y, m, 1)
    while d.month == m:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _shift_month(y, m, delta):
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def resolve(rule, deliv_y, deliv_m):
    """按规则算期权到期日。deliv_* = 标的期货交割年月"""
    if rule == 'cffex':                       # 到期月=交割月, 第三个周五
        tds = trading_days(deliv_y, deliv_m)
        fridays = [d for d in tds if d.weekday() == 4]
        return fridays[2] if len(fridays) >= 3 else None

    if rule.endswith('_2m'):                  # 前两个月
        base = rule[:-3]
        y, m = _shift_month(deliv_y, deliv_m, -2)
    else:                                     # 前一个月
        base = rule
        y, m = _shift_month(deliv_y, deliv_m, -1)

    tds = trading_days(y, m)
    if not tds:
        return None

    if base == 'shfe':                        # 倒数第 5
        return tds[-5] if len(tds) >= 5 else None
    if base == 'shfe_fu':                     # 倒数第 10
        return tds[-10] if len(tds) >= 10 else None
    if base == 'ine_sc':                      # 倒数第 13
        return tds[-13] if len(tds) >= 13 else None
    if base == 'ine':                         # 倒数第 5
        return tds[-5] if len(tds) >= 5 else None
    if base == 'dce':                         # 正数第 12
        return tds[11] if len(tds) >= 12 else None
    if base == 'gfex':                        # 正数第 5
        return tds[4] if len(tds) >= 5 else None
    if base == 'czce':                        # 前 15 个日历日内的倒数第 3 个交易日
        sub = [d for d in tds if d.day <= 15]
        return sub[-3] if len(sub) >= 3 else None
    if base == 'czce_last':                   # 全月倒数第 3 个交易日
        return tds[-3] if len(tds) >= 3 else None
    return None


# ---------------------------------------------------------------- 品种表
# prefix -> (rule, 交易所, 中文名)
OPTION_VARIETIES = {
    # 上期所(不含燃料油)
    'CU': ('shfe', '上期所', '沪铜'), 'AL': ('shfe', '上期所', '沪铝'),
    'ZN': ('shfe', '上期所', '沪锌'), 'PB': ('shfe', '上期所', '沪铅'),
    'NI': ('shfe', '上期所', '沪镍'), 'SN': ('shfe', '上期所', '沪锡'),
    'AO': ('shfe', '上期所', '氧化铝'), 'AD': ('shfe', '上期所', '铸造铝合金'),
    'AU': ('shfe', '上期所', '黄金'), 'AG': ('shfe', '上期所', '白银'),
    'RB': ('shfe', '上期所', '螺纹钢'), 'BU': ('shfe', '上期所', '石油沥青'),
    'BR': ('shfe', '上期所', '丁二烯橡胶'), 'RU': ('shfe', '上期所', '天然橡胶'),
    'SP': ('shfe', '上期所', '纸浆'), 'OP': ('shfe', '上期所', '胶版印刷纸'),
    'FU': ('shfe_fu', '上期所', '燃料油'),
    # 能源中心
    'SC': ('ine_sc', '能源中心', '原油'), 'NR': ('ine', '能源中心', '20号胶'),
    'BC': ('ine', '能源中心', '国际铜'),
    # 大商所
    'B': ('dce', '大商所', '黄大豆2号'), 'BZ': ('dce', '大商所', '纯苯'),
    'EB': ('dce', '大商所', '苯乙烯'), 'EG': ('dce', '大商所', '乙二醇'),
    'I': ('dce', '大商所', '铁矿石'), 'L': ('dce', '大商所', '聚乙烯'),
    'JM': ('dce', '大商所', '焦煤'), 'JD': ('dce', '大商所', '鸡蛋'),
    'P': ('dce', '大商所', '棕榈油'), 'PG': ('dce', '大商所', '液化石油气'),
    'PP': ('dce', '大商所', '聚丙烯'), 'V': ('dce', '大商所', 'PVC'),
    'CS': ('dce', '大商所', '玉米淀粉'), 'LH': ('dce', '大商所', '生猪'),
    'LG': ('dce', '大商所', '原木'),
    # 大商所系列期权(豆粕/豆一/豆油/玉米)
    'M': ('dce_2m', '大商所', '豆粕'), 'A': ('dce_2m', '大商所', '黄大豆1号'),
    'Y': ('dce_2m', '大商所', '豆油'), 'C': ('dce_2m', '大商所', '玉米'),
    # 郑商所常规
    'SR': ('czce_2m', '郑商所', '白糖'), 'CF': ('czce', '郑商所', '棉花'),
    'TA': ('czce', '郑商所', 'PTA'), 'MA': ('czce', '郑商所', '甲醇'),
    'RM': ('czce', '郑商所', '菜粕'), 'OI': ('czce', '郑商所', '菜油'),
    'PK': ('czce', '郑商所', '花生'), 'ZC': ('czce', '郑商所', '动力煤'),
    'FG': ('czce', '郑商所', '玻璃'), 'SA': ('czce', '郑商所', '纯碱'),
    'SF': ('czce', '郑商所', '硅铁'), 'SM': ('czce', '郑商所', '锰硅'),
    'SH': ('czce', '郑商所', '烧碱'), 'UR': ('czce', '郑商所', '尿素'),
    'PF': ('czce', '郑商所', '短纤'), 'PL': ('czce', '郑商所', '丙烯'),
    'PR': ('czce', '郑商所', '瓶片'),
    # 郑商所: 苹果/对二甲苯/红枣
    'AP': ('czce_last_2m', '郑商所', '苹果'), 'PX': ('czce_last_2m', '郑商所', '对二甲苯'),
    'CJ': ('czce_last_2m', '郑商所', '红枣'),
    # 广期所
    'SI': ('gfex', '广期所', '工业硅'), 'LC': ('gfex', '广期所', '碳酸锂'),
    'PS': ('gfex', '广期所', '多晶硅'), 'PT': ('gfex', '广期所', '铂'),
    'PD': ('gfex', '广期所', '钯'),
}

RULE_TEXT = {
    'shfe': '交割月前一个月倒数第5个交易日',
    'shfe_fu': '交割月前一个月倒数第10个交易日',
    'ine_sc': '交割月前一个月倒数第13个交易日',
    'ine': '交割月前一个月倒数第5个交易日',
    'dce': '交割月前一个月第12个交易日',
    'dce_2m': '交割月前两个月第12个交易日',
    'czce': '交割月前一个月第15个日历日之前(含该日)的倒数第3个交易日',
    'czce_2m': '交割月前两个月第15个日历日之前(含该日)的倒数第3个交易日',
    'czce_last_2m': '交割月前两个月最后一个日历日之前(含该日)的倒数第3个交易日',
    'gfex': '交割月前一个月第5个交易日',
    'cffex': '到期月份第三个星期五',
}

# 中金所股指期权: 合约月份为当月/下月/随后两个季月, 规则统一为第三个周五
INDEX_OPTIONS = {'IO': '沪深300股指期权', 'HO': '上证50股指期权', 'MO': '中证1000股指期权'}

# 2026-09 公告核对基准(广发期货《关于2026年9月商品期权合约到期日有关事项的通知》)
KNOWN_2026_09 = {
    ('GFEX', 2610): date(2026, 9, 7),
    ('CZCE', 2610): date(2026, 9, 11),
    ('CZCE_SR', 2611): date(2026, 9, 11),
    ('INE_SC', 2610): date(2026, 9, 11),
    ('DCE', 2610): date(2026, 9, 16),
    ('DCE_SERIES', 2611): date(2026, 9, 16),
    ('SHFE_FU', 2610): date(2026, 9, 16),
    ('SHFE', 2610): date(2026, 9, 23),
    ('INE', 2610): date(2026, 9, 23),
    ('CZCE_LAST', 2611): date(2026, 9, 28),
    ('CFFEX', 2609): date(2026, 9, 18),
}


def verify(verbose=True):
    """用 2026-09 公告日期核对规则实现, 全部通过才说明规则与交易日历正确"""
    cases = [
        ('GFEX', 'gfex', 2026, 10), ('CZCE', 'czce', 2026, 10),
        ('CZCE_SR', 'czce_2m', 2026, 11), ('INE_SC', 'ine_sc', 2026, 10),
        ('DCE', 'dce', 2026, 10), ('DCE_SERIES', 'dce_2m', 2026, 11),
        ('SHFE_FU', 'shfe_fu', 2026, 10), ('SHFE', 'shfe', 2026, 10),
        ('INE', 'ine', 2026, 10), ('CZCE_LAST', 'czce_last_2m', 2026, 11),
        ('CFFEX', 'cffex', 2026, 9),
    ]
    ok = True
    for key, rule, y, m in cases:
        got = resolve(rule, y, m)
        want = KNOWN_2026_09.get((key, y % 100 * 100 + m))
        mark = 'OK ' if got == want else 'FAIL'
        if got != want:
            ok = False
        if verbose:
            print('  [%s] %-12s 算得 %s  公告 %s' % (mark, key, got, want))
    return ok


# ---------------------------------------------------------------- 主流程
def _cn_map():
    """code0 -> 中文名"""
    out = {}
    for _cat, lst in COMMODITY_CODES.items():
        for code, cn, _n in lst:
            out[code] = cn
    for _cat, lst in INDEX_CODES.items():
        for code, cn, _n in lst:
            out[code] = cn
    return out


def _contract_exists(codes):
    """批量判断期货合约是否存在(有行情即存在)"""
    try:
        from main_contract import _http_get
    except Exception:
        return set()
    exist = set()
    for i in range(0, len(codes), 100):
        try:
            raw = _http_get(codes[i:i + 100])
            exist.update(raw.keys())
        except Exception:
            pass
    return exist


def build_option_expiry(today=None, days_ahead=45, verbose=True):
    """
    返回 {'date': 'YYYY-MM-DD', 'verified': bool, 'items': [...]}
    item: {code, cn, exchange, contract, expiry, days, rule, level}
          level: imminent(<=3天) / caution(<=7天) / warn(<=15天) / soon(其余)
    """
    today = today or date.today()

    # ---- 缓存: 同日且未过期则复用(避免每次刷新都枚举上百个合约) ----
    try:
        if os.path.exists(CACHE_FILE):
            c = json.load(open(CACHE_FILE, encoding='utf-8'))
            if c.get('date') == today.strftime('%Y-%m-%d') and \
                    time.time() - float(c.get('_ts', 0)) < CACHE_TTL and c.get('items'):
                for it in c['items']:
                    d = date.fromisoformat(it['expiry'])
                    it['days'] = (d - today).days
                    it['level'] = ('imminent' if it['days'] <= 3 else
                                   'caution' if it['days'] <= 7 else
                                   'warn' if it['days'] <= 15 else 'soon')
                if verbose:
                    print('  [option_expiry] 复用缓存 %d 项' % len(c['items']))
                return c
    except Exception:
        pass

    end = today + timedelta(days=days_ahead)
    cn_map = _cn_map()

    # ---- 1) 枚举候选合约并判定存在性 ----
    ym = []
    for i in range(0, 4):
        y, m = _shift_month(today.year, today.month, i - 1)
        ym.append((y, m))

    cand = []                       # (prefix, rule, y, m, contract_code)
    for prefix, (rule, _ex, _cn) in OPTION_VARIETIES.items():
        for y, m in ym:
            cand.append((prefix, rule, y, m, '%s%02d%02d' % (prefix, y % 100, m)))
    exist = _contract_exists([c[4] for c in cand])
    if verbose:
        print('  [option_expiry] 候选合约 %d 个, 实际存在 %d 个' % (len(cand), len(exist)))

    # ---- 2) 计算到期日 ----
    items = []
    for prefix, rule, y, m, code in cand:
        if code not in exist:
            continue
        d = resolve(rule, y, m)
        if not d or d < today or d > end:
            continue
        _rule, exchange, cn = OPTION_VARIETIES[prefix]
        days = (d - today).days
        level = ('imminent' if days <= 3 else
                 'caution' if days <= 7 else
                 'warn' if days <= 15 else 'soon')
        items.append({
            'code': prefix + '0', 'cn': cn, 'contract': code,
            'exchange': exchange,
            'expiry': d.strftime('%Y-%m-%d'), 'days': days,
            'rule': RULE_TEXT.get(rule, ''),
            'level': level,
        })

    # ---- 3) 中金所股指期权: 当月/下月/随后两个季月 ----
    for prefix, name in INDEX_OPTIONS.items():
        months = [(today.year, today.month), _shift_month(today.year, today.month, 1)]
        # 随后两个季月
        q = ((today.month - 1) // 3 + 1) * 3 + 1
        for _ in range(2):
            y, m = _shift_month(today.year, 1, q - 1)
            if (y, m) not in months:
                months.append((y, m))
            q += 3
        for y, m in months:
            d = resolve('cffex', y, m)
            if not d or d < today or d > end:
                continue
            items.append({
                'code': prefix, 'cn': name, 'contract': '%s%02d%02d' % (prefix, y % 100, m),
                'exchange': '中金所', 'expiry': d.strftime('%Y-%m-%d'),
                'days': (d - today).days,
                'rule': RULE_TEXT['cffex'],
                'level': ('imminent' if (d - today).days <= 3 else
                          'caution' if (d - today).days <= 7 else
                          'warn' if (d - today).days <= 15 else 'soon'),
            })

    items.sort(key=lambda x: (x['expiry'], x['exchange'], x['cn']))
    out = {'date': today.strftime('%Y-%m-%d'), '_ts': time.time(),
           'verified': verify(verbose=verbose),
           'items': items,
           'note': ('按各交易所公布的期权到期日规则推算, 并用期货公司已公布的到期通知核对;'
                    '商品期权到期日以交易所/期货公司公告为准。')}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump(out, open(CACHE_FILE, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception:
        pass
    if verbose:
        print('  [option_expiry] 未来 %d 天内到期 %d 项, 规则核对 %s'
              % (days_ahead, len(items), '通过' if out['verified'] else '未通过'))
    return out


if __name__ == '__main__':
    print('=== 2026-09 公告核对 ===')
    verify()
    print('\n=== 未来 45 天到期 ===')
    r = build_option_expiry()
    cur = None
    for it in r['items']:
        if it['expiry'] != cur:
            cur = it['expiry']
            print('\n%s（%d 天后）' % (cur, it['days']))
        print('  %-8s %-10s %-8s %s' % (it['contract'], it['cn'], it['exchange'], it['rule']))
