"""
fetch_quotes.py v2 — 行情 + 新闻 + 品种分析 + 数据日历(全自动打包)
=================================================================

数据源:
  1) 新浪 hq.sinajs.cn → 国内期货主力连续 + 外盘 + 股指国债
  2) 新浪 feed.mix.sina.com.cn → 7×24 新闻(股市/宏观/全球)
  3) 静态品种分析字典 → 每品种"基本面/驱动/事件/关注"4维度
  4) 重要数据日历(预排) → 每周固定宏观数据 + 美国重要数据时间窗

用法:
  python fetch_quotes.py              # 全量
  python fetch_quotes.py --news-only  # 只刷新闻
"""
import urllib.request
import json
import gzip
import io
import os
from datetime import datetime, timedelta, date
from pathlib import Path
import sys
import time
import argparse
import re

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)

SINA_QUOTES_URL = 'https://hq.sinajs.cn/list={codes}'
SINA_NEWS_URL = 'https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&num={num}&page=1'

# ============================================================
# 1) 期货品种代码表(同 V1)
# ============================================================
COMMODITY_CODES = {
    'metal': [
        ('CU0', '沪铜', '铜连续'), ('AL0', '沪铝', '铝连续'),
        ('ZN0', '沪锌', '沪锌连续'), ('PB0', '沪铅', '铅连续'),
        ('NI0', '沪镍', '镍连续'), ('SN0', '沪锡', '锡连续'),
        ('AU0', '黄金', '黄金连续'), ('AG0', '白银', '白银连续'),
        ('AO0', '氧化铝', '氧化铝连续'), ('BC0', '国际铜', '国际铜连续'),
        ('LC0', '碳酸锂', '碳酸锂连续'),
    ],
    'black': [
        ('I0', '铁矿石', '铁矿石连续'), ('RB0', '螺纹钢', '螺纹钢连续'),
        ('HC0', '热卷', '热卷连续'), ('SS0', '不锈钢', '不锈钢连续'),
        ('J0', '焦炭', '焦炭连续'), ('JM0', '焦煤', '焦煤连续'),
        ('SF0', '硅铁', '硅铁连续'), ('SM0', '锰硅', '锰硅连续'),
    ],
    'energy': [
        ('SC0', '原油', '上海原油连续'), ('NR0', '20号胶', '20号胶连续'),
        ('RU0', '橡胶', '天然橡胶连续'), ('FU0', '燃油', '燃油连续'),
        ('LU0', '低硫燃料油', '低硫燃料油连续'), ('BU0', '沥青', '石油沥青连续'),
        ('SP0', '纸浆', '纸浆连续'), ('EB0', '苯乙烯', '苯乙烯连续'),
        ('EG0', '乙二醇', '乙二醇连续'), ('PG0', '液化石油气', '液化石油气连续'),
        ('SA0', '纯碱', '纯碱连续'), ('FG0', '玻璃', '玻璃连续'),
        ('V0', 'PVC', 'PVC连续'), ('PP0', '聚丙烯', '聚丙烯连续'),
        ('L0', '塑料', 'LLDPE连续'), ('TA0', 'PTA', 'PTA连续'),
        ('MA0', '甲醇', '甲醇连续'), ('PF0', '短纤', '短纤连续'),
        ('UR0', '尿素', '尿素连续'), ('PS0', '多晶硅', '多晶硅连续'),
        ('SI0', '工业硅', '工业硅连续'),
    ],
    'agri': [
        ('M0', '豆粕', '豆粕连续'), ('Y0', '豆油', '豆油连续'),
        ('P0', '棕榈油', '棕榈油连续'), ('C0', '玉米', '玉米连续'),
        ('A0', '豆一', '豆一连续'), ('B0', '豆二', '豆二连续'),
        ('RM0', '菜粕', '菜粕连续'), ('OI0', '菜油', '菜油连续'),
        ('RS0', '菜籽', '菜籽连续'), ('CF0', '棉花', '棉花连续'),
        ('CY0', '棉纱', '棉纱连续'), ('SR0', '白糖', '白糖连续'),
        ('AP0', '苹果', '苹果连续'), ('CJ0', '红枣', '红枣连续'),
        ('PK0', '花生', '花生连续'), ('LH0', '生猪', '生猪连续'),
        ('CS0', '玉米淀粉', '淀粉连续'), ('JD0', '鸡蛋', '鸡蛋连续'),
    ],
}
INDEX_CODES = {
    'finance': [
        ('IF0', '沪深300', '沪深300指数期货连续'),
        ('IH0', '上证50', '上证50指数期货连续'),
        ('IC0', '中证500', '中证500指数期货连续'),
        ('IM0', '中证1000', '中证1000指数期货连续'),
        ('T0', '10年国债', '10年期国债期货连续'),
        ('TF0', '5年国债', '5年期国债期货连续'),
        ('TS0', '2年国债', '2年期国债期货连续'),
        ('TL0', '30年国债', '30年期国债期货连续'),
    ],
}
OVERSEAS_CODES = {
    'overseas': [
        ('hf_GC', 'COMEX黄金', 'COMEX Gold'), ('hf_SI', 'COMEX白银', 'COMEX Silver'),
        ('hf_CL', 'WTI原油', 'NYMEX WTI Crude'), ('hf_OIL', '布伦特原油', 'ICE Brent Crude'),
        ('hf_NG', '天然气', 'NYMEX Natural Gas'),
        ('hf_CAD', '铜', 'LME Copper 3M'), ('hf_ZSD', '锌', 'LME Zinc 3M'),
        ('hf_NID', '镍', 'LME Nickel 3M'), ('hf_SND', '锡', 'LME Tin 3M'),
        ('hf_AHD', '铝', 'LME Aluminium 3M'), ('hf_PBD', '铅', 'LME Lead 3M'),
        ('hf_S', '大豆', 'CBOT Soybean'), ('hf_C', '美玉米', 'CBOT Corn'),
        ('hf_W', '美麦', 'CBOT Wheat'), ('hf_SM', '豆粕', 'CBOT Soybean Meal'),
        ('hf_BO', '豆油', 'CBOT Soybean Oil'), ('hf_CT', '美棉', 'ICE Cotton'),
        ('hf_KC', '咖啡', 'ICE Coffee'),
        ('hf_OJ', '橙汁', 'ICE Orange Juice'), ('hf_CC', '可可', 'ICE Cocoa'),
        ('hf_RS', '菜籽', 'ICE Canola'),
        # 注: hf_SB(ICE糖)/hf_RB(糙米)/hf_DX(美元指数) 新浪已无数据返回, 已移除
        ('int_dji', '道琼斯', 'Dow Jones'), ('int_sp500', '标普500', 'S&P 500'),
        ('int_nasdaq', '纳斯达克', 'NASDAQ'), ('int_nikkei', '日经指数', 'Nikkei 225'),
        ('int_hangseng', '恒生指数', 'Hang Seng'), ('int_dax30', '德国DAX', 'DAX'),
        ('int_ftse', '英国富时', 'FTSE 100'),
    ],
}

# A 股指数(行情用 - 来自 sina s_sh/s_sz 前缀)
ASTOCK_INDICES = [
    ('s_sh000001', '上证指数'),
    ('s_sz399001', '深证成指'),
    ('s_sz399006', '创业板指'),
    ('s_sh000300', '沪深300'),
    ('s_sh000016', '上证50'),
    ('s_sh000688', '科创50'),
    ('s_sh000852', '中证1000'),
    ('s_sz399905', '中证500'),
]

# ============================================================
# 2) 品种静态分析字典 (AI 推送内容 - 我手写模板)
# ============================================================
# 每个品种: drivers(驱动因子) / events(近期待跟踪事件) / focus(关注要点) / base(基本面)
VARIETY_KB = {
    'CU0': {
        'cn': '沪铜', 'cat': 'metal',
        'drivers': ['美元指数', 'LME铜库存', '中国地产+电网投资', '智利秘鲁铜矿罢工/扰动', '全球制造业PMI'],
        'base': '全球最大工业金属,中国电网投资+新能源车/光伏用铜拉动,供需长期紧平衡',
        'focus': 'LME 铜库存周变化、CIF 中国到岸升贴水、洋山铜溢价',
        'key_news_tags': ['铜价', 'LME铜', '智利铜矿', '电网投资'],
    },
    'AU0': {
        'cn': '黄金', 'cat': 'metal',
        'drivers': ['实际利率(美10Y-通胀)', '美元指数', '央行购金', '地缘风险', '避险情绪'],
        'base': '避险资产+抗通胀,近年央行购金+去美元化趋势支撑中枢上移',
        'focus': '美联储议息、CPI/PCE 数据、地缘事件(中东/俄乌)',
        'key_news_tags': ['黄金', '美联储', '美元', '避险'],
    },
    'AG0': {
        'cn': '白银', 'cat': 'metal',
        'drivers': ['金银比(高→修复)', '光伏银浆需求', '工业属性 vs 金融属性'],
        'base': '兼具贵金属避险+工业品属性,金银比回归时弹性大于黄金',
        'focus': '金银比走势、光伏装机量、印度进口需求',
        'key_news_tags': ['白银', '金银比', '光伏银浆'],
    },
    'I0': {
        'cn': '铁矿石', 'cat': 'black',
        'drivers': ['钢厂铁水产量', '港口到港量+库存', '四大矿山发货', '房地产新开工'],
        'base': '中国钢铁产业链上游,高度依赖进口(澳洲/巴西),钢厂利润是核心',
        'focus': '港口库存周变化、铁水日均产量、必和必拓/力拓发货',
        'key_news_tags': ['铁矿石', '铁水产量', '钢厂', '房地产'],
    },
    'RB0': {
        'cn': '螺纹钢', 'cat': 'black',
        'drivers': ['房地产新开工/施工', '基建项目落地', '钢厂限产', '表观消费'],
        'base': '中国最大钢材品种,与基建+地产强相关,季节性显著(春季旺季/冬季淡季)',
        'focus': '建材成交、库存去化速度、钢厂利润',
        'key_news_tags': ['螺纹钢', '钢厂', '房地产', '基建'],
    },
    'SC0': {
        'cn': '原油', 'cat': 'energy',
        'drivers': ['OPEC+ 减产', '美国战略储备', '中国炼厂开工率', '地缘冲突(中东/俄罗斯)'],
        'base': '大宗之王,地缘+供需双重驱动,上海原油反映中东原油到岸',
        'focus': 'EIA 库存周报、OPEC 月会、贝克休斯钻井数',
        'key_news_tags': ['原油', 'OPEC', 'EIA库存', '中东'],
    },
    'AU0_macro': {},  # placeholder
    'LC0': {
        'cn': '碳酸锂', 'cat': 'energy',
        'drivers': ['锂矿产能(宜春/海外盐湖)', '新能源车销量', '储能装机', '库存(广期所仓单)'],
        'base': '新能源产业链上游,2024-2026 经历暴跌(从60万→14万/吨),当前寻底',
        'focus': '广期所仓单日变化、宜春云母停产、宁德/比亚迪采购价',
        'key_news_tags': ['碳酸锂', '锂矿', '新能源车', '储能'],
    },
    'M0': {
        'cn': '豆粕', 'cat': 'agri',
        'drivers': ['美豆种植/天气', 'USDA 供需报告', '中国进口大豆到港', '生猪存栏(饲料需求)'],
        'base': '中国进口大豆压榨副产品,成本端跟随 CBOT 大豆',
        'focus': 'USDA 月度供需报告、出口销售报告、油厂开机率',
        'key_news_tags': ['豆粕', '大豆', 'USDA', '生猪'],
    },
    'Y0': {
        'cn': '豆油', 'cat': 'agri',
        'drivers': ['美豆油/马棕油', '生物柴油政策(B10/B15)', '原油价格'],
        'base': '油脂板块之一,与棕榈油/菜油有替代关系',
        'focus': '马棕出口、美豆油制生物柴油用量',
        'key_news_tags': ['豆油', '棕榈油', '生物柴油'],
    },
    'P0': {
        'cn': '棕榈油', 'cat': 'agri',
        'drivers': ['马来/印尼产量', '印度/中国进口', '生柴掺混政策', '厄尔尼诺/拉尼娜'],
        'base': '全球最大植物油,产地集中东南亚,受天气和生柴政策影响大',
        'focus': 'MPOB 月报、马来出口、印度采购',
        'key_news_tags': ['棕榈油', 'MPOB', '马来出口'],
    },
    'SR0': {
        'cn': '白糖', 'cat': 'agri',
        'drivers': ['巴西/印度产量', '印度出口配额', '国内收储', '原油(替代乙醇)'],
        'base': '全球供需+国内收储双轨,周期性显著,3年一周期',
        'focus': 'UNICA 双周报、印度出口政策、国内产销进度',
        'key_news_tags': ['白糖', '巴西甘蔗', '印度出口'],
    },
    'CF0': {
        'cn': '棉花', 'cat': 'agri',
        'drivers': ['美棉种植区天气', '中国新疆产量', '印度/巴基斯坦', '下游纺织订单'],
        'base': '纺织产业链上游,郑棉与 ICE 美棉联动',
        'focus': 'USDA 报告、新疆产量、纺企开机率',
        'key_news_tags': ['棉花', '美棉', '新疆', '纺织'],
    },
    'CF_macro': {},
    'LH0': {
        'cn': '生猪', 'cat': 'agri',
        'drivers': ['能繁母猪存栏', '出栏节奏', '饲料成本', '疫病'],
        'base': '中国特色品种,周期性极强(猪周期约 4 年),当前产能去化阶段',
        'focus': '能繁母猪存栏(农业农村部)、出栏均重、饲料价格',
        'key_news_tags': ['生猪', '猪价', '母猪存栏'],
    },
    'SA0': {
        'cn': '纯碱', 'cat': 'energy',
        'drivers': ['光伏玻璃需求', '浮法玻璃冷修', '远兴能源产能', '原盐/动力煤成本'],
        'base': '光伏产业链上游,2024-2025 供需严重失衡,价格低位震荡',
        'focus': '光伏玻璃月度产量、纯碱开工率、库存',
        'key_news_tags': ['纯碱', '光伏玻璃', '远兴能源'],
    },
    'FG0': {
        'cn': '玻璃', 'cat': 'energy',
        'drivers': ['地产竣工', '汽车玻璃', '光伏玻璃', '冷修产线'],
        'base': '与房地产竣工强相关,近年产能过剩',
        'focus': '地产竣工面积、玻璃冷修进度、库存',
        'key_news_tags': ['玻璃', '地产竣工', '冷修'],
    },
    'TA0': {
        'cn': 'PTA', 'cat': 'energy',
        'drivers': ['PX 成本', '聚酯开工率', '原油', '新装置投产'],
        'base': '聚酯产业链中游,加工费是核心观察指标',
        'focus': 'PX-石脑油价差、聚酯开工率、社会库存',
        'key_news_tags': ['PTA', 'PX', '聚酯'],
    },
    'MA0': {
        'cn': '甲醇', 'cat': 'energy',
        'drivers': ['煤/天然气成本', '伊朗进口', 'MTO/CTO 开工', '港口库存'],
        'base': '煤化工+天然气化工双工艺,伊朗货源季节性影响大',
        'focus': '伊朗装船、港口库存、MTO 装置开工',
        'key_news_tags': ['甲醇', '伊朗', 'MTO'],
    },
    'J0': {
        'cn': '焦炭', 'cat': 'black',
        'drivers': ['钢厂铁水', '焦化厂开工', '山西环保限产', '煤炭价格'],
        'base': '焦煤下游,钢厂补库节奏关键',
        'focus': '焦化厂利润、钢厂日耗、库存',
        'key_news_tags': ['焦炭', '焦煤', '钢厂'],
    },
    'JM0': {
        'cn': '焦煤', 'cat': 'black',
        'drivers': ['澳煤/蒙煤进口', '国内煤矿安监', '焦化厂补库', '煤炭整体价格'],
        'base': '澳煤进口受地缘扰动大,蒙煤替代性强',
        'focus': '甘其毛都口岸通关、煤矿库存',
        'key_news_tags': ['焦煤', '澳煤', '蒙煤'],
    },
    'NI0': {
        'cn': '沪镍', 'cat': 'metal',
        'drivers': ['印尼镍矿出口政策', '硫酸镍(电池)', '不锈钢需求', 'LME 库存'],
        'base': '印尼占全球镍产量 50%+,政策风险高;新能源属性增强',
        'focus': '印尼镍矿配额、MHP/硫酸镍价格、不锈钢排产',
        'key_news_tags': ['镍', '印尼', '硫酸镍'],
    },
    'SN0': {
        'cn': '沪锡', 'cat': 'metal',
        'drivers': ['缅甸佤邦禁矿', '半导体周期', '印尼出口', '焊料需求'],
        'base': '小品种+半导体属性,供给端高度集中,弹性大',
        'focus': '缅甸矿进口、印尼出口、半导体景气',
        'key_news_tags': ['锡', '缅甸', '半导体'],
    },
}

# ============================================================
# 3) 重要数据日历(预排,每月更新)
# ============================================================
# 中国宏观: PMI(月末)、CPI/PPI(每月10号左右)、社融(10-15号)、LPR(20号)
# 美国宏观: 非农(每月第一个周五)、CPI(每月10-14号)、PPI、零售、PCE、利率决议
# 美国能源: EIA 原油库存(周三10:30 ET)、API库存(周三4:30 ET)、贝克休斯钻井(周五)
# 农产品: USDA 月度供需报告(每月10-12号)、MPOB(每月10号左右)、UNICA 周报
# 期货市场: 持仓报告(周五)、产业大会
KEY_CALENDAR_2026 = [
    # 月度固定 - 美国利率
    {'date': '2026-09-17', 'time': '02:00', 'name': '美联储利率决议', 'impact': 'high', 'cat': 'us_rate'},
    {'date': '2026-09-11', 'time': '20:30', 'name': '美国 8 月 CPI', 'impact': 'high', 'cat': 'us_macro'},
    # 美国非农
    {'date': '2026-09-04', 'time': '20:30', 'name': '美国 8 月非农就业', 'impact': 'high', 'cat': 'us_macro'},
    # EIA 库存(每周三,只列未来 4 周)
    {'date': '2026-09-09', 'time': '22:30', 'name': 'EIA 原油库存周报', 'impact': 'medium', 'cat': 'us_energy'},
    {'date': '2026-09-16', 'time': '22:30', 'name': 'EIA 原油库存周报', 'impact': 'medium', 'cat': 'us_energy'},
    {'date': '2026-09-23', 'time': '22:30', 'name': 'EIA 原油库存周报', 'impact': 'medium', 'cat': 'us_energy'},
    {'date': '2026-09-30', 'time': '22:30', 'name': 'EIA 原油库存周报', 'impact': 'medium', 'cat': 'us_energy'},
    # 农产品
    {'date': '2026-09-11', 'time': '00:00', 'name': 'USDA 9 月供需报告', 'impact': 'high', 'cat': 'usda'},
    {'date': '2026-09-10', 'time': '12:00', 'name': 'MPOB 8 月报告', 'impact': 'medium', 'cat': 'mpob'},
    # 中国宏观
    {'date': '2026-09-09', 'time': '09:30', 'name': '中国 8 月 CPI/PPI', 'impact': 'medium', 'cat': 'cn_macro'},
    {'date': '2026-09-15', 'time': '09:30', 'name': '中国 8 月社零/工业增加值/固投', 'impact': 'medium', 'cat': 'cn_macro'},
    {'date': '2026-09-20', 'time': '09:15', 'name': '中国 LPR 报价', 'impact': 'medium', 'cat': 'cn_macro'},
    {'date': '2026-09-30', 'time': '09:30', 'name': '中国 9 月 PMI', 'impact': 'high', 'cat': 'cn_macro'},
    # 期货市场
    {'date': '2026-09-12', 'time': '15:30', 'name': '期货市场周度持仓报告', 'impact': 'medium', 'cat': 'futures'},
    {'date': '2026-09-19', 'time': '15:30', 'name': '期货市场周度持仓报告', 'impact': 'medium', 'cat': 'futures'},
    {'date': '2026-09-26', 'time': '15:30', 'name': '期货市场周度持仓报告', 'impact': 'medium', 'cat': 'futures'},
]

# ============================================================
# 4) 网络抓取函数
# ============================================================
def http_get(url, timeout=10, decode_gbk=False):
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    })
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        if decode_gbk:
            return data.decode('gbk')
        # 自动检测 gzip
        if data[:2] == b'\x1f\x8b':
            data = gzip.decompress(data)
        return data.decode('utf-8', errors='replace')
    except Exception as e:
        print(f'[ERR] {url[:60]}: {e}', file=sys.stderr)
        return None


def fetch_sina_quotes():
    """抓所有期货+外盘+股指国债 + A股指数"""
    all_codes = []
    for cat in COMMODITY_CODES.values():
        all_codes += ['nf_' + c[0] for c in cat]
    for cat in INDEX_CODES.values():
        all_codes += ['nf_' + c[0] for c in cat]
    for cat in OVERSEAS_CODES.values():
        all_codes += [c[0] for c in cat]  # hf_/int_ 已有前缀
    # A 股指数
    all_codes += [c[0] for c in ASTOCK_INDICES]

    raw = http_get(SINA_QUOTES_URL.format(codes=','.join(all_codes)), decode_gbk=True)
    if not raw:
        return {}

    result = {}
    for line in raw.strip().split('\n'):
        if '""' in line:
            continue
        try:
            key = line.split('hq_str_')[1].split('=')[0].replace('nf_', '')
            payload = line.split('"')[1]
            result[key] = payload.split(',')
        except (IndexError, ValueError):
            continue
    return result


def fetch_news(lid, num=20):
    """抓新浪 lid=2516/2517/2509/2518 的 7×24 新闻"""
    url = SINA_NEWS_URL.format(lid=lid, num=num)
    raw = http_get(url)
    if not raw:
        return []
    try:
        o = json.loads(raw)
        items = o.get('result', {}).get('data', [])
        news = []
        for it in items:
            news.append({
                'title': it.get('title', '').strip(),
                'ctime': it.get('ctime', 0),
                'media': it.get('media_name', ''),
                'url': it.get('url', ''),
                'lid': lid,
            })
        return news
    except Exception as e:
        print(f'[ERR] news parse lid={lid}: {e}', file=sys.stderr)
        return []


# ============================================================
# 品种合约规格表 V2.7 (交割月自然人"取整/取零"规则)
#
# 【数据来源 — 2026-09-06 多源交叉核对】
#   1. 上期所官网《上海期货交易所交割管理办法》第四条(2025-08-07 修订)
#      https://www.shfe.com.cn/regulation/exchangerules/otherrules/202508/t20250807_828519.html
#   2. 上期所官网 风控专题「各期货合约交割单位对应手数」表
#      https://www.shfe.com.cn/specialtopic/investor/risk_control/
#   3. 郑商所官网《郑州商品交易所期货交易风险控制管理办法》第二十五条
#   4. 上期所〔2025〕157号 / 中金所〔2025〕55号 / 广期所 2026年休市安排公告
#   5. 期货公司实务通知(南华期货 2606、华泰期货 2609、信达期货 2609)— 用于反推验证日期算法
#
# 【核心规则】
#   上期所(SHFE)/能源中心(INE):
#     · 自然人可持仓进入交割月(燃料油FU除外)
#     · 取整: 交割月前一月最后一个交易日收盘前,持仓须调为"交割单位整数倍"
#     · 取零: 最后交易日前第5个交易日收盘前,自然人持仓须为0手(原油SC为前第8个)
#   大商所(DCE)/郑商所(CZCE)/广期所(GFEX):
#     · 自然人不得进入交割月
#     · 直接取零: 交割月前一月最后一个交易日收盘前清0,无"取整"环节
#     · 交割单位整数倍仅约束法人(套保/交割),自然人不适用
#   中金所(CFFEX):
#     · 股指: 现金交割,自然人可持有至最后交易日(交割月第三个周五)
#     · 国债: 未通过国债托管账户审核的,须在交割月前第二个交易日收盘前清0
# ============================================================

# ---------- 2026 年休市安排(上期所〔2025〕157号 / 中金所〔2025〕55号 / 广期所) ----------
_HOLIDAY_RANGES_2026 = [
    ('2026-01-01', '2026-01-03'),   # 元旦
    ('2026-02-15', '2026-02-23'),   # 春节
    ('2026-04-04', '2026-04-06'),   # 清明
    ('2026-05-01', '2026-05-05'),   # 劳动节
    ('2026-06-19', '2026-06-21'),   # 端午
    ('2026-09-25', '2026-09-27'),   # 中秋
    ('2026-10-01', '2026-10-07'),   # 国庆
]
# 调休上班的周末,期货市场照常休市
_EXTRA_CLOSED_2026 = ['2026-01-04', '2026-02-14', '2026-02-28',
                      '2026-05-09', '2026-09-20', '2026-10-10']

_NON_TRADING_DAYS = set()
for _s, _e in _HOLIDAY_RANGES_2026:
    _d = datetime.strptime(_s, '%Y-%m-%d').date()
    _end = datetime.strptime(_e, '%Y-%m-%d').date()
    while _d <= _end:
        _NON_TRADING_DAYS.add(_d)
        _d += timedelta(days=1)
for _s in _EXTRA_CLOSED_2026:
    _NON_TRADING_DAYS.add(datetime.strptime(_s, '%Y-%m-%d').date())


def is_trading_day(d):
    """是否为交易日(排除周末 + 法定休市)。d 为 date 对象"""
    if d.weekday() >= 5:
        return False
    return d not in _NON_TRADING_DAYS


def trading_days_of_month(year, month):
    """返回该月全部交易日(升序 date 列表)"""
    out, d = [], date(year, month, 1)
    while d.month == month:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def nth_trading_day_before(d, n):
    """d 之前的第 n 个交易日。n=1 表示 d 的前一个交易日"""
    cur, cnt = d - timedelta(days=1), 0
    while cnt < n:
        if is_trading_day(cur):
            cnt += 1
            if cnt == n:
                return cur
        cur -= timedelta(days=1)
    return cur


def first_trading_day_on_or_after(d):
    """d 当日或其后第一个交易日(处理"15日遇非交易日顺延")"""
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_month(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


# ---------- 各交易所自然人交割月规则 ----------
# enter_delivery : 自然人能否持仓进入交割月
# needs_round    : 自然人是否需先"取整"(调为交割单位整数倍)
# round_deadline : 取整截止日规则
# clear_offset   : 取零截止 = 最后交易日前第 N 个交易日收盘前 (None=无提前强平)
# clear_deadline : 取零截止日规则(当 clear_offset 为 None 时用此)
EXCHANGE_NATURAL_RULES = {
    'SHFE':  {'enter_delivery': True,  'needs_round': True,
              'round_deadline': 'prev_month_last', 'clear_offset': 5,  'clear_deadline': None},
    'INE':   {'enter_delivery': True,  'needs_round': True,
              'round_deadline': 'prev_month_last', 'clear_offset': 5,  'clear_deadline': None},
    'DCE':   {'enter_delivery': False, 'needs_round': False,
              'round_deadline': None, 'clear_offset': None, 'clear_deadline': 'prev_month_last'},
    'CZCE':  {'enter_delivery': False, 'needs_round': False,
              'round_deadline': None, 'clear_offset': None, 'clear_deadline': 'prev_month_last'},
    'GFEX':  {'enter_delivery': False, 'needs_round': False,
              'round_deadline': None, 'clear_offset': None, 'clear_deadline': 'prev_month_last'},
    'CFFEX': {'enter_delivery': True,  'needs_round': False,
              'round_deadline': None, 'clear_offset': None, 'clear_deadline': None},
}

# 自然人规则例外(覆盖交易所默认)
#   key = 品种代码前缀, value = 覆盖字段
# 说明: SC/LU/FU 的"最后交易日"就是交割月前一月最后一个交易日,自然人会在此前
#       5~8 个交易日就被强制清0,根本进不了交割月,因此不存在"取整"环节。
#       华泰期货2609通知中,INE 的整数倍要求也只列了 NR(10手)和 BC(5手),未列 SC/LU。
NATURAL_RULE_OVERRIDES = {
    'FU0': {'needs_round': False, 'enter_delivery': False,
            'note': '燃料油自然人不可进入交割月,最后交易日前第5个交易日取零(不取整)'},
    'SC0': {'needs_round': False, 'clear_offset': 8,
            'note': '原油自然人清仓最早:最后交易日前第8个交易日取零(不取整)'},
    'LU0': {'needs_round': False, 'clear_offset': 5,
            'note': '低硫燃料油自然人提前5个交易日取零(不取整)'},
    'EC0': {'clear_offset': None,
            'note': '集运欧线现金交割,自然人可持有至最后交易日'},
}

# 最后交易日规则
#   month_15        : 交割月15日,遇非交易日顺延 (上期所除FU / INE的NR·BC)
#   prev_month_last : 交割月前一月最后一个交易日 (上期所FU / INE的SC·LU)
#   month_10th_td   : 交割月第10个交易日 (郑商所 / 大商所)
#   month_3rd_fri   : 交割月第三个周五 (中金所股指)
#   month_2nd_fri   : 交割月第二个周五 (中金所国债)
#   ec_last_monday  : 交割月最后一个开展期货交易的周一 (集运欧线)

CONTRACT_SPECS = {
    # === 金属 — 上期所 (按时点月份最后交易日规则) ===
    'CU0': {'exchange': 'SHFE', 'product': '铜', 'multiplier': 5, 'margin': 0.10, 'lot_round': 5, 'last_notice_rule': 'month_15'},
    'AL0': {'exchange': 'SHFE', 'product': '铝', 'multiplier': 5, 'margin': 0.10, 'lot_round': 5, 'last_notice_rule': 'month_15'},
    'ZN0': {'exchange': 'SHFE', 'product': '锌', 'multiplier': 5, 'margin': 0.10, 'lot_round': 5, 'last_notice_rule': 'month_15'},
    'PB0': {'exchange': 'SHFE', 'product': '铅', 'multiplier': 5, 'margin': 0.10, 'lot_round': 5, 'last_notice_rule': 'month_15'},
    'NI0': {'exchange': 'SHFE', 'product': '镍', 'multiplier': 1, 'margin': 0.12, 'lot_round': 6, 'last_notice_rule': 'month_15'},
    'SN0': {'exchange': 'SHFE', 'product': '锡', 'multiplier': 1, 'margin': 0.12, 'lot_round': 2, 'last_notice_rule': 'month_15'},
    'AU0': {'exchange': 'SHFE', 'product': '黄金', 'multiplier': 1000, 'margin': 0.10, 'lot_round': 3, 'last_notice_rule': 'month_15'},
    'AG0': {'exchange': 'SHFE', 'product': '白银', 'multiplier': 15, 'margin': 0.12, 'lot_round': 2, 'last_notice_rule': 'month_15'},
    # 黑色 — 上期所 + 大商所
    'RB0': {'exchange': 'SHFE', 'product': '螺纹钢', 'multiplier': 10, 'margin': 0.12, 'lot_round': 30, 'last_notice_rule': 'month_15'},
    'HC0': {'exchange': 'SHFE', 'product': '热卷', 'multiplier': 10, 'margin': 0.12, 'lot_round': 30, 'last_notice_rule': 'month_15'},
    'I0':  {'exchange': 'DCE',  'product': '铁矿', 'multiplier': 100, 'margin': 0.12, 'lot_round': 50, 'last_notice_rule': 'month_10th_td'},
    'J0':  {'exchange': 'DCE',  'product': '焦炭', 'multiplier': 100, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'JM0': {'exchange': 'DCE',  'product': '焦煤', 'multiplier': 60, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'SS0': {'exchange': 'SHFE', 'product': '不锈钢', 'multiplier': 5, 'margin': 0.12, 'lot_round': 12, 'last_notice_rule': 'month_15'},
    # 能源化工 — 大商所 + 上期所
    'SC0': {'exchange': 'INE',  'product': '原油', 'multiplier': 1000, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'prev_month_last'},
    'FU0': {'exchange': 'SHFE', 'product': '燃料油', 'multiplier': 10, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'prev_month_last'},
    'LU0': {'exchange': 'INE',  'product': '低硫燃料油', 'multiplier': 10, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'prev_month_last'},
    'BU0': {'exchange': 'SHFE', 'product': '沥青', 'multiplier': 10, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'month_15'},
    'RU0': {'exchange': 'SHFE', 'product': '橡胶', 'multiplier': 10, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_15'},
    'SP0': {'exchange': 'SHFE', 'product': '纸浆', 'multiplier': 10, 'margin': 0.12, 'lot_round': 2, 'last_notice_rule': 'month_15'},
    'L0':  {'exchange': 'DCE',  'product': '塑料', 'multiplier': 5, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'V0':  {'exchange': 'DCE',  'product': 'PVC', 'multiplier': 5, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'PP0': {'exchange': 'DCE',  'product': 'PP', 'multiplier': 5, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'EG0': {'exchange': 'DCE',  'product': '乙二醇', 'multiplier': 10, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'TA0': {'exchange': 'CZCE', 'product': 'PTA', 'multiplier': 5, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'MA0': {'exchange': 'CZCE', 'product': '甲醇', 'multiplier': 10, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'FG0': {'exchange': 'CZCE', 'product': '玻璃', 'multiplier': 20, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'SA0': {'exchange': 'CZCE', 'product': '纯碱', 'multiplier': 20, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'SF0': {'exchange': 'CZCE', 'product': '硅铁', 'multiplier': 5, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'SM0': {'exchange': 'CZCE', 'product': '锰硅', 'multiplier': 5, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'UR0': {'exchange': 'CZCE', 'product': '尿素', 'multiplier': 20, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    # 农产品
    'M0':  {'exchange': 'DCE',  'product': '豆粕', 'multiplier': 10, 'margin': 0.10, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'Y0':  {'exchange': 'DCE',  'product': '豆油', 'multiplier': 10, 'margin': 0.10, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'P0':  {'exchange': 'DCE',  'product': '棕榈油', 'multiplier': 10, 'margin': 0.10, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'OI0': {'exchange': 'CZCE', 'product': '菜油', 'multiplier': 10, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'RM0': {'exchange': 'CZCE', 'product': '菜粕', 'multiplier': 10, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'SR0': {'exchange': 'CZCE', 'product': '白糖', 'multiplier': 10, 'margin': 0.10, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'CF0': {'exchange': 'CZCE', 'product': '棉花', 'multiplier': 5, 'margin': 0.12, 'lot_round': 8, 'last_notice_rule': 'month_10th_td'},
    'AP0': {'exchange': 'CZCE', 'product': '苹果', 'multiplier': 10, 'margin': 0.15, 'lot_round': 2, 'last_notice_rule': 'month_10th_td'},
    'CJ0': {'exchange': 'CZCE', 'product': '红枣', 'multiplier': 5, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'PK0': {'exchange': 'CZCE', 'product': '花生', 'multiplier': 5, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'C0':  {'exchange': 'DCE',  'product': '玉米', 'multiplier': 10, 'margin': 0.10, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'CS0': {'exchange': 'DCE',  'product': '玉米淀粉', 'multiplier': 10, 'margin': 0.10, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'JD0': {'exchange': 'DCE',  'product': '鸡蛋', 'multiplier': 10, 'margin': 0.10, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    'LH0': {'exchange': 'DCE',  'product': '生猪', 'multiplier': 16, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    # 广期所
    'LC0': {'exchange': 'GFEX', 'product': '碳酸锂', 'multiplier': 1, 'margin': 0.15, 'lot_round': 1, 'last_notice_rule': 'month_10th_td'},
    # 股指国债
    'IF0': {'exchange': 'CFFEX', 'product': '沪深300股指', 'multiplier': 300, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_3rd_fri'},
    'IH0': {'exchange': 'CFFEX', 'product': '上证50股指', 'multiplier': 300, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_3rd_fri'},
    'IC0': {'exchange': 'CFFEX', 'product': '中证500股指', 'multiplier': 200, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_3rd_fri'},
    'IM0': {'exchange': 'CFFEX', 'product': '中证1000股指', 'multiplier': 200, 'margin': 0.12, 'lot_round': 1, 'last_notice_rule': 'month_3rd_fri'},
    'T0':  {'exchange': 'CFFEX', 'product': '10年国债', 'multiplier': 10000, 'margin': 0.03, 'lot_round': 1, 'last_notice_rule': 'month_2nd_fri'},
    'TF0': {'exchange': 'CFFEX', 'product': '5年国债', 'multiplier': 10000, 'margin': 0.025, 'lot_round': 1, 'last_notice_rule': 'month_2nd_fri'},
    'TS0': {'exchange': 'CFFEX', 'product': '2年国债', 'multiplier': 10000, 'margin': 0.012, 'lot_round': 1, 'last_notice_rule': 'month_2nd_fri'},
    'TL0': {'exchange': 'CFFEX', 'product': '30年国债', 'multiplier': 10000, 'margin': 0.035, 'lot_round': 1, 'last_notice_rule': 'month_2nd_fri'},
}


def compute_last_trading_day(spec, year, month):
    """按交易所规则计算某合约的最后交易日。返回 date 或 None"""
    rule = spec.get('last_notice_rule')
    try:
        if rule == 'month_15':
            return first_trading_day_on_or_after(date(year, month, 15))
        if rule == 'prev_month_last':
            py, pm = prev_month(year, month)
            tds = trading_days_of_month(py, pm)
            return tds[-1] if tds else None
        if rule == 'month_10th_td':
            tds = trading_days_of_month(year, month)
            return tds[9] if len(tds) >= 10 else (tds[-1] if tds else None)
        if rule in ('month_3rd_fri', 'month_2nd_fri'):
            fris = [d for d in trading_days_of_month(year, month) if d.weekday() == 4]
            idx = 3 if rule == 'month_3rd_fri' else 2
            return fris[idx - 1] if len(fris) >= idx else (fris[-1] if fris else None)
        if rule == 'ec_last_monday':
            mons = [d for d in trading_days_of_month(year, month) if d.weekday() == 0]
            return mons[-1] if mons else None
    except Exception as e:
        print('[WARN] 计算最后交易日失败 %s %s-%s: %s' % (spec.get('product'), year, month, e))
    return None


def _trading_days_between(start_d, end_d):
    """[start_d, end_d] 区间内的交易日数(含两端)"""
    if not end_d or not start_d or end_d < start_d:
        return 0
    n, d = 0, start_d
    while d <= end_d:
        if is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def compute_delivery_deadlines():
    """
    自然人交割月清仓日历 —— 取整日 / 取零日

    规则来源(2026-09-06 多源交叉核对):
      · 上期所《交割管理办法》第四条:最后交易日前第五个交易日收盘后,自然人持仓应为0手
      · 上期所风控专题:交割月前第一月最后一个交易日收盘前,持仓调整为交割单位整倍数
      · 郑商所《风控办法》第二十五条 + 南华/华泰/信达期货实务通知:
        郑商所·大商所·广期所自然人不得进入交割月,交割月前一月最后交易日收盘前清0
      · 能源中心:SC 提前第8个交易日, LU/NR/BC 提前第5个交易日
    返回: [{'code','cn','exchange','contract','ltd','round_day','round_lots',
            'clear_day','tds_left','level','note'}]
    """
    out = []
    today = datetime.now().date()

    for code, spec in CONTRACT_SPECS.items():
        ex = spec['exchange']
        base = EXCHANGE_NATURAL_RULES.get(ex)
        if not base:
            continue
        rule = dict(base)
        rule.update(NATURAL_RULE_OVERRIDES.get(code, {}))

        # 当前月 + 后续 2 个合约月份,取最近一个仍有效的
        for offset in (0, 1, 2):
            y, m = today.year, today.month + offset
            while m > 12:
                m -= 12
                y += 1

            ltd = compute_last_trading_day(spec, y, m)
            if not ltd or ltd < today:
                continue

            # ---- 取整日:交割月前一月最后一个交易日收盘前 ----
            round_day, round_lots = None, None
            if rule.get('needs_round') and rule.get('round_deadline') == 'prev_month_last':
                py, pm = prev_month(y, m)
                tds = trading_days_of_month(py, pm)
                if tds:
                    round_day = tds[-1]
                    round_lots = spec.get('lot_round')

            # ---- 取零日 ----
            clear_day = None
            if rule.get('clear_offset'):
                clear_day = nth_trading_day_before(ltd, rule['clear_offset'])
            elif rule.get('clear_deadline') == 'prev_month_last':
                py, pm = prev_month(y, m)
                tds = trading_days_of_month(py, pm)
                clear_day = tds[-1] if tds else None

            # 中金所股指/国债无自然人强制提前清仓 → 不进清仓日历
            if not clear_day or clear_day < today:
                continue

            tds_left = _trading_days_between(today, clear_day)
            level = 'imminent' if tds_left <= 3 else ('warn' if tds_left <= 8 else 'info')

            out.append({
                'code': code,
                'cn': spec['product'],
                'exchange': ex,
                'contract': code[:-1].lower() + '%02d%02d' % (y % 100, m),
                'delivery_ym': '%d-%02d' % (y, m),
                'ltd': ltd.isoformat(),
                'round_day': round_day.isoformat() if round_day else None,
                'round_lots': round_lots,
                'clear_day': clear_day.isoformat(),
                'tds_left': tds_left,
                'level': level,
                'note': rule.get('note', ''),
            })
            break  # 每个品种只取最近一个有效合约

    out.sort(key=lambda x: (x['clear_day'], x['code']))
    return out


def compute_main_contract_expiry_alerts(main_map=None):
    """
    主力合约到期提醒。
    按交易所规则推算每个品种**当前主力合约**的最后交易日:
      - 主力合约月份由 main_contract.resolve_main_contracts() 按持仓量判定;
        若取不到主力,退化为"从本月起第一个未到期合约月份"。
      - SHFE / INE / DCE / CZCE / GFEX: 交割月前一个月倒数第 N 个交易日
      - CFFEX 股指/国债: 交割月前一月份的第三个周五
    返回结构: [{'date','code','cn','exchange','contract','days','level'}]
    """
    main_map = main_map or {}
    out = []
    now = datetime.now()
    today = now.date()
    for code, spec in CONTRACT_SPECS.items():
        cn = spec['product']

        # ---- 起始月份: 优先真实主力合约 ----
        start_year, start_month = today.year, today.month
        mc = main_map.get(code)
        if mc and mc.get('month') and len(mc['month']) == 4:
            try:
                yy, mm = int(mc['month'][:2]), int(mc['month'][2:])
                cur_yy = today.year % 100
                year = today.year + (yy - cur_yy if yy >= cur_yy else yy + 100 - cur_yy)
                if (year, mm) >= (today.year, today.month):
                    start_year, start_month = year, mm
            except (ValueError, TypeError):
                pass

        # 从起始月份起向后找第一个"最后交易日尚未到期"的合约月份
        last_notice_date = None
        hit_ym = None
        cur_year, cur_month = start_year, start_month
        for _ in range(4):
            cand = compute_last_trading_day(spec, cur_year, cur_month)
            if cand and cand >= today:
                last_notice_date = cand
                hit_ym = (cur_year, cur_month)
                break
            cur_month += 1
            if cur_month > 12:
                cur_month = 1
                cur_year += 1
        if not last_notice_date or not hit_ym:
            continue

        days_to_notice = (last_notice_date - today).days

        if days_to_notice <= 0:
            continue

        level = 'notice'
        if days_to_notice <= 3:
            level = 'imminent'  # 3 天内 — 红色
        elif days_to_notice <= 7:
            level = 'caution'  # 7 天 — 黄色
        elif days_to_notice <= 14:
            level = 'warn'  # 14 天 — 蓝色

        if level != 'notice':
            out.append({
                'date': last_notice_date.isoformat(),
                'code': code,
                'cn': cn,
                'exchange': spec['exchange'],
                'contract': code[:-1].upper() + '%02d%02d' % (hit_ym[0] % 100, hit_ym[1]),
                'days': days_to_notice,
                'level': level,
            })

    out.sort(key=lambda x: x['days'])
    return out


def compute_lot_round_alerts():
    """
    [V2.7 重写] 自然人交割月清仓日历 —— 取整日 / 取零日
    直接委托 compute_delivery_deadlines(),保留键名 lot_round_alerts 以兼容前端。
    """
    return compute_delivery_deadlines()


OI_BASELINE_PATH = DATA_DIR / 'oi_baseline.json'


def update_oi_baseline(quotes_by_code):
    """维护持仓基线(data/oi_baseline.json),计算「日增仓」(相对上一交易日收盘持仓的仓差)。

    逻辑:每个品种记录最近两次快照日的持仓。跨日首次快照时,把旧日快照转成
    prev_end(=上一交易日收盘持仓),此后 oi_chg = 当前持仓 - prev_end。
    周末/节假日快照的持仓值仍是上一交易日收盘值,因此跨到下一交易日时差值依然正确。

    返回: {code: {'oi_chg': int|None, 'vol_oi': float}}  oi_chg=None 表示基线尚未建立
    """
    baseline = {}
    try:
        if OI_BASELINE_PATH.exists():
            with open(OI_BASELINE_PATH, 'r', encoding='utf-8') as f:
                baseline = json.load(f)
    except Exception as _e:
        print(f'[WARN] 读持仓基线失败,重建: {_e}')
        baseline = {}

    today_str = date.today().isoformat()
    out = {}
    for _cat, lst in quotes_by_code.items():
        for q in lst:
            if q.get('paused'):
                continue
            code = q.get('code')
            if not code:
                continue
            try:
                oi_now = float(q.get('oi') or 0)
                vol_now = float(q.get('volume') or 0)
            except (ValueError, TypeError):
                continue
            if oi_now <= 0:
                continue
            rec = baseline.get(code)
            if not rec:
                # 首次见该品种:建立基线,本日仓差不可知
                baseline[code] = {'date': today_str, 'oi_end': oi_now,
                                  'prev_date': None, 'oi_prev_end': None}
                oi_chg = None
            elif rec.get('date') != today_str:
                # 跨日首次快照:旧快照转成上一日收盘持仓
                rec['prev_date'] = rec.get('date')
                rec['oi_prev_end'] = rec.get('oi_end')
                rec['date'] = today_str
                rec['oi_end'] = oi_now
                prev = rec.get('oi_prev_end')
                oi_chg = int(round(oi_now - prev)) if prev else None
            else:
                rec['oi_end'] = oi_now
                prev = rec.get('oi_prev_end')
                oi_chg = int(round(oi_now - prev)) if prev else None
            out[code] = {
                'oi_chg': oi_chg,
                'vol_oi': round(vol_now / oi_now, 2) if oi_now else None,
            }
    try:
        with open(OI_BASELINE_PATH, 'w', encoding='utf-8') as f:
            json.dump(baseline, f, ensure_ascii=False)
    except Exception as _e:
        print(f'[WARN] 写持仓基线失败: {_e}')
    return out


def compute_future_signals(quotes_by_code, kb_map, oi_info=None):
    """
    期货端信号(基于当日数据):
      1. 涨跌幅异常 (|pct| ≥ 3%) + 量价仓验证(增仓/减仓 + 活跃度)
      2. 大波动关注 (1.5% ≤ |pct| < 3%) + 量价仓验证
      3. 持仓资金沉淀 (OI × 价格 × 乘数 × 保证金)
    每条信号附带证据字段: oi / volume / oi_chg(日增仓) / vol_oi(量仓比)
    / sector(板块) / driver(品种核心驱动,来自知识库) / fund_yi(资金沉淀亿元)
    返回: [ {'kind':.., 'code':.., 'reason':.., ...证据字段} ]
    """
    oi_info = oi_info or {}
    flat = []
    for cat, lst in quotes_by_code.items():
        for q in lst:
            if q.get('paused'):
                continue
            try:
                change = float(q.get('change') or 0)
                pct = float(q.get('pct') or 0)
                last = float(q.get('last') or 0)
                prev_close = float(q.get('prev_close') or 0)
            except (ValueError, TypeError):
                continue
            if last <= 0 or prev_close <= 0:
                continue
            info = oi_info.get(q['code'], {})
            try:
                oi = float(q.get('oi', '0'))
                vol = float(q.get('volume', '0'))
            except (ValueError, TypeError):
                oi, vol = 0.0, 0.0
            kb = kb_map.get(q['code'], {})
            flat.append({'cat': cat, 'code': q['code'], 'cn_name': q.get('cn_name', q.get('code')),
                         'last': last, 'pct': pct, 'change': change,
                         'contract': q.get('contract', ''), 'contract_full': q.get('contract_full', ''),
                         'oi': oi, 'volume': vol,
                         'oi_chg': info.get('oi_chg'),
                         'vol_oi': info.get('vol_oi'),
                         'driver': '、'.join((kb.get('drivers') or [])[:2]),
                         'base': kb.get('base', '')})

    def vol_pose_txt(q):
        """量价仓组合描述(涨跌 × 增减仓),作为信号依据"""
        parts = []
        if q.get('oi_chg') is not None:
            chg = q['oi_chg']
            if abs(chg) >= 1000:
                w = chg >= 0
                parts.append(f"{'增' if w else '减'}仓 {abs(chg)/10000:.1f}万手"
                             if abs(chg) >= 10000 else f"{'增' if w else '减'}仓 {abs(chg):.0f}手")
            else:
                parts.append('仓差持平')
        elif q.get('vol_oi') is not None:
            parts.append(f"量/仓 {q['vol_oi']:.2f}")
        return '、'.join(parts)

    signals = []
    # 1. 涨跌幅异常
    extreme = [q for q in flat if abs(q['pct']) >= 3]
    for q in extreme:
        kind = 'extreme_up' if q['pct'] > 0 else 'extreme_down'
        vp = vol_pose_txt(q)
        reason = f"涨跌幅 {q['pct']:+.2f}% 触发 ±3% 异动" + (f",{vp}" if vp else '')
        signals.append({
            'kind': kind, 'code': q['code'], 'cn': q['cn_name'],
                'contract': q.get('contract', ''), 'contract_full': q.get('contract_full', ''),
            'last': q['last'], 'pct': q['pct'], 'change': q['change'],
            'oi': q['oi'], 'volume': q['volume'], 'oi_chg': q['oi_chg'],
            'vol_oi': q['vol_oi'], 'sector': q['cat'], 'driver': q['driver'],
            'reason': reason,
            'priority': 1,
        })
    # 2. 大波动关注(1.5-3%):中等波动
    medium = [q for q in flat if 1.5 <= abs(q['pct']) < 3 and q not in extreme]
    for q in medium[:12]:
        kind = 'medium_up' if q['pct'] > 0 else 'medium_down'
        vp = vol_pose_txt(q)
        reason = f"涨跌 {q['pct']:+.2f}%" + (f",{vp}" if vp else '') + ",关注持仓/外盘联动"
        signals.append({
            'kind': kind, 'code': q['code'], 'cn': q['cn_name'],
                'contract': q.get('contract', ''), 'contract_full': q.get('contract_full', ''),
            'last': q['last'], 'pct': q['pct'], 'change': q['change'],
            'oi': q['oi'], 'volume': q['volume'], 'oi_chg': q['oi_chg'],
            'vol_oi': q['vol_oi'], 'sector': q['cat'], 'driver': q['driver'],
            'reason': reason,
            'priority': 2,
        })

    # 3. 按 OI 异常(资金沉注定向) — 用 OI × last × mult × margin 估算
    mult_map = {code: spec['multiplier'] for code, spec in CONTRACT_SPECS.items()}
    mg_map = {code: spec['margin'] for code, spec in CONTRACT_SPECS.items()}
    cny_map = []
    for q in flat:
        m = mult_map.get(q['code']) or 10
        mg = mg_map.get(q['code']) or 0.10
        if q['oi']:
            fund = q['last'] * q['oi'] * m * mg
            cny_map.append((q, fund))
    cny_map.sort(key=lambda x: x[1], reverse=True)
    for q, fund in cny_map[:5]:
        if fund > 1e9:
            signals.append({
                'kind': 'big_position', 'code': q['code'], 'cn': q['cn_name'],
                'contract': q.get('contract', ''), 'contract_full': q.get('contract_full', ''),
                'last': q['last'], 'pct': q['pct'], 'change': q['change'],
                'oi': q['oi'], 'volume': q['volume'], 'oi_chg': q['oi_chg'],
                'vol_oi': q['vol_oi'], 'sector': q['cat'], 'driver': q['driver'],
                'fund_yi': round(fund / 1e8, 1),
                'reason': f"持仓资金 ≈ {fund/1e8:.1f}亿元,板块头部品种,关注资金流向",
                'priority': 3,
            })

    # 按 priority + |pct| 排序
    signals.sort(key=lambda s: (s['priority'], -abs(s.get('pct', 0))))
    return signals[:30]


def build_paris_asset_summary(quotes_by_code, kb_map):
    """指数级别 A 股指数商品期货 + 海外要闻"""
    return {}


def parse_commodity(code, cn_name, name, parts):
    """商品期货解析 (parts len 44)
    新浪 nf_ 字段: 0名称 1时间 2开 3高 4低 5昨收 6买价 7卖价 8最新价
    9结算价 10昨结算 11买量 12卖量 13持仓量 14成交量 15交易所 16品种 17日期
    (字段含义已用日K接口交叉核对: 持仓/成交量与 InnerFuturesNewService 完全一致)"""
    if not parts or len(parts) < 15:
        return None
    try:
        last = float(parts[8] or 0)
        prev_settle = float(parts[10] or 0)
        if not last:
            # 部分换月期/不活跃合约新浪最新价为空(如硅铁SF0),
            # 用当日结算价兜底(交易所真实发布值), 否则品种会从行情表消失
            last = float(parts[9] or 0)
        if not last:
            return None
        change = last - prev_settle if prev_settle else 0
        pct = (change / prev_settle * 100) if prev_settle else 0
        return {
            'code': code, 'cn_name': cn_name, 'name': name,
            'open': f'{float(parts[2] or 0):.3f}',
            'prev_close': f'{prev_settle:.3f}',
            'high': f'{float(parts[3] or 0):.3f}',
            'low': f'{float(parts[4] or 0):.3f}',
            'bid': f'{float(parts[6] or 0):.3f}',
            'ask': f'{float(parts[7] or 0):.3f}',
            'last': f'{last:.3f}',
            'change': f'{change:.3f}',
            'pct': f'{pct:.2f}',
            'volume': f'{float(parts[14] or 0):.0f}',
            'oi': f'{float(parts[13] or 0):.0f}',
            'date': parts[17] if len(parts) > 17 else '',
            'time': parts[1] if len(parts) > 1 else '',
            'paused': False,
        }
    except (ValueError, IndexError):
        return None


def parse_index(code, cn_name, name, parts):
    """股指/国债期货解析 (parts len 50)
    新浪字段(已用日K接口交叉核对 IM0/T0): 0开 1高 2低 3最新 4成交量 5成交额
    6持仓量 9涨停 10跌停 13昨收 14昨结算 15昨日持仓 (旧版把 昨收=最高/量/仓 全用反了)"""
    if not parts or len(parts) < 15:
        return None
    try:
        open_p = float(parts[0] or 0)
        high = float(parts[1] or 0)
        low = float(parts[2] or 0)
        last = float(parts[3] or 0)
        volume = float(parts[4] or 0)
        turnover = float(parts[5] or 0)
        oi = float(parts[6] or 0)
        prev_settle = float(parts[14] or 0)
        prev_close = float(parts[13] or 0)
        if not last:
            return None
        base = prev_settle or prev_close
        change = last - base if base else 0
        pct = (change / base * 100) if base else 0
        time_str = parts[37] if len(parts) > 37 else ''
        return {
            'code': code, 'cn_name': cn_name, 'name': name,
            'open': f'{open_p:.3f}',
            'prev_close': f'{base:.3f}',
            'high': f'{high:.3f}',
            'low': f'{low:.3f}',
            'last': f'{last:.3f}',
            'change': f'{change:.3f}',
            'pct': f'{pct:.2f}',
            'volume': f'{volume:.0f}',
            'turnover': f'{turnover:.3f}',
            'oi': f'{oi:.0f}',
            'date': parts[36] if len(parts) > 36 else '',
            'time': time_str.replace(':', '') if time_str else '',
        }
    except (ValueError, IndexError):
        return None


def parse_overseas(code, cn_name, name, parts):
    """外盘解析(hf_/int_)"""
    if not parts:
        return None
    try:
        if code.startswith('hf_'):
            if len(parts) < 9:
                return None
            last = float(parts[0] or 0)
            open_p = float(parts[2] or 0)
            high = float(parts[3] or 0)
            low = float(parts[5] or 0)
            prev = float(parts[8] or 0)
            change = last - prev if prev else 0
            pct = (change / prev * 100) if prev else 0
            date = parts[12] if len(parts) > 12 else ''
            time_str = parts[6] if len(parts) > 6 else ''
        else:
            if len(parts) < 4:
                return None
            last = float(parts[1] or 0)
            change = float(parts[2] or 0)
            pct = float(parts[3] or 0)
            prev = last - change
            open_p = high = low = last
            date = ''
            time_str = ''
        if not last:
            return None
        return {
            'code': code, 'cn_name': cn_name, 'name': name,
            'open': f'{open_p:.3f}', 'prev_close': f'{prev:.3f}',
            'high': f'{high:.3f}', 'low': f'{low:.3f}',
            'last': f'{last:.3f}', 'change': f'{change:.3f}',
            'pct': f'{pct:.2f}',
            'date': date, 'time': time_str,
        }
    except (ValueError, IndexError):
        return None


def parse_astock_index(code, cn_name, parts):
    """A 股指数解析 (s_sh/s_sz 格式, len 6):
    [0]=name [1]=now [2]=change [3]=pct [4]=volume [5]=amount"""
    if not parts or len(parts) < 4:
        return None
    try:
        name = parts[0]
        now = float(parts[1] or 0)
        change = float(parts[2] or 0)
        pct = float(parts[3] or 0)
        prev_close = now - change if change else now
        if not now:
            return None
        volume = float(parts[4] or 0) if len(parts) > 4 else 0
        return {
            'code': code, 'cn_name': cn_name, 'name': cn_name,
            'open': f'{prev_close:.2f}',   # sina 指数接口不给 open, 用昨收占位
            'prev_close': f'{prev_close:.2f}',
            'last': f'{now:.2f}',
            'change': f'{change:.2f}',
            'pct': f'{pct:.2f}',
            'volume': f'{volume:.0f}',
            'date': '', 'time': '',
        }
    except (ValueError, IndexError):
        return None


# ============================================================
# 5) 主流程
# ============================================================
def normalize_dates(result):
    from collections import Counter
    from datetime import datetime, timedelta, date
    dates = []
    for cat, lst in result['categories'].items():
        for q in lst:
            d = q.get('date', '')
            if d and len(d) == 10:
                dates.append(d)
    if not dates:
        return
    counter = Counter(dates)
    sorted_dates = sorted(counter.keys())
    if len(sorted_dates) < 2:
        return
    main_date_str = counter.most_common(1)[0][0]
    main_date = datetime.strptime(main_date_str, '%Y-%m-%d')
    target_str = (main_date + timedelta(days=1)).strftime('%Y-%m-%d')
    if counter[target_str] > 0:
        for cat, lst in result['categories'].items():
            for q in lst:
                if q.get('date') == target_str:
                    q['date'] = main_date_str


def derive_trading_day(result):
    from collections import Counter
    dates = []
    for cat, lst in result['categories'].items():
        for q in lst:
            d = q.get('date', '')
            if d and len(d) == 10:
                dates.append(d)
    if not dates:
        return ''
    counter = Counter(dates)
    return counter.most_common(1)[0][0]


def build_variety_kb_map():
    """把 VARIETY_KB 转成 code→对象 的映射,方便 query"""
    out = {}
    for code, info in VARIETY_KB.items():
        if not info or not info.get('cn'):
            continue
        out[code] = info
    return out


def filter_upcoming_calendar():
    """过滤未来 14 天内的重要数据"""
    now = datetime.now()
    horizon = now + timedelta(days=14)
    out = []
    for e in KEY_CALENDAR_2026:
        try:
            dt = datetime.strptime(e['date'], '%Y-%m-%d')
            if now.date() <= dt.date() <= horizon.date():
                out.append(e)
        except Exception:
            continue
    out.sort(key=lambda x: (x['date'], x['time']))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--news-only', action='store_true')
    args = parser.parse_args()

    _t0 = time.time()

    def _lap(label):
        print('[LAP] %-12s %5.1fs' % (label, time.time() - _t0))

    def _tech_allow_stale():
        """
        日K每天只需补齐一次。实测新浪当日日K在 15:00 收盘后并不会立刻生成,
        若严格要求"缓存日期 = 最新交易日", 盘中每次刷新都会全量重抓 66 个品种(约 33 秒),
        这是刷新频率提不上去的主要瓶颈。

        规则: 只在收盘后 16:00-17:00 这个窗口强制重抓一次(补齐当日日K),
              其余时间(日盘/夜盘/凌晨/周末)一律复用已有缓存。
              缓存缺失时该参数无效, 仍会正常抓取。
        """
        try:
            _hm = datetime.now().hour * 60 + datetime.now().minute
            return not (16 * 60 <= _hm <= 17 * 60)
        except Exception:
            return False

    # 1) 抓行情
    print('[INFO] 抓行情...')
    raw = fetch_sina_quotes()
    if not raw:
        print('[FATAL] 行情抓取失败', file=sys.stderr)
        sys.exit(1)

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    result = {
        'updated_at': now_str,
        'source': 'sina',
        'categories': {},
    }

    _lap('行情')
    # ---- 主力合约解析(标注具体合约月份, 如 RB2701) ----
    try:
        import main_contract
        main_map = main_contract.resolve_main_contracts()
    except Exception as e:
        print(f'[WARN] main_contract 解析失败(退化为连续合约): {e}')
        main_map = {}

    def attach_main(q, code):
        """给行情条目挂上主力合约信息, 并用主力合约真实价格覆盖连续合约拼接价"""
        mc = main_map.get(code)
        if not mc:
            q['contract'] = ''
            q['contract_full'] = ''
            return q
        q['contract'] = mc['month']
        q['contract_full'] = mc['contract']
        try:
            if float(mc['last']) > 0:
                q['last'] = mc['last']
                q['pct'] = mc['pct']
                q['change'] = mc['change']
                q['oi'] = mc['oi']
                q['volume'] = mc['vol']
                q['prev_close'] = mc['prev_settle']
        except (ValueError, TypeError):
            pass
        return q

    for cat, lst in COMMODITY_CODES.items():
        result['categories'][cat] = []
        for code, cn, n in lst:
            if code in raw:
                q = parse_commodity(code, cn, n, raw[code])
                if q:
                    result['categories'][cat].append(attach_main(q, code))
        print(f'[OK] {cat}: {len(result["categories"][cat])}/{len(lst)}')

    for cat, lst in INDEX_CODES.items():
        result['categories'][cat] = []
        for code, cn, n in lst:
            if code in raw:
                q = parse_index(code, cn, n, raw[code])
                if q:
                    result['categories'][cat].append(attach_main(q, code))
        print(f'[OK] {cat}: {len(result["categories"][cat])}/{len(lst)}')

    for cat, lst in OVERSEAS_CODES.items():
        result['categories'][cat] = []
        for code, cn, n in lst:
            if code in raw:
                q = parse_overseas(code, cn, n, raw[code])
                if q:
                    result['categories'][cat].append(q)
        print(f'[OK] {cat}: {len(result["categories"][cat])}/{len(lst)}')

    # A 股指数
    result['astock_indices'] = []
    for code, cn in ASTOCK_INDICES:
        if code in raw:
            q = parse_astock_index(code, cn, raw[code])
            if q:
                result['astock_indices'].append(q)
    print(f'[OK] astock_indices: {len(result["astock_indices"])}/{len(ASTOCK_INDICES)}')

    # 归一化日期
    normalize_dates(result)
    trading_day = derive_trading_day(result)
    result['trading_day'] = trading_day
    result['trading_day_cn'] = (
        datetime.strptime(trading_day, '%Y-%m-%d').strftime('%m月%d日')
        if trading_day else ''
    )

    _lap('主力合约')
    # 2) 抓新闻(股市/宏观/全球 + 新增:商品期货)
    # 先读旧 quotes.json,保留多源(tdx/wind/dzh)合并字段,避免自动刷新覆盖
    _preserved_multi_source = {'tdx': [], 'wind': [], 'dzh': []}
    try:
        _existing_path = DATA_DIR / 'quotes.json'
        if os.path.exists(_existing_path):
            with open(_existing_path, 'r', encoding='utf-8') as _ef:
                _existing = json.load(_ef)
                _existing_news = (_existing.get('news') or {})
                for _src in ('tdx', 'wind', 'dzh'):
                    _items = _existing_news.get(_src)
                    if isinstance(_items, list) and _items:
                        _preserved_multi_source[_src] = _items
            if any(_preserved_multi_source.values()):
                print(f'[INFO] 保留多源新闻: tdx={len(_preserved_multi_source["tdx"])} '
                      f'wind={len(_preserved_multi_source["wind"])} '
                      f'dzh={len(_preserved_multi_source["dzh"])}')
    except Exception as _e:
        print(f'[WARN] 读旧 quotes.json 保留多源失败: {_e}')

    print('[INFO] 抓 7×24 新闻(股市/宏观/全球/期货)...')
    result['news'] = {
        'astock':   fetch_news(2516, num=20),  # 股市动态
        'macro':    fetch_news(2517, num=15),  # 宏观财经
        'global':   fetch_news(2509, num=15),  # 全球市场
        'futures':  fetch_news(2518, num=25),  # 商品期货(本次新增)
    }
    # 合并手动拉取的多源新闻(AI 通过 tdx/wind/dzh MCP 拉的)
    for _src in ('tdx', 'wind', 'dzh'):
        if _preserved_multi_source[_src]:
            result['news'][_src] = _preserved_multi_source[_src]
    print(f'[OK] news: astock={len(result["news"]["astock"])}, '
          f'macro={len(result["news"]["macro"])}, '
          f'global={len(result["news"]["global"])}, '
          f'futures={len(result["news"]["futures"])}, '
          f'tdx={len(result["news"].get("tdx", []))}, '
          f'wind={len(result["news"].get("wind", []))}, '
          f'dzh={len(result["news"].get("dzh", []))}')

    # 3) 品种知识库(静态 AI 推送模板)
    result['variety_kb'] = build_variety_kb_map()
    print(f'[OK] variety_kb: {len(result["variety_kb"])} 个品种有深度分析')

    # 4) 重要数据日历(未来 14 天)
    result['calendar'] = filter_upcoming_calendar()
    print(f'[OK] calendar: 未来 14 天 {len(result["calendar"])} 项重要数据')

    # 5) 品种合约规格 + 主力到期 + 1手起提醒 (V2.6 新增)
    result['contract_specs'] = CONTRACT_SPECS
    print(f'[OK] contract_specs: {len(CONTRACT_SPECS)} 个品种规格')

    result['expiry_alerts'] = compute_main_contract_expiry_alerts(main_map)
    print(f'[OK] expiry_alerts: {len(result["expiry_alerts"])} 项主力合约到期提醒')

    result['lot_round_alerts'] = compute_lot_round_alerts()
    print(f'[OK] lot_round_alerts: {len(result["lot_round_alerts"])} 项取整/1手起提示')

    _lap('新闻+日历')
    # 6) 期货端信号(基于当日数据自动派生,附量价仓证据)
    _oi_info = update_oi_baseline(result['categories'])
    result['future_signals'] = compute_future_signals(result['categories'], result['variety_kb'], _oi_info)
    print(f'[OK] future_signals: {len(result["future_signals"])} 个信号(含仓差/量仓比证据)')

    _lap('信号')
    # 6.5) 技术指标 + 历史分位 (V3.0 新增)
    #      日K按交易日缓存, 同一交易日不重复拉取(全品种一次约 30MB)
    try:
        import tech_indicators
        _sym_map = {}
        for _cat, _lst in COMMODITY_CODES.items():
            for _c, _cn, _n in _lst:
                _sym_map[_c] = {'symbol': _c, 'cn': _cn}
        for _cat, _lst in INDEX_CODES.items():
            for _c, _cn, _n in _lst:
                _sym_map[_c] = {'symbol': _c, 'cn': _cn}
        # V4.6: 收集实时行情, 供技术指标合并"当日临时K线"(盘中实时化)
        _live_map = {}
        for _cat, _lst in (result.get('categories') or {}).items():
            if _cat == 'overseas':
                continue        # 外盘无日K对齐, 不参与
            for _q in (_lst or []):
                _cd = _q.get('code')
                if not _cd:
                    continue
                try:
                    _last = float(_q.get('last') or 0)
                except Exception:
                    _last = 0
                if _last <= 0 or _q.get('paused'):
                    continue
                _live_map[_cd] = {
                    'date': _q.get('date'), 'last': _last,
                    'open': _q.get('open'), 'high': _q.get('high'),
                    'low': _q.get('low'), 'volume': _q.get('volume'),
                    'oi': _q.get('oi'),
                }
        result['tech'] = tech_indicators.build_tech_map(
            _sym_map, result.get('trading_day', ''), allow_stale=_tech_allow_stale(),
            live_map=_live_map)
        _n_live = sum(1 for _v in result['tech'].values() if _v.get('live'))
        result['tech_live_n'] = _n_live
        result['tech_updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
        result['tech_note'] = ('技术指标由本工具基于新浪主连日K自算(MA/MACD/RSI/KDJ/BOLL/ATR/量比),'
                               '历史分位 = 当前价在最近N根收盘价中的百分位;'
                               + (f'其中 {_n_live} 个品种已合并当日盘中实时行情(指标随盘面变化);' if _n_live else '')
                               + '主连换月存在跳空,长周期(3年/5年)分位仅供方向参考。')
    except Exception as _e:
        print(f'[WARN] tech_indicators 失败: {_e}')
        result['tech'] = {}

    _lap('技术面')
    # 6.6) 基差(现货 vs 期货主力) + 近一年基差分位 (V3.1 新增)
    #      数据源: 生意社现期表(每日全品种基差表), 历史按交易日缓存于 data/basis_days.json
    try:
        import basis
        result['basis'] = basis.build_basis_data(result.get('trading_day', ''))
        result['basis_note'] = ('基差 = 现货价格(生意社基准价口径) - 期货主力价格;'
                                '180日区间为生意社统计; 近一年分位由本工具按回补的交易日序列计算,'
                                '样本随时间累积变厚。')
    except Exception as _e:
        print(f'[WARN] basis 失败: {_e}')
        result['basis'] = {}

    _lap('基差')
    # 6.7) 企业套保决策(期货 vs 期权 / 现在套不套) (V3.2 新增)
    #      依据: 价格分位(tech) + 基差分位(basis) + 波动率 + 临近事件
    try:
        import hedge_advisor
        _cn_fb = {}
        for _cat, _lst in result['categories'].items():
            for _q in _lst:
                _cn_fb[_q['code']] = _q.get('cn_name', _q['code'])
        result['hedge'] = hedge_advisor.build_hedge_advice(
            result.get('tech') or {}, (result.get('basis') or {}).get('items') or {},
            result.get('calendar') or [], cn_fallback=_cn_fb)
        print(f'[OK] hedge: {len(result["hedge"])} 个品种套保决策')
    except Exception as _e:
        print(f'[WARN] hedge_advisor 失败: {_e}')
        result['hedge'] = {}

    _lap('套保')
    # 6.8) 期权到期日提醒 (V3.3 新增)
    #      按各交易所到期日规则推算; 已用期货公司公布的到期通知逐条核对
    try:
        import option_expiry
        result['option_expiry'] = option_expiry.build_option_expiry(verbose=False)
        print(f'[OK] option_expiry: {len(result["option_expiry"].get("items", []))} 项, '
              f'规则核对 {"通过" if result["option_expiry"].get("verified") else "未通过"}')
    except Exception as _e:
        print(f'[WARN] option_expiry 失败: {_e}')
        result['option_expiry'] = {}

    _lap('期权到期')
    # 6.9) 持仓龙虎榜 (会员成交持仓排名, 交易所官网公开文件)
    #      明细数据量大(约 340KB), 单独写 data/rank.json 由前端按需加载, 不并入主 quotes.json
    try:
        import rank
        _rk = rank.build(verbose=False)
        result['rank_meta'] = {
            'date': _rk.get('date'),
            'count': len(_rk.get('items', [])),
            'sources': _rk.get('sources', {}),
            'dce': _rk.get('dce'),
            'file': 'data/rank.json',
        }
        print(f'[OK] rank: {len(_rk.get("items", []))} 品种, '
              f'日期 {_rk.get("date")}, 源 {_rk.get("sources")}')
    except Exception as _e:
        print(f'[WARN] rank 失败: {_e}')
        result['rank_meta'] = {}

    _lap('龙虎榜')
    # 6.10) 盘后复盘「涨跌归因」(基本面 + 消息面 + 板块共振 + 接下来关注)
    #      消息源: 新浪 4 路财经流 + 新浪 7×24 快讯 + 和讯期货要闻
    try:
        import review
        _news = review.gather_news()
        result['review'] = review.build(
            result.get('categories') or {}, result.get('tech') or {},
            result.get('basis') or {}, result.get('calendar') or [], _news, verbose=True)
        _r = result['review']
        print(f'[OK] review: 归因 {len(_r.get("movers", []))} 品种 / '
              f'共振板块 {len(_r.get("sector", []))} / 传闻 {len(_r.get("rumors", []))} 条')
    except Exception as _e:
        print(f'[WARN] review 失败: {_e}')
        result['review'] = {}

    _lap('复盘归因')
    # 6.11) 品种产业基本面库(64 品种: base/drivers/focus/tags)
    #      供前端品种档案(renderVarietyKB)在知识库未覆盖时填充详情, 避免"通用框架"空白
    try:
        import review as _rv
        result['variety_fundamentals'] = _rv.FUNDAMENTALS
    except Exception as _e:
        print(f'[WARN] variety_fundamentals 失败: {_e}')

    _lap('基本面库')
    # 6.12) 期限结构与展期收益(主力 vs 次主力价差 -> 年化斜率 -> 多头/空头展期收益)
    #       数据质量: 远月流动性薄(thin) / 近月临近交割(near, 自然人吃不到完整展期) 均打标
    try:
        import term_structure
        result['term'] = term_structure.load(verbose=False)
        print(f'[OK] term: 期限结构 {len(result["term"])} 品种')
    except Exception as _e:
        print(f'[WARN] term_structure 失败: {_e}')
        result['term'] = {}

    _lap('期限结构')
    # 7) 写文件(原子写: 先写临时文件再 rename, 避免 15 分钟刷新瞬间读到半截 JSON 导致页面打不开)
    out_path = DATA_DIR / 'quotes.json'
    tmp_path = DATA_DIR / 'quotes.json.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, out_path)

    total = sum(len(v) for v in result['categories'].values())
    print(f'[DONE] saved → {out_path}')
    print(f'       total {total} instruments + '
          f'{len(result["astock_indices"])} A股指数 + '
          f'{sum(len(v) for v in result["news"].values())} news + '
          f'{len(result["calendar"])} events')


if __name__ == '__main__':
    main()
