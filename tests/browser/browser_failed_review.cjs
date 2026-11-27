// Failed-frame opening, screening, recovery and reporting; isolated fixture only.
const {chromium}=require('/Users/mugua-zhaojiangtao/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const root=process.argv[2];assert(root);
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1366,height:768}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8796');await page.waitForFunction(()=>state.meta&&!state.busy);
  const rows=await page.evaluate(()=>state.rows),geometry=rows.find(r=>r.capture_id==='geometry_failed'),model=rows.find(r=>r.capture_id==='model_failed'),missing=rows.find(r=>r.capture_id==='unreadable');
  async function open(row){await page.evaluate(id=>openFrame(state.rows.find(r=>r.id===id)),row.id);await page.waitForFunction(id=>state.current?.id===id&&state.meta&&!state.busy,row.id)}
  await page.locator('#recordsOpen').click();await page.locator('#recordsFilter').selectOption('failed');
  await page.locator(`[data-review-id="${geometry.id}"]`).click();
  await page.waitForFunction(()=>state.meta?.review_only&&!state.busy);
  assert.equal(await page.locator('#recordsDialog').isVisible(),false);
  assert.deepEqual(await page.evaluate(()=>[$('base').width,$('base').height]),[120,160]);
  assert((await page.locator('#geometryNote').innerText()).includes('原始 RGB 尺寸'));
  assert.equal(await page.locator('#save').innerText(),'保存复核记录');
  for(const id of ['keepResult','rejectResult','unsureResult','save'])assert(await page.locator('#'+id).isEnabled(),id);
  for(const id of ['approve','samPredict','restore'])assert(await page.locator('#'+id).isDisabled(),id);
  assert(await page.locator('[data-tool="brush"]').isDisabled());
  for(const [w,h]of [[1366,768],[1280,720],[1024,768]]){
   await page.setViewportSize({width:w,height:h});await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   const size=await page.evaluate(()=>({side:document.querySelector('.right').scrollHeight-document.querySelector('.right').clientHeight,bottom:$('approve').getBoundingClientRect().bottom,w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight}));
   assert(size.side<=1&&size.bottom<=h&&size.w<=w&&size.h<=h,JSON.stringify({w,h,size}));
  }
  await page.setViewportSize({width:1366,height:768});
  await page.locator('#head').selectOption('right');await page.locator('#notes').fill('尺寸异常，需核查');
  await page.locator('#rejectResult').click();await page.waitForFunction(()=>!state.busy&&!state.dirty&&state.meta.decision==='reject');
  assert.equal(await page.evaluate(()=>state.meta.status),'failed');
  await page.locator('#notes').fill('尺寸异常，已登记复核');await page.locator('#save').click();await page.waitForFunction(()=>!state.busy&&!state.dirty);
  await page.reload();await page.waitForFunction(()=>state.meta&&!state.busy);await open(geometry);
  assert.equal(await page.locator('#notes').inputValue(),'尺寸异常，已登记复核');assert.equal(await page.locator('#head').inputValue(),'right');
  assert(await page.locator('#rejectResult').evaluate(e=>e.classList.contains('selected')));
  const frameDir=path.join(root,'results',geometry.relative_dir);
  assert(!fs.existsSync(path.join(frameDir,'trunk_mask.png')));
  await open(missing);assert((await page.locator('#geometryNote').innerText()).includes('原图无法读取'));
  await page.locator('#notes').fill('需重新采集');await page.locator('#unsureResult').click();await page.waitForFunction(()=>!state.busy&&!state.dirty&&state.meta.decision==='unsure');
  await open(model);assert.equal(await page.evaluate(()=>state.meta.review_only),false);
  assert((await page.locator('#samPredict').innerText()).includes('重新分割'));assert(await page.locator('#restore').isDisabled());
  await page.locator('#autoNext').check();await page.locator('#head').selectOption('left');await page.locator('#approve').click();
  await page.waitForFunction(()=>!state.busy&&$('message').textContent.includes('掩码为空'));
  assert.equal(await page.evaluate(()=>state.current.id),model.id);
  await page.locator('[data-tool="samBox"]').click();
  const canvas=await page.locator('#paint').boundingBox();
  await page.mouse.move(canvas.x+canvas.width*.2,canvas.y+canvas.height*.2);await page.mouse.down();
  await page.mouse.move(canvas.x+canvas.width*.8,canvas.y+canvas.height*.8);await page.mouse.up();
  await page.locator('#samPredict').click();await page.waitForFunction(()=>state.samPredictionId&&!state.busy);
  await page.locator('#keepResult').click();await page.waitForFunction(()=>!state.busy&&state.meta.decision==='keep');
  assert(await page.evaluate(()=>state.dirty&&state.maskDirty),'Screening preserves unsaved mask changes');
  await page.locator('#autoNext').uncheck();await page.locator('#approve').click();
  await page.waitForFunction(()=>!state.busy&&!state.dirty&&state.meta.status==='reviewed');
  assert(await page.locator('#restore').isDisabled(),'Recovered frame has no automatic draft');
  assert.equal(await page.evaluate(()=>workspaceData.failures.find(f=>f.relative_dir===state.current.relative_dir).failure_status),'resolved');
  const csv=await (await page.request.get('http://127.0.0.1:8796'+await page.locator('#csvExport').getAttribute('href'))).text();
  assert(csv.includes('尺寸异常，已登记复核')&&csv.includes('右侧')&&csv.includes('需重新采集')&&csv.includes('已补充分割'));
  await page.locator('#exportOpen').click();await page.locator('#exportParent').fill(path.join(root,'exports'));await page.locator('#exportName').fill('checked');
  await page.locator('#exportStart').click();await page.waitForFunction(()=>currentExport?.status==='completed');
  const dest=path.join(root,'exports/checked');assert.equal(JSON.parse(fs.readFileSync(path.join(dest,'dataset.json'))).frames.length,1);
  for(const filename of ['筛选记录.csv','分割失败记录.csv']){
   const text=fs.readFileSync(path.join(dest,filename),'utf8');assert(text.includes('尺寸异常，已登记复核')&&text.includes('需重新采集')&&text.includes('右侧'),filename);
  }
  await page.locator('#exportClose').click();await page.locator('#openResults').click();await page.locator('#resultFolder').fill(dest);await page.locator('#loadResults').click();
  await page.waitForFunction(()=>state.rows.length===1&&!state.busy&&$('workflow').hidden);
  const reopened=await (await page.request.get('http://127.0.0.1:8796'+await page.locator('#csvExport').getAttribute('href'))).text();
  assert(reopened.includes('尺寸异常，已登记复核')&&reopened.includes('右侧')&&reopened.includes('需重新采集'));
  assert.deepEqual(errors,[]);
  console.log('PASS: failed raw review, unreadable source review, screening and head/notes persistence, strict geometry, SAM/manual recovery, empty approval stays, missing draft disabled, CSV/export/reopen history, desktop layout');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
