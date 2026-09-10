#!/usr/bin/env bash
# 单次刷新: 抓取 -> 校验 -> 发布到 gh-pages
# 由 .github/workflows/refresh.yml 循环调用(每轮间隔 5 分钟), 也可单独执行。
# 依赖环境变量: TOKEN(GITHUB_TOKEN), REPO(owner/name)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

echo "--- 抓取 $(TZ=Asia/Shanghai date '+%F %H:%M:%S') ---"
python3 fetch_quotes.py 2>&1 | grep -E "^\[LAP\]|^\[OK\]|^\[WARN\]|^\[FATAL\]" | tail -20

# 校验主数据是否有效, 并生成前端轮询用的 meta.json
OK=$(python3 - <<'PYEOF'
import json, datetime, sys
ok = False
try:
    d = json.load(open('data/quotes.json', encoding='utf-8'))
    cats = d.get('categories') or {}
    n = sum(len(v) for v in cats.values() if isinstance(v, list))
    ok = bool(d.get('updated_at')) and n > 10
    sys.stderr.write('  品种数=%d 数据时间=%s\n' % (n, d.get('updated_at')))
    if ok:
        json.dump({
            'updated_at': d.get('updated_at'),
            'trading_day': d.get('trading_day_cn'),
            'n': n,
            'published_at': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        }, open('data/meta.json', 'w', encoding='utf-8'), ensure_ascii=False)
except Exception as e:
    sys.stderr.write('  校验失败: %s\n' % e)
print('true' if ok else 'false')
PYEOF
)

if [ "$OK" != "true" ]; then
    echo "!! 校验未通过, 本轮跳过发布(保留上一版数据)"
    exit 0
fi

# 信号变化检测: 用 5 年日K(kline_cache.json, 不发布)重算上一交易日与今日的均线排列,
# 结果写 data/changes.json(仅几KB), 供前端顶部警示条使用。
python3 - <<'PYEOF'
import json
try:
    q = json.load(open('data/quotes.json', encoding='utf-8'))
    kl = json.load(open('data/kline_cache.json', encoding='utf-8'))
    tech = q.get('tech') or {}
    cats = q.get('categories') or {}
    secmap = {}
    for k, v in cats.items():
        for it in (v or []):
            if isinstance(it, dict) and it.get('code'):
                secmap[it['code']] = k

    def arr(bars, end):
        if end < 59:
            return ''
        def m(n):
            s = bars[max(0, end - n + 1):end + 1]
            return sum(b['c'] for b in s) / len(s) if s else 0
        m5, m10, m20, m60 = m(5), m(10), m(20), m(60)
        if m5 > m10 > m20 > m60:
            return '多头排列'
        if m5 < m10 < m20 < m60:
            return '空头排列'
        return '均线纠缠'

    out = []
    for code, klo in kl.items():
        bars = (klo or {}).get('bars') or []
        if len(bars) < 62:
            continue
        n = len(bars)
        today, prev = arr(bars, n - 1), arr(bars, n - 2)
        if not today or not prev or today == prev:
            continue
        up = lambda a: a == '多头排列'
        dn = lambda a: a == '空头排列'
        neu = lambda a: a == '均线纠缠'
        if (up(prev) and dn(today)) or (dn(prev) and up(today)):
            kind, lv = '反转', 3
        elif (up(prev) and neu(today)) or (dn(prev) and neu(today)):
            kind, lv = '走弱', 2
        elif neu(prev) and (up(today) or dn(today)):
            kind, lv = '新进', 1
        else:
            continue
        lb, pb = bars[n - 1], bars[n - 2]
        pct = (lb['c'] / pb['c'] - 1) * 100 if pb.get('c') else None
        t = tech.get(code) or {}
        out.append({
            'code': code,
            'cn': t.get('cn') or klo.get('cn') or code,
            'kind': kind, 'lv': lv,
            'prev': prev, 'today': today,
            'close': lb['c'],
            'pct': round(pct, 2) if pct is not None else None,
            'sector': secmap.get(code, ''),
            'date': lb.get('d', ''),
        })
    out.sort(key=lambda x: (-x['lv'], x['code']))
    json.dump({'generated': q.get('updated_at'), 'trading_day': q.get('trading_day_cn'),
               'n': len(out), 'items': out},
              open('data/changes.json', 'w', encoding='utf-8'), ensure_ascii=False)
    print('  变化检测: %d 个品种 (反转%d 走弱%d 新进%d)' % (
        len(out),
        sum(1 for x in out if x['kind'] == '反转'),
        sum(1 for x in out if x['kind'] == '走弱'),
        sum(1 for x in out if x['kind'] == '新进')))
except Exception as e:
    print('  变化检测失败(不影响主流程): %s' % e)
PYEOF

echo "--- 发布 $(TZ=Asia/Shanghai date '+%F %H:%M:%S') ---"
# 页面文件以 main 分支最新为准: 循环任务启动时 checkout 的是当次版本,
# 之后改了页面若不重新拉取, 每轮发布都会把改动覆盖回旧版
git fetch -q origin main 2>/dev/null \
  && git checkout -q origin/main -- index.html sw.js manifest.json 2>/dev/null \
  || echo "   (未取到 main 最新页面文件, 沿用本次 checkout 版本)"
rm -rf /tmp/pub && mkdir -p /tmp/pub
# 跳过 Jekyll 构建, 直接发布静态文件(更快, 也不会被 Jekyll 处理 HTML)
touch /tmp/pub/.nojekyll
cp index.html sw.js manifest.json /tmp/pub/ 2>/dev/null || true
cp icon-192.png icon-512.png icon.svg /tmp/pub/ 2>/dev/null || true
cp -r data /tmp/pub/
# 抓取缓存不发布: 前端不加载, 且省去每轮 11MB 传输(它走 Actions cache 传递)
rm -f /tmp/pub/data/kline_cache.json /tmp/pub/data/basis_days.json
cd /tmp/pub || exit 1
git init -q
git checkout -q -b gh-pages
git add -A
git -c user.name='github-actions[bot]' \
    -c user.email='github-actions[bot]@users.noreply.github.com' \
    commit -q -m "data $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
git push -q -f "https://x-access-token:${TOKEN}@github.com/${REPO}.git" gh-pages
echo "   已发布"
