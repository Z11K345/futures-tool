# merge_extra_news.py — 把 AI 在对话里通过 MCP 拉到的多源新闻(接管 fetch_quotes.py 自动跑不动)
# 用法:
#   1. 在 WorkBuddy 对话里调用 dzh-mcp / tdx-connector / wind-finance, 把结果保存到 extra_news.json
#      extra_news.json 格式:
#          {
#              "dzh":   [{"title":"","ctime":0,"media":"","url":"","lid":"dzh"}],
#              "tdx":   [...],
#              "wind":  [...]
#          }
#   2. 运行: python merge_extra_news.py extra_news.json
#   3. 把 data/quotes.json 重新部署(workbuddy_sites_deploy action=deploy 同样目录)

import sys
import json
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent / 'data'
QUOTES = DATA_DIR / 'quotes.json'


def main():
    if len(sys.argv) < 2:
        print('用法: python merge_extra_news.py <extra_news.json>')
        sys.exit(1)
    extra_path = Path(sys.argv[1])
    if not extra_path.exists():
        print(f'找不到 {extra_path}')
        sys.exit(1)
    with open(extra_path, encoding='utf-8') as f:
        extra = json.load(f)
    if not isinstance(extra, dict):
        print('extra_news.json 必须是 JSON 对象 {dzh:[], tdx:[], wind:[]}')
        sys.exit(1)

    # 读取现有 quotes.json
    with open(QUOTES, encoding='utf-8') as f:
        quotes = json.load(f)
    news = quotes.get('news', {})

    # 合并(同名源覆盖; UI 上每个源独立列出)
    for src in ['dzh', 'tdx', 'wind']:
        items = extra.get(src) or []
        if not items:
            continue
        # 标准化字段(tdx/wind 是字符串 ctime 还是要从 date 解析,统一为 unix seconds)
        normalized = []
        for it in items:
            normalized.append({
                'title': it.get('title') or it.get('content', '')[:80],
                'ctime': it.get('ctime') or 0,
                'media': it.get('media') or it.get('doc_type') or src.upper(),
                'url': it.get('url', ''),
                'lid': src,
            })
        news[src] = normalized
    quotes['news'] = news
    quotes['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 写回
    with open(QUOTES, 'w', encoding='utf-8') as f:
        json.dump(quotes, f, ensure_ascii=False, indent=2)

    added = sum(len(news.get(s, [])) for s in ['dzh', 'tdx', 'wind'])
    print(f'[OK] 合并 {added} 条多源新闻到 quotes.json (来源: dzh/tdx/wind)')
    print(f'     news 现包含:', {k: len(news.get(k, [])) for k in news})
    print(f'     updated_at: {quotes["updated_at"]}')


if __name__ == '__main__':
    main()
