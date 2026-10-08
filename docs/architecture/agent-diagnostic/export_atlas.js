async (page) => {
  // Run on a generated atlas page using Playwright CLI run-code.
  const qaDir = "output/playwright/agent-diagnostic";
  const metadata = await page.locator('#atlas-data').textContent().then(JSON.parse);
  const baseUrl = page.url().split('#')[0];
  const report = { project: metadata.project, views: [], interactions: [] };
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1600, height: 1100 });
  for (const [index, view] of metadata.diagrams.entries()) {
    await page.locator('[data-view="' + index + '"]').click();
    await page.evaluate(() => document.fonts.ready);
    const checked = await page.evaluate(() => {
      const svg = document.querySelector('.panel:not([hidden]) svg'), v = svg.viewBox.baseVal;
      const texts = [...svg.querySelectorAll('text')].filter(el => el.textContent.trim()).map(el => {
        const b = el.getBBox();
        return { text: el.textContent, x: b.x, y: b.y, w: b.width, h: b.height, region: el.dataset.region?.split(',').map(Number) };
      });
      const overlaps = [];
      for (let i=0;i<texts.length;i++) for (let j=i+1;j<texts.length;j++) {
        const a=texts[i],b=texts[j];
        if (Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)>2 && Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)>2) overlaps.push([a.text,b.text]);
      }
      const outOfBounds = texts.filter(t => t.x<0 || t.y<0 || t.x+t.w>v.width || t.y+t.h>v.height);
      const outsideCard = texts.filter(t => t.region && (t.x<t.region[0]-2 || t.y<t.region[1]-2 || t.x+t.w>t.region[0]+t.region[2]+2 || t.y+t.h>t.region[1]+t.region[3]+2));
      return { textCount: texts.length, overlaps, outOfBounds, outsideCard };
    });
    const downloaded = page.waitForEvent('download');
    const dimensions = await page.evaluate(async id => {
      const svg = document.querySelector('.panel:not([hidden]) svg');
      const {width,height} = svg.viewBox.baseVal;
      const scale = Math.min(2,16000/height,8000/width,Math.sqrt(60000000/(width*height)));
      const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], {type:'image/svg+xml;charset=utf-8'}));
      const bitmap = new Image();bitmap.src=url;await bitmap.decode();
      const canvas=document.createElement('canvas');canvas.width=Math.round(width*scale);canvas.height=Math.round(height*scale);
      canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
      if(!blob) throw new Error('PNG canvas export failed');
      const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=id+'.png';link.click();URL.revokeObjectURL(url);
      return {width:canvas.width,height:canvas.height,scale};
    },view.id);
    await (await downloaded).saveAs(qaDir+'/'+view.id+'.png');
    await page.screenshot({path:qaDir+'/'+view.id+'-desktop.png',fullPage:true});
    report.views.push({id:view.id,sha256:view.sha256,...checked,png:dimensions});
  }
  for (const [index,view] of metadata.diagrams.entries()) {
    await page.setViewportSize({width:390,height:844});
    await page.locator('[data-view="'+index+'"]').click();
    await page.getByRole('button',{name:'放大图形',exact:true}).click();
    const zoom = await page.locator('#zoom-value').textContent();
    await page.getByRole('button',{name:'适合宽度',exact:true}).click();
    const mobile=await page.evaluate(()=>({width:innerWidth,contentWidth:document.documentElement.scrollWidth,visiblePanels:document.querySelectorAll('.panel:not([hidden])').length}));
    await page.screenshot({path:qaDir+'/'+view.id+'-mobile.png',fullPage:true});
    let navigation=null;
    if(view.steps.length){
      const step=view.steps[Math.floor(view.steps.length/2)];
      await page.locator('[data-step-target="'+step.id+'"]').click();
      navigation=await page.evaluate(id=>({top:document.getElementById(id).getBoundingClientRect().top,bottom:document.getElementById('step-nav').getBoundingClientRect().bottom}),step.id);
    }
    await page.goto(baseUrl+'#'+view.id);
    const deepLink=await page.locator('[data-view="'+index+'"]').getAttribute('aria-selected');
    report.interactions.push({id:view.id,zoom,mobile,navigation,deepLink});
  }
  const download=page.waitForEvent('download');
  await page.evaluate(report=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));a.download='atlas-qa.json';a.click()},report);
  await (await download).saveAs(qaDir+'/atlas-qa.json');
  await page.setViewportSize({width:1600,height:1100});
  await page.goto(baseUrl);
  console.log(JSON.stringify(report));
}
