// Uses the running app's HTML with all API requests mocked; never saves user data.
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));

(async()=>{
  const chrome=process.env.CHROME_PATH||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
  const browser=await chromium.launch({executablePath:chrome,headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:720}});
    const errors=[],unexpected=[];
    page.on('pageerror',error=>errors.push(error.message));
    const png=Buffer.from(await page.evaluate(()=>{
      const canvas=document.createElement('canvas');canvas.width=32;canvas.height=24;
      const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,32,24);
      return canvas.toDataURL('image/png').split(',')[1];
    }),'base64');
    const initial=[['p1a','0001'],['p1b','0001'],['p2a','0002'],['p3a','0003']];
    let frames,attempts,saveError,frameError,holdSave,releaseSave,notifyPendingSave;
    function meta(row){return {...row,width:32,height:24,input_mode:'infrared',head_side:row.head_side||'unknown',notes:row.notes||'',stats:{area_px:100,valid_depth_ratio:1,warnings:[]},geometry:{status:'verified',pointcloud_ready:true},reference_available:false,depth_available:false}}
    await page.route('**/api/**',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.pathname==='/api/captures')return route.fulfill({json:{frames,pigs:3,source:'C:/fixture/raw',output:'C:/fixture/results',dataset_id:'review-test',input_mode:'infrared',backend:'sam2',sam_ready:false,job:null,export_job:null,startup_warning:''}});
      if(url.pathname==='/api/job')return route.fulfill({json:{job:null,export_job:null}});
      if(url.pathname==='/api/frame'){
        const id=url.searchParams.get('id');
        if(id===frameError)return route.fulfill({status:400,json:{error:'下一张预览读取失败'}});
        return route.fulfill({json:meta(frames.find(row=>row.id===id))});
      }
      if(url.pathname==='/api/image')return route.fulfill({contentType:'image/png',body:png});
      if(url.pathname==='/api/save'){
        const payload=request.postDataJSON();attempts.push(payload);
        if(holdSave)await new Promise(resolve=>{releaseSave=resolve;notifyPendingSave()});
        if(saveError)return route.fulfill({status:400,json:{error:saveError}});
        const row=frames.find(row=>row.id===payload.id);
        Object.assign(row,{status:payload.reviewed?'reviewed':'corrected',reviewed:payload.reviewed,revision:row.revision+1,head_side:payload.head_side,notes:payload.notes});
        return route.fulfill({json:meta(row)});
      }
      unexpected.push(request.method()+' '+url.pathname);
      return route.fulfill({status:400,json:{error:'Unexpected fixture request'}});
    });
    async function reset(autoNext=true){
      frames=initial.map(([id,pig_id])=>({id,pig_id,capture_id:id,relative_dir:'pig_'+pig_id+'/'+id,camera_type:id==='p1b'?'stereo':'itof',status:'draft',reviewed:false,revision:0,decision:'pending'}));
      attempts=[];saveError=null;frameError=null;holdSave=false;releaseSave=null;
      await page.goto(process.argv[2]||'http://127.0.0.1:8768');
      await page.waitForFunction(()=>state.current?.id==='p1a'&&!state.busy);
      if(autoNext!==null)await page.locator('#autoNextPig').setChecked(autoNext);
    }
    async function head(){await page.locator('#head input[value=left]').check()}
    async function approved(){await page.locator('#approve').click();await page.waitForFunction(()=>!state.busy)}

    // All directions are immediately visible; save and reload each stored choice.
    await reset(false);
    assert.equal(await page.locator('#head input:checked').inputValue(),'unknown');
    assert.equal(await page.evaluate(()=>state.dirty),false);
    for(const [value,label] of [['unknown','尚未记录'],['left','左侧'],['right','右侧'],['up','上侧'],['down','下侧']]){
      assert(await page.locator('#head').getByText(label,{exact:true}).isVisible());
      await page.locator('#head input[value='+value+']').check();
      if(value!=='unknown')assert.equal(await page.evaluate(()=>state.dirty),true);
      await page.locator('#save').click();await page.waitForFunction(()=>!state.busy&&!state.dirty);
      assert.equal(attempts.at(-1).head_side,value);
      await page.locator('.frame[data-id="p1b"]').click();
      await page.waitForFunction(()=>state.current?.id==='p1b'&&!state.busy);
      assert.equal(await page.locator('#head input:checked').inputValue(),'unknown');
      await page.locator('.frame[data-id="p1a"]').click();
      await page.waitForFunction(()=>state.current?.id==='p1a'&&!state.busy);
      assert.equal(await page.locator('#head input:checked').inputValue(),value);
    }
    // Radio arrow keys change direction without invoking frame-navigation shortcuts.
    await page.locator('#head input[value=left]').check();
    await page.locator('#head input[value=left]').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#head input:checked').inputValue(),'right');
    assert.equal(await page.evaluate(()=>state.current.id),'p1a');

    // Automatic navigation is opt-in, remembered on reload, and independent of edits.
    await reset(null);
    assert.equal(await page.locator('#autoNextPig').isChecked(),false);
    assert.match(await page.locator('.autoAdvanceOption').textContent(),/确认保存后自动切换下一张/);
    await head();await approved();
    assert.equal(await page.evaluate(()=>state.current.id),'p1a');
    assert.equal(frames[0].reviewed,true);
    assert.equal(await page.evaluate(()=>state.dirty),false);
    await page.locator('#autoNextPig').check();
    assert.equal(await page.evaluate(()=>state.dirty),false);
    await page.reload();await page.waitForFunction(()=>state.meta&&!state.busy);
    assert.equal(await page.locator('#autoNextPig').isChecked(),true);
    await page.locator('#autoNextPig').uncheck();
    await page.reload();await page.waitForFunction(()=>state.meta&&!state.busy);
    assert.equal(await page.locator('#autoNextPig').isChecked(),false);
    await head();await approved();
    assert.equal(await page.evaluate(()=>state.current.id),'p1a');

    // Validation and ordinary draft saving both leave the current capture open.
    await reset();await approved();
    assert.equal(attempts.length,0);assert((await page.locator('#message').textContent()).includes('头侧方向'));
    assert.equal(await page.evaluate(()=>document.activeElement===document.querySelector('#head input:checked')),true);
    await head();await page.locator('#notes').fill('边界已修正');
    await page.locator('#save').click();await page.waitForFunction(()=>!state.busy&&state.meta.status==='corrected');
    assert.equal(await page.evaluate(()=>state.current.id),'p1a');
    assert.equal(attempts[0].reviewed,false);assert.equal(attempts[0].notes,'边界已修正');
    assert(attempts[0].mask.startsWith('data:image/png;base64,'));

    // Approval must await the save response and open the very next capture, even for the same pig.
    await reset();await head();holdSave=true;
    const pendingSave=new Promise(resolve=>{notifyPendingSave=resolve});
    await page.locator('#approve').click();
    await page.waitForFunction(()=>state.busy);
    assert.equal(await page.evaluate(()=>state.current.id),'p1a');
    assert(await page.locator('#autoNextPig').isDisabled(),'Preference must stay fixed while saving');
    for(const radio of await page.locator('#head input').all())assert(await radio.isDisabled(),'Head direction must stay fixed while saving');
    await pendingSave;
    assert(releaseSave,'Save request should be waiting for acknowledgement');
    releaseSave();
    await page.waitForFunction(()=>!state.busy&&state.current?.id==='p1b');
    assert.equal(attempts.length,1);assert.equal(attempts[0].id,'p1a');assert.equal(attempts[0].reviewed,true);
    assert.equal(frames.find(row=>row.id==='p1b').reviewed,false);
    assert((await page.locator('#message').textContent()).includes('已切换到下一张：耳标 0001 · p1b'));

    // Approval can remove the old row from the current filter; keep its original position.
    await reset();await page.locator('#filter').selectOption('draft');await head();await approved();
    await page.waitForFunction(()=>state.current?.id==='p1b'&&!state.busy);
    assert.equal(await page.locator('.frame[data-id="p1a"]').count(),0);

    // Failed saves preserve the current edits and never navigate.
    await reset();await head();saveError='保存失败：修订版本冲突';await approved();
    assert.equal(await page.evaluate(()=>state.current.id),'p1a');
    assert.equal(await page.evaluate(()=>state.dirty),true);
    assert.equal(frames[0].reviewed,false);
    assert((await page.locator('#message').textContent()).includes(saveError));

    // The last capture stays open; searching also limits the automatic destination.
    await reset();await page.locator('.frame[data-id="p3a"]').click();
    await page.waitForFunction(()=>state.current?.id==='p3a'&&!state.busy);
    await head();await approved();
    assert.equal(await page.evaluate(()=>state.current.id),'p3a');
    assert((await page.locator('#message').textContent()).includes('最后一张'));
    await reset();await page.locator('#search').fill('0001');await head();await approved();
    await page.waitForFunction(()=>state.current?.id==='p1b'&&!state.busy);
    await head();await approved();
    assert.equal(await page.evaluate(()=>state.current.id),'p1b');
    assert((await page.locator('#message').textContent()).includes('最后一张'));

    // Camera filtering limits the next capture, and the normal list crosses pigs in order.
    await reset();await head();await approved();
    await page.waitForFunction(()=>state.current?.id==='p1b'&&!state.busy);
    await head();await approved();
    await page.waitForFunction(()=>state.current?.id==='p2a'&&!state.busy);
    await reset();await page.locator('#cameraFilters button[data-camera="itof"]').click();
    await head();await approved();
    await page.waitForFunction(()=>state.current?.id==='p2a'&&!state.busy);

    // A failed next preview must not hide the fact that the current mask was saved.
    await reset();await head();frameError='p1b';await approved();
    await page.waitForFunction(()=>!state.busy&&$('message').classList.contains('error'));
    assert.equal(frames[0].reviewed,true);assert.equal(await page.evaluate(()=>state.dirty),false);
    assert.equal(await page.evaluate(()=>state.current.id),'p1a');
    assert((await page.locator('#message').textContent()).includes('下一张预览读取失败'));
    assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
    console.log('PASS: optional automatic navigation, preference persistence, save acknowledgement, camera/search/status filters, same-pig captures, last capture, failures and draft saves. User data untouched.');
  }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
