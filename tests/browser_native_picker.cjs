// Exercise every folder entry with mocked APIs; never change the user's dataset.
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:720}});
    const errors=[],unexpected=[],requests=[];
    let response={cancelled:false,path:'C:\\中文 空格\\所选目录'},failure=false,release=null,hold=false;
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/**',async route=>{
      const request=route.request(),pathname=new URL(request.url()).pathname;
      if(pathname==='/api/captures')return route.fulfill({json:{frames:[],pigs:0,source:'C:/fixture/raw',output:'C:/fixture/results',dataset_id:'folder-picker-test',native_picker:true,last_parent:'C:/fixture',input_mode:'infrared',backend:'sam2',sam_ready:false,job:null,export_job:null}});
      if(pathname==='/api/job')return route.fulfill({json:{job:null,export_job:null}});
      if(pathname==='/api/choose_directory'){
        requests.push(request.postDataJSON());
        if(hold)await new Promise(resolve=>{release=resolve});
        return route.fulfill({status:failure?400:200,json:failure?{error:'模拟系统窗口失败'}:response});
      }
      unexpected.push(request.method()+' '+pathname);
      return route.fulfill({status:400,json:{error:'Unexpected fixture request'}});
    });
    await page.goto(process.argv[2]||'http://127.0.0.1:8768');
    await page.waitForFunction(()=>workspaceData?.dataset_id==='folder-picker-test');
    for(const [mode,button,input,title] of [
      ['new','chooseSource','batchSource','选择 iToF 或双目原始数据目录'],
      ['new','chooseParent','batchParent','选择成果保存位置'],
      ['open','chooseResults','resultFolder','选择分割成果根目录'],
      ['open','chooseRelocated','resultSource','选择移动后的原始数据目录'],
      ['export','chooseExportParent','exportParent','选择筛选成果的导出位置'],
    ]){
      if(mode==='export')await page.locator('#exportOpen').click();
      else await page.locator(mode==='new'?'#newBatch':'#openResults').click();
      const before='C:\\旧目录\\'+input;
      await page.locator('#'+input).fill(before);
      const count=requests.length;
      await page.locator('#'+button).click();
      await page.waitForFunction(()=>!directorySelecting);
      assert.equal(requests.length,count+1);
      assert.equal(requests.at(-1).path,before);
      assert.equal(requests.at(-1).title,title);
      assert.equal(await page.locator('#'+input).inputValue(),response.path);
      assert.equal(await page.locator('#directoryDialog').evaluate(e=>e.open),false);
    }
    response={cancelled:true,path:null};
    const before=await page.locator('#exportParent').inputValue();
    await page.locator('#chooseExportParent').click();
    await page.waitForFunction(()=>!directorySelecting);
    assert.equal(await page.locator('#exportParent').inputValue(),before);
    failure=true;
    await page.locator('#chooseExportParent').click();
    await page.waitForFunction(()=>!directorySelecting);
    assert.match(await page.locator('#exportMessage').textContent(),/模拟系统窗口失败/);
    assert.equal(await page.locator('#chooseExportParent').isEnabled(),true);
    failure=false;hold=true;
    await page.locator('#chooseExportParent').click();
    await page.waitForFunction(()=>directorySelecting);
    assert.equal(await page.locator('#chooseExportParent').isDisabled(),true);
    assert.equal(await page.locator('#exportClose').isDisabled(),true);
    while(!release)await new Promise(resolve=>setTimeout(resolve,10));
    release();
    await page.waitForFunction(()=>!directorySelecting);
    assert.equal(await page.locator('#chooseExportParent').isEnabled(),true);
    assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
    console.log('PASS: all five native picker entries, cancel, failure recovery, and busy controls');
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
