// v44 排版重组验收: 起本地静态服务 + Playwright 检查 tab 结构与渲染
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8813;
const MIME = { '.html': 'text/html; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };

const server = http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/') p = '/index.html';
  const fp = path.join(ROOT, p);
  if (!fp.startsWith(ROOT) || !fs.existsSync(fp) || fs.statSync(fp).isDirectory()) {
    res.writeHead(404); res.end('404'); return;
  }
  res.writeHead(200, { 'Content-Type': MIME[path.extname(fp)] || 'application/octet-stream' });
  fs.createReadStream(fp).pipe(res);
});

(async () => {
  await new Promise(r => server.listen(PORT, '127.0.0.1', r));
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  page.on('response', r => { if (r.status() >= 400) errs.push('HTTP ' + r.status() + ' ' + r.url()); });

  const t0 = Date.now();
  await page.goto(`http://127.0.0.1:${PORT}/index.html`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  // 1) tab 名称
  const tabs = await page.$$eval('.tab-item', els => els.map(e => e.dataset.tab + ':' + e.textContent.trim()));
  console.log('TABS:', JSON.stringify(tabs));

  // 2) 各 pane 内可见卡片与分区标题的顺序
  for (const t of tabs.map(x => x.split(':')[0])) {
    const info = await page.evaluate(async (tn) => {
      const el = document.querySelector('.tab-item[data-tab="' + tn + '"]');
      if (!el) return { err: 'no tab ' + tn };
      el.click();
      await new Promise(r => setTimeout(r, 400));
      const pane = document.querySelector('.tab-pane.active');
      if (!pane) return { err: 'no active pane' };
      const out = [];
      pane.querySelectorAll('.sect-div, .card').forEach(n => {
        if (n.classList.contains('sect-div')) out.push('== ' + n.textContent.trim());
        else {
          const ti = n.querySelector('.card-title');
          out.push('   ' + (ti ? ti.textContent.trim().slice(0, 26) : n.id));
        }
      });
      return { id: pane.id, items: out };
    }, t);
    console.log(`\n--- ${t} (${info.id}) ---`);
    (info.items || []).forEach(x => console.log(x));
  }

  // 3) 信号变化警示条
  await page.evaluate(() => { const e = document.querySelector('.tab-item'); e.click(); });
  await page.waitForTimeout(600);
  const alert = await page.evaluate(() => {
    const b = document.getElementById('chg-alert');
    if (!b || b.style.display === 'none') return { shown: false };
    return {
      shown: true, cls: b.className,
      title: (document.getElementById('chg-title') || {}).textContent,
      rows: document.querySelectorAll('#chg-body .chg-row').length,
      tip: (document.querySelector('#chg-body .chg-tip') || {}).textContent || ''
    };
  });
  console.log('\nCHG-ALERT:', JSON.stringify(alert, null, 1));

  // 4) 机会列表变化标记
  const chgMark = await page.evaluate(() => {
    const e = [...document.querySelectorAll('.tab-item')].find(x => x.textContent.trim() === '机会');
    if (e) e.click();
    return new Promise(r => setTimeout(() => {
      r([...document.querySelectorAll('.opp-chg')].map(n => n.textContent.trim()));
    }, 700));
  });
  console.log('OPP-CHG:', JSON.stringify(chgMark));

  console.log('\nERRORS(' + errs.length + '):');
  errs.slice(0, 12).forEach(e => console.log('  ' + e));
  console.log('耗时 ' + ((Date.now() - t0) / 1000).toFixed(1) + 's');

  await browser.close();
  server.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
