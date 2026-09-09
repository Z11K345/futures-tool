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

echo "--- 发布 $(TZ=Asia/Shanghai date '+%F %H:%M:%S') ---"
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
