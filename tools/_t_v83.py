# -*- coding: utf-8 -*-
# v83 验收: 涨跌幅榜 ECharts 图表化 + 文案梳理 + tabular-nums
import os, json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8124/index.html"
OUT = os.path.dirname(os.path.abspath(__file__))

res = {"errors": []}

with sync_playwright() as p:
    b = p.chromium.launch()

    # ---- 手机端 390 ----
    pg = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    pg.on("pageerror", lambda e: res["errors"].append("pageerror: " + str(e)))
    pg.on("console", lambda m: res["errors"].append("console: " + m.text) if m.type == "error" else None)
    pg.goto(BASE)
    pg.wait_for_timeout(4000)

    # 1) 涨跌幅榜: 默认图表视图, canvas 有渲染
    pg.evaluate("openModule('card-movers')")
    pg.wait_for_timeout(1500)
    has_canvas = pg.eval_on_selector_all("#movers-chart canvas", "els=>els.length")
    box = None
    if has_canvas:
        box = pg.eval_on_selector("#movers-chart canvas", "el=>JSON.stringify({w:el.width,h:el.height})")
    res["movers_canvas"] = has_canvas
    res["movers_canvas_size"] = box

    # 2) 切到明细视图 -> 切回图表视图
    pg.evaluate("setMoversView('table')")
    pg.wait_for_timeout(400)
    res["mn_items"] = pg.eval_on_selector_all("#movers-list .mn-item", "els=>els.length")
    pg.evaluate("setMoversView('chart')")
    pg.wait_for_timeout(1200)
    res["canvas_after_back"] = pg.eval_on_selector_all("#movers-chart canvas", "els=>els.length")
    pg.screenshot(path=os.path.join(OUT, "_v83_mv_chart_mob.png"))

    # 3) 行情搜索
    pg.evaluate("openModule('card-quotes')")
    pg.wait_for_timeout(2500)
    pg.fill("#quotes-search", "螺纹")
    pg.wait_for_timeout(600)
    res["search_rows"] = pg.eval_on_selector_all("#quotes-table tbody tr", "els=>els.length")
    pg.screenshot(path=os.path.join(OUT, "_v83_quotes_mob.png"))
    pg.close()

    # ---- 桌面端 1280 ----
    pg2 = b.new_page(viewport={"width": 1280, "height": 900})
    pg2.on("pageerror", lambda e: res["errors"].append("pageerror-d: " + str(e)))
    pg2.on("console", lambda m: res["errors"].append("console-d: " + m.text) if m.type == "error" else None)
    pg2.goto(BASE)
    pg2.wait_for_timeout(4000)

    # 4) 页脚文案已去技术腔
    foot = pg2.eval_on_selector(".risk-note", "el=>el.innerText")
    res["footer"] = foot.strip().replace("\n", " | ")
    res["footer_clean"] = ("fetch_quotes" not in foot) and ("0.8 秒" not in foot)

    # 5) 桌面涨跌幅榜图表
    pg2.evaluate("openModule('card-movers')")
    pg2.wait_for_timeout(1500)
    res["mv_canvas_desktop"] = pg2.eval_on_selector_all("#movers-chart canvas", "els=>els.length")
    pg2.screenshot(path=os.path.join(OUT, "_v83_desktop.png"))

    # 6) tabular-nums 生效
    res["tabular"] = pg2.eval_on_selector(
        "#quotes-table td", "el=>getComputedStyle(el).fontVariantNumeric")

    b.close()

print(json.dumps(res, ensure_ascii=False, indent=1))
