// Mixed cameras, automatic navigation, durable errors and compact layouts.
const {chromium}=require('/Users/mugua-zhaojiangtao/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const root=process.argv[2];assert(root);
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1366,height:768}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8795');await page.waitForFunction(()=>state.meta&&!state.busy);
  assert.equal(await page.locator('#list .frame').count(),5);
  assert.equal(await page.locator('[data-camera="itof"]').innerText(),'iToF 2');
  assert.equal(await page.locator('[data-camera="stereo"]').innerText(),'双目 2');
  assert((await page.locator('#segProgressDetail').innerText()).includes('失败 1'));
  assert((await page.locator('#capture').innerText()).includes('iToF'));
  fs.mkdirSync(path.join(__dirname,'..','artifacts'),{recursive:true});
  for(const [w,h] of [[1440,900],[1366,768],[1280,720],[1024,768]]){
   await page.setViewportSize({width:w,height:h});await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   const dims=await page.evaluate(()=>({w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight,side:document.querySelector('.right').scrollHeight-document.querySelector('.right').clientHeight,view:$('viewport').clientHeight,tools:$('viewport').getBoundingClientRect().top-document.querySelector('main').getBoundingClientRect().top,approve:$('approve').getBoundingClientRect().bottom}));
   assert(dims.w<=w&&dims.h<=h,JSON.stringify({w,h,dims}));assert(dims.side<=1&&dims.approve<=h,JSON.stringify({w,h,dims}));
   if(w>=1280)assert(dims.tools<=160&&dims.view>=h*.5,JSON.stringify({w,h,dims}));
   await page.screenshot({path:path.join(__dirname,'..','artifacts',`review_compact_${w}.png`)});
  }
  await page.setViewportSize({width:1366,height:768});
  await page.locator('[data-camera="itof"]').click();assert.equal(await page.locator('#list .frame').count(),2);
  await page.locator('#filter').selectOption('draft');await page.locator('#list .frame').first().click();
  await page.waitForFunction(()=>state.current.camera_type==='itof'&&!state.busy);
  const first=await page.evaluate(()=>state.current.id);
  await page.locator('#autoNext').check();await page.locator('#head').selectOption('left');await page.locator('#approve').click();
  await page.waitForFunction(id=>state.current.id!==id&&!state.busy,first);
  assert.equal(await page.evaluate(()=>state.current.camera_type),'itof');const second=await page.evaluate(()=>state.current.id);
  await page.locator('#head').selectOption('left');
  await page.route('**/api/save',route=>route.fulfill({status:400,json:{error:'测试保存失败'}}));
  await page.locator('#approve').click();await page.waitForFunction(()=>!state.busy&&$('message').textContent.includes('测试保存失败'));
  assert.equal(await page.evaluate(()=>state.current.id),second);
  await page.unroute('**/api/save');
  await page.locator('#save').click();await page.waitForFunction(()=>!state.busy&&!state.dirty);
  assert.equal(await page.evaluate(()=>state.current.id),second); // ordinary save never advances
  await page.locator('#filter').selectOption('all');
  await page.locator('#approve').click();await page.waitForFunction(()=>!state.busy&&state.meta.reviewed);
  assert.equal(await page.evaluate(()=>state.current.id),second);assert((await page.locator('#message').innerText()).includes('最后一张'));
  await page.reload();await page.waitForFunction(()=>state.meta&&!state.busy);assert(await page.locator('#autoNext').isChecked());
  await page.locator('#autoNext').uncheck();await page.locator('[data-camera="stereo"]').click();
  await page.locator('#list .frame').first().click();await page.waitForFunction(()=>state.current.camera_type==='stereo'&&!state.busy);
  const stereo=await page.evaluate(()=>state.current.id);await page.locator('#head').selectOption('left');await page.locator('#approve').click();await page.waitForFunction(()=>!state.busy&&state.meta.reviewed);
  assert.equal(await page.evaluate(()=>state.current.id),stereo);
  await page.locator('#recordsOpen').click();await page.waitForFunction(()=>$('recordsDialog').open);
  await page.locator('#recordsFilter').selectOption('failed');assert((await page.locator('#recordsBody').innerText()).includes('模型未找到猪体'));
  await page.locator('#recordsClose').click();await page.locator('#keepResult').click();await page.waitForFunction(()=>!state.busy&&state.meta.decision==='keep');
  const csvUrl=await page.locator('#csvExport').getAttribute('href');const csv=await (await page.request.get('http://127.0.0.1:8795'+csvUrl)).text();
  assert(csv.includes('模型未找到猪体')&&csv.includes('相机')&&csv.includes('failed'));
  await page.locator('#exportOpen').click();await page.locator('#exportParent').fill(path.join(root,'exports'));await page.locator('#exportName').fill('reviewed');
  assert((await page.locator('#exportSummary').innerText()).includes('失败记录 1 条'));
  await page.locator('#exportStart').click();await page.waitForFunction(()=>currentExport?.status==='completed');
  const dest=path.join(root,'exports/reviewed');assert(fs.existsSync(path.join(dest,'分割失败记录.csv')));
  assert(fs.readFileSync(path.join(dest,'筛选记录.csv'),'utf8').includes('否（仅失败记录）'));
  assert.equal(JSON.parse(fs.readFileSync(path.join(dest,'dataset.json'))).frames.length,1);
  await page.locator('#exportClose').click();await page.locator('#openResults').click();await page.locator('#resultFolder').fill(dest);await page.locator('#loadResults').click();
  await page.waitForFunction(()=>state.rows.length===1&&!state.busy&&$('workflow').hidden);
  await page.locator('#recordsOpen').click();assert((await page.locator('#recordsBody').innerText()).includes('模型未找到猪体'));await page.locator('#recordsClose').click();
  // A failed image load clears the previous canvas and leaves manual navigation usable.
  await page.route('**/api/frame?*',route=>route.fulfill({status:400,json:{error:'测试原始目录不存在'}}));
  await page.locator('#list .frame').first().click();await page.waitForFunction(()=>!state.busy&&state.meta===null);
  assert.equal(await page.evaluate(()=>bc.getImageData(0,0,1,1).data[3]),0);
  assert(await page.locator('#next').isEnabled());
  await page.unroute('**/api/frame?*');
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);console.log('PASS: mixed camera labels/filter, compact desktop/mobile layouts, auto-next persistence/filter/end/failure, ordinary save stays, failure CSV/export/reopen, empty failed-load canvas');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
