async (page) => {
await page.goto('http://127.0.0.1:8873/index.html');
await page.setViewportSize({ width: 1600, height: 1100 });
await page.screenshot({ path: 'output/playwright/talonmart-atlas-desktop.png', fullPage: true });
const tabs = ['01 · 整体架构', '02 · Agent × RAG 目标态', '03 · Agent 链路拆解'];
const files = ['01-system-overview', '02-agent-rag-flow', '03-agent-flow-breakdown'];
const results = [];
for (let index = 0; index < tabs.length; index++) {
  await page.getByRole('tab', { name: tabs[index], exact: true }).click();
  results.push(await page.evaluate(() => {
    const panel = document.querySelector('.panel:not([hidden])');
    const svg = panel.querySelector('svg');
    const size = svg.viewBox.baseVal;
    const texts = [...svg.querySelectorAll('text')].map(el => {
      const box = el.getBBox();
      return { text: el.textContent, x: box.x, y: box.y, w: box.width, h: box.height };
    });
    const overlaps = [];
    for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
      const a = texts[i], b = texts[j];
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (ox > 2 && oy > 2) overlaps.push([a.text, b.text, Math.round(ox), Math.round(oy)]);
    }
    const forbidden = /任务书|TASKBOOK|DEV_SPEC|演进边界|演进阶段|A[–—-]I|A[–—-]D|E[–—-]I|M6[–—-][AB]|I-DATA-READY|\b[BCDEFGHI][1-5]\b/i;
    return { view: panel.id, textCount: texts.length, overlaps, forbiddenLabels: texts.filter(t => forbidden.test(t.text)), outOfBounds: texts.filter(t => t.x < 0 || t.y < 0 || t.x + t.w > size.width || t.y + t.h > size.height) };
  }));
}
await page.setViewportSize({ width: 390, height: 844 });
await page.getByRole('tab', { name: tabs[0], exact: true }).click();
await page.getByRole('button', { name: '放大图形', exact: true }).click();
const zoomTest = await page.locator('#zoom-value').textContent();
await page.getByRole('button', { name: '适合宽度', exact: true }).click();
const mobile = await page.evaluate(() => ({ width: innerWidth, contentWidth: document.documentElement.scrollWidth, visiblePanels: [...document.querySelectorAll('.panel')].filter(p => !p.hidden).length }));
await page.screenshot({ path: 'output/playwright/talonmart-atlas-mobile.png', fullPage: true });
await page.getByRole('tab', { name: tabs[2], exact: true }).click();
const flowMobile = await page.evaluate(() => ({ width: innerWidth, contentWidth: document.documentElement.scrollWidth, visiblePanels: [...document.querySelectorAll('.panel')].filter(p => !p.hidden).length, stepCount: document.querySelectorAll('#panel-flow g[id^="flow-step-"]').length, navigationVisible: !document.querySelector('#step-nav').hidden }));
await page.screenshot({ path: 'output/playwright/talonmart-agent-flow-mobile.png', fullPage: true });
await page.emulateMedia({ reducedMotion: 'reduce' });
await page.getByRole('button', { name: '07 事实过滤', exact: true }).click();
const stepNavigation = await page.evaluate(() => ({ top: document.getElementById('flow-step-07').getBoundingClientRect().top, expectedTop: document.getElementById('step-nav').getBoundingClientRect().height + 24, navigationBottom: document.getElementById('step-nav').getBoundingClientRect().bottom, hash: location.hash, download: document.getElementById('png-download').getAttribute('href') }));
await page.goto('http://127.0.0.1:8873/index.html#agent-flow');
const deepLink = await page.getByRole('tab', { name: tabs[2], exact: true }).getAttribute('aria-selected');
const forbiddenPageText = await page.evaluate(() => /任务书|TASKBOOK|DEV_SPEC|演进边界|演进阶段|A[–—-]I|M6[–—-][AB]/i.test(document.body.textContent));
const report = { diagrams: results, zoomTest, mobile, flowMobile, stepNavigation, deepLink, forbiddenPageText };
const reportDownload = page.waitForEvent('download');
await page.evaluate(data => {
  const anchor = document.createElement('a');
  anchor.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  anchor.download = 'talonmart-atlas-qa.json';
  anchor.click();
}, report);
await (await reportDownload).saveAs('output/playwright/talonmart-atlas-qa.json');
console.log(JSON.stringify(report));
await page.getByRole('tab', { name: tabs[1], exact: true }).click();
await page.setViewportSize({ width: 1600, height: 1100 });
await page.screenshot({ path: 'output/playwright/talonmart-agent-rag-target-desktop.png', fullPage: true });
await page.getByRole('tab', { name: tabs[2], exact: true }).click();
await page.screenshot({ path: 'output/playwright/talonmart-agent-flow-desktop.png', fullPage: true });
await page.setViewportSize({ width: 1600, height: 1100 });
for (let i = 0; i < files.length; i++) {
  await page.getByRole('tab', { name: tabs[i], exact: true }).click();
  const download = page.waitForEvent('download');
  await page.evaluate(async filename => {
    const svg = document.querySelector('.panel:not([hidden]) svg');
    const { width, height } = svg.viewBox.baseVal;
    const source = new XMLSerializer().serializeToString(svg);
    const imageUrl = URL.createObjectURL(new Blob([source], { type: 'image/svg+xml;charset=utf-8' }));
    const image = new Image();
    image.src = imageUrl;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = width * 2;
    canvas.height = height * 2;
    canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    const anchor = document.createElement('a');
    anchor.href = URL.createObjectURL(blob);
    anchor.download = filename + '.png';
    anchor.click();
    URL.revokeObjectURL(imageUrl);
  }, files[i]);
  await (await download).saveAs('output/playwright/' + files[i] + '.png');
}
await page.goto('http://127.0.0.1:8873/index.html');
await page.setViewportSize({ width: 1600, height: 1100 });
}
