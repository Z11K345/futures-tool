"""
tech_indicators.py — 期货技术指标 / 历史分位 / K线形态计算
=================================================================
数据源: 新浪期货日K接口 InnerFuturesNewService.getDailyKLine
        (实测可用: RB0 4237 根, 含 2005 年至今; 字段 d/o/h/l/c/v/p/s)

设计要点:
  1) 日K按交易日缓存到 data/kline_cache.json, 同一交易日不重复拉取
     (全品种一次约 30MB, 云端 15 分钟刷新不能每次全拉)
  2) 所有指标纯 Python 实现, 不依赖 numpy/pandas (保持 0 依赖可部署)
  3) 历史分位 = 当前价在最近 N 根收盘价序列中的百分位(0~100)

输出结构(挂在 quotes.json['tech'][code]):
  {
    'date': '2026-09-04',        # 最新K线日期
    'close': 3166.0,
    'ma5/ma10/ma20/ma60': float,
    'ma_arr': '多头排列'/'空头排列'/'纠缠',
    'macd': {'dif','dea','hist','signal':'金叉'/'死叉'/'多头'/'空头'},
    'rsi14': float,
    'kdj': {'k','d','j','signal'},
    'boll': {'mid','up','low','width','pos': '上轨附近'/...},
    'atr14': float, 'atr_pct': float,
    'vol_ratio': float,          # 今日量/5日均量
    'pattern': '看涨吞没' 等,
    'pct_1y': 62.5, 'pct_3y': 40.1, 'pct_5y': 33.0,   # 历史分位
    'high_250': float, 'low_250': float,
    'trend': '上升趋势'/'下降趋势'/'震荡',
    'summary': '一句话技术面结论'
  }
"""
import json
import os
import re
import ssl
import time
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, 'data')
CACHE_FILE = os.path.join(DATA_DIR, 'kline_cache.json')

KLINE_API = ('https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_=/'
             'InnerFuturesNewService.getDailyKLine?symbol={symbol}')

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0',
    'Referer': 'https://finance.sina.com.cn',
}

# 需要计算技术指标的品种(代码 -> 新浪日K symbol)
# 国内商品/股指用 XXX0 主连; 外盘新浪日K不支持, 暂不计算
TECH_SYMBOLS = {}   # 由 fetch_quotes.py 注入


# ============================================================
# 数据抓取与缓存
# ============================================================
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CACHE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


def fetch_daily_kline(symbol, retries=2):
    """拉取日K, 返回 [{d,o,h,l,c,v,p(持仓),s(结算)}]"""
    url = KLINE_API.format(symbol=symbol)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            raw = urllib.request.urlopen(req, timeout=20, context=_SSL_CTX).read()
            text = raw.decode('utf-8', 'ignore')
            m = re.search(r'\((\[.*\])\)', text, re.S)
            if not m:
                return []
            data = json.loads(m.group(1))
            bars = []
            for b in data:
                try:
                    bars.append({
                        'd': b.get('d', ''),
                        'o': float(b.get('o') or 0),
                        'h': float(b.get('h') or 0),
                        'l': float(b.get('l') or 0),
                        'c': float(b.get('c') or 0),
                        'v': float(b.get('v') or 0),
                        'p': float(b.get('p') or 0),   # 持仓量
                        's': float(b.get('s') or 0),   # 结算价
                    })
                except (ValueError, TypeError):
                    continue
            return [b for b in bars if b['c'] > 0]
        except Exception:
            if attempt < retries:
                time.sleep(1.5)
    return []


def get_bars(code, symbol, latest_trading_day, cache, allow_stale=False):
    """
    带缓存: 同一交易日已抓过则直接用缓存。

    allow_stale=True 时(日盘进行中, 当日日K尚未生成)允许复用上一交易日的缓存,
    避免盘中每次刷新都全量重抓 66 个品种的日K(约 3 分钟)。
    """
    hit = cache.get(code)
    if hit and hit.get('bars'):
        if hit.get('date') == latest_trading_day:
            return hit['bars'], True
        if allow_stale:
            return hit['bars'], True
    bars = fetch_daily_kline(symbol)
    if bars:
        # 保留 1300 根(约 5 年), 否则 3 年/5 年历史分位会退化成同一窗口
        cache[code] = {'date': bars[-1]['d'], 'bars': bars[-1300:]}
    return bars, False


# ============================================================
# 基础指标(纯 Python)
# ============================================================
def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema_series(vals, n):
    if not vals:
        return []
    k = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None
    ef = ema_series(closes, fast)
    es = ema_series(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema_series(dif, signal)
    hist = [(a - b) * 2 for a, b in zip(dif, dea)]
    # 金叉/死叉: 看最近两根
    sig = '多头' if dif[-1] > dea[-1] else '空头'
    if len(dif) >= 2:
        prev = dif[-2] - dea[-2]
        now = dif[-1] - dea[-1]
        if prev <= 0 < now:
            sig = '金叉'
        elif prev >= 0 > now:
            sig = '死叉'
    return {'dif': round(dif[-1], 3), 'dea': round(dea[-1], 3),
            'hist': round(hist[-1], 3), 'signal': sig}


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0))
        losses.append(max(-ch, 0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return round(100 - 100 / (1 + rs), 2)


def kdj(highs, lows, closes, n=9, m1=3, m2=3):
    if len(closes) < n:
        return None
    k, d = 50.0, 50.0
    for i in range(n - 1, len(closes)):
        hh = max(highs[i - n + 1:i + 1])
        ll = min(lows[i - n + 1:i + 1])
        rsv = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100
        k = (m1 - 1) / m1 * k + 1.0 / m1 * rsv
        d = (m2 - 1) / m2 * d + 1.0 / m2 * k
    j = 3 * k - 2 * d
    if k > 80:
        sig = '超买'
    elif k < 20:
        sig = '超卖'
    else:
        sig = '中性'
    return {'k': round(k, 2), 'd': round(d, 2), 'j': round(j, 2), 'signal': sig}


def boll(closes, n=20, k=2):
    if len(closes) < n:
        return None
    mid = sum(closes[-n:]) / n
    var = sum((c - mid) ** 2 for c in closes[-n:]) / n
    sd = var ** 0.5
    up, low = mid + k * sd, mid - k * sd
    last = closes[-1]
    if last >= up:
        pos = '触及上轨'
    elif last <= low:
        pos = '触及下轨'
    elif last > mid:
        pos = '中轨上方'
    else:
        pos = '中轨下方'
    width = (up - low) / mid * 100 if mid else 0
    return {'mid': round(mid, 2), 'up': round(up, 2), 'low': round(low, 2),
            'width': round(width, 2), 'pos': pos}


def atr(highs, lows, closes, n=14):
    if len(closes) < n + 1:
        return None, None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    a = sum(trs[:n]) / n
    for i in range(n, len(trs)):
        a = (a * (n - 1) + trs[i]) / n
    pct = a / closes[-1] * 100 if closes[-1] else 0
    return round(a, 3), round(pct, 2)


def percentile(vals, value):
    """value 在 vals 序列中的百分位(0~100)"""
    if not vals:
        return None
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return round((below + equal * 0.5) / len(vals) * 100, 1)


# ============================================================
# K线形态识别(最近 1~3 根)
# ============================================================
def detect_pattern(bars):
    if len(bars) < 3:
        return '数据不足'
    c0 = bars[-1]
    p1 = bars[-2]
    p2 = bars[-3]

    body = abs(c0['c'] - c0['o'])
    rng = c0['h'] - c0['l']
    if rng <= 0:
        return '无波动'
    body_r = body / rng

    up_sh = (c0['h'] - max(c0['c'], c0['o'])) / rng
    dn_sh = (min(c0['c'], c0['o']) - c0['l']) / rng

    # 十字星
    if body_r < 0.1:
        return '十字星(多空僵持)'
    # 锤子线 / 上吊线
    if dn_sh > 0.5 and up_sh < 0.15 and body_r < 0.4:
        prev_down = p1['c'] < p1['o']
        return '锤子线(止跌信号)' if prev_down else '上吊线(见顶信号)'
    if up_sh > 0.5 and dn_sh < 0.15 and body_r < 0.4:
        prev_up = p1['c'] > p1['o']
        return '射击之星(见顶信号)' if prev_up else '倒锤子(试探性反弹)'

    # 吞没形态
    if c0['c'] > c0['o'] and p1['c'] < p1['o']:
        if c0['c'] > p1['o'] and c0['o'] < p1['c']:
            return '看涨吞没(反转偏多)'
    if c0['c'] < c0['o'] and p1['c'] > p1['o']:
        if c0['c'] < p1['o'] and c0['o'] > p1['c']:
            return '看跌吞没(反转偏空)'

    # 三连阳 / 三连阴
    if c0['c'] > c0['o'] and p1['c'] > p1['o'] and p2['c'] > p2['o']:
        if c0['c'] > p1['c'] > p2['c']:
            return '三连阳(强势延续)'
    if c0['c'] < c0['o'] and p1['c'] < p1['o'] and p2['c'] < p2['o']:
        if c0['c'] < p1['c'] < p2['c']:
            return '三连阴(弱势延续)'

    # 大阳/大阴
    chg = (c0['c'] - p1['c']) / p1['c'] * 100 if p1['c'] else 0
    if chg >= 2:
        return f'大阳线(+{chg:.1f}%)'
    if chg <= -2:
        return f'大阴线({chg:.1f}%)'
    return '小幅整理'


# ============================================================
# 主计算
# ============================================================
def merge_live_bar(bars, live):
    """
    盘中把实时行情合并成"当日临时K线"(V4.6)。

    live 形如 {'date':'2026-09-10','open':..,'high':..,'low':..,'last':..,'volume':..}
    日K尚未含当日时, 追加一根近似K线, 让 MA/MACD/RSI/KDJ/BOLL/ATR/量比/形态
    全部把"今日盘中"纳入计算 —— 而不是停留在昨日收盘。

    返回 (bars2, merged, note):
      merged=True  表示确实合并了实时数据
    """
    if not live or not bars:
        return bars, False, ''
    # 容错: 有些调用方传的是缓存外层 dict({'date':..,'bars':[..]}) 或非列表
    if isinstance(bars, dict):
        bars = bars.get('bars') or []
    if not isinstance(bars, list) or not bars:
        return bars, False, ''
    if not isinstance(bars[-1], dict):
        return bars, False, ''
    ld = (live.get('date') or '').strip()
    last = live.get('last')
    if not ld or last is None or last <= 0:
        return bars, False, ''
    last_d = bars[-1].get('d', '')
    if last_d == ld:
        # 日K已含当日(收盘后): 用实时价校准收盘价, 保证与行情一致
        b = dict(bars[-1])
        if abs(b.get('c', 0) - last) > 1e-9:
            hi = max(b.get('h', last), last)
            lo = min(b.get('l', last), last) or last
            b['c'], b['h'], b['l'] = last, hi, lo
            return bars[:-1] + [b], True, '校准当日收盘'
        return bars, False, ''
    if last_d > ld:
        return bars, False, ''      # 异常: 日K比实时还新, 不处理
    # 日K停留在上一交易日 -> 追加当日临时K线
    o = live.get('open') or last
    h = live.get('high') or last
    l = live.get('low') or last
    try:
        o = float(o) or last
        h = float(h) or last
        l = float(l) or last
    except Exception:
        o = h = l = last
    h = max(h, last, o)
    l = min(l, last, o)
    v = live.get('volume')
    try:
        v = float(v or 0)
    except Exception:
        v = 0.0
    bar = {'d': ld, 'o': o, 'h': h, 'l': l, 'c': last, 'v': v,
           'oi': live.get('oi'), 'live': True}
    return bars + [bar], True, '含盘中实时'


def compute_tech(bars, last_price=None, live=None):
    """输入日K bars, 输出技术指标 dict

    V4.6: live 传入实时行情时, 先合并成"当日临时K线", 使全部指标盘中实时化。
    """
    bars, merged, merge_note = merge_live_bar(bars, live)
    if not bars or len(bars) < 30:
        return None
    closes = [b['c'] for b in bars]
    highs = [b['h'] for b in bars]
    lows = [b['l'] for b in bars]
    vols = [b['v'] for b in bars]

    # 合并了实时K线后, 收盘价已是盘中最新价; last_price 仅作兜底
    price = last_price if (last_price and last_price > 0) else closes[-1]
    if live and live.get('last') and live['last'] > 0:
        price = live['last']

    ma5, ma10, ma20, ma60 = (sma(closes, n) for n in (5, 10, 20, 60))
    if ma5 and ma10 and ma20 and ma60:
        if ma5 > ma10 > ma20 > ma60:
            ma_arr = '多头排列'
        elif ma5 < ma10 < ma20 < ma60:
            ma_arr = '空头排列'
        else:
            ma_arr = '均线纠缠'
    else:
        ma_arr = '数据不足'

    # 趋势判定: 20日 vs 60日 + 20日斜率
    trend = '震荡'
    if ma20 and ma60:
        slope = (ma20 - sma(closes[:-10], 20)) if len(closes) > 30 else 0
        if ma20 > ma60 and (slope or 0) > 0:
            trend = '上升趋势'
        elif ma20 < ma60 and (slope or 0) < 0:
            trend = '下降趋势'
        elif price > ma20 * 1.02:
            trend = '偏强震荡'
        elif price < ma20 * 0.98:
            trend = '偏弱震荡'

    v5 = sma(vols, 5)
    vol_ratio = round(vols[-1] / v5, 2) if v5 else None

    a14, a14p = atr(highs, lows, closes)
    m = macd(closes)
    r = rsi(closes)
    k = kdj(highs, lows, closes)
    b = boll(closes)

    def pct_rank(n):
        # 历史K线不足 n 根时返回 None, 不用短窗口冒充长周期分位(避免误导)
        if len(closes) < n:
            return None
        return percentile(closes[-n:], price)

    hi250 = max(highs[-250:]) if len(highs) >= 250 else max(highs)
    lo250 = min(lows[-250:]) if len(lows) >= 250 else min(lows)

    pat = detect_pattern(bars)

    # 技术面一句话结论(基于上述指标的组合判断)
    parts = []
    if ma_arr == '多头排列':
        parts.append('均线多头')
    elif ma_arr == '空头排列':
        parts.append('均线空头')
    if m:
        if m['signal'] == '金叉':
            parts.append('MACD金叉')
        elif m['signal'] == '死叉':
            parts.append('MACD死叉')
        elif m['signal'] == '多头':
            parts.append('MACD多头')
        else:
            parts.append('MACD空头')
    if r is not None:
        if r >= 70:
            parts.append(f'RSI{r:.0f}超买')
        elif r <= 30:
            parts.append(f'RSI{r:.0f}超卖')
    if b:
        parts.append(f"布林{b['pos']}")
    p1y = pct_rank(250)
    if p1y is not None:
        parts.append(f'近1年分位{p1y}%')
    summary = '、'.join(parts) if parts else '技术面中性'

    return {
        'date': bars[-1]['d'],
        'close': round(closes[-1], 2),
        'ma5': round(ma5, 2) if ma5 else None,
        'ma10': round(ma10, 2) if ma10 else None,
        'ma20': round(ma20, 2) if ma20 else None,
        'ma60': round(ma60, 2) if ma60 else None,
        'ma_arr': ma_arr,
        'trend': trend,
        'macd': m,
        'rsi14': r,
        'kdj': k,
        'boll': b,
        'atr14': a14,
        'atr_pct': a14p,
        'vol_ratio': vol_ratio,
        'pattern': pat,
        'pct_1y': pct_rank(250),
        'pct_3y': pct_rank(750),
        'pct_5y': pct_rank(1250),
        'high_250': round(hi250, 2),
        'low_250': round(lo250, 2),
        'bars': len(bars),
        'summary': summary,
        '_merged': merged,
        'merge_note': merge_note,
    }


def build_tech_map(symbol_map, latest_trading_day, verbose=True, allow_stale=False,
                   live_map=None):
    """
    symbol_map: {code: {'symbol': 'RB0', 'cn': '螺纹钢'}}
    allow_stale: 日盘进行中复用上一交易日 K 线(见 get_bars)
    live_map: {code: 实时行情dict} —— 传入后, 各指标合并"当日临时K线"盘中实时化(V4.6)
    返回 {code: tech_dict}
    """
    cache = load_cache()
    out = {}
    fetched, cached_n, live_n = 0, 0, 0
    for code, info in symbol_map.items():
        sym = info.get('symbol') or code
        bars, from_cache = get_bars(code, sym, latest_trading_day, cache, allow_stale)
        if not bars:
            continue
        lv = (live_map or {}).get(code)
        t = compute_tech(bars, live=lv)
        if t:
            t['cn'] = info.get('cn', code)
            if lv and t.get('_merged'):
                live_n += 1
                t['live'] = True
            out[code] = t
        if from_cache:
            cached_n += 1
        else:
            fetched += 1
            time.sleep(0.12)      # 轻微限速, 避免被封
    save_cache(cache)
    if verbose:
        tag = ' / 复用昨日K线' if (allow_stale and cached_n) else ''
        tag += f' / 盘中实时{live_n}' if live_n else ''
        print(f'[OK] tech: {len(out)} 个品种 (新抓 {fetched} / 缓存 {cached_n}){tag}')
    return out
