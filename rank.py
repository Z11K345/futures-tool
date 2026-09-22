# -*- coding: utf-8 -*-
"""
持仓龙虎榜(会员成交持仓排名) —— 各交易所官方公开数据抓取。

数据源(均为交易所官网公开文件, 无需鉴权):
  1) 上期所 SHFE    https://www.shfe.com.cn/data/tradedata/future/dailydata/pmYYYYMMDD.dat  (JSON)
  2) 能源中心 INE   https://www.ine.cn/data/tradedata/future/dailydata/pmYYYYMMDD.dat       (JSON)
  3) 郑商所 CZCE    http://www.czce.com.cn/cn/DFSStaticFiles/Future/YYYY/YYYYMMDD/FutureDataHolding.txt
  4) 中金所 CFFEX   http://www.cffex.com.cn/sj/ccpm/YYYYMM/DD/{IF,IC,IH,IM,TS,TF,T,TL}.xml
  5) 广期所 GFEX    POST http://www.gfex.com.cn/u/interfacesWebTiMemberDealPosiQuotes/loadList
  6) 大商所 DCE     官网对本机出口返回 412(WAF), 无法直接爬; 改用东方财富 datacenter
                  (RPT_FUTU_DAILYPOSITION, 市场码 069001007) 接入, 无需鉴权、覆盖全市场。
                  见 fetch_eastmoney_dce()。

口径说明:
  - 交易所公布的是"会员"维度排名, 分三个榜单: 成交量 / 持买仓量 / 持卖仓量, 各榜前 20 名。
  - SHFE/INE/CFFEX/GFEX 为"合约级"数据, 这里统一取**主力合约**(与工具其它模块同一口径, 由 main_contract 判定)。
  - CZCE 公布的是"品种级"(该品种所有合约合计), 已在 level 字段标注。
  - 净持仓 = 前20持买仓量合计 − 前20持卖仓量合计, 正值表示主力席位偏多。

输出 data/rank.json, 由 fetch_quotes.py 并入 quotes.json 的 'rank' 字段。
"""
import json
import os
import re
import urllib.parse
import urllib.request
import gzip
import ssl
from datetime import date, timedelta

ssl._create_default_https_context = ssl._create_unverified_context

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, 'data')
CACHE_FILE = os.path.join(DATA_DIR, 'rank.json')
TTL = 3 * 3600          # 3 小时缓存(收盘后数据不再变化)

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


def http_get(url, referer=None, data=None, timeout=25):
    """返回文本; 失败返回 None。"""
    h = {'User-Agent': UA, 'Accept': '*/*', 'Accept-Language': 'zh-CN,zh;q=0.9',
         'Accept-Encoding': 'gzip, deflate'}
    if referer:
        h['Referer'] = referer
    if data is not None:
        h['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
        data = urllib.parse.urlencode(data).encode()
    try:
        req = urllib.request.Request(url, headers=h, data=data)
        raw = urllib.request.urlopen(req, timeout=timeout).read()
        if raw[:2] == b'\x1f\x8b':
            raw = gzip.decompress(raw)
        return raw.decode('utf-8', errors='replace')
    except Exception:
        return None


def _int(s):
    if s is None:
        return 0
    s = str(s).strip().replace(',', '').replace('+', '')
    if not s or s in ('-', '--'):
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0


def _cn_of(code):
    """工具 code(如 RB0) 的中文名兜底。"""
    return CODE_CN.get(code, code.replace('0', ''))


# ---------------------------------------------------------------- 中文名兜底表
CODE_CN = {
    'RB0': '螺纹钢', 'HC0': '热卷', 'CU0': '沪铜', 'AL0': '沪铝', 'ZN0': '沪锌',
    'PB0': '沪铅', 'NI0': '沪镍', 'SN0': '沪锡', 'AO0': '氧化铝', 'AD0': '铸造铝合金',
    'AU0': '黄金', 'AG0': '白银', 'SS0': '不锈钢', 'SC0': '原油', 'FU0': '燃料油',
    'LU0': '低硫燃料油', 'BU0': '沥青', 'RU0': '天然橡胶', 'BR0': '合成橡胶',
    'NR0': '20号胶', 'SP0': '纸浆', 'OP0': '胶版印刷纸', 'BC0': '国际铜',
    'WR0': '线材', 'EC0': '集运指数',
    'AP0': '苹果', 'CF0': '棉花', 'CJ0': '红枣', 'CY0': '棉纱', 'FG0': '玻璃',
    'MA0': '甲醇', 'OI0': '菜油', 'PF0': '短纤', 'PK0': '花生', 'PM0': '普麦',
    'PX0': '对二甲苯', 'RM0': '菜粕', 'RS0': '菜籽', 'SA0': '纯碱', 'SF0': '硅铁',
    'SH0': '烧碱', 'SM0': '锰硅', 'SR0': '白糖', 'TA0': 'PTA', 'UR0': '尿素',
    'WH0': '强麦', 'ZC0': '动力煤', 'PR0': '瓶片', 'PL0': '丙烯',
    'SI0': '工业硅', 'LC0': '碳酸锂', 'PS0': '多晶硅', 'PT0': '铂', 'PD0': '钯',
    'IF0': '沪深300股指', 'IH0': '上证50股指', 'IC0': '中证500股指',
    'IM0': '中证1000股指', 'T0': '10年国债', 'TF0': '5年国债',
    'TS0': '2年国债', 'TL0': '30年国债',
    # ---- 大商所 DCE(经东方财富数据源接入) ----
    'A0': '豆一', 'B0': '豆二', 'C0': '玉米', 'CS0': '玉米淀粉', 'M0': '豆粕',
    'Y0': '豆油', 'P0': '棕榈油', 'JD0': '鸡蛋', 'LH0': '生猪', 'L0': '塑料',
    'V0': 'PVC', 'PP0': '聚丙烯', 'J0': '焦炭', 'JM0': '焦煤', 'I0': '铁矿石',
    'EG0': '乙二醇', 'EB0': '苯乙烯', 'RR0': '粳米', 'PG0': '液化石油气',
    'FB0': '纤维板', 'BB0': '胶合板',
}
DCE_CN = {k: v for k, v in CODE_CN.items() if k in (
    'A0', 'B0', 'C0', 'CS0', 'M0', 'Y0', 'P0', 'JD0', 'LH0', 'L0', 'V0',
    'PP0', 'J0', 'JM0', 'I0', 'EG0', 'EB0', 'RR0', 'PG0', 'FB0', 'BB0')}
# 东方财富市场码 -> 本工具交易所标签
EM_MK = {'069001005': 'SHFE', '069001007': 'DCE', '069001008': 'CZCE',
         '069001009': 'CFFEX', '069001016': 'INE', '069001021': 'GFEX'}

# ---------------------------------------------------------------- 交易日
def recent_trade_days(n=8):
    d = date.today()
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


# ---------------------------------------------------------------- 上期所 / 能源中心
def fetch_shfe_ine(day):
    """SHFE + INE 合约级排名。返回 {code: {contract: {...}}}"""
    ymd = day.strftime('%Y%m%d')
    out = {}
    sources = {}
    for ex, host in (('SHFE', 'www.shfe.com.cn'), ('INE', 'www.ine.cn')):
        url = f'https://{host}/data/tradedata/future/dailydata/pm{ymd}.dat'
        txt = http_get(url, referer=f'https://{host}/')
        if not txt:
            sources[ex] = 'fail'
            continue
        try:
            cur = json.loads(txt).get('o_cursor') or []
        except Exception:
            sources[ex] = 'parse'
            continue
        if not cur:
            sources[ex] = 'empty'
            continue
        sources[ex] = 'ok'
        # 按合约聚合成 {instrument: ranks}
        byc = {}
        for x in cur:
            iid = (x.get('INSTRUMENTID') or '').lower()
            if not iid or iid.endswith('all'):
                continue
            byc.setdefault(iid, []).append(x)
        for iid, rows in byc.items():
            prefix = re.sub(r'\d', '', iid)
            code = prefix.upper() + '0'
            item = _shfe_item(rows, code, iid, ex)
            if item:
                out.setdefault(code, {})[iid] = item
    return out, sources


def _shfe_item(rows, code, iid, ex):
    """rows = 同一合约的所有行(RANK 1..20 会员 + 999 合计)"""
    vol, buy, sell = [], [], []
    vsum = bsum = ssum = 0
    vchg = bchg = schg = 0
    cn = ''
    for x in rows:
        cn = cn or (x.get('PRODUCTNAME') or '')
        r = x.get('RANK')
        if r == 999:                      # 合计行
            vsum = _int(x.get('CJ1'))
            bsum = _int(x.get('CJ2'))
            ssum = _int(x.get('CJ3'))
            vchg = _int(x.get('CJ1_CHG'))
            bchg = _int(x.get('CJ2_CHG'))
            schg = _int(x.get('CJ3_CHG'))
            continue
        if not isinstance(r, int) or r < 1 or r > 20:
            continue
        vol.append({'r': r, 'm': (x.get('PARTICIPANTABBR1') or '').strip(),
                    'q': _int(x.get('CJ1')), 'c': _int(x.get('CJ1_CHG'))})
        buy.append({'r': r, 'm': (x.get('PARTICIPANTABBR2') or '').strip(),
                    'q': _int(x.get('CJ2')), 'c': _int(x.get('CJ2_CHG'))})
        sell.append({'r': r, 'm': (x.get('PARTICIPANTABBR3') or '').strip(),
                     'q': _int(x.get('CJ3')), 'c': _int(x.get('CJ3_CHG'))})
    if not vol and not buy:
        return None
    vol.sort(key=lambda a: a['r']); buy.sort(key=lambda a: a['r']); sell.sort(key=lambda a: a['r'])
    return {'code': code, 'cn': cn or _cn_of(code), 'contract': iid, 'ex': ex,
            'level': '合约', 'vol': vol[:20], 'buy': buy[:20], 'sell': sell[:20],
            'vsum': vsum, 'bsum': bsum, 'ssum': ssum,
            'vchg': vchg, 'bchg': bchg, 'schg': schg}


# ---------------------------------------------------------------- 郑商所
def fetch_czce(day):
    ymd = day.strftime('%Y%m%d')
    url = (f'http://www.czce.com.cn/cn/DFSStaticFiles/Future/{day.year}/'
           f'{day.strftime("%Y%m%d")}/FutureDataHolding.txt')
    txt = http_get(url, referer='http://www.czce.com.cn/')
    if not txt or '持仓排名' not in txt:
        return {}, ('fail' if txt is None else 'empty')
    out = {}
    cur = None
    for line in txt.split('\n'):
        s = line.strip()
        if s.startswith('品种：') or s.startswith('品种:'):
            parts = re.split(r'[：:]', s)
            head = parts[1] if len(parts) > 1 else s
            head = re.sub(r'\s*日期.*$', '', head).strip()
            mm = re.search(r'([A-Za-z]{1,2})\s*$', head)
            code = (mm.group(1).upper() + '0') if mm else ''
            cn = re.sub(r'[A-Za-z]{1,2}\s*$', '', re.sub(r'\s*日期.*$', '', head).strip()).strip()
            cur = {'code': code, 'cn': cn, 'ex': 'CZCE', 'level': '品种',
                   'contract': (mm.group(1).upper() if mm else '') + '(全合约)',
                   'vol': [], 'buy': [], 'sell': [],
                   'vsum': 0, 'bsum': 0, 'ssum': 0, 'vchg': 0, 'bchg': 0, 'schg': 0}
            if code:
                out[code] = cur
            continue
        if not cur or '|' not in s:
            continue
        cells = [c.strip() for c in s.split('|')]
        if len(cells) < 10:
            continue
        if cells[0].startswith('合计'):
            cur['vsum'] = _int(cells[2]); cur['bsum'] = _int(cells[5]); cur['ssum'] = _int(cells[8])
            cur['vchg'] = _int(cells[3]); cur['bchg'] = _int(cells[6]); cur['schg'] = _int(cells[9])
            continue
        if not cells[0].isdigit():
            continue
        r = int(cells[0])
        if r > 20:
            continue
        def nm(x):
            return re.sub(r'（.*?）', '', x).strip()
        cur['vol'].append({'r': r, 'm': nm(cells[1]), 'q': _int(cells[2]), 'c': _int(cells[3])})
        cur['buy'].append({'r': r, 'm': nm(cells[4]), 'q': _int(cells[5]), 'c': _int(cells[6])})
        cur['sell'].append({'r': r, 'm': nm(cells[7]), 'q': _int(cells[8]), 'c': _int(cells[9])})
    out = {k: v for k, v in out.items() if v['buy'] or v['vol']}
    return out, 'ok' if out else 'empty'


# ---------------------------------------------------------------- 中金所
CFFEX_PRODUCTS = ['IF', 'IH', 'IC', 'IM', 'TS', 'TF', 'T', 'TL']


def fetch_cffex(day):
    ym = day.strftime('%Y%m')
    dd = day.strftime('%d')
    out = {}
    ok = 0
    for p in CFFEX_PRODUCTS:
        url = f'http://www.cffex.com.cn/sj/ccpm/{ym}/{dd}/{p}.xml'
        txt = http_get(url, referer='http://www.cffex.com.cn/')
        if not txt or '<positionRank' not in txt:
            continue
        blocks = re.findall(r'<data\b.*?</data>', txt, re.S)
        if not blocks:
            continue
        ok += 1
        byc = {}
        for b in blocks:
            iid = _xml(b, 'instrumentid') or ''
            dt = _xml(b, 'datatypeid') or '1'
            try:
                r = int(_xml(b, 'rank') or 0)
            except Exception:
                r = 0
            if not iid or r < 1 or r > 20:
                continue
            rec = byc.setdefault(iid, {'vol': [], 'buy': [], 'sell': []})
            # 名称形如 "国泰君安(代客)", 半角/全角括号都要去掉
            nm = re.sub(r'[（(].*?[)）]', '', _xml(b, 'shortname') or '').strip()
            e = {'r': r, 'm': nm, 'q': _int(_xml(b, 'volume')), 'c': _int(_xml(b, 'varvolume'))}
            # 中金所 datatypeid: 0=成交量  1=持买仓量  2=持卖仓量
            if dt == '0':
                rec['vol'].append(e)
            elif dt == '1':
                rec['buy'].append(e)
            elif dt == '2':
                rec['sell'].append(e)
        for iid, rec in byc.items():
            if not rec['buy'] and not rec['vol']:
                continue
            code = re.sub(r'\d', '', iid).upper() + '0'
            for k in ('vol', 'buy', 'sell'):
                rec[k].sort(key=lambda a: a['r'])
            item = {'code': code, 'cn': _cn_of(code), 'contract': iid, 'ex': 'CFFEX',
                    'level': '合约', 'vol': rec['vol'], 'buy': rec['buy'], 'sell': rec['sell'],
                    'vsum': sum(a['q'] for a in rec['vol']),
                    'bsum': sum(a['q'] for a in rec['buy']),
                    'ssum': sum(a['q'] for a in rec['sell']),
                    'vchg': sum(a['c'] for a in rec['vol']),
                    'bchg': sum(a['c'] for a in rec['buy']),
                    'schg': sum(a['c'] for a in rec['sell'])}
            out.setdefault(code, {})[iid.lower()] = item
    return out, ('ok' if ok else 'empty')


def _xml(seg, tag):
    m = re.search(rf'<{tag}>(.*?)</{tag}>', seg, re.S)
    return m.group(1).strip() if m else ''


# ---------------------------------------------------------------- 广期所
GFEX_VARIETIES = {'si': 'SI0', 'lc': 'LC0', 'ps': 'PS0', 'pt': 'PT0', 'pd': 'PD0'}


def fetch_gfex(day, main_map):
    """广期所: 需要 品种+合约, 这里逐品种用主力合约月份去取。"""
    ymd = day.strftime('%Y%m%d')
    url = 'http://www.gfex.com.cn/u/interfacesWebTiMemberDealPosiQuotes/loadList'
    ref = 'http://www.gfex.com.cn/gfex/rcjccpm/hqsj_tjsj.shtml'
    out = {}
    ok = 0
    for v, code in GFEX_VARIETIES.items():
        info = (main_map or {}).get(code) or {}
        contract = (info.get('contract') or '').lower()
        if not contract:
            continue
        rec = {'vol': [], 'buy': [], 'sell': []}
        for dt, key in ((1, 'vol'), (2, 'buy'), (3, 'sell')):
            txt = http_get(url, referer=ref, data={
                'trade_date': ymd, 'trade_type': '0', 'variety': v,
                'contract_id': contract, 'data_type': str(dt)})
            if not txt:
                continue
            try:
                j = json.loads(txt)
            except Exception:
                continue
            if j.get('code') != '0' or not j.get('data'):
                continue
            for i, row in enumerate(j['data'][:20], 1):
                rec[key].append({'r': i, 'm': (row.get('abbr') or '').strip(),
                                 'q': _int(row.get('todayQty')), 'c': _int(row.get('qtySub'))})
        if not rec['buy'] and not rec['vol']:
            continue
        ok += 1
        out.setdefault(code, {})[contract] = {
            'code': code, 'cn': _cn_of(code), 'contract': contract, 'ex': 'GFEX',
            'level': '合约', 'vol': rec['vol'], 'buy': rec['buy'], 'sell': rec['sell'],
            'vsum': sum(a['q'] for a in rec['vol']),
            'bsum': sum(a['q'] for a in rec['buy']),
            'ssum': sum(a['q'] for a in rec['sell']),
            'vchg': sum(a['c'] for a in rec['vol']),
            'bchg': sum(a['c'] for a in rec['buy']),
            'schg': sum(a['c'] for a in rec['sell'])}
    return out, ('ok' if ok else 'empty')


# ---------------------------------------------------------------- 大商所(可达性探测)
def probe_dce():
    """大商所官网对本机出口返回 412(WAF)。这里只探测可达性, 可达则由调用方决定后续。"""
    try:
        import urllib.error
        req = urllib.request.Request(
            'http://www.dce.com.cn/publicweb/quotesdata/memberDealPosiQuotes.html',
            headers={'User-Agent': UA, 'Accept': '*/*'})
        try:
            urllib.request.urlopen(req, timeout=12).read(200)
            return 'ok'
        except urllib.error.HTTPError as e:
            return f'blocked({e.code})'
    except Exception as e:
        return f'blocked({type(e).__name__})'


# ---------------------------------------------------------------- 大商所(东方财富 datacenter)
# 大商所官网对本机出口返回 412(WAF) 无法直爬; 但东方财富 datacenter 的
# RPT_FUTU_DAILYPOSITION 覆盖全部 6 家交易所(含 DCE), 无需鉴权、无 WAF 拦截。
# 该接口同时为后续统一替代 5 个官网爬虫提供可能(当前仅先接 DCE, 降低改动面)。
EM_BASE = 'https://datacenter.eastmoney.com/api/data/v1/get'
EM_REPORT = 'RPT_FUTU_DAILYPOSITION'
EM_MAINREPORT = 'RPT_FUTU_POSITIONCODE'
MK_DCE = '069001007'


def _em_query(report, flt, size=200, pg=1):
    """东方财富 datacenter 查询; 返回 data 列表或 None。"""
    f = urllib.parse.quote(flt)
    url = (f"{EM_BASE}?reportName={report}&columns=ALL&filter={f}"
           f"&pageNumber={pg}&pageSize={size}&source=WEB&client=WEB"
           f"&sortColumns=SECURITY_CODE&sortTypes=1")
    txt = http_get(url, referer='https://data.eastmoney.com/')
    if not txt:
        return None
    try:
        j = json.loads(txt)
        return j.get('result', {}).get('data') or []
    except Exception:
        return None


def _em_item(rows, code, cn, contract):
    """把单个合约的 DAILYPOSITION 行解析成本工具 item 结构。
    TYPE='0' 为会员行(成交量/多头/空头各占一行, 只填对应指标);
    MEM='本日合计'(带 VOLUME) 为总量行; MEM='总量增减' 为变化行。"""
    vol, buy, sell = [], [], []
    vsum = bsum = ssum = vchg = bchg = schg = 0
    for x in rows:
        mem = (x.get('MEMBER_NAME_ABBR') or '').strip()
        if x.get('TYPE') != '0':
            if mem == '本日合计' and x.get('VOLUME') not in (None, '', 0):
                vsum = _int(x.get('VOLUME'))
                bsum = _int(x.get('LONG_POSITION'))
                ssum = _int(x.get('SHORT_POSITION'))
            elif mem == '总量增减' and x.get('VOLUME') not in (None, '', 0):
                vchg = _int(x.get('VOLUME'))
                bchg = _int(x.get('LONG_POSITION'))
                schg = _int(x.get('SHORT_POSITION'))
            continue
        nm = re.sub(r'[（(][^）)]*[）)]', '', mem).strip()
        if not nm:
            continue
        if x.get('VOLUME') not in (None, '', 0):
            vol.append({'r': _int(x.get('VOLUME_RANK')), 'm': nm,
                        'q': _int(x.get('VOLUME')), 'c': _int(x.get('VOLUME_CHANGE'))})
        if x.get('LONG_POSITION') not in (None, '', 0):
            buy.append({'r': _int(x.get('LP_RANK')), 'm': nm,
                        'q': _int(x.get('LONG_POSITION')), 'c': _int(x.get('LP_CHANGE'))})
        if x.get('SHORT_POSITION') not in (None, '', 0):
            sell.append({'r': _int(x.get('SP_RANK')), 'm': nm,
                         'q': _int(x.get('SHORT_POSITION')), 'c': _int(x.get('SP_CHANGE'))})
    vol.sort(key=lambda a: a['r']); buy.sort(key=lambda a: a['r']); sell.sort(key=lambda a: a['r'])
    return {'code': code, 'cn': cn, 'contract': contract, 'ex': 'DCE', 'level': '合约',
            'vol': vol[:20], 'buy': buy[:20], 'sell': sell[:20],
            'vsum': vsum, 'bsum': bsum, 'ssum': ssum,
            'vchg': vchg, 'bchg': bchg, 'schg': schg}


def fetch_eastmoney_dce(day):
    """取大商所(DCE)每日会员持仓排名。返回 ({code:{contract:item}}, status)。"""
    import time
    ymd = day.strftime('%Y-%m-%d')
    # 1) DCE 主力合约列表(IS_MAINCODE=1)
    mains = _em_query(EM_MAINREPORT,
                      f'(TRADE_MARKET_CODE="{MK_DCE}")(IS_MAINCODE="1")', 50)
    if mains is None:
        return {}, 'fail'
    if not mains:
        return {}, 'empty'
    out, ok = {}, 0
    for m in mains:
        sec = (m.get('SECURITY_CODE') or '').lower()
        tc = (m.get('TRADE_CODE') or '').upper()
        if not sec or not tc:
            continue
        code = tc + '0'
        cn = DCE_CN.get(code, tc)
        rows = _em_query(EM_REPORT, f"(SECURITY_CODE=\"{sec.upper()}\")(TRADE_DATE='{ymd}')", 200)
        if not rows:
            continue
        item = _em_item(rows, code, cn, sec)
        item['net'] = item['bsum'] - item['ssum']
        item['netchg'] = item['bchg'] - item['schg']
        item['main'] = True
        out.setdefault(code, {})[sec] = item
        ok += 1
        time.sleep(0.12)          # 礼貌限速, 避免触发限频
    return out, ('ok' if ok else 'empty')


# ---------------------------------------------------------------- 汇总
def build(force=False, verbose=True):
    import time
    if not force and os.path.exists(CACHE_FILE):
        try:
            c = json.load(open(CACHE_FILE, encoding='utf-8'))
            # 任一交易所取到空/失败(常为收盘后数据稍晚发布的时序竞争)则不信任缓存, 强制重抓
            _srcs = c.get('sources', {}) or {}
            _all_ok = all(v not in ('empty', 'fail') for v in _srcs.values())
            if time.time() - c.get('_ts', 0) < TTL and c.get('items') and _all_ok:
                if verbose:
                    print(f'[rank] 使用缓存 {c.get("date")} ({len(c["items"])} 品种)')
                return c
        except Exception:
            pass

    try:
        from main_contract import resolve_main_contracts
        mm = resolve_main_contracts(verbose=False) or {}
    except Exception:
        mm = {}

    result = {'date': None, 'sources': {}, 'items': [], 'dce': None}
    primary_day = None
    for day in recent_trade_days(8):
        if verbose:
            print(f'[rank] 尝试 {day} ...')
        merged = {}          # code -> {contract: item}
        src = {}
        got = 0

        a, sa = fetch_shfe_ine(day)
        src.update(sa)
        for code, d in a.items():
            merged.setdefault(code, {}).update(d)

        b, sb = fetch_czce(day)
        src['CZCE'] = sb
        for code, item in b.items():
            merged.setdefault(code, {})[item['contract']] = item

        c, sc = fetch_cffex(day)
        src['CFFEX'] = sc
        for code, d in c.items():
            merged.setdefault(code, {}).update(d)

        d, sd = fetch_gfex(day, mm)
        src['GFEX'] = sd
        for code, dd in d.items():
            merged.setdefault(code, {}).update(dd)

        got = sum(1 for v in merged.values() if v)
        if got >= 10:
            primary_day = day
            result['date'] = day.strftime('%Y-%m-%d')
            result['sources'] = src
            # 大商所经东方财富 datacenter 接入(官网 412 无法直爬)
            de, sde = fetch_eastmoney_dce(day)
            src['DCE'] = sde
            for code, d in de.items():
                merged.setdefault(code, {}).update(d)
            break

    if primary_day is None:
        result['sources'] = src
        result['dce'] = 'no-data'
        return result

    # 中金所(CFFEX)/大商所(DCE)有时在收盘后稍晚发布, 主交易日取到空时,
    # 回退到更早的交易日补取, 避免整栏缺数据(尤其股指/国债龙虎榜)
    for _ex, _fetcher in (('CFFEX', fetch_cffex), ('DCE', fetch_eastmoney_dce)):
        if result['sources'].get(_ex) in ('empty', 'fail', None):
            for _day in recent_trade_days(8):
                if _day >= primary_day:
                    continue
                _data, _st = _fetcher(_day)
                if _data:
                    result['sources'][_ex] = _st
                    for _code, _d in _data.items():
                        merged.setdefault(_code, {}).update(_d)
                    if verbose:
                        print(f'[rank] {_ex} 回退到 {_day} 补取成功')
                    break

    # 选主力合约: 优先 main_contract 判定的月份, 否则取持仓最大的合约
    items = []
    for code, byc in merged.items():
        if not byc:
            continue
        pick = None
        mc = (mm or {}).get(code) or {}
        want = (mc.get('contract') or '').lower()
        if want and want in byc:
            pick = byc[want]
        elif len(byc) == 1:
            pick = list(byc.values())[0]
        else:
            pick = max(byc.values(), key=lambda x: (x.get('bsum', 0) + x.get('ssum', 0)))
        it = dict(pick)
        it['code'] = code
        it['cn'] = it.get('cn') or _cn_of(code)
        it['net'] = it.get('bsum', 0) - it.get('ssum', 0)
        it['netchg'] = it.get('bchg', 0) - it.get('schg', 0)
        it['main'] = bool(want and want in byc)
        items.append(it)

    items.sort(key=lambda x: x['code'])
    result['items'] = items
    result['dce'] = src.get('DCE') or probe_dce()
    result['_ts'] = __import__('time').time()
    result['_time'] = __import__('time').strftime('%Y-%m-%d %H:%M:%S')

    os.makedirs(DATA_DIR, exist_ok=True)
    # 原子写: 避免前端懒加载 rank.json 时读到半截 JSON
    _tmp = os.path.join(DATA_DIR, 'rank.json.tmp')
    with open(_tmp, 'w', encoding='utf-8') as _f:
        json.dump(result, _f, ensure_ascii=False)
        _f.flush()
        os.fsync(_f.fileno())
    os.replace(_tmp, CACHE_FILE)
    if verbose:
        print(f'[rank] {result["date"]} 品种 {len(items)} | 源 {result["sources"]} | DCE {result["dce"]}')
    return result


if __name__ == '__main__':
    r = build(force=True)
    print(f'\n日期 {r["date"]}  品种 {len(r.get("items", []))}  源 {r["sources"]}  DCE {r["dce"]}')
    for it in r.get('items', [])[:8]:
        b1 = it['buy'][0]['m'] if it['buy'] else '-'
        s1 = it['sell'][0]['m'] if it['sell'] else '-'
        print(f"  {it['code']:5}{it['cn']:8}{it['contract']:10} "
              f"买1={b1:8} 卖1={s1:8} 净={it['net']:>9,} 净变={it['netchg']:>8,}")
