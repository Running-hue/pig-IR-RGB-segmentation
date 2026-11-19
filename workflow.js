// Folder selection, background jobs and result decisions for the local editor.
let workspaceData=null;
let currentJob=null;
let dismissedJobId=sessionStorage.getItem('dismissedBatchJobId');
let directoryTarget=null;
let currentDirectory=null;
let polling=false;
let flowBusy=false;
let directorySelecting=false;
let currentExport=null;
let batchInputInitialized=false;

function flowMessage(text,error=false){$('flowMessage').textContent=text;$('flowMessage').style.color=error?'#ffb0aa':'#a9d8c6'}
function jobRunning(job){return job&&['running','stopping'].includes(job.status)}
function setFlowBusy(value){flowBusy=value;for(const id of ['batchInputMode','startBatch','scanSource','loadResults','newBatch','openResults','reviewCurrent','exportOpen','chooseSource','chooseParent','chooseResults','chooseRelocated'])$(id).disabled=value||state.jobActive||state.exportActive||directorySelecting}
function setMode(mode){
  if(state.busy||flowBusy||state.exportActive||directorySelecting)return;
  if(state.jobActive&&mode!=='review'){message('请等待分割完成，或先停止当前任务。',true);return}
  $('workflow').hidden=mode==='review';
  $('editor').hidden=mode!=='review'||!workspaceData?.output||state.jobActive;
  $('newBatchForm').hidden=mode!=='new';$('openResultsForm').hidden=mode!=='open';
  $('currentDataset').hidden=!workspaceData?.output;
  $('flowClose').hidden=!workspaceData?.output;
  $('flowOpenResults').disabled=!workspaceData?.output;
  if(mode==='review'&&currentJob&&!state.jobActive){
    dismissedJobId=currentJob.id;
    sessionStorage.setItem('dismissedBatchJobId',dismissedJobId);
    showJob(currentJob);
  }
  if(mode!=='review')flowMessage('');
  requestAnimationFrame(fit);
}
function newFolderName(){const d=new Date();const pad=n=>String(n).padStart(2,'0');return 'itof_SAM2_'+($('batchInputMode').value==='rgb'?'RGB_':'IR_')+d.getFullYear()+pad(d.getMonth()+1)+pad(d.getDate())+'_'+pad(d.getHours())+pad(d.getMinutes())+pad(d.getSeconds())}
function batchInputHint(){
  const rgb=$('batchInputMode').value==='rgb';
  $('inputModeHint').textContent=rgb?'按 reconstruction.json 将 RGB 校正到保存的对齐深度网格，分割与复核共用该坐标。':'保留红外／原始深度网格；点云使用实际深度内参与畸变模型。';
}
function allowDirectoryChange(){return !state.dirty||window.confirm('当前图像有未保存修改。是否放弃这些修改并切换目录？')}
function resetEditor(){
  state.current=null;state.meta=null;state.dirty=false;state.points=[];state.samPoints=[];
  state.samBox=null;state.samStart=null;state.samPredictionId=null;state.undo=[];state.redo=[];
  for(const [canvas,ctx]of [[$('base'),bc],[$('paint'),pc],[$('mask'),mc]])ctx.clearRect(0,0,canvas.width,canvas.height);
  $('title').textContent='选择一张底图';$('capture').textContent='';$('notes').value='';
  $('geometryNote').textContent='';
}
async function adoptWorkspace(data,{loadFirst=true}={}){
  const changed=state.datasetId!==data.dataset_id;
  if(changed)resetEditor();
  workspaceData=data;state.datasetId=data.dataset_id;state.rows=data.frames||[];
  $('samTools').hidden=!data.sam_ready;$('generate').hidden=data.backend==='sam2'||data.input_mode==='rgb';
  $('source').textContent=data.output?'当前成果（'+(data.input_mode==='rgb'?'RGB':'红外')+'）：'+data.output:'尚未打开成果目录';
  $('source').title=[data.output?'当前成果：'+data.output:'',data.source?'原始数据：'+data.source:''].filter(Boolean).join('\n');
  $('currentDataset').textContent=data.output?'当前成果：'+data.output:'';
  $('reviewCurrent').hidden=!data.output;
  $('csvExport').href='/api/review.csv?dataset_id='+encodeURIComponent(data.dataset_id||'');
  $('csvExport').hidden=!data.output;
  $('exportOpen').hidden=!data.output;
  if(!$('exportParent').value)$('exportParent').value=data.last_export_parent||data.last_parent||'';
  if(!$('batchSource').value)$('batchSource').value=data.last_source||data.source||'';
  if(!$('batchParent').value)$('batchParent').value=data.last_parent||'';
  if(!batchInputInitialized){$('batchInputMode').value=data.last_input_mode||'infrared';batchInputHint();$('batchName').value=newFolderName();batchInputInitialized=true}
  if(!$('resultFolder').value)$('resultFolder').value=data.output||'';
  showJob(data.job);
  showExportJob(data.export_job);
  $('editor').hidden=!data.output||state.jobActive;
  $('emptyHint').hidden=!!data.output&&!state.jobActive;
  $('emptyHint').replaceChildren();
  if(state.jobActive){const h=document.createElement('h2');h.textContent='正在分割，进度会自动更新';const p=document.createElement('p');p.textContent='可以暂时关闭页面；保持本机工作台服务运行，稍后重新打开查看。';$('emptyHint').append(h,p)}
  else if(!data.output){const h=document.createElement('h2');h.textContent='选择原始目录开始分割';const p=document.createElement('p');p.textContent='已有结果可使用上方“打开成果目录”继续筛选和修正。';$('emptyHint').append(h,p)}
  renderList();
  if(loadFirst&&!state.jobActive&&state.rows.length){
    const row=state.rows.find(r=>r.id===state.current?.id)||state.rows[0];
    if(!state.dirty)await openFrame(row);
  }
  controls(false);
}
function showJob(job){
  currentJob=job;state.jobActive=!!jobRunning(job);
  const completedCleanly=job?.status==='completed'&&!job.failed&&!job.error&&!job.errors?.length;
  const dismissed=!!job?.id&&job.id===dismissedJobId&&!state.jobActive;
  $('jobPanel').hidden=!job||completedCleanly||dismissed;
  $('jobDetails').hidden=!job||completedCleanly||state.jobActive||!$('jobPanel').hidden;
  if(job){
    const label={running:'正在分割',stopping:'正在停止',completed:'分割完成',stopped:'已停止',failed:'任务失败',interrupted:'任务中断'}[job.status]||job.status;
    $('jobText').textContent=`${label} · ${job.done} / ${job.total} · 成功 ${job.success} · 失败 ${job.failed}　${job.current||''}`;
    $('jobProgress').max=Math.max(1,job.total);$('jobProgress').value=job.done;
    $('stopBatch').hidden=!state.jobActive;$('stopBatch').disabled=job.status==='stopping';
    $('enterReview').hidden=state.jobActive;
    const errors=(job.errors||[]).map(e=>e.relative_dir+'：'+e.error);
    if(job.error)errors.unshift(job.error);
    $('jobErrorDetails').hidden=!errors.length;$('jobErrors').textContent=errors.join('\n');
  }
  setFlowBusy(flowBusy);controls(state.busy);
}
async function browseDirectory(path){
  $('directoryError').textContent='';$('directoryUse').disabled=true;
  for(const id of ['directoryPath','directoryGo','directoryUp'])$(id).disabled=true;
  try{
    const d=await api('/api/directories',{path:path||null});currentDirectory=d;
    $('directoryPath').value=d.path;
    $('directoryInfo').textContent=d.is_results?'该目录包含分割成果清单':'可逐级打开子文件夹，或直接使用当前文件夹';
    const frag=document.createDocumentFragment();
    for(const row of d.directories){const b=document.createElement('button');b.textContent='📁 '+row.name;b.onclick=()=>browseDirectory(row.path);frag.append(b)}
    if(!d.directories.length){const p=document.createElement('p');p.className='muted';p.textContent='当前目录没有可见子文件夹';frag.append(p)}
    $('directoryList').replaceChildren(frag);$('directoryUse').disabled=false;
  }catch(e){currentDirectory=null;$('directoryError').textContent=e.message}
  finally{for(const id of ['directoryPath','directoryGo','directoryUp'])$(id).disabled=false}
}
async function chooseDirectory(target,title){
  if(flowBusy||state.jobActive||state.exportActive||state.busy||directorySelecting)return;
  if(workspaceData?.native_picker){
    directorySelecting=true;setFlowBusy(flowBusy);updateExportForm();
    const feedback=target==='exportParent'?$('exportMessage'):$('flowMessage');
    feedback.textContent='请在系统窗口中选择文件夹…';
    try{
      const result=await api('/api/choose_directory',{path:$(target).value||workspaceData?.last_parent||null,title});
      if(!result.cancelled){$(target).value=result.path;$(target).dispatchEvent(new Event('input'))}
      feedback.textContent='';
    }catch(e){feedback.textContent=e.message}
    finally{directorySelecting=false;setFlowBusy(flowBusy);updateExportForm()}
    return;
  }
  directoryTarget=target;$('directoryTitle').textContent=title;$('directoryDialog').showModal();
  await browseDirectory($(target).value||workspaceData?.last_parent||null);
}
$('directoryGo').onclick=()=>browseDirectory($('directoryPath').value);
$('directoryPath').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();browseDirectory($('directoryPath').value)}};
$('directoryUp').onclick=()=>{if(currentDirectory)browseDirectory(currentDirectory.parent)};
$('directoryCancel').onclick=()=>$('directoryDialog').close();
$('directoryUse').onclick=async()=>{
  // Resolve typed paths even if the user did not press Enter first.
  await browseDirectory($('directoryPath').value);
  if(!currentDirectory)return;
  $(directoryTarget).value=currentDirectory.path;$(directoryTarget).dispatchEvent(new Event('input'));
  $('directoryDialog').close();
};
$('chooseSource').onclick=()=>chooseDirectory('batchSource','选择 iToF 或双目原始数据目录');
$('chooseParent').onclick=()=>chooseDirectory('batchParent','选择成果保存位置');
$('chooseResults').onclick=()=>chooseDirectory('resultFolder','选择分割成果根目录');
$('chooseRelocated').onclick=()=>chooseDirectory('resultSource','选择移动后的原始数据目录');
$('chooseExportParent').onclick=()=>chooseDirectory('exportParent','选择筛选成果的导出位置');
$('newBatch').onclick=()=>{if(!$('batchName').value)$('batchName').value=newFolderName();setMode('new')};
$('openResults').onclick=()=>setMode('open');
$('reviewCurrent').onclick=()=>setMode('review');
$('jobDetails').onclick=()=>{
  dismissedJobId=null;
  sessionStorage.removeItem('dismissedBatchJobId');
  showJob(currentJob);
  requestAnimationFrame(fit);
};
$('flowClose').onclick=()=>setMode('review');$('flowOpenResults').onclick=()=>setMode('review');
$('batchSource').oninput=()=>{$('scanResult').textContent=''};
$('batchInputMode').onchange=()=>{$('scanResult').textContent='';batchInputHint();if(!$('batchName').value||/^itof_SAM2_(IR_|RGB_)?\d{8}_\d{6}$/.test($('batchName').value))$('batchName').value=newFolderName()};
$('scanSource').onclick=async()=>{
  setFlowBusy(true);flowMessage('正在检查数据目录…');
  try{const r=await api('/api/scan',{source:$('batchSource').value,input_mode:$('batchInputMode').value});$('scanResult').textContent=`${r.pigs} 头猪 · ${r.frames} 次采集 · ${r.input_mode==='rgb'?'RGB':'红外'}`;flowMessage('数据目录可用，可以开始分割。')}
  catch(e){flowMessage(e.message,true)}finally{setFlowBusy(false)}
};
$('startBatch').onclick=async()=>{
  if(state.busy||!allowDirectoryChange())return;
  setFlowBusy(true);flowMessage('正在准备分割任务…');
  try{
    const d=await api('/api/batch/start',{source:$('batchSource').value,parent:$('batchParent').value,name:$('batchName').value,input_mode:$('batchInputMode').value});
    state.dirty=false;await adoptWorkspace(d);$('resultFolder').value=d.output;
    $('workflow').hidden=true;$('batchName').value=newFolderName();
  }catch(e){flowMessage(e.message,true)}finally{setFlowBusy(false)}
};
$('loadResults').onclick=async()=>{
  if(state.busy||!allowDirectoryChange())return;
  setFlowBusy(true);flowMessage('正在载入成果…');
  try{const d=await api('/api/open_results',{output:$('resultFolder').value,source_override:$('resultSource').value||null});state.dirty=false;await adoptWorkspace(d);$('workflow').hidden=true}
  catch(e){flowMessage(e.message,true)}finally{setFlowBusy(false)}
};
$('stopBatch').onclick=async()=>{try{const d=await api('/api/batch/stop',{job_id:currentJob.id});showJob(d.job)}catch(e){message(e.message,true)}};
$('enterReview').onclick=async()=>{
  try{const d=await api('/api/captures');await adoptWorkspace(d);setMode('review');message('成果已载入；可以保留、淘汰、待定，或修正掩码。')}
  catch(e){message(e.message,true)}
};
async function decideResult(decision){
  if(!state.current||state.busy||state.jobActive)return;
  if(decision==='reject'&&!$('notes').value.trim()){message('请在备注里填写淘汰原因后再点击淘汰。',true);$('notes').focus();return}
  controls(true);
  try{
    const meta=await api('/api/decide',{id:state.current.id,revision:state.meta.revision||0,decision,notes:$('notes').value});
    state.meta={...state.meta,...meta};Object.assign(state.current,{decision:meta.decision,revision:meta.revision});
    readStats(state.meta);renderList();message('筛选已记录：'+decisionNames[decision]+'。'+(state.dirty?'画布修改仍需点击“保存修正”。':''));
  }catch(e){message(e.message,true)}finally{controls(false)}
}
$('keepResult').onclick=()=>decideResult('keep');$('rejectResult').onclick=()=>decideResult('reject');$('unsureResult').onclick=()=>decideResult('unsure');
async function pollJob(){
  if(polling)return;polling=true;
  try{
    const d=await api('/api/job');const wasActive=state.jobActive;showJob(d.job);showExportJob(d.export_job);
    if(wasActive&&!state.jobActive){await adoptWorkspace(await api('/api/captures'));$('workflow').hidden=true;message('任务已结束。请复核分割边界，并记录保留、淘汰或待定。')}
  }catch(e){if(currentJob)$('jobText').textContent='暂时无法连接本机服务；恢复后自动更新。'+e.message}
  finally{polling=false}
}
function exportRows(){
  const scope=$('exportScope').value;
  return state.rows.filter(row=>row.status&&row.status!=='pending'&&(scope==='all'||(scope==='keep_reviewed'&&row.decision==='keep'&&row.reviewed)||(row.decision||'pending')===scope));
}
function updateExportForm(){
  const rows=exportRows();$('exportSummary').textContent=`将导出 ${new Set(rows.map(r=>r.pig_id)).size} 头猪 · ${rows.length} 次采集`;
  const locked=state.exportActive||flowBusy||directorySelecting||state.jobActive;
  for(const id of ['exportScope','exportParent','exportName','chooseExportParent'])$(id).disabled=locked;
  $('exportClose').disabled=locked;
  $('exportStart').disabled=locked||!rows.length;
}
function showExportJob(job){
  currentExport=job||null;state.exportActive=job?.status==='running';
  const progress=$('exportProgress');
  if(job){
    const label={running:'正在导出',completed:'导出完成',failed:'导出失败'}[job.status]||job.status;
    progress.textContent=`${label} · ${job.done} / ${job.total}\n${job.output}`+(job.error?'\n'+job.error:'')+(job.warning?'\n'+job.warning:'');
  }else progress.textContent='';
  if(state.exportActive&&!$('exportDialog').open)$('exportDialog').showModal();
  setFlowBusy(flowBusy);controls(state.busy);updateExportForm();
}
$('exportOpen').onclick=()=>{
  if(state.busy||state.jobActive||flowBusy)return;
  if(state.dirty){message('当前有未保存修改，请先保存修正，再导出筛选成果。',true);return}
  $('exportMessage').textContent='';
  $('exportName').value=newFolderName().replace('itof_SAM2_','itof_筛选成果_');
  updateExportForm();$('exportDialog').showModal();
};
$('exportClose').onclick=()=>$('exportDialog').close();
$('exportDialog').addEventListener('cancel',event=>{if(state.exportActive||flowBusy||directorySelecting)event.preventDefault()});
$('exportScope').onchange=updateExportForm;
$('exportStart').onclick=async()=>{
  if(state.exportActive||flowBusy||directorySelecting||state.busy)return;
  if(state.dirty){$('exportMessage').textContent='请先关闭窗口并保存当前修改，再导出。';return}
  setFlowBusy(true);updateExportForm();$('exportMessage').textContent='';
  try{
    const result=await api('/api/export',{parent:$('exportParent').value,name:$('exportName').value,scope:$('exportScope').value});
    showExportJob(result.export_job);
  }catch(e){$('exportMessage').textContent=e.message}
  finally{setFlowBusy(false);updateExportForm()}
};
async function initWorkflow(){
  $('batchName').value=newFolderName();
  try{
    const d=await api('/api/captures');await adoptWorkspace(d);
    if(d.output){$('workflow').hidden=true}else setMode('new');
    if(d.startup_warning){$('workflow').hidden=false;flowMessage(d.startup_warning,true)}
  }catch(e){flowMessage(e.message,true)}
  setInterval(pollJob,1500);
}
initWorkflow();
