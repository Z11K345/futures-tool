# -*- coding: utf-8 -*-
"""
review.py — 盘后复盘「涨跌归因」模块
=====================================
回答三个问题:
  1. 今天哪些品种动了？(涨跌幅前列)
  2. 为什么动？(基本面 + 消息面 + 板块共振)
  3. 接下来看什么？(待跟踪事件 / 数据 / 日历)

数据源:
  - 行情: 主 quotes.json (categories)
  - 技术面: tech (均线/MACD/RSI/布林/ATR/价格分位)
  - 基差: basis (现货/期货/基差/历史分位)
  - 消息: 新浪 4 路财经流 + 新浪 7x24 快讯 + 和讯期货要闻
  - 日历: calendar (交易所/宏观事件)

合规:
  - 标注为「市场传闻/未证实」的条目仅作提示, 不作为结论
  - 全部内容基于公开信息自动生成, 不构成投资建议
"""
import re
import json
import time
import urllib.request
import ssl
from datetime import datetime

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'

# ============================================================
# 一、品种基本面库
#   base    : 产业格局一句话(解释"这个品种由什么决定")
#   drivers : 核心驱动因子(3~5 条, 按影响力排序)
#   focus   : 接下来要盯的指标/事件
#   tags    : 消息关键词(用于新闻匹配)
# ============================================================
FUNDAMENTALS = {
    # ---------- 有色 ----------
    'CU0': dict(base='全球定价的工业金属,中国占精炼消费约一半;电网/新能源/地产竣工是主要需求端,矿端扰动与冶炼TC决定供给弹性。',
                drivers=['美元指数与实际利率(反向)', 'LME/上期所库存变化', '智利秘鲁矿端罢工与品位下降', '中国电网投资与新能源装机', '全球制造业PMI'],
                focus=['LME铜库存周变化', '洋山铜溢价与到岸升水', '冶炼加工费TC/RC', '国内社库与保税区库存'],
                tags=['铜', 'LME铜', '沪铜', '智利铜矿', '电网投资', 'Copper']),
    'AL0': dict(base='产能受4500万吨天花板约束,供给刚性;成本端看氧化铝+电力(云南水电季节性),需求看地产与光伏/新能源车。',
                drivers=['氧化铝价格(成本端)', '云南水电丰枯期(复产/减产)', '地产竣工与光伏边框需求', '电解铝社会库存', '海外能源成本'],
                focus=['氧化铝现货价', '云南来水与复产进度', '铝锭社会库存周度', '铝棒加工费'],
                tags=['电解铝', '沪铝', '氧化铝', '云南水电', '铝锭库存', '铝']),
    'ZN0': dict(base='矿端与冶炼博弈明显,加工费TC是核心变量;需求偏地产基建与镀锌板,库存低位时弹性大。',
                drivers=['锌精矿加工费TC', '冶炼开工与检修', '地产基建需求', 'LME锌库存与升水', '美元与实际利率'],
                focus=['国产/进口锌精矿TC', '冶炼厂检修安排', '锌锭社会库存', 'LME锌库存'],
                tags=['锌', '沪锌', '锌精矿', '镀锌', 'LME锌']),
    'NI0': dict(base='印尼镍矿政策(配额/出口税)是供给端最大变量;需求由不锈钢与三元电池(硫酸镍)双轮驱动。',
                drivers=['印尼镍矿配额RKAB与出口政策', '菲律宾雨季出货', '不锈钢排产与利润', '新能源车三元装机(硫酸镍)', 'LME镍库存'],
                focus=['印尼RKAB审批进度', '镍铁/硫酸镍价格与价差', '不锈钢厂排产', 'LME与上期所库存'],
                tags=['镍', '沪镍', '印尼镍', '镍矿', '硫酸镍', '不锈钢']),
    'SN0': dict(base='供给高度集中(缅甸佤邦/印尼),需求看半导体焊料与光伏焊带;库存低、盘子小,消息面冲击放大。',
                drivers=['缅甸佤邦禁矿/复产政策', '印尼出口许可', '半导体景气(焊料需求)', '光伏焊带需求', '交易所库存'],
                focus=['缅甸佤邦政策动向', '印尼出口配额', '锡锭社会库存', '费城半导体指数'],
                tags=['锡', '沪锡', '缅甸', '佤邦', '印尼锡', '半导体']),
    'AU0': dict(base='避险与抗通胀双属性;实际利率(名义利率-通胀预期)是最核心定价锚,央行购金提供长期中枢支撑。',
                drivers=['实际利率(美10年期TIPS)', '美元指数', '地缘冲突避险', '央行购金节奏', 'ETF持仓与资金流'],
                focus=['美联储议息与点阵图', '美国CPI/PCE与就业', 'SPDR黄金ETF持仓', '地缘事件'],
                tags=['黄金', '金价', '美联储', '实际利率', '避险', '央行购金', 'gold']),
    'AG0': dict(base='兼具贵金属与工业属性,金融属性放大波动;光伏银浆需求提供工业端增量,金银比修复时弹性大于黄金。',
                drivers=['黄金走势与金银比', '光伏装机(银浆需求)', '实际利率与美元', '工业需求景气', '交易所库存'],
                focus=['金银比位置', '光伏装机与银浆耗量', '上期所/COMEX库存', '工业景气度'],
                tags=['白银', '银价', '金银比', '光伏银浆', 'silver']),
    'AO0': dict(base='电解铝的核心原料,价格受铝土矿(几内亚)供给与烧碱成本影响;几内亚矿端扰动是最大变量。',
                drivers=['几内亚铝土矿供给与发运', '烧碱与能源成本', '氧化铝厂检修/复产', '电解铝需求与开工', '进出口窗口'],
                focus=['几内亚发运与政策', '氧化铝厂开工率', '现货升贴水', '赤泥堆场环保检查'],
                tags=['氧化铝', '铝土矿', '几内亚', '烧碱']),
    'LC0': dict(base='新能源产业链最上游,价格由锂矿(澳洲/非洲/南美)供给与动力电池+储能需求共同决定,波动极大。',
                drivers=['锂矿/锂盐供给释放节奏', '新能源车产销与带电量', '储能装机增速', '正极厂排产与补库', '碳酸锂社会库存'],
                focus=['澳洲/非洲锂矿发运与成本线', '正极材料排产', '碳酸锂社会库存', '电池厂招标价'],
                tags=['碳酸锂', '锂矿', '锂盐', '新能源车', '储能', '正极', 'lithium']),
    # ---------- 黑色 ----------
    'I0': dict(base='全球定价+中国需求,四大矿山发运决定供给节奏;铁水日产是需求的直接映射,钢厂利润决定补库意愿。',
                drivers=['铁水日产量(需求核心)', '澳巴发运量与到港', '钢厂利润与检修', '粗钢平控/减产政策', '港口库存'],
                focus=['247家钢厂铁水日产', '45港到港量与疏港量', '港口库存变化', '钢厂盈利率'],
                tags=['铁矿石', '铁矿', '铁水', '钢厂', '澳巴发运', '粗钢']),
    'RB0': dict(base='地产与基建需求的直接映射;供给看电炉/高炉开工与利润,库存去化速度决定价格弹性。',
                drivers=['地产新开工与施工', '基建投资与专项债', '钢厂利润与开工率', '螺纹产量与表观消费', '社会库存去化'],
                focus=['螺纹周产量与表需', '社会库存与厂库', '电炉开工率', '建材成交(全国237家)'],
                tags=['螺纹钢', '螺纹', '建材', '地产', '基建', '钢厂']),
    'HC0': dict(base='需求端更偏制造业(汽车/家电/机械),出口占比高;与螺纹的价差反映制造业与地产的相对强弱。',
                drivers=['制造业PMI与出口订单', '汽车/家电排产', '热卷产量与库存', '出口报价与接单', '卷螺差'],
                focus=['热卷周产量与库存', '出口FOB报价', '卷螺差位置', '制造业PMI'],
                tags=['热卷', '热轧', '制造业', '出口', '汽车', '家电']),
    'J0': dict(base='焦炭是焦煤的下游、钢厂的上游,夹在中间;利润由焦煤成本与钢厂压价共同决定,提涨/提降轮次是价格节奏。',
                drivers=['焦煤成本(入炉煤)', '钢厂铁水与补库', '焦企开工与利润', '干熄焦置换与环保限产', '港口与钢厂库存'],
                focus=['焦企提涨/提降轮次', '吨焦利润', '钢厂焦炭库存可用天数', '焦企开工率'],
                tags=['焦炭', '焦企', '铁水', '吨焦利润']),
    'JM0': dict(base='供给端受安监与进口(蒙煤/澳煤)影响大,需求看焦企补库;低库存+安监扰动时弹性极强。',
                drivers=['煤矿安监与停产整顿', '蒙煤通关车数与澳煤进口', '焦企补库节奏', '钢厂利润传导', '矿山与港口库存'],
                focus=['蒙煤通关量', '矿山开工与安监检查', '焦企/钢厂炼焦煤库存', '产地现货竞拍价'],
                tags=['焦煤', '炼焦煤', '蒙煤', '澳煤', '安监', '煤矿']),
    'SS0': dict(base='不锈钢由镍铁/铬铁成本与下游需求(制品/设备)共同决定,与镍价高度联动但受自身供需节奏调节。',
                drivers=['镍铁与高碳铬铁成本', '钢厂排产与检修', '下游制品/设备需求', '社会库存与仓单', '进出口'],
                focus=['300系不锈钢排产', '镍铁成交价', '无锡/佛山社会库存', '钢厂利润'],
                tags=['不锈钢', '镍铁', '铬铁', '300系']),
    'SF0': dict(base='硅铁主产区在西北,成本看兰炭与电价;需求以钢厂合金招标为主,金属镁出口为辅。',
                drivers=['钢厂合金招标量与价', '兰炭与电力成本', '主产区开工率', '金属镁需求与出口', '环保限产'],
                focus=['钢厂招标定价', '主产区开工与库存', '兰炭价格', '金属镁出口'],
                tags=['硅铁', '合金', '兰炭', '金属镁']),
    'SM0': dict(base='锰硅成本看锰矿(南非/澳洲发运)与电价,需求几乎全部来自钢厂招标,供给弹性大。',
                drivers=['锰矿价格与发运', '钢厂招标量与定价', '主产区开工率', '电价与焦炭成本', '南北方供给差异'],
                focus=['钢厂招标情况', '锰矿港口库存与报价', '开工率与库存', '成本线'],
                tags=['锰硅', '硅锰', '锰矿', '合金']),
    # ---------- 能化 ----------
    'SC0': dict(base='全球定价之锚,OPEC+产量政策与地缘冲突是主导;库存(EIA/API)与需求预期决定中期方向。',
                drivers=['OPEC+产量政策与减产执行', '地缘冲突(中东/俄乌)', 'EIA/API原油库存', '全球需求预期(IEA/EIA月报)', '美元与风险偏好'],
                focus=['OPEC+会议与配额', 'EIA周度库存', '地缘事件进展', '月报需求预测调整'],
                tags=['原油', 'OPEC', 'EIA', 'API', '布伦特', 'WTI', '地缘', '中东', '霍尔木兹']),
    'FU0': dict(base='高硫燃料油,与原油高度联动;新加坡库存与船燃需求、以及发电需求(中东/南亚)决定自身价差。',
                drivers=['原油成本', '新加坡燃料油库存', '船燃加注需求', '中东/南亚发电需求', '炼厂开工与进料'],
                focus=['新加坡燃料油库存', '高低硫价差', '原油走势', '发电旺季需求'],
                tags=['燃料油', '燃油', '船燃', '新加坡库存', '高硫']),
    'LU0': dict(base='低硫燃料油,IMO限硫令后船燃主力;受原油、船运景气(BDI)与脱硫塔安装比例影响。',
                drivers=['原油成本', '船运景气度与BDI', '低硫燃料油产量与进口', '脱硫塔安装比例', '新加坡库存'],
                focus=['低硫燃料油裂解价差', 'BDI指数', '新加坡库存', '船燃加注量'],
                tags=['低硫燃料油', '低硫燃油', '船燃', 'BDI', '脱硫塔']),
    'BU0': dict(base='沥青需求看道路施工(基建/专项债),供给看炼厂开工与稀释沥青原料;季节性极强(旺季金九银十)。',
                drivers=['道路基建投资与专项债', '炼厂开工与排产', '稀释沥青原料成本', '天气与施工条件', '社会库存'],
                focus=['炼厂开工率与排产', '社会库存与厂库', '沥青加工利润', '天气与施工进度'],
                tags=['沥青', '道路', '基建', '稀释沥青', '施工']),
    'TA0': dict(base='PTA 是聚酯产业链核心,成本看PX,需求看聚酯(长丝/短纤/瓶片)开工与纺织出口。',
                drivers=['PX价格与加工费', 'PTA装置检修与投产', '聚酯开工率与库存', '纺织服装出口', '原油成本传导'],
                focus=['PTA加工费', '装置检修/重启计划', '聚酯开工与产销', '长丝库存天数'],
                tags=['PTA', 'PX', '聚酯', '长丝', '短纤', '瓶片', '纺织']),
    'PF0': dict(base='短纤是PTA/MEG的直接下游,需求看纺织服装内外销与无纺布;加工费是核心指标。',
                drivers=['PTA与MEG成本', '纺织服装内外销订单', '短纤加工费与开工', '无纺布/填充料需求', '纱线库存'],
                focus=['短纤加工费', '纱厂开工与订单', '成品库存天数', '原料成本'],
                tags=['短纤', '涤纶短纤', '纺织', '纱线']),
    'EG0': dict(base='乙二醇是聚酯原料,煤制与油制工艺并存,煤价与油价双成本;投产周期决定长期格局。',
                drivers=['煤制/油制成本与利润', '新装置投产节奏', '聚酯开工与需求', '港口库存', '进口到港量'],
                focus=['乙二醇港口库存', '煤制与油制利润', '装置检修与投产', '聚酯开工'],
                tags=['乙二醇', 'MEG', '煤制', '聚酯']),
    'MA0': dict(base='甲醇是能化与煤化工的桥梁;国内煤制为主、进口看伊朗,需求看MTO与甲醛/醋酸。',
                drivers=['煤价与天然气成本', '伊朗装置与进口到港', 'MTO装置开工与外采', '港口与内地库存', '传统下游需求'],
                focus=['港口库存与到港量', 'MTO开工率与利润', '伊朗装置运行', '内地-港口价差'],
                tags=['甲醇', 'MTO', '伊朗', '煤化工', '港口库存']),
    'PP0': dict(base='聚丙烯原料多元(油制/煤制/PDH),产能高速扩张压制价格中枢;需求看塑编、注塑与BOPP。',
                drivers=['原油/丙烷/煤成本', '新产能投放节奏', 'PDH利润与开工', '下游塑编/BOPP开工', '两油库存'],
                focus=['PP拉丝排产比例', 'PDH利润', '两油库存', '下游开工率'],
                tags=['聚丙烯', 'PP', 'PDH', '丙烷', '塑编', 'BOPP']),
    'L0': dict(base='聚乙烯(塑料),产能扩张+进口冲击;需求偏农膜与包装,季节性看农膜旺季。',
                drivers=['原油与乙烷成本', '新产能投放', '进口量与国际报价', '农膜/包装需求', '石化库存'],
                focus=['石化两油库存', '农膜旺季开工', '进口到港与报价', '检修计划'],
                tags=['塑料', '聚乙烯', 'LLDPE', '农膜', '包装']),
    'V0': dict(base='PVC 电石法为主,成本看电石与氯碱平衡;需求高度依赖地产(PVC管材/型材)。',
                drivers=['电石与乙烯成本', '氯碱平衡(烧碱联产)', '地产需求与管材开工', '装置检修', '社会库存'],
                focus=['电石价格与开工', '烧碱价格(氯碱平衡)', 'PVC社会库存', '型材/管材开工率'],
                tags=['PVC', '电石', '氯碱', '烧碱', '管材', '型材']),
    'EB0': dict(base='苯乙烯上游纯苯+乙烯,下游EPS/PS/ABS;装置集中度高,检修扰动对价格影响大。',
                drivers=['纯苯与乙烯成本', '装置检修与重启', '下游EPS/PS/ABS开工', '家电与建材需求', '港口库存'],
                focus=['苯乙烯港口库存', '装置检修动态', '纯苯价格', '下游开工与利润'],
                tags=['苯乙烯', '纯苯', 'EPS', 'ABS', 'PS']),
    'PG0': dict(base='LPG(液化气)与原油、中东CP价格联动;需求分燃烧(民用/餐饮)与化工(PDH丙烷脱氢)。',
                drivers=['沙特CP合同价', '原油与天然气', 'PDH装置需求与利润', '燃烧需求季节性(冬季旺)', '进口到港量'],
                focus=['沙特CP月度定价', 'PDH开工与利润', '港口库存', '气温与燃烧需求'],
                tags=['液化气', 'LPG', '丙烷', 'CP价格', 'PDH', '燃烧']),
    'SA0': dict(base='纯碱需求七成来自玻璃(平板+光伏),供给受天然碱新产能投放影响;与玻璃价格高度联动。',
                drivers=['玻璃产量与冷修/复产', '光伏玻璃投产', '纯碱新产能投放', '装置检修与开工', '库存与交割'],
                focus=['纯碱厂家库存', '装置检修与开工率', '玻璃产线冷修/点火', '光伏玻璃投产进度'],
                tags=['纯碱', '玻璃', '光伏玻璃', '天然碱', '冷修']),
    'FG0': dict(base='平板玻璃需求绑定地产竣工,供给由产线冷修/点火调节;深加工订单与库存是高频指标。',
                drivers=['地产竣工面积', '产线冷修与复产点火', '深加工订单天数', '纯碱与燃料成本', '厂家库存'],
                focus=['浮法玻璃日熔量', '厂家库存与社会库存', '深加工订单天数', '冷修/点火计划'],
                tags=['玻璃', '浮法', '地产竣工', '冷修', '深加工']),
    'UR0': dict(base='尿素需求以农业施肥为主(季节性),工业需求看三聚氰胺与板材;出口政策(法检/配额)影响大。',
                drivers=['农业施肥季节性', '出口政策与法检', '尿素装置开工与检修', '煤炭与天然气成本', '复合肥与工业需求'],
                focus=['尿素日产量与开工', '出口法检与配额', '农业备肥节奏', '企业库存'],
                tags=['尿素', '施肥', '出口法检', '复合肥', '三聚氰胺']),
    'RU0': dict(base='天然橡胶主产东南亚(泰国/印尼),供给看割胶季与天气,需求看轮胎(重卡/乘用车配套与替换)。',
                drivers=['东南亚天气与割胶', '轮胎开工与出口', '重卡与汽车产销', '青岛保税区库存', '合成胶替代'],
                focus=['青岛保税与一般贸易库存', '全钢胎/半钢胎开工率', '泰国原料价格', '产区天气'],
                tags=['橡胶', '天胶', '轮胎', '泰国', '全钢胎', '半钢胎']),
    'NR0': dict(base='20号胶是轮胎专用胶,与全乳胶RU存在替代;交割品为国际标准胶,更贴近轮胎厂实际用料。',
                drivers=['东南亚供给与天气', '轮胎厂开工与采购', '与RU价差', '保税区库存', '海外需求'],
                focus=['20号胶与RU价差', '保税区库存', '轮胎出口订单', '海外报价'],
                tags=['20号胶', 'NR', '轮胎', '标准胶']),
    'SP0': dict(base='纸浆100%依赖进口(针叶/阔叶),供给看海外浆厂发运与检修,需求看文化纸与生活用纸。',
                drivers=['海外浆厂报价与检修', '针叶/阔叶价差', '文化纸与生活用纸开工', '港口库存', '人民币汇率'],
                focus=['外盘报价(智利Arauco/加拿大)', '主要港口库存', '纸厂开工与利润', '阔叶新增产能'],
                tags=['纸浆', '针叶浆', '阔叶浆', '文化纸', '生活用纸', 'Arauco']),
    'SI0': dict(base='工业硅下游为多晶硅、有机硅与铝合金;丰水期(西南水电)与枯水期供给差异显著。',
                drivers=['西南丰枯水期与电价', '多晶硅需求与产量', '有机硅开工与需求', '硅厂开工与库存', '出口'],
                focus=['硅厂开工率(新疆/云南/四川)', '工业硅社会库存', '多晶硅排产', '丰水期电价'],
                tags=['工业硅', '金属硅', '多晶硅', '有机硅', '丰水期']),
    'PS0': dict(base='多晶硅是光伏主产业链上游,价格由光伏装机需求与产能投放节奏决定,政策(反内卷/收储)影响大。',
                drivers=['光伏装机与组件排产', '多晶硅产量与库存', '行业自律/收储政策', '硅片价格与开工', 'N型料占比'],
                focus=['多晶硅周产量与库存', '硅片排产与价格', '行业政策动向', '组件招标价'],
                tags=['多晶硅', '光伏', '硅片', '组件', '反内卷', '收储']),
    # ---------- 农产品 ----------
    'M0': dict(base='豆粕是大豆压榨副产品,成本看美豆(CBOT)与巴西升贴水,需求看生猪/禽类存栏与饲料配方。',
                drivers=['USDA供需报告与单产', '美豆产区天气', '巴西/阿根廷产量与出口', '大豆到港与压榨量', '生猪存栏与饲料需求'],
                focus=['USDA月度供需报告', '美豆优良率与天气', '港口大豆到港与库存', '豆粕库存与开机率'],
                tags=['豆粕', '大豆', 'USDA', '美豆', '压榨', '生猪', '饲料']),
    'Y0': dict(base='豆油与棕榈油/菜油互相替代,生物柴油政策(美国/印尼/巴西)提供边际需求增量。',
                drivers=['美豆与大豆压榨', '东南亚棕榈油产量', '生物柴油政策(RVO/B40)', '豆棕价差', '国内油脂库存'],
                focus=['豆油商业库存', '豆棕价差', '生柴政策与掺混利润', '压榨开机率'],
                tags=['豆油', '棕榈油', '生物柴油', '油脂', '生柴']),
    'P0': dict(base='棕榈油主产印尼马来,MPOB月度报告是核心;产量季节性、出口需求与生柴(B40/B50)决定方向。',
                drivers=['MPOB月度产量库存', '印尼出口政策与生柴掺混(B40/B50)', '印度与中国进口需求', '豆油/菜油替代价差', '马来令吉汇率'],
                focus=['MPOB月度报告', '印尼出口税与生柴政策', 'ITS/Amspec出口数据', '产地与国内库存'],
                tags=['棕榈油', 'MPOB', '印尼', '马来', 'B40', '生柴', '出口']),
    'OI0': dict(base='菜油供给看加拿大菜籽与进口压榨,需求受高价抑制与替代影响;储备轮换政策影响阶段性供应。',
                drivers=['加拿大菜籽产量与出口', '进口菜籽到港与压榨', '储备轮换与拍卖', '与豆油/棕油价差', '餐饮与包装需求'],
                focus=['加菜籽产量与报价', '菜籽到港与开机率', '菜油库存', '价差与替代'],
                tags=['菜油', '菜籽', '加拿大', '储备']),
    'RM0': dict(base='菜粕是水产饲料主要蛋白源,需求季节性明显(水产旺季);与豆粕价差决定替代比例。',
                drivers=['菜籽进口与压榨', '水产养殖旺季', '豆粕价格与替代价差', 'DDGS进口', '饲料配方调整'],
                focus=['菜粕库存与开机', '水产投苗与旺季', '豆菜粕价差', '进口颗粒粕'],
                tags=['菜粕', '水产', '菜籽', '豆菜粕价差', '饲料']),
    'A0': dict(base='国产大豆(非转基因),供给看国内种植面积与国储拍卖,需求看食品加工与豆制品。',
                drivers=['国产大豆种植面积与产量', '国储收购与拍卖', '食品加工需求', '进口大豆替代', '产区天气'],
                focus=['产区天气与产量预期', '国储拍卖成交', '农户售粮节奏', '销区走货'],
                tags=['豆一', '国产大豆', '国储', '大豆拍卖']),
    'B0': dict(base='进口大豆(转基因),定价锚是CBOT美豆+升贴水,受国际供需与贸易政策影响。',
                drivers=['CBOT美豆走势', '巴西/美国升贴水', '中美贸易与关税政策', '到港量与压榨利润', 'USDA报告'],
                focus=['美豆出口检验与销售', '巴西升贴水', '压榨利润', '到港节奏'],
                tags=['豆二', '进口大豆', 'CBOT', '升贴水', '关税']),
    'C0': dict(base='玉米供需看国内产量、进口配额与深加工/饲用需求;政策性拍卖与收储是关键扰动。',
                drivers=['国内产量与产区天气', '进口配额与到港', '深加工(淀粉/酒精)需求', '饲用替代(小麦/稻谷)', '政策拍卖与收储'],
                focus=['产区天气与新作长势', '深加工开工与库存', '北港与南港库存', '政策拍卖'],
                tags=['玉米', '深加工', '淀粉', '拍卖', '饲用替代']),
    'CS0': dict(base='玉米淀粉是玉米的深加工产物,价格由玉米成本与淀粉自身供需(造纸/食品/糖浆)共同决定。',
                drivers=['玉米原料成本', '淀粉加工利润与开机', '造纸与食品需求', '副产品(蛋白粉/纤维)价格', '行业库存'],
                focus=['淀粉开机率与库存', '加工利润', '玉米成本', '下游需求'],
                tags=['玉米淀粉', '淀粉', '深加工', '开机率']),
    'LH0': dict(base='生猪价格由能繁母猪存栏决定的产能周期主导,二次育肥与冻品分割放大短期波动。',
                drivers=['能繁母猪存栏(产能核心)', '二次育肥与压栏', '出栏体重与冻品分割', '季节性消费(腌腊/节假日)', '疫病与政策收储'],
                focus=['能繁母猪存栏与宰后均重', '二次育肥进出节奏', '屠宰量与冻品库容', '收储/放储政策'],
                tags=['生猪', '猪价', '能繁母猪', '二次育肥', '屠宰', '收储']),
    'JD0': dict(base='鸡蛋价格由在产蛋鸡存栏与补栏/淘汰节奏决定,季节性(中秋备货、春节)极强。',
                drivers=['在产蛋鸡存栏与淘鸡', '养殖利润与补栏', '季节性备货(中秋/春节)', '饲料(玉米豆粕)成本', '蔬菜与替代品价格'],
                focus=['淘鸡量与鸡龄结构', '产区库存与走货', '养殖利润', '节前备货节奏'],
                tags=['鸡蛋', '蛋价', '淘鸡', '存栏', '备货']),
    'SR0': dict(base='白糖全球定价,巴西(甘蔗制糖比/乙醇)是最大变量,国内受配额与进口政策保护。',
                drivers=['巴西甘蔗产量与制糖比', '印度出口政策', '泰国产量', '国内库存与进口配额', '替代品(淀粉糖)'],
                focus=['UNICA双周压榨报告', '巴西醇糖比', '进口配额与到港', '国内工业库存'],
                tags=['白糖', '甘蔗', '巴西', 'UNICA', '印度', '制糖比']),
    'CF0': dict(base='棉花供给看新疆与美国,需求看纺织服装内外销;收抛储政策与进口配额是关键调节手段。',
                drivers=['新疆天气与产量', '美棉出口与USDA报告', '纺织内销与外需订单', '收抛储与进口配额', '纱线库存与开机'],
                focus=['USDA供需与美棉出口', '新疆天气与采收', '纱厂开机与库存', '抛储成交'],
                tags=['棉花', '棉价', '新疆棉', '美棉', '纺织', '抛储']),
    'CY0': dict(base='棉纱是棉花的直接下游,加工费与纱厂开机是核心;内外棉价差决定进口纱冲击。',
                drivers=['棉花成本', '纱厂开机与库存', '内外棉价差与进口纱', '织造订单', '替代品(化纤)'],
                focus=['纱厂开机率与库存', '纱线加工利润', '织造订单天数', '进口纱到港'],
                tags=['棉纱', '纱线', '织造', '进口纱']),
    'AP0': dict(base='苹果是典型生鲜品,供给看产区天气(倒春寒/冰雹)与优果率,需求看替代水果与走货。',
                drivers=['产区天气与优果率', '冷库库存与出库', '替代水果价格', '节日消费', '交割品与仓单'],
                focus=['冷库库存与出库量', '产区天气', '批发市场价格', '交割标准与仓单'],
                tags=['苹果', '优果率', '冷库', '产区天气']),
    'CJ0': dict(base='红枣主产新疆,供给看花期天气与产量,需求偏节日消费与加工;库存与仓单是交割关键。',
                drivers=['新疆产区天气与产量', '库存与仓单', '节日消费与加工需求', '替代品', '交割标准'],
                focus=['产区天气与坐果', '库存与仓单量', '销区走货', '新季开秤预期'],
                tags=['红枣', '新疆', '天气', '仓单']),
    'PK0': dict(base='花生主产河南山东,供给看种植面积与天气,需求看油用压榨与食用;进口(塞内加尔)影响边际。',
                drivers=['主产区天气与种植面积', '油厂压榨与收购', '进口花生到港', '食用需求', '与豆油/菜油比价'],
                focus=['产区天气与上市节奏', '油厂开机与收购价', '进口到港量', '油粕比价'],
                tags=['花生', '油厂', '压榨', '塞内加尔']),
    'RS0': dict(base='油菜籽国产+进口并行,供给看加拿大与国内产量,需求看压榨(菜油+菜粕)利润。',
                drivers=['加拿大菜籽产量与出口', '国内种植面积', '压榨利润与开机', '进口政策', '产区天气'],
                focus=['加菜籽到港与报价', '压榨利润', '开机率', '进口政策'],
                tags=['菜籽', '油菜籽', '加拿大', '压榨']),
    # ---------- 金融期货 ----------
    'IF0': dict(base='沪深300股指期货,反映大盘蓝筹预期;受宏观政策、外资流向与权重板块业绩驱动。',
                drivers=['宏观政策与流动性', '北向资金与外资流向', '权重板块(金融/消费)业绩', '外围市场情绪', '基差与年化贴水'],
                focus=['政策面与流动性', '北向资金', '权重股业绩', '期指基差结构'],
                tags=['沪深300', '股指', '外资', '北向', 'A股']),
    'IH0': dict(base='上证50股指期货,权重以银行保险等大盘金融为主,防御属性较强。',
                drivers=['银行保险板块走势', '利率与信用环境', '高股息策略资金', '宏观政策', '外资偏好'],
                focus=['银行保险板块', '高股息资金流', '利率环境', '基差'],
                tags=['上证50', '银行', '保险', '高股息']),
    'IC0': dict(base='中证500股指期货,代表中盘成长;对流动性与风险偏好更敏感。',
                drivers=['中盘成长股业绩', '市场流动性与风险偏好', '产业政策支持方向', '量化与中性策略需求', '基差与对冲成本'],
                focus=['中盘股业绩与估值', '市场流动性', '量化策略对冲需求', '年化贴水'],
                tags=['中证500', '成长', '量化', '流动性']),
    'IM0': dict(base='中证1000股指期货,代表小盘;弹性最大,对小盘流动性与主题炒作高度敏感。',
                drivers=['小盘股流动性与成交', '主题热点轮动', '量化中性需求(空头端)', '波动率水平', '监管政策'],
                focus=['小盘成交额占比', '量化对冲需求', '年化贴水', '市场情绪'],
                tags=['中证1000', '小盘', '量化', '主题']),
    'T0': dict(base='10年期国债期货,定价锚是经济基本面与货币政策预期,对通胀与供给敏感度中等。',
                drivers=['货币政策(降准降息/公开市场)', '经济数据(PMI/社融/经济数据)', '通胀(CPI/PPI)', '债券供给与配置需求', '海外利率与汇率'],
                focus=['央行操作与资金面', '经济数据', '政府债供给', '海外利率'],
                tags=['国债', '10年国债', '货币政策', '降准', '降息', '社融']),
    'TF0': dict(base='5年期国债期货,对资金面与短端政策利率更敏感,曲线交易常用腿。',
                drivers=['资金面与公开市场操作', '政策利率(OMO/MLF)', '配置盘需求', '曲线形态', '同业存单利率'],
                focus=['资金面与DR007', 'MLF/OMO操作', '同业存单利率', '期限利差'],
                tags=['国债', '5年国债', '资金面', 'MLF', '同业存单']),
    'TS0': dict(base='2年期国债期货,久期最短,几乎是资金面与政策利率的直接映射。',
                drivers=['央行公开市场操作', 'DR007与资金面', '政策利率预期', '短端配置需求', '杠杆与回购'],
                focus=['DR007与资金面', '央行操作', '政策利率预期', '回购成交'],
                tags=['国债', '2年国债', 'DR007', '资金面']),
    'TL0': dict(base='30年期国债期货,久期最长,对超长端配置需求与供给、以及长期增长预期最敏感。',
                drivers=['超长端配置需求(保险/农商)', '政府债发行结构与超长债供给', '长期增长与通胀预期', '机构行为与止盈', '海外长端利率'],
                focus=['超长债供给节奏', '保险配置需求', '期限利差(30Y-10Y)', '机构持仓'],
                tags=['30年国债', '超长债', '保险', '期限利差']),
}

# 板块级共振说明(当同板块多个品种同向大幅波动时给出)
SECTOR_LOGIC = {
    'energy': dict(name='能化', chain='原油 → 石脑油/燃料 → 化工品',
                   note='能化品种成本端高度同源,原油大涨/大跌时全链共振;若整链同向,多为成本推动而非各自基本面,需警惕跟随性品种回调风险。'),
    'black': dict(name='黑色', chain='铁矿石/焦煤 → 焦炭 → 螺纹/热卷',
                  note='黑色系由钢厂利润串联:原料涨挤压钢厂利润,钢厂减产又反过来压制原料。整链同向需看是需求驱动(真实)还是成本/情绪推动(易回吐)。'),
    'metal': dict(name='有色', chain='美元/实际利率 → 铜铝锌 → 锡镍',
                  note='有色宏观属性强,美元与实际利率是共同定价锚;宏观驱动时同涨同跌,产业驱动时分化明显。'),
    'agri': dict(name='农产品', chain='国际供需报告 → 油脂油料 → 饲料/养殖',
                 note='农产品受USDA/MPOB等报告与天气主导,报告落地前后波动放大;油脂三兄弟(豆油/棕油/菜油)替代关系强。'),
    'finance': dict(name='金融', chain='股指/国债',
                    note='金融期货对宏观政策与流动性最敏感,股债跷跷板效应常见。'),
}

# 传闻/小作文关键词
RUMOR_KEYS = ['传闻', '网传', '市场传', '消息人士', '知情人士', '小作文', '未经证实',
              '有消息称', '据悉', '或称', '传将', '市场消息', '据知情', '不愿具名']

# 重要数据/事件关键词(用于"接下来关注")
WATCH_KEYS = ['USDA', 'MPOB', 'EIA', 'OPEC', '美联储', '议息', 'CPI', 'PCE', '非农', 'PMI',
              '社融', 'LPR', '降准', '降息', '库存报告', '供需报告', '产量报告', '出口数据',
              'UNICA', 'ITS', 'Amspec', '财新', '统计局', '海关', '国常会', '政治局会议']


# ============================================================
# 二、消息抓取
# ============================================================
def _http_get(url, timeout=15, headers=None, decode='utf-8'):
    h = {'User-Agent': _UA, 'Referer': 'https://finance.sina.com.cn'}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX).read().decode(decode, 'ignore')


def fetch_sina_roll(lid, num=40):
    """新浪财经滚动新闻 lid=2516(A股)/2517(宏观)/2509(全球)/2518(商品期货)"""
    url = f'https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&num={num}&page=1'
    try:
        raw = _http_get(url)
        d = json.loads(raw)
        out = []
        for it in d.get('result', {}).get('data', []) or []:
            out.append({
                'title': (it.get('title') or '').strip(),
                'media': it.get('media_name') or it.get('author') or '新浪财经',
                'url': it.get('url') or '',
                'ctime': int(it.get('ctime') or 0),
                'src': 'sina',
            })
        return out
    except Exception as e:
        print(f'[review] sina roll {lid} 失败: {e}')
        return []


def fetch_sina_flash(num=60):
    """新浪 7x24 全球直播快讯(zhibo_id=152) — 时效最快,含大量传闻/消息人士类条目"""
    url = f'https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size={num}&zhibo_id=152&tag_id=0&dire=f&dpc=1'
    try:
        raw = _http_get(url)
        d = json.loads(raw)
        lst = d.get('result', {}).get('data', {}).get('feed', {}).get('list', []) or []
        out = []
        for it in lst:
            txt = re.sub(r'<[^>]+>', '', it.get('rich_text') or '')
            txt = txt.replace('\n', ' ').strip()
            if not txt:
                continue
            try:
                ts = int(time.mktime(time.strptime(it.get('create_time', ''), '%Y-%m-%d %H:%M:%S')))
            except Exception:
                ts = 0
            out.append({'title': txt[:120], 'media': '新浪7×24', 'url': it.get('docurl') or '',
                        'ctime': ts, 'src': 'flash'})
        return out
    except Exception as e:
        print(f'[review] sina flash 失败: {e}')
        return []


# 和讯期货首页夹杂历史文章, 需剔除
_HEXUN_JUNK = ['周周谈', '上市暨', '回顾', '展望', '年报', '周报', '月报', '操作建议',
               '晨会', '日报（', '20', '19', '期货公司', '开户', '手续费']


def fetch_hexun_futures():
    """和讯期货要闻(GBK) — 过滤历史/营销类条目"""
    try:
        raw = _http_get('http://futures.hexun.com/', decode='gbk')
        out, seen = [], set()
        for u, t in re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]{8,50})</a>', raw):
            t = t.strip()
            if t in seen or 'hexun' not in u:
                continue
            # 剔除明显的历史文章/营销内容(如"2020上衍能源论坛")
            if re.search(r'20\d\d[年\-/.]', t) or any(k in t for k in _HEXUN_JUNK):
                continue
            seen.add(t)
            out.append({'title': t, 'media': '和讯期货', 'url': u, 'ctime': 0, 'src': 'hexun'})
            if len(out) >= 30:
                break
        return out
    except Exception as e:
        print(f'[review] 和讯期货失败: {e}')
        return []


# 收盘综述/收评类关键词 — 单独归类, 供"今日收评"区块使用
DIGEST_KEYS = ['收评', '收盘', '晚评', '多数收', '涨跌互现', '日评', '盘后',
               '涨超', '跌超', '涨停', '跌停', '大涨', '大跌', '全线']


def pick_digest(news, limit=8):
    """从消息里挑出收盘综述/收评类条目"""
    out = []
    for n in news:
        t = n.get('title', '')
        if any(k in t for k in DIGEST_KEYS) and len(t) >= 8:
            x = dict(n)
            x['time'] = _fmt_time(n.get('ctime'))
            out.append(x)
        if len(out) >= limit:
            break
    return out


def gather_news():
    """汇总多源消息并去重"""
    items = []
    for lid in (2516, 2517, 2509, 2518):
        items += fetch_sina_roll(lid, num=40)
    items += fetch_sina_flash(60)
    items += fetch_hexun_futures()
    # 去重(按标题前 24 字)
    seen, uniq = set(), []
    for it in items:
        if not it.get('title'):
            continue
        k = it['title'][:24]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    uniq.sort(key=lambda x: -(x.get('ctime') or 0))
    print(f'[review] 消息汇总 {len(uniq)} 条 (新浪{4}路+7x24+和讯)')
    return uniq


# ============================================================
# 三、归因
# ============================================================
def _fmt_time(ts):
    if not ts:
        return ''
    try:
        return datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')
    except Exception:
        return ''


def _match_news(code, news, limit=4):
    """用品种 tags 匹配相关消息"""
    info = FUNDAMENTALS.get(code)
    if not info:
        return []
    tags = info.get('tags', [])
    hits = []
    for n in news:
        t = n['title']
        score = sum(1 for tg in tags if tg in t)
        if score:
            hits.append((score, n))
    hits.sort(key=lambda x: (-x[0], -(x[1].get('ctime') or 0)))
    out = []
    for score, n in hits[:limit]:
        nn = dict(n)
        nn['rumor'] = any(k in n['title'] for k in RUMOR_KEYS)
        nn['time'] = _fmt_time(n.get('ctime'))
        out.append(nn)
    return out


def _sector_resonance(rows):
    """板块共振检测: 同板块 N 个以上品种同向且平均波幅显著"""
    by = {}
    for r in rows:
        by.setdefault(r['cat'], []).append(r)
    res = []
    for cat, lst in by.items():
        if len(lst) < 4:
            continue
        ups = [x for x in lst if x['pct'] >= 2]
        downs = [x for x in lst if x['pct'] <= -2]
        for grp, direction in ((ups, 'up'), (downs, 'down')):
            if len(grp) < 3:
                continue
            avg = sum(x['pct'] for x in grp) / len(grp)
            if abs(avg) < 2.2:
                continue
            logic = SECTOR_LOGIC.get(cat, {})
            res.append({
                'cat': cat,
                'name': logic.get('name', cat),
                'direction': direction,
                'count': len(grp),
                'avg': round(avg, 2),
                'members': [f"{x['cn']} {x['pct']:+.2f}%" for x in sorted(grp, key=lambda y: -abs(y['pct']))[:6]],
                'chain': logic.get('chain', ''),
                'note': logic.get('note', ''),
            })
    res.sort(key=lambda x: -abs(x['avg']))
    return res


def _basis_view(basis_items, code):
    """基差视角: 分位高低 → 判断是期现结构偏强还是偏弱"""
    b = (basis_items or {}).get(code)
    if not b:
        return None
    prc = b.get('prc_hist')
    pct = b.get('pct')
    if prc is None:
        return None
    if prc >= 80:
        tag, desc = '基差高位', '期货相对现货大幅贴水,做多安全边际较高;对买保(锁成本)有利'
    elif prc >= 60:
        tag, desc = '基差偏高', '期货贴水偏深,期现回归有向上修复空间'
    elif prc <= 20:
        tag, desc = '基差低位', '期货升水明显,追多性价比低;对卖保(锁库存价值)有利'
    elif prc <= 40:
        tag, desc = '基差偏低', '期货相对现货偏高,警惕高位回落'
    else:
        tag, desc = '基差中性', '期现结构处于历史中枢附近,驱动不明显'
    return {'tag': tag, 'desc': desc, 'prc': prc, 'pct': pct,
            'spot': b.get('spot'), 'fut': b.get('fut'), 'unit_warn': b.get('unit_warn')}


def _tech_view(tech, code):
    """技术面一句话: 趋势 + 关键位"""
    t = (tech or {}).get(code)
    if not t:
        return None
    parts = []
    ma = t.get('ma_arr')
    if ma:
        parts.append('均线' + ('多头排列' if ma == 'bull' else '空头排列' if ma == 'bear' else '交织'))
    sig = (t.get('macd') or {}).get('signal')
    if sig == 'gold':
        parts.append('MACD金叉')
    elif sig == 'dead':
        parts.append('MACD死叉')
    rsi = t.get('rsi14')
    if rsi is not None:
        parts.append('RSI ' + f'{rsi:.0f}' + ('（超买）' if rsi >= 70 else '（超卖）' if rsi <= 30 else ''))
    pos = (t.get('boll') or {}).get('pos')
    if pos in ('upper', 'lower'):
        parts.append('触及布林' + ('上轨' if pos == 'upper' else '下轨'))
    p1 = t.get('pct_1y')
    if p1 is not None:
        parts.append(f'近1年价格分位 {p1:.0f}%')
    return ' / '.join(parts) if parts else None


def _position_view(q):
    """持仓与成交视角"""
    try:
        oi = float(q.get('oi') or 0)
        vol = float(q.get('volume') or 0)
    except Exception:
        return None
    if not oi:
        return None
    return {'oi': oi, 'vol': vol, 'vol_oi': round(vol / oi, 2) if oi else None,
            'desc': '成交/持仓比 %.2f，%s' % (vol / oi if oi else 0,
                                             '交投活跃、资金分歧大' if (vol / oi if oi else 0) >= 1.5
                                             else '交投平稳' if (vol / oi if oi else 0) >= 0.5 else '持仓沉淀、交投清淡')}


def _watch_list(code, news, calendar):
    """接下来关注什么: 品种 focus + 日历/数据事件"""
    info = FUNDAMENTALS.get(code) or {}
    watch = []
    for f in info.get('focus', [])[:4]:
        watch.append({'type': '指标', 'text': f})
    # 日历事件(匹配品种名)
    cn = info.get('cn') or (FUNDAMENTALS.get(code, {}).get('cn'))
    for ev in (calendar or []):
        txt = f"{ev.get('title', '')} {ev.get('note', '')} {ev.get('desc', '')}"
        key = cn or ''
        if key and key in txt:
            watch.append({'type': '日历', 'text': f"{ev.get('title', '')}（{ev.get('date', '')}）"})
    # 消息里提到的重要数据/事件(优先与品种相关的短句)
    for n in news[:80]:
        t = n['title']
        if len(t) > 60:
            continue
        for k in WATCH_KEYS:
            if k in t:
                # 提炼: 去掉开头的媒体前缀, 保留核心短句
                core = re.sub(r'^【[^】]*】', '', t).strip()
                watch.append({'type': '消息', 'text': core[:34]})
                break
        if len(watch) >= 7:
            break
    # 去重
    seen, out = set(), []
    for w in watch:
        k = w['text'][:20]
        if k in seen:
            continue
        seen.add(k)
        out.append(w)
    return out[:6]


def build(categories=None, tech=None, basis=None, calendar=None, news=None,
          top_n=5, min_abs=1.5, verbose=False):
    """
    生成盘后复盘归因
    返回 {'date','movers','rumors','sector','summary','news_count','sources'}
    """
    categories = categories or {}
    tech = tech or {}
    basis_items = (basis or {}).get('items', {}) if isinstance(basis, dict) else {}
    calendar = calendar or []
    news = news or []

    # 1) 拉平行情
    rows = []
    for cat, lst in categories.items():
        for q in lst or []:
            if q.get('paused'):
                continue
            try:
                pct = float(q.get('pct'))
                last = float(q.get('last') or 0)
            except Exception:
                continue
            if last <= 0:
                continue
            rows.append({
                'code': q.get('code'), 'cn': q.get('cn_name'), 'cat': cat,
                'pct': pct, 'last': last,
                'contract': q.get('contract_full') or q.get('contract') or '',
                'oi': q.get('oi'), 'volume': q.get('volume'),
            })

    # 只做国内主力品种: 排除外盘(无对应产业基本面数据)
    rows = [r for r in rows if r['cat'] != 'overseas']

    ups = sorted([r for r in rows if r['pct'] > 0], key=lambda x: -x['pct'])
    downs = sorted([r for r in rows if r['pct'] < 0], key=lambda x: x['pct'])
    # 优先取有基本面库的品种; 若该方向无品种达到阈值(如普涨日跌幅都很小), 则放宽到该方向前 2 名
    def _pick(lst, n, sign):
        a = [r for r in lst if r['code'] in FUNDAMENTALS and sign * r['pct'] >= min_abs][:n]
        if len(a) < n:
            b = [r for r in lst if r['code'] not in FUNDAMENTALS
                 and r not in a and sign * r['pct'] >= min_abs]
            a += b[:n - len(a)]
        if not a:
            a = [r for r in lst if r['code'] in FUNDAMENTALS][:2] or lst[:1]
        return a
    picked = _pick(ups, top_n, 1) + _pick(downs, top_n, -1)

    # 2) 逐个归因
    movers = []
    for r in picked:
        code = r['code']
        info = FUNDAMENTALS.get(code, {})
        matched = _match_news(code, news)
        bv = _basis_view(basis_items, code)
        tv = _tech_view(tech, code)
        pv = _position_view(r)
        # 大幅波动但没匹配到公开消息 → 提示可能存在未公开传闻/资金推动
        unexplained = abs(r['pct']) >= 3 and not matched
        movers.append({
            'code': code, 'cn': r['cn'], 'cat': r['cat'],
            'contract': r['contract'], 'pct': round(r['pct'], 2), 'last': r['last'],
            'dir': 'up' if r['pct'] > 0 else 'down',
            'unexplained': unexplained,
            'level': '大幅' if abs(r['pct']) >= 3 else ('显著' if abs(r['pct']) >= 2 else '温和'),
            'base': info.get('base', ''),
            'drivers': info.get('drivers', []),
            'news': matched,
            'basis': bv,
            'tech': tv,
            'position': pv,
            'watch': _watch_list(code, news, calendar),
            'has_kb': bool(info),
        })

    # 3) 传闻/小作文
    rumors = []
    for n in news:
        t = n.get('title', '')
        if any(k in t for k in RUMOR_KEYS):
            vary = []
            for code, info in FUNDAMENTALS.items():
                if any(tg in t for tg in info.get('tags', [])):
                    vary.append(FUNDAMENTALS[code].get('cn') or code)
            rumors.append({'title': t[:80], 'media': n.get('media', ''), 'url': n.get('url', ''),
                           'time': _fmt_time(n.get('ctime')), 'vary': vary[:4]})
        if len(rumors) >= 8:
            break

    # 4) 板块共振
    sector = _sector_resonance(rows)

    # 5) 一句话总结
    nup = len([r for r in rows if r['pct'] > 0])
    ndn = len([r for r in rows if r['pct'] < 0])
    big = [r for r in rows if abs(r['pct']) >= 3]
    if sector:
        s0 = sector[0]
        lead = f"{s0['name']}板块{'领涨' if s0['direction'] == 'up' else '领跌'}({s0['count']}个品种平均{s0['avg']:+.2f}%)"
    else:
        lead = '板块分化、无明显共振'
    summary = f"全市场 {nup} 涨 {ndn} 跌, {len(big)} 个品种波幅超 3%; {lead}。"

    out = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'summary': summary,
        'n_up': nup, 'n_down': ndn, 'n_big': len(big),
        'movers': movers,
        'rumors': rumors,
        'sector': sector,
        'digest': pick_digest(news),
        'news_count': len(news),
        'sources': ['新浪财经(4路)', '新浪7×24快讯', '和讯期货'],
        'note': '驱动因子与关注要点基于公开产业逻辑整理;消息为公开渠道抓取并按关键词匹配,仅供参考。标注「传闻」的条目未经证实,请勿作为决策依据。投资有风险,入市需谨慎。',
    }
    if verbose:
        print(f"[review] 归因品种 {len(movers)} 个 / 传闻 {len(rumors)} 条 / 共振板块 {len(sector)} 个")
    return out


if __name__ == '__main__':
    data = json.load(open('data/quotes.json', encoding='utf-8'))
    news = gather_news()
    r = build(data.get('categories'), data.get('tech'), data.get('basis'),
              data.get('calendar'), news, verbose=True)
    print('\n== 总结 ==')
    print(r['summary'])
    for m in r['movers']:
        print(f"\n【{m['cn']} {m['contract']} {m['pct']:+.2f}%】{m['level']}{'涨' if m['dir']=='up' else '跌'}")
        print('  基本面:', (m['base'] or '—')[:60])
        print('  驱动:', ' / '.join(m['drivers'][:3]))
        if m['basis']:
            print('  基差:', m['basis']['tag'], m['basis']['desc'][:30])
        if m['tech']:
            print('  技术:', m['tech'])
        for n in m['news'][:2]:
            print('  消息:', ('[传闻] ' if n['rumor'] else '') + n['title'][:46])
        print('  关注:', ' | '.join(w['text'][:20] for w in m['watch'][:3]))
    if r['rumors']:
        print('\n== 传闻 ==')
        for x in r['rumors'][:5]:
            print(' -', x['title'][:60], '| 相关:', ','.join(x['vary']) or '—')
    print('\n== 共振 ==')
    for s in r['sector']:
        print(f" - {s['name']}({s['direction']}) {s['count']}个 均{s['avg']:+.2f}%: {'/'.join(s['members'][:4])}")
