"""
basis.py V1 — 基差(现货 vs 期货)数据模块
============================================================
数据源: 生意社 现期表-主力基差表
  https://www.100ppi.com/sf2/day-YYYY-MM-DD.html   (每日一页, 全品种)
每品种字段: 现货价格 / 主力合约代码+价格 / 基差+基差率 / 180日内基差 最高·最低·平均
  基差 = 现货价格 - 期货主力价格 (生意社口径)

自建历史: data/basis_days.json 按交易日累积缓存
  · 首次运行回补约 240 个交易日(≈1年)
  · 之后每日增量抓 1-2 页
用途: 近一年基差分位 (当前基差在过去一年交易日基差中的百分位)
"""
import urllib.request
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
DAYS_PATH = DATA_DIR / 'basis_days.json'

SF2_URL = 'https://www.100ppi.com/sf2/day-{date}.html'

# 基差率绝对值超过此阈值 → 判定为"现货报价口径与期货交割品级存在差异",
# 绝对基差/基差率不具跨品种可比性, 前端给出提示(历史分位仍可靠, 不作废)。
BASIS_ABS_PCT_WARN = 20

# 生意社名称(规范化后) → 本工具品种代码
#   名称规范化: 取开头连续汉字(菜籽油OI→菜籽油), 纯字母品种(PTA/PX/LPG/PVC)保留原样
NAME2CODE = {
    # 有色/贵金属 (上期所)
    '铜': 'CU0', '铝': 'AL0', '锌': 'ZN0', '铅': 'PB0', '镍': 'NI0', '锡': 'SN0',
    '黄金': 'AU0', '白银': 'AG0',
    # 黑色
    '螺纹钢': 'RB0', '热轧卷板': 'HC0', '不锈钢': 'SS0',
    '焦炭': 'J0', '焦煤': 'JM0', '铁矿石': 'I0', '硅铁': 'SF0', '锰硅': 'SM0',
    # 能源化工
    '燃料油': 'FU0', '石油沥青': 'BU0', '沥青': 'BU0', '天然橡胶': 'RU0', '橡胶': 'RU0',
    '纸浆': 'SP0', 'PTA': 'TA0', '甲醇': 'MA0', '玻璃': 'FG0', '纯碱': 'SA0',
    '尿素': 'UR0', '涤纶短纤': 'PF0', '短纤': 'PF0',
    '聚氯乙烯': 'V0', '聚乙烯': 'L0', '聚丙烯': 'PP0',
    '乙二醇': 'EG0', '苯乙烯': 'EB0', '液化石油气': 'PG0',
    # 农产品
    '豆一': 'A0', '豆粕': 'M0', '豆油': 'Y0', '玉米': 'C0', '棕榈油': 'P0',
    '菜籽油': 'OI0', '菜籽粕': 'RM0', '白糖': 'SR0', '棉花': 'CF0', '棉纱': 'CY0',
    '鸡蛋': 'JD0', '生猪': 'LH0', '油菜籽': 'RS0',
    # 新能源/广期所
    '工业硅': 'SI0', '碳酸锂': 'LC0', '多晶硅': 'PS0',
}

# 代码 → 中文名(覆盖不在行情表里的品种)
CN_MAP = {
    'CU0': '沪铜', 'AL0': '沪铝', 'ZN0': '沪锌', 'PB0': '沪铅', 'NI0': '沪镍',
    'SN0': '沪锡', 'AU0': '黄金', 'AG0': '白银', 'RB0': '螺纹钢', 'HC0': '热卷',
    'SS0': '不锈钢', 'J0': '焦炭', 'JM0': '焦煤', 'I0': '铁矿石', 'SF0': '硅铁',
    'SM0': '锰硅', 'FU0': '燃油', 'BU0': '沥青', 'RU0': '橡胶', 'SP0': '纸浆',
    'TA0': 'PTA', 'MA0': '甲醇', 'FG0': '玻璃', 'SA0': '纯碱', 'UR0': '尿素',
    'PF0': '短纤', 'V0': 'PVC', 'L0': '塑料', 'PP0': '聚丙烯', 'EG0': '乙二醇',
    'EB0': '苯乙烯', 'PG0': 'LPG', 'A0': '豆一', 'M0': '豆粕', 'Y0': '豆油',
    'C0': '玉米', 'P0': '棕榈油', 'OI0': '菜油', 'RM0': '菜粕', 'SR0': '白糖',
    'CF0': '棉花', 'CY0': '棉纱', 'JD0': '鸡蛋', 'LH0': '生猪', 'RS0': '菜籽',
    'CS0': '玉米淀粉', 'SI0': '工业硅', 'LC0': '碳酸锂', 'PS0': '多晶硅',
}

_TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
_TAG = re.compile(r'<[^>]+>')


def _clean(s):
    return _TAG.sub('', s).replace('&nbsp;', '').strip()


def _f(s):
    """宽松转 float: 失败返回 None"""
    try:
        return float(_clean(s))
    except (ValueError, TypeError):
        return None


def http_get(url, timeout=12, retries=1):
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120',
                'Referer': 'https://www.100ppi.com/sf2/',
            })
            import gzip as _gz
            d = urllib.request.urlopen(req, timeout=timeout).read()
            if d[:2] == b'\x1f\x8b':
                d = _gz.decompress(d)
            return d.decode('utf-8', errors='replace')
        except urllib.error.HTTPError:
            return None          # 404 = 非交易日/未发布, 不重试
        except Exception:
            if i < retries:
                time.sleep(1)
    return None


def normalize_name(raw):
    m = re.match(r'^[\u4e00-\u9fa5]+', raw.strip())
    if m:
        return m.group(0)
    return raw.strip().upper()      # PTA / PX / LPG / PVC


_ROW = re.compile(
    r'<a href="[^"]*/sf/\d+\.html"[^>]*>\s*([^<]+?)\s*</a>\s*</td>\s*'
    r'<td[^>]*>([^<]*)</td>\s*'                       # 现货
    r'<td[^>]*>([^<]*)</td>\s*'                       # 主力合约代码
    r'<td[^>]*>([^<]*)</td>\s*'                       # 期货价
    r'<td[^>]*>\s*<table.*?<font[^>]*>([^<]*)</font>.*?<font[^>]*>([^<]*)</font>'  # 基差+基差率
    r'.*?</table>\s*</td>\s*'
    r'<td[^>]*>([^<]*)</td>\s*'                       # 180日最高
    r'<td[^>]*>([^<]*)</td>\s*'                       # 180日最低
    r'<td[^>]*>([^<]*)</td>', re.S)                   # 180日平均


def parse_day(date_str):
    """抓某日全品种基差表。返回 {规范名: row} 或 None(无数据)
    row: {spot, contract, fut, basis, pct, hi180, lo180, avg180, name_raw}
    """
    t = http_get(SF2_URL.format(date=date_str))
    if not t or '/sf/' not in t or '基差' not in t:
        return None
    out = {}
    for m in _ROW.finditer(t):
        name_raw, spot_s, contract, fut_s, basis_s, pct_s, hi_s, lo_s, avg_s = m.groups()
        spot, fut, basis = _f(spot_s), _f(fut_s), _f(basis_s)
        pct = None
        pm = re.match(r'^(-?[\d.]+)%$', _clean(pct_s))
        if pm:
            pct = float(pm.group(1))
        if spot is None or fut is None or basis is None:
            continue
        key = normalize_name(name_raw)
        out[key] = {
            'name_raw': name_raw.strip(), 'spot': spot, 'contract': _clean(contract),
            'fut': fut, 'basis': basis, 'pct': pct,
            'hi180': _f(hi_s), 'lo180': _f(lo_s), 'avg180': _f(avg_s),
        }
    return out if len(out) >= 10 else None


def load_days():
    try:
        with open(DAYS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_days(days):
    try:
        with open(DAYS_PATH, 'w', encoding='utf-8') as f:
            json.dump(days, f, ensure_ascii=False)
    except Exception as e:
        print(f'[WARN] 写基差历史失败: {e}', file=sys.stderr)


def backfill(days_cache, target_days=240, max_scan=430):
    """向前回补历史(一次性)。返回新增天数"""
    added, miss_streak = 0, 0
    d = datetime.now().date()
    if days_cache:
        try:
            d = max(datetime.strptime(k, '%Y-%m-%d').date() for k in days_cache)
        except ValueError:
            pass
    scanned = 0
    while len(days_cache) < target_days and scanned < max_scan and miss_streak < 30:
        ds = d.isoformat()
        if ds not in days_cache:
            scanned += 1
            row = parse_day(ds)
            if row:
                days_cache[ds] = row
                added += 1
                miss_streak = 0
                if added % 40 == 0:
                    print(f'[basis] 回补中: 已 {len(days_cache)} 个交易日 ({ds})')
            else:
                miss_streak += 1
        d -= timedelta(days=1)
    return added


def incremental(days_cache, max_fetch=8):
    """补齐缓存中最新日期之后到今天的缺口。返回新增天数"""
    try:
        last = max(datetime.strptime(k, '%Y-%m-%d').date() for k in days_cache)
    except ValueError:
        return backfill(days_cache)
    added, d = 0, last + timedelta(days=1)
    today = datetime.now().date()
    while d <= today and added < max_fetch:
        ds = d.isoformat()
        if ds not in days_cache:
            row = parse_day(ds)
            if row:
                days_cache[ds] = row
                added += 1
        d += timedelta(days=1)
    return added


def percentile_rank(values, x):
    """x 在 values 中的百分位 (0-100)。"""
    if not values:
        return None
    below = sum(1 for v in values if v <= x)
    return round(below / len(values) * 100, 1)


def build_basis_data(trading_day=''):
    """主入口: 返回 result['basis'] 结构
    {
      'updated': ..., 'source': ..., 'hist_days': N,
      'items': { code: {'cn','spot','contract','fut','basis','pct',
                        'hi180','lo180','avg180','pos180','prc_hist','days','date'} }
    }
    """
    t0 = time.time()
    days = load_days()
    first_run = len(days) == 0
    if first_run:
        n = backfill(days)
        print(f'[OK] basis 回补 {n} 个交易日历史')
    else:
        n = incremental(days)
        if n:
            print(f'[OK] basis 增量 {n} 个交易日')

    # 当日快照: 优先 trading_day, 否则用缓存里最新一天
    if trading_day and trading_day in days:
        cur_date = trading_day
    else:
        cur_date = max(days.keys()) if days else ''
    cur = days.get(cur_date, {})

    items = {}
    for key, row in cur.items():
        code = NAME2CODE.get(key)
        if not code:
            continue
        # 历史序列(仅本品种, 升序)
        hist = []
        for ds in sorted(days.keys()):
            v = days[ds].get(key, {}).get('basis')
            if v is not None:
                hist.append((ds, v))
        values = [v for _, v in hist]
        prc = percentile_rank(values, row['basis']) if len(values) >= 20 else None

        pos180 = None
        if row['hi180'] is not None and row['lo180'] is not None \
                and row['hi180'] > row['lo180']:
            pos180 = round((row['basis'] - row['lo180']) /
                           (row['hi180'] - row['lo180']) * 100, 0)

        # 现货报价口径与期货交割品级可能存在差异:
        # 表现为基差率绝对值异常大(如燃油41.9%/焦煤29%/鸡蛋27.3%)。
        # 该偏移经实测为系统性稳定(燃油近240日基差CV仅14.7%),
        # 故基差"历史分位"仍可靠, 但绝对基差/基差率不具跨品种可比性 → 前端提示。
        pct_abs = abs(row['pct']) if row['pct'] is not None else 0
        unit_warn = pct_abs > BASIS_ABS_PCT_WARN

        items[code] = {
            'cn': CN_MAP.get(code, key),
            'spot': row['spot'], 'contract': row['contract'], 'fut': row['fut'],
            'basis': row['basis'], 'pct': row['pct'],
            'unit_warn': unit_warn,
            'hi180': row['hi180'], 'lo180': row['lo180'], 'avg180': row['avg180'],
            'pos180': pos180, 'prc_hist': prc, 'days': len(values),
            'date': cur_date,
            # 近60点迷你序列(详情用)
            'hist_tail': [[ds, v] for ds, v in hist[-60:]],
        }

    if n:
        save_days(days)

    out = {
        'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'date': cur_date,
        'source': '生意社现期表(100ppi.com) 主力基差',
        'hist_days': len(days),
        'items': items,
    }
    print(f'[OK] basis: {len(items)} 品种, 历史序列 {len(days)} 个交易日, '
          f'基准日 {cur_date}, 耗时 {time.time()-t0:.1f}s')
    return out


if __name__ == '__main__':
    r = build_basis_data()
    demo = ['RB0', 'CU0', 'M0', 'I0']
    for c in demo:
        it = r['items'].get(c)
        if it:
            print(c, it['contract'], '现货', it['spot'], '期货', it['fut'],
                  '基差', it['basis'], f"({it['pct']}%)",
                  '180日位', it['pos180'], '近1年分位', it['prc_hist'],
                  f"({it['days']}天)")
