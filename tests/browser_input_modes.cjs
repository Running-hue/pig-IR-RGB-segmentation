// Run against browser_input_modes_fixture.py; never edits the user's results.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
let chromium;
try{({chromium}=require('playwright'))}catch{
  ({chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')));
}
(async()=>{
  const root=process.argv[2];assert(root,'Pass isolated fixture root');
  const address=process.argv[3]||'http://127.0.0.1:8793';
  const chrome=process.env.CHROME_PATH||(process.platform==='darwin'?'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':undefined);
  const browser=await chromium.launch({executablePath:chrome,headless:true,args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:1366,height:768}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(address);await page.waitForFunction(()=>workspaceData!==null);
    const folders={};
    for(const mode of ['infrared','rgb']){
      await page.locator('#newBatch').click();
      await page.locator('#batchInputMode').selectOption(mode);
      await page.locator('#batchSource').fill(path.join(root,'raw'));
      await page.locator('#batchParent').fill(path.join(root,'results'));
      await page.locator('#batchName').fill(mode);
      await page.locator('#scanSource').click();
      await page.waitForFunction(()=>!flowBusy);
      assert((await page.locator('#scanResult').innerText()).includes('1 次采集'));
      await page.locator('#startBatch').click();
      await page.waitForFunction(m=>currentJob?.input_mode===m&&!state.jobActive&&state.meta?.input_mode===m&&!state.busy,mode,{timeout:180000});
      assert.equal(await page.evaluate(()=>currentJob.success),1,await page.locator('#jobText').innerText());
      const label=mode==='rgb'?'RGB':'红外';
      assert((await page.locator('[data-view="original"]').innerText()).includes(label));
      assert((await page.locator('#referenceLabel').innerText()).includes(mode==='rgb'?'红外':'RGB'));
      assert((await page.locator('#samPredict').innerText()).includes(label));
      const dimensions=await page.evaluate(()=>({w:$('base').width,h:$('base').height,mw:$('mask').width,mh:$('mask').height}));
      assert.equal(dimensions.w,dimensions.mw);assert.equal(dimensions.h,dimensions.mh);
      folders[mode]={folder:path.join(root,'results',mode),dimensions};
      // Interactive inference must use the same sensor and native coordinates as the batch.
      await page.locator('[data-tool="samBox"]').click();
      const canvas=await page.locator('#paint').boundingBox();
      await page.mouse.move(canvas.x+canvas.width*.12,canvas.y+canvas.height*.15);
      await page.mouse.down();await page.mouse.move(canvas.x+canvas.width*.92,canvas.y+canvas.height*.88);await page.mouse.up();
      await page.locator('#samPredict').click();
      await page.waitForFunction(()=>state.samPredictionId&&!state.busy,null,{timeout:120000});
      await page.locator('#head input[value=left]').check();await page.locator('#save').click();
      await page.waitForFunction(()=>!state.dirty&&!state.busy&&state.meta.status==='corrected');
      await page.locator('#keepResult').click();await page.waitForFunction(()=>state.meta.decision==='keep'&&!state.busy);
      for(const [width,height] of [[1366,768],[1280,720],[1024,768]]){
        await page.setViewportSize({width,height});
        await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
        const layout=await page.evaluate(()=>({w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight,
          side:$('approve').getBoundingClientRect().bottom,overflow:document.querySelector('.right').scrollHeight-document.querySelector('.right').clientHeight}));
        assert(layout.w<=width&&layout.h<=height,JSON.stringify({mode,width,height,layout}));
        assert(layout.side<=height&&layout.overflow<=1,JSON.stringify({mode,width,height,layout}));
      }
      await page.setViewportSize({width:1366,height:768});
      const artifacts=path.join(__dirname,'artifacts');fs.mkdirSync(artifacts,{recursive:true});
      await page.screenshot({path:path.join(artifacts,`input_mode_${mode}.png`)});
      const rowDir=path.join(folders[mode].folder,'pig_1','capture');
      const meta=JSON.parse(fs.readFileSync(path.join(rowDir,'segmentation.json')));
      assert.equal(meta.input_mode,mode);assert.equal(meta.last_sam_prediction.input_mode,mode);
      assert.deepEqual(meta.shape_hw,[dimensions.h,dimensions.w]);
      const geometry=JSON.parse(fs.readFileSync(path.join(rowDir,'geometry.json')));
      assert.equal(geometry.status,'verified');assert.equal(geometry.pointcloud_ready,true);
      assert.equal(geometry.coordinate_space,mode==='rgb'?'aligned_color':'depth_native');
      assert(fs.existsSync(path.join(rowDir,'image_valid_mask.png')));
      assert((await page.locator('#geometryNote').innerText()).includes(mode==='rgb'?'匹配保存的对齐深度':'原始深度像素几何'));
      await page.reload();await page.waitForFunction(m=>state.meta?.input_mode===m&&!state.busy,mode);
    }
    assert.notDeepEqual(folders.rgb.dimensions,folders.infrared.dimensions);
    // RGB exports retain the chosen mode and can be reopened for further review.
    await page.locator('#exportOpen').click();await page.locator('#exportParent').fill(path.join(root,'results'));
    await page.locator('#exportName').fill('rgb_export');await page.locator('#exportStart').click();
    await page.waitForFunction(()=>currentExport?.status==='completed',null,{timeout:30000});
    await page.locator('#exportClose').click();
    assert(fs.existsSync(path.join(root,'results/rgb_export/pig_1/capture/geometry.json')));
    for(const [folder,mode] of [[path.join(root,'results','rgb_export'),'rgb'],[folders.infrared.folder,'infrared'],[folders.rgb.folder,'rgb']]){
      await page.locator('#openResults').click();await page.locator('#resultFolder').fill(folder);await page.locator('#loadResults').click();
      await page.waitForFunction(m=>state.meta?.input_mode===m&&!state.busy&&$('workflow').hidden,mode);
      assert((await page.locator('#reviewInputMode').innerText()).includes(mode==='rgb'?'RGB':'红外'));
    }
    const legacy=path.join(root,'results/legacy_rgb');fs.mkdirSync(path.join(legacy,'pig_1/capture'),{recursive:true});
    const manifest=JSON.parse(fs.readFileSync(path.join(folders.rgb.folder,'dataset.json')));
    delete manifest.geometry_version;manifest.output=legacy;
    fs.writeFileSync(path.join(legacy,'dataset.json'),JSON.stringify(manifest));
    const frame=path.join(legacy,'pig_1/capture');
    fs.copyFileSync(path.join(folders.rgb.folder,'pig_1/capture/trunk_mask_draft.png'),path.join(frame,'trunk_mask_draft.png'));
    fs.writeFileSync(path.join(frame,'segmentation.json'),JSON.stringify({input_mode:'rgb',status:'draft',stats:{valid_depth_ratio:1}}));
    await page.locator('#openResults').click();await page.locator('#resultFolder').fill(legacy);await page.locator('#loadResults').click();
    await page.waitForFunction(()=>state.meta?.geometry_legacy&&!state.busy&&$('workflow').hidden);
    assert(await page.locator('#save').isDisabled());assert(await page.locator('#samPredict').isDisabled());
    assert((await page.locator('#geometryNote').innerText()).includes('旧 RGB 成果未校正'));
    assert((await page.locator('#stats').innerText()).includes('—'));
    assert.deepEqual(errors,[]);
  console.log(JSON.stringify({pass:true,folders,checks:['IR/RGB batch selector','verified depth coordinates','interactive SAM','save and screening','three desktop sizes','refresh restore','RGB geometry export and reopen','legacy RGB view and screening']},null,2));
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
