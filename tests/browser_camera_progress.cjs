// Mock every API: verify mixed cameras and progress without changing real results.
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const fs=require('node:fs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:720}});
    const errors=[],unexpected=[],saves=[];
    let captures=0,failNextListing=false;
    page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(()=>{window.setInterval=()=>0;sessionStorage.setItem('dismissedBatchJobId','camera-fixture')});
    const png=Buffer.from(await page.evaluate(()=>{
      const c=document.createElement('canvas');c.width=32;c.height=24;
      const ctx=c.getContext('2d');ctx.fillStyle='#ddd';ctx.fillRect(0,0,32,24);
      return c.toDataURL('image/png').split(',')[1];
    }),'base64');
    function initialFrames(){return [
      ['a','0001','itof-1110/captures/pig_0001/20261110_090001','draft',false],
      ['b','0001','双目-1110/captures/pig_0001/20261110_090002','corrected',false],
      ['c','0002','itof-1110/captures/pig_0002/20261110_090003','reviewed',true],
      ['d','0003','双目-1110/captures/pig_0003/20261110_090004','pending',false],
      ['e','0004','itof-1110/captures/pig_0004/20261110_090005','pending',false],
      ['u','0005','unlabelled/captures/pig_0005/20261110_090006','pending',false],
    ].map(([id,pig_id,relative_dir,status,reviewed])=>({id,pig_id,relative_dir,status,reviewed,capture_id:relative_dir.split('/').at(-1),revision:0,decision:'pending'}))}
    let frames=initialFrames();
    function initialJob(){return {id:'camera-fixture',status:'stopped',done:4,success:3,failed:1,total:6,output:'C:/fixture/results',errors:[{relative_dir:frames[4].relative_dir,error:'fixture failure'}]}}
    let job=initialJob();
    const metadata=r=>({...r,width:32,height:24,input_mode:'infrared',head_side:'left',notes:'',stats:{area_px:100,valid_depth_ratio:1,warnings:[]},geometry:{status:'verified',pointcloud_ready:true},reference_available:false,depth_available:false});
    await page.route('**/api/**',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.pathname==='/api/captures'){captures++;if(failNextListing){failNextListing=false;return route.fulfill({status:400,json:{error:'temporary listing failure'}})}return route.fulfill({json:{frames,pigs:5,source:'C:/fixture/raw',output:'C:/fixture/results',dataset_id:'camera-progress-fixture',input_mode:'infrared',backend:'sam2',sam_ready:false,native_picker:true,job,export_job:null}})}
      if(url.pathname==='/api/job')return route.fulfill({json:{job,export_job:null}});
      if(url.pathname==='/api/frame')return route.fulfill({json:metadata(frames.find(r=>r.id===url.searchParams.get('id')))});
      if(url.pathname==='/api/image')return route.fulfill({contentType:'image/png',body:png});
      if(url.pathname==='/api/save'){
        const payload=request.postDataJSON();saves.push(payload);const row=frames.find(r=>r.id===payload.id);
        Object.assign(row,{status:payload.reviewed?'reviewed':'corrected',reviewed:payload.reviewed,revision:row.revision+1});
        return route.fulfill({json:metadata(row)});
      }
      unexpected.push(request.method()+' '+url.pathname);return route.fulfill({status:400,json:{error:'Unexpected fixture request'}});
    });
    async function ready(){await page.waitForFunction(()=>state.current?.id==='a'&&!state.busy)}
    async function camera(key){await page.locator(`#cameraFilters button[data-camera="${key}"]`).click()}
    async function ids(){return page.locator('#list .frame').evaluateAll(rows=>rows.map(r=>r.dataset.id))}
    async function text(id){return page.locator('#'+id).textContent()}
    await page.goto(process.argv[2]||'http://127.0.0.1:8768');await ready();
    assert.equal(await text('cameraCountItof'),'3');assert.equal(await text('cameraCountStereo'),'2');assert.equal(await text('cameraCountUnknown'),'1');
    assert.equal(await text('segmentationProgressText'),'4 / 6 · 66.7%');assert.equal(await text('reviewProgressText'),'1 / 3 · 33.3%');
    assert.match(await text('segmentationDetail'),/已生成 3 · 失败 1 · 待处理 2/);
    assert.equal(await page.locator('.cameraBadge.itof').count(),3);assert.equal(await page.locator('.cameraBadge.stereo').count(),2);
    assert.match(await text('capture'),/iToF/);
    await camera('itof');assert.deepEqual(await ids(),['a','c','e']);
    assert.equal(await text('segmentationProgressText'),'3 / 3 · 100%');assert.equal(await text('reviewProgressText'),'1 / 2 · 50%');
    await page.locator('#next').click();await page.waitForFunction(()=>state.current?.id==='c'&&!state.busy);
    await camera('stereo');assert.deepEqual(await ids(),['b','d']);assert.equal(await text('segmentationProgressText'),'1 / 2 · 50%');
    await page.locator('.frame[data-id="b"]').click();await page.waitForFunction(()=>state.current?.id==='b'&&!state.busy);assert.match(await text('capture'),/双目/);
    await page.locator('#filter').selectOption('pending');assert.deepEqual(await ids(),['d']);
    assert.equal(await text('segmentationProgressText'),'1 / 2 · 50%');
    await page.locator('#search').fill('0001');assert.deepEqual(await ids(),[]);assert.equal(await text('segmentationProgressText'),'1 / 2 · 50%');
    assert.equal(await page.locator('.listEmpty').count(),1);
    await page.locator('#search').fill('');await page.locator('#filter').selectOption('all');
    await camera('unknown');assert.deepEqual(await ids(),['u']);assert.equal(await text('segmentationProgressText'),'0 / 1 · 0%');assert.equal(await text('reviewProgressText'),'0 / 0 · 0%');
    await camera('itof');await page.locator('.frame[data-id="a"]').click();await ready();
    await page.locator('#autoNextPig').uncheck();await page.locator('#approve').click();await page.waitForFunction(()=>!state.busy);
    assert.equal(saves.length,1);assert.equal(await text('reviewProgressText'),'2 / 2 · 100%');
    // Old jobs from another dataset must not supply this dataset's failures.
    await page.evaluate(()=>{currentJob={...currentJob,output:'C:/other/results'};renderList()});
    assert.equal(await text('segmentationProgressText'),'2 / 3 · 66.7%');
    // Classification is independent of image mode and understands Windows paths.
    const types=await page.evaluate(()=>[
      cameraType({relative_dir:'captures/pig_1/capture',source_dir:'D:\\双目-1110\\captures\\pig_1\\capture'}),
      cameraType({relative_dir:'itof-1110/captures/pig_1/capture',source_dir:'D:/双目备份/itof-1110/captures/pig_1/capture'}),
      cameraType({relative_dir:'unknown/capture',input_mode:'rgb'}),
      cameraType({relative_dir:'itof/capture',camera_type:'stereo'}),
    ]);assert.deepEqual(types,['stereo','itof','unknown','stereo']);
    frames=initialFrames();job=initialJob();await page.reload();await ready();
    job={...job,status:'running'};await page.evaluate(()=>pollJob());
    const prior=captures;frames[3].status='draft';job={...job,done:5,success:4};
    failNextListing=true;await page.evaluate(()=>pollJob());
    await page.evaluate(()=>pollJob());assert.equal(captures,prior+2);
    await page.evaluate(()=>pollJob());assert.equal(captures,prior+2,'Unchanged progress should not reload captures');
    assert.equal(await text('segmentationProgressText'),'5 / 6 · 83.3%');assert.match(await text('segmentationDetail'),/正在分割/);
    frames[5].status='draft';job={...job,status:'completed',done:6,success:5};
    await page.evaluate(()=>pollJob());await page.waitForFunction(()=>!state.busy);
    assert.equal(await text('segmentationProgressText'),'6 / 6 · 100%');assert.match(await text('segmentationDetail'),/已生成 5 · 失败 1 · 待处理 0/);
    assert.equal(await page.evaluate(()=>$('editor').hidden),false);
    // Empty and single-camera datasets retain valid counts and finite progress.
    await page.evaluate(()=>{state.rows=[];renderList()});assert.equal(await text('segmentationProgressText'),'0 / 0 · 0%');
    await page.evaluate(()=>{state.rows=[{id:'one',pig_id:'1',capture_id:'one',relative_dir:'itof/captures/pig_1/one',status:'draft'}];renderList()});
    await camera('stereo');assert.deepEqual(await ids(),[]);assert.equal(await text('segmentationProgressText'),'0 / 0 · 0%');
    assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
    console.log('PASS: mixed cameras, filters, navigation, approval progress, failures, live updates, unknown/empty datasets');
    // Read-only screenshot against the user's loaded results.
    const live=await browser.newPage({viewport:{width:1440,height:900}});
    const liveErrors=[];live.on('pageerror',e=>liveErrors.push(e.message));
    await live.route('**/api/**',route=>route.request().method()==='GET'?route.continue():route.abort());
    await live.goto(process.argv[2]||'http://127.0.0.1:8768');await live.waitForFunction(()=>state.meta&&!state.busy);
    await live.evaluate(()=>setMode('review'));
    const info=await live.evaluate(()=>({itof:$('cameraCountItof').textContent,stereo:$('cameraCountStereo').textContent,progress:$('segmentationProgressText').textContent,detail:$('segmentationDetail').textContent}));
    console.log('Live dataset:',JSON.stringify(info));
    if(process.env.BROWSER_ARTIFACTS){fs.mkdirSync(process.env.BROWSER_ARTIFACTS,{recursive:true});await live.screenshot({path:path.join(process.env.BROWSER_ARTIFACTS,'camera_progress_1440x900.png')});
      await live.setViewportSize({width:1280,height:600});await live.screenshot({path:path.join(process.env.BROWSER_ARTIFACTS,'camera_progress_1280x600.png')});
      const size=await live.evaluate(()=>({left:document.querySelector('.left').clientHeight,leftContent:document.querySelector('.left').scrollHeight,list:$('list').clientHeight,preview:$('viewport').clientHeight,bodyWidth:document.documentElement.scrollWidth}));
      assert(size.list>=120,'Capture list should retain usable height');assert(size.preview>=300);assert(size.bodyWidth<=1280);assert(size.leftContent<=size.left+1);console.log('1280x600:',JSON.stringify(size));}
    assert.deepEqual(liveErrors,[]);
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
