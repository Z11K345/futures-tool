# -*- coding: utf-8 -*-
# v83 补充验收: 明细行数 + 404 定位
import os, json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8124/index.html"
OUT = os.path.dirname(os.path.abspath(__file__))
res = {"errors": []}

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 390, "height": 844})
    pg.on("pageerror", lambda e: res["errors"].append("pageerror: " + str(e)))
    pg.on("response", lambda r: res.setdefault("404", []).append(r.url) if r.status == 404 else None)
    pg.goto(BASE)
    pg.wait_for_timeout(4000)

    pg.evaluate("openModule('card-movers')")
    pg.wait_for_timeout(1000)
    pg.evaluate("setMoversView('table')")
    pg.wait_for_timeout(800)
    res["mv_rows"] = pg.eval_on_selector_all(
        "#movers-table-wrap tr", "els=>els.length")
    res["mv_rows_text"] = pg.eval_on_selector(
        "#movers-table-wrap", "el=>el.innerText.split('\\n').slice(0,4).join(' | ')")
    b.close()

print(json.dumps(res, ensure_ascii=False, indent=1))
