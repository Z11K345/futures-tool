# -*- coding: utf-8 -*-
"""
主力合约解析
------------
新浪 `nf_RB0` 这类「连续合约」只给拼接后的价格,不告诉你当前主力到底是哪个月份。
本模块通过枚举各月份合约、按持仓量(并过滤无成交的虚挂合约)判定真正的主力合约,
并返回主力合约的真实行情(价格/涨跌/持仓/成交),供行情表标注具体合约(如 RB2701)。

判定规则:
  1. 枚举品种当前月起未来 13 个月的所有合约,过滤掉无数据的;
  2. 计算该品种所有合约的成交量最大值 max_vol;
  3. 剔除成交量 < max_vol * 2% 的合约(几乎无成交,不是主力);
  4. 剩余合约中取持仓量最大者为主力;若全部无成交,退化为持仓量最大者。

结果缓存 60 分钟(主力换月不频繁,避免每次刷新都枚举上千个代码)。
"""
import os
import ssl
import json
import time
import urllib.request
from datetime import datetime

try:
    from fetch_quotes import COMMODITY_CODES, INDEX_CODES, DATA_DIR
except ImportError:  # 允许独立测试
    DATA_DIR = 'data'
    COMMODITY_CODES = {}
    INDEX_CODES = {}

SINA_QUOTES_URL = 'https://hq.sinajs.cn/list={codes}'
HEADERS = {'Referer': 'https://finance.sina.com.cn',
           'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

CACHE_FILE = os.path.join(DATA_DIR, 'main_contracts.json')
CACHE_TTL = 3600          # 缓存有效期(秒)
LOOKAHEAD_MONTHS = 13     # 向前枚举月份数
BATCH = 100               # 单次批量查询合约数


def _http_get(codes):
    """批量查询新浪行情,返回 {code: parts(list)}"""
    url = SINA_QUOTES_URL.format(codes=','.join('nf_' + c for c in codes))
    req = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=20, context=SSL_CTX).read().decode('gbk')
    out = {}
    for line in raw.strip().split('\n'):
        if '="' not in line or '=""' in line:
            continue
        key = line.split('=')[0].replace('var hq_str_nf_', '').strip()
        try:
            parts = line.split('"')[1].split(',')
        except IndexError:
            continue
        out[key] = parts
    return out


def _month_candidates():
    """当前月起向后 LOOKAHEAD_MONTHS 个月的 (yy, mm) 列表"""
    now = datetime.now()
    res = []
    for i in range(LOOKAHEAD_MONTHS):
        idx = now.month - 1 + i
        y = now.year + idx // 12
        m = idx % 12 + 1
        res.append((y % 100, m))
    return res


def _variety_prefixes():
    """返回 [(code0, prefix, cn_name, kind)],kind ∈ {'cmd','idx'}"""
    out = []
    for _cat, lst in COMMODITY_CODES.items():
        for code, cn, _name in lst:
            out.append((code, code.rstrip('0'), cn, 'cmd'))
    for _cat, lst in INDEX_CODES.items():
        for code, cn, _name in lst:
            out.append((code, code.rstrip('0'), cn, 'idx'))
    return out


def _extract(parts, kind):
    """从新浪字段里取 (last, oi, vol, prev_settle)"""
    try:
        if kind == 'idx':
            # 股指/国债: 6=持仓 4=成交 3=最新价 13=昨收(与 fetch_quotes.parse_index 口径一致)
            oi = float(parts[6] or 0)
            vol = float(parts[4] or 0)
            last = float(parts[3] or 0)
            prev = float(parts[13] or 0) if len(parts) > 13 else 0
        else:
            # 商品: 8=最新价 13=持仓 14=成交 10=昨结
            last = float(parts[8] or 0)
            if not last:
                last = float(parts[7] or 0)      # 兜底(换月期最新价为0)
            oi = float(parts[13] or 0)
            vol = float(parts[14] or 0)
            prev = float(parts[10] or 0)
    except (ValueError, IndexError, TypeError):
        return None
    if last <= 0:
        return None
    return last, oi, vol, prev


def resolve_main_contracts(force=False, verbose=True):
    """
    返回 {'RB0': {'contract': 'RB2701', 'month': '2701', 'kind': 'cmd',
                  'last': '3173.000', 'oi': '1502783', 'vol': '277062',
                  'pct': '0.28', 'change': '9.000', 'prev_settle': '3164.000'}, ...}
    """
    # ---- 缓存 ----
    if not force:
        try:
            if os.path.exists(CACHE_FILE):
                c = json.load(open(CACHE_FILE, encoding='utf-8'))
                if time.time() - c.get('_ts', 0) < CACHE_TTL and c.get('map'):
                    return c['map']
        except Exception:
            pass

    varieties = _variety_prefixes()
    months = _month_candidates()

    # ---- 1) 构造全部候选代码并批量查询 ----
    cand = {}          # code0 -> [contract_code, ...]
    all_codes = []
    for code0, prefix, cn, kind in varieties:
        lst = ['%s%02d%02d' % (prefix, yy, mm) for yy, mm in months]
        cand[code0] = (lst, kind)
        all_codes.extend(lst)

    raw = {}
    for i in range(0, len(all_codes), BATCH):
        try:
            raw.update(_http_get(all_codes[i:i + BATCH]))
        except Exception as e:
            if verbose:
                print('[WARN] main contract batch %d fail: %s' % (i // BATCH, e))

    # ---- 2) 按品种判定主力 ----
    result = {}
    for code0, (lst, kind) in cand.items():
        rows = []
        for c in lst:
            parts = raw.get(c)
            if not parts:
                continue
            v = _extract(parts, kind)
            if not v:
                continue
            rows.append((c, v[0], v[1], v[2], v[3]))   # code,last,oi,vol,prev
        if not rows:
            continue
        max_vol = max(r[3] for r in rows)
        active = [r for r in rows if r[3] >= max_vol * 0.02] if max_vol > 0 else rows
        pool = active or rows
        pool.sort(key=lambda r: -r[2])                  # 按持仓量降序
        top = pool[0]
        code, last, oi, vol, prev = top
        change = last - prev if prev else 0
        pct = (change / prev * 100) if prev else 0
        result[code0] = {
            'contract': code,
            'month': code[len(code0) - 1:],             # 如 '2701'
            'kind': kind,
            'last': '%.3f' % last,
            'oi': '%.0f' % oi,
            'vol': '%.0f' % vol,
            'prev_settle': '%.3f' % prev,
            'change': '%.3f' % change,
            'pct': '%.2f' % pct,
        }

    # ---- 3) 写缓存 ----
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({'_ts': time.time(),
                   '_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                   'map': result},
                  open(CACHE_FILE, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception as e:
        if verbose:
            print('[WARN] main contract cache write fail: %s' % e)

    if verbose:
        print('[OK] main_contracts: %d/%d 品种解析出主力合约' % (len(result), len(varieties)))
    return result


if __name__ == '__main__':
    m = resolve_main_contracts(force=True)
    for k in ['RB0', 'CU0', 'AU0', 'IF0', 'T0', 'SC0', 'LC0']:
        v = m.get(k)
        if v:
            print('%-5s -> %-8s  最新 %10s  涨跌 %6s%%  持仓 %10s  成交 %8s'
                  % (k, v['contract'], v['last'], v['pct'], v['oi'], v['vol']))
        else:
            print('%-5s -> 未解析到主力合约' % k)
