# -*- coding: utf-8 -*-
# v84 验收: 重要数据卡更新 + 冷调深蓝底色
import os, json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8124/index.html"
OUT = os.path.dirname(os.path.abspath(__file__))
res = {"errors": []}

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 1400})
    pg.on("pageerror", lambda e: res["errors"].append("pageerror: " + str(e)))
    pg.goto(BASE)
    pg.wait_for_timeout(4000)

    # 1) 底色: body / card / risk-note
    res["body_bg"] = pg.evaluate("getComputedStyle(document.body).backgroundColor")
    res["card_bg"] = pg.evaluate("getComputedStyle(document.querySelector('.card')).backgroundColor")
    rn = pg.evaluate("""(() => { const s=getComputedStyle(document.querySelector('.risk-note'));
        return s.backgroundColor+' | '+s.color; })()""")
    res["risk_note"] = rn

    # 2) 重要数据卡: 展开并检查回顾+日历
    pg.evaluate("openModule('card-data')")
    pg.wait_for_timeout(2500)
    res["events_n"] = pg.eval_on_selector_all("#events-list .rv-card, #events-list .ev-it, #events-list > *",
        "els=>els.length")
    res["events_names"] = pg.eval_on_selector("#events-list", "el=>el.innerText.split('\\n').filter(t=>t.includes('CPI')||t.includes('非农')||t.includes('美联储')||t.includes('CFTC')).slice(0,8)")
    cal_txt = pg.eval_on_selector("#calendar-list", "el=>el.innerText")
    res["calendar_txt"] = cal_txt[:200].replace("\n", " | ")
    pg.screenshot(path=os.path.join(OUT, "_v84_datacard.png"), full_page=False)

    # 3) 页脚截图
    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    pg.wait_for_timeout(400)
    pg.screenshot(path=os.path.join(OUT, "_v84_footer.png"))
    b.close()

print(json.dumps(res, ensure_ascii=False, indent=1))
