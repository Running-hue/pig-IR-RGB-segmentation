// Read-only layout checks against an already running workbench with results loaded.
const {chromium}=require('/Users/mugua-zhaojiangtao/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

(async()=>{
  const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--no-sandbox']});
  try{
    const page=await browser.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    // Never mutate the current dataset during visual verification.
    await page.route('**/api/**',route=>route.request().method()==='GET'?route.continue():route.abort());
    await page.goto(process.argv[2]||'http://127.0.0.1:8768');
    await page.waitForFunction(()=>state.meta&&!state.busy);
    assert.equal(await page.locator('header #csvExport').count(),1);
    assert.equal(await page.locator('.left #csvExport').count(),0);
    const csv=await page.locator('#csvExport').getAttribute('href');
    assert(csv.includes('dataset_id='));
    assert((await page.request.get(new URL(csv,page.url()).href)).ok());
    const artifacts=path.join(__dirname,'..','artifacts');fs.mkdirSync(artifacts,{recursive:true});
    for(const [width,height]of [[1440,900],[1423,776],[1366,768],[1280,720],[1024,768]]){
      await page.setViewportSize({width,height});
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const layout=await page.evaluate(()=>{
        const side=document.querySelector('.right'),approve=$('approve').getBoundingClientRect();
        return {sideHeight:side.clientHeight,sideContent:side.scrollHeight,approveBottom:approve.bottom,
          bodyWidth:document.documentElement.scrollWidth,bodyHeight:document.documentElement.scrollHeight,
          width:innerWidth,height:innerHeight};
      });
      console.log(width+'×'+height,JSON.stringify(layout));
      assert(layout.sideContent<=layout.sideHeight+1,'Right sidebar overflows at '+width+'×'+height);
      assert(layout.approveBottom<=height,'Approval button below viewport');
      assert(layout.bodyWidth<=width,'Page overflows horizontally');
      assert(layout.bodyHeight<=height,'Page overflows vertically');
      for(const id of ['keepResult','rejectResult','unsureResult','save','approve']){
        const button=page.locator('#'+id);
        if(await button.isEnabled())await button.click({trial:true});
        else assert(await page.evaluate(()=>state.meta.geometry_legacy),'Unexpected disabled control: '+id);
      }
      await page.screenshot({path:path.join(artifacts,`layout_${width}x${height}.png`)});
    }
    // Freeze polling at the network boundary while exercising banner states.
    await page.route('**/api/job',route=>route.fulfill({json:{job:null}}));
    await page.waitForTimeout(1700);
    for(const [status,extra,hidden]of [
      ['completed',{},true],['running',{},false],['stopping',{},false],
      ['completed',{failed:1,errors:[{relative_dir:'test',error:'测试失败记录'}]},true],
      ['failed',{error:'测试异常'},false],['stopped',{},true],['interrupted',{},true]
    ]){
      const result=await page.evaluate(({status,extra})=>{
        showJob({status,total:2,done:2,success:2,failed:0,errors:[],...extra});
        return {hidden:$('jobPanel').hidden,errorVisible:!$('jobErrorDetails').hidden};
      },{status,extra});
      assert.equal(result.hidden,hidden,status+' banner visibility');
      if(extra.error||extra.errors)assert(result.errorVisible);
    }
    await page.evaluate(()=>{showJob(null);$('warningDetails').open=true});
    assert(await page.locator('#warn').isVisible(),'Review warnings can be expanded');
    await page.evaluate(()=>$('warningDetails').open=false);
    // Read-only regression against failed captures in the user's selected batch.
    const failed=await page.evaluate(()=>state.rows.filter(r=>r.status==='failed').map(r=>r.id));
    for(const id of failed){
      await page.evaluate(id=>openFrame(state.rows.find(r=>r.id===id)),id);
      await page.waitForFunction(id=>state.current?.id===id&&state.meta&&!state.busy,id);
      for(const control of ['keepResult','rejectResult','unsureResult'])assert(await page.locator('#'+control).isEnabled());
      if(await page.evaluate(()=>state.meta.review_only)){
        assert(await page.locator('#save').isEnabled());
        assert(await page.locator('#approve').isDisabled());assert(await page.locator('#samPredict').isDisabled());
        assert((await page.locator('#geometryNote').innerText()).includes('校正底图不可用'));
      }
      const dims=await page.evaluate(()=>({w:$('base').width,h:$('base').height,mw:$('mask').width,mh:$('mask').height}));
      assert(dims.w===dims.mw&&dims.h===dims.mh,'Failed capture preview grid mismatch');
      await page.screenshot({path:path.join(artifacts,`failed_review_${id}.png`)});
    }
    await page.setViewportSize({width:390,height:844});
    assert(await page.locator('#csvExport').isVisible());
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Mobile horizontal overflow');
    assert.deepEqual(errors,[]);
    console.log(`PASS: read-only layout, CSV download, job states, warnings and ${failed.length} failed captures opened for review`);
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
