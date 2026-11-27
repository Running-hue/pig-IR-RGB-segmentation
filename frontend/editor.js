const TOKEN=document.querySelector('meta[name="app-token"]').content;
const $=id=>document.getElementById(id);
const state={rows:[],current:null,meta:null,tool:'brush',view:'overlay',dirty:false,maskDirty:false,draw:false,points:[],undo:[],redo:[],busy:false,loadSeq:0,samPoints:[],samBox:null,samStart:null,samPredictionId:null,datasetId:null,jobActive:false,exportActive:false,cameraFilter:'all'};
const mc=$('mask').getContext('2d',{willReadFrequently:true}),bc=$('base').getContext('2d'),pc=$('paint').getContext('2d');
let renderPending=false;
function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error)}
async function api(path,data){if(!data&&path.startsWith('/api/frame?'))path+='&dataset_id='+encodeURIComponent(state.datasetId||'');const res=await fetch(path,data?{method:'POST',headers:{'Content-Type':'application/json','X-App-Token':TOKEN},body:JSON.stringify({dataset_id:state.datasetId,...data})}:{});const value=await res.json();if(!res.ok)throw Error(value.error||'操作失败');return value}
function imageURL(id,kind){return '/api/image?id='+encodeURIComponent(id)+'&kind='+kind+'&dataset_id='+encodeURIComponent(state.datasetId||'')+'&v='+Date.now()}
function loadImage(url){return new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(Error('图片读取失败'));img.src=url})}
const names={failed:'分割失败',pending:'未生成',draft:'待复核',corrected:'待确认',reviewed:'已确认'};
const decisionNames={pending:'未筛选',keep:'已保留',reject:'已淘汰',unsure:'待定'};
function cameraLabel(row){return row.camera_label||'未识别'}
function visibleRows(){const term=$('search').value.trim().toLowerCase(),filter=$('filter').value;return state.rows.filter(r=>(state.cameraFilter==='all'||(r.camera_type||'unknown')===state.cameraFilter)&&(filter==='all'||(filter.startsWith('decision:')?(r.decision||'pending')===filter.split(':')[1]:r.status===filter))&&(r.pig_id+' '+r.capture_id+' '+cameraLabel(r)).toLowerCase().includes(term))}
function renderList(){
  const frag=document.createDocumentFragment(),rows=visibleRows();
  for(const r of rows){
    const b=document.createElement('button');b.className='frame'+(r.id===state.current?.id?' current':'');b.dataset.id=r.id;
    const line=document.createElement('span');line.className='line';const label=document.createElement('span');label.textContent='耳标 '+r.pig_id;
    const status=document.createElement('span');status.textContent=(r.decision&&r.decision!=='pending'?decisionNames[r.decision]+' · ':'')+(names[r.status]||'未生成');status.className='pill '+r.status;line.append(label,status);
    const sub=document.createElement('small');sub.className='frameSub';const badge=document.createElement('span');badge.className='cameraBadge '+(r.camera_type||'unknown');badge.textContent=cameraLabel(r);badge.title=r.camera_name||'按采集目录识别';const time=document.createElement('span');time.className='captureTime';time.textContent=r.capture_id;time.title=r.capture_id;sub.append(badge,time);b.append(line,sub);b.onclick=()=>openFrame(r);frag.append(b);
  }
  $('list').replaceChildren(frag);
  const reviewed=state.rows.filter(r=>r.reviewed).length,kept=state.rows.filter(r=>r.decision==='keep').length,generated=state.rows.filter(r=>['draft','corrected','reviewed'].includes(r.status)).length,failed=state.rows.filter(r=>r.status==='failed').length,total=state.rows.length;
  $('counts').textContent=`${new Set(state.rows.map(r=>r.pig_id)).size} 头猪 · ${total} 次采集\n保留 ${kept} · 掩码已确认 ${reviewed}`;
  for(const button of document.querySelectorAll('[data-camera]')){const kind=button.dataset.camera,count=kind==='all'?total:state.rows.filter(r=>(r.camera_type||'unknown')===kind).length;button.textContent=(kind==='all'?'全部':{itof:'iToF',stereo:'双目',unknown:'未识别'}[kind])+' '+count;button.hidden=kind==='unknown'&&!count;button.classList.toggle('active',state.cameraFilter===kind)}
  $('segProgressText').textContent=`${generated+failed} / ${total}`;$('segProgress').max=Math.max(1,total);$('segProgress').value=generated+failed;
  $('segProgressDetail').textContent=`已生成 ${generated} · 失败 ${failed} · 待处理 ${total-generated-failed}`;
  $('maskProgressText').textContent=`${reviewed} / ${generated}`;$('maskProgress').max=Math.max(1,generated);$('maskProgress').value=reviewed;
  $('visibleCount').textContent=`${state.cameraFilter==='all'?'全部相机':{itof:'iToF',stereo:'双目',unknown:'未识别'}[state.cameraFilter]} · 显示 ${rows.length} / ${total} 次`;
}
for(const b of document.querySelectorAll('[data-camera]'))b.onclick=()=>{if(state.busy)return;state.cameraFilter=b.dataset.camera;renderList()};
function fit(){if(!state.meta)return;const w=state.meta.width,h=state.meta.height,z=$('zoom').value;const s=z==='fit'?Math.min(($('viewport').clientWidth-12)/w,($('viewport').clientHeight-12)/h):Number(z);$('stage').style.width=w*s+'px';$('stage').style.height=h*s+'px'}
new ResizeObserver(fit).observe($('viewport'));$('zoom').onchange=fit;
function render(){renderPending=false;if(!state.meta)return;const w=$('mask').width,h=$('mask').height;pc.clearRect(0,0,w,h);if(state.view==='binary'){pc.drawImage($('mask'),0,0)}else if(state.view==='overlay'){const bits=mc.getImageData(0,0,w,h),out=pc.createImageData(w,h),a=Number($('opacity').value)*2.55;for(let i=0;i<bits.data.length;i+=4){if(bits.data[i]>127){out.data[i]=49;out.data[i+1]=229;out.data[i+2]=170;out.data[i+3]=a}}pc.putImageData(out,0,0)}if(state.points.length){pc.strokeStyle='#ffcf72';pc.lineWidth=2;pc.beginPath();state.points.forEach((p,i)=>i?pc.lineTo(p.x,p.y):pc.moveTo(p.x,p.y));pc.stroke();for(const p of state.points){pc.fillStyle='#ffcf72';pc.beginPath();pc.arc(p.x,p.y,3,0,Math.PI*2);pc.fill()}}drawSamPrompts()}
function drawSamPrompts(){for(const p of state.samPoints){pc.fillStyle=p.label?'#51ff83':'#ff5f68';pc.strokeStyle='white';pc.lineWidth=1;pc.beginPath();pc.arc(p.x,p.y,5,0,Math.PI*2);pc.fill();pc.stroke();pc.fillStyle='#112028';pc.font='bold 12px sans-serif';pc.textAlign='center';pc.fillText(p.label?'+':'−',p.x,p.y+4)}pc.textAlign='start';if(state.samBox){const [x0,y0,x1,y1]=state.samBox;pc.strokeStyle='#ffc95e';pc.lineWidth=2;pc.setLineDash([6,4]);pc.strokeRect(x0,y0,x1-x0,y1-y0);pc.setLineDash([])}}
function promptCount(){$('samPromptCount').textContent=`目标点 ${state.samPoints.filter(p=>p.label).length} · 背景点 ${state.samPoints.filter(p=>!p.label).length}`;schedule()}
function schedule(){if(!renderPending){renderPending=true;requestAnimationFrame(render)}}
function snapshot(){state.undo.push(mc.getImageData(0,0,$('mask').width,$('mask').height));if(state.undo.length>30)state.undo.shift();state.redo=[]}
function dirty(mask=true){state.dirty=true;if(mask)state.maskDirty=true;$('title').textContent='耳标 '+state.current.pig_id+' · 有未保存修改';schedule()}
function readStats(meta){
  const s=meta.stats||{},frag=document.createDocumentFragment();
  if(state.meta?.geometry_legacy)s.valid_depth_ratio=null;
  for(const [label,value]of [['状态',names[meta.status]||'未生成'],['修订',meta.revision||0],['面积',(s.area_px?.toLocaleString()||'—')+' px'],['有效深度',s.valid_depth_ratio!=null?(100*s.valid_depth_ratio).toFixed(1)+'%':'—']]){
    const item=document.createElement('div'),name=document.createElement('span'),text=document.createElement('strong');
    name.textContent=label;text.textContent=value;item.append(name,text);frag.append(item);
  }
  $('stats').replaceChildren(frag);
  const warnings=[...new Set([...(s.warnings||[]),...(meta.segmentation_error?['分割失败：'+meta.segmentation_error]:[]),...(meta.review_error?['底图准备失败：'+meta.review_error]:[])])],locked=state.busy||state.jobActive||state.exportActive;
  $('warn').textContent=warnings.join('；');$('warningDetails').hidden=!warnings.length;
  $('warningSummary').textContent=`复核提醒（${warnings.length} 项）`;
  $('generate').disabled=locked||meta.status==='reviewed'||meta.status==='corrected';
  $('restore').disabled=locked||!meta.draft_available;
  $('decisionState').textContent=decisionNames[meta.decision||'pending'];
  for(const [id,value]of [['keepResult','keep'],['rejectResult','reject'],['unsureResult','unsure']]){
    $(id).classList.toggle('selected',meta.decision===value);$(id).disabled=locked||!['draft','corrected','reviewed','failed'].includes(meta.status);
  }
  $('save').textContent=meta.review_only?'保存复核记录':'保存修正，稍后确认';
  $('samPredict').textContent=(meta.status==='failed'?'重新分割':'运行 SAM')+'（'+(meta.input_mode==='rgb'?'RGB':'红外')+'）';
}
function maskEditable(){return !!state.meta&&!state.meta.geometry_legacy&&!state.meta.review_only}
function controls(disabled){
  state.busy=disabled;
  for(const id of ['prev','next','save','approve','restore','generate','samPredict','samClear','keepResult','rejectResult','unsureResult'])$(id).disabled=disabled||(!state.meta&&!(['prev','next'].includes(id)&&state.current))||state.exportActive||(state.jobActive&&!['prev','next'].includes(id));
  for(const id of ['head','notes'])$(id).disabled=disabled||state.jobActive||state.exportActive;
  if(!disabled&&state.meta)readStats(state.meta);
  if(state.meta?.geometry_legacy)for(const id of ['save','approve','generate','restore','samPredict','samClear'])if(id!=='save'||!state.meta.review_only)$(id).disabled=true;
  if(state.meta?.review_only)for(const id of ['approve','generate','restore','samPredict','samClear'])$(id).disabled=true;
  for(const b of document.querySelectorAll('[data-tool],#finishPoly,#undo,#redo,#brush'))b.disabled=disabled||!maskEditable()||state.jobActive||state.exportActive;
  $('stage').style.cursor=maskEditable()?'crosshair':'default';
}
function reviewInputUI(meta){
  const rgb=meta.input_mode==='rgb',label=rgb?'RGB':'红外';
  $('reviewInputMode').textContent=label+(meta.geometry_legacy?' · 旧坐标':' 分割成果');
  document.querySelector('[data-view="original"]').textContent=rgb&&!meta.geometry_legacy?'RGB 校正底图':label+' 原图';
  $('samPredict').textContent='运行 SAM（'+label+'）';
  $('inputReviewHint').textContent=rgb?'RGB 已按保存的对齐深度 profile 重映射；在这个坐标网格上修正掩码，红外只作参考。':'红外掩码保留原始深度网格；生成点云时使用原始深度内参与畸变模型，RGB 只作参考。';
  const geometry=meta.geometry||{};
  $('geometryNote').textContent=meta.geometry_legacy?'旧 RGB 成果未校正，仅供查看和筛选；用于点云请新建 RGB 分割任务。':geometry.status==='verified'?
    (rgb?'RGB 已匹配保存的对齐深度坐标':'红外／原始深度像素几何已核对')+' · '+(geometry.pointcloud_ready?'点云坐标系：'+(rgb?'彩色相机':'深度相机')+'，单位 mm':'缺少对应深度，暂不能生成点云'):
    '缺少完整标定记录：可复核分割，尚不能确认点云几何。';
  $('geometryNote').style.color=meta.geometry_legacy||geometry.status!=='verified'?'#e6c58a':'#95acb4';
  if(meta.geometry_legacy)$('inputReviewHint').textContent='旧掩码仍在原始 RGB 像素坐标，不会自动叠到校正图上；请新建 RGB 任务后用于点云。';
  if(meta.review_only){
    $('reviewInputMode').textContent=label+' · 失败复核';
    document.querySelector('[data-view="original"]').textContent=label+' 原图';
    $('inputReviewHint').textContent='当前仅展示采集原图，可记录筛选、头侧方向和备注；修复图像或标定问题后再重新分割。';
    $('geometryNote').textContent=(meta.image_available?'当前为原图复核；':'原图无法读取，仍可保存复核记录；')+'校正底图不可用：'+meta.review_error+'。暂不能生成有效掩码或点云。';
    $('geometryNote').style.color='#e6c58a';
  }
  const reference=rgb?'红外参考':'RGB 参考';
  $('referenceLabel').textContent=reference+' ↗';$('rgb').alt=reference;$('rgbBtn').title='放大'+reference;
  $('depthLabel').textContent=rgb?'RGB 对齐深度 ↗':'深度预览 ↗';
  $('rgbBtn').hidden=!meta.reference_available;$('depthBtn').hidden=!meta.depth_available;
  for(const [id,button,kind,available]of [['rgb','rgbBtn','reference',meta.reference_available],['depth','depthBtn','depth',meta.depth_available]]){
    $(id).removeAttribute('src');$(id).onerror=()=>{$(button).hidden=true};
    if(available)$(id).src=imageURL(state.current.id,kind);
  }
}
async function openFrame(row){
  if(state.busy||!row)return;
  if(state.dirty&&!window.confirm('当前修改尚未保存，是否放弃修改并切换？'))return;
  state.current=row;state.meta=null;state.dirty=false;state.maskDirty=false;state.points=[];state.samPoints=[];state.samBox=null;state.samStart=null;state.samPredictionId=null;state.undo=[];state.redo=[];
  for(const [canvas,ctx]of [[$('base'),bc],[$('paint'),pc],[$('mask'),mc]])ctx.clearRect(0,0,canvas.width,canvas.height);
  $('title').textContent='耳标 '+row.pig_id;$('capture').textContent=row.capture_id+' · '+cameraLabel(row);$('geometryNote').textContent='';$('notes').value='';$('head').value='unknown';$('stats').textContent='—';$('warningDetails').hidden=true;
  for(const id of ['rgb','depth'])$(id).removeAttribute('src');$('rgbBtn').hidden=true;$('depthBtn').hidden=true;
  renderList();controls(true);const seq=++state.loadSeq;
  try{
    const meta=await api('/api/frame?id='+row.id);
    const [base,mask]=await Promise.all([loadImage(imageURL(row.id,'base')),loadImage(imageURL(row.id,'mask'))]);
    if(seq!==state.loadSeq)return;
    if(base.naturalWidth!==meta.width||base.naturalHeight!==meta.height||mask.naturalWidth!==meta.width||mask.naturalHeight!==meta.height)throw Error('底图与掩码尺寸不一致，请检查成果目录');
    state.current=row;state.meta=meta;state.dirty=false;state.points=[];state.samPoints=[];state.samBox=null;state.samPredictionId=null;promptCount();state.undo=[];state.redo=[];
    for(const id of ['base','paint','mask']){$(id).width=meta.width;$(id).height=meta.height}
    bc.drawImage(base,0,0);mc.drawImage(mask,0,0);
    $('title').textContent='耳标 '+row.pig_id;$('capture').textContent=row.capture_id+' · '+cameraLabel(row)+' · '+(meta.input_mode==='rgb'?'RGB':'红外')+' · '+meta.width+'×'+meta.height;
    $('head').value=meta.head_side||'unknown';$('notes').value=meta.notes||'';
    if(meta.review_only)view('original');
    reviewInputUI(meta);readStats(meta);renderList();fit();render();
    $('warningDetails').open=false;
    message(meta.review_only?'可保留、淘汰或待定；也可填写头侧方向和备注，点击“保存复核记录”。':meta.status==='failed'?'此帧分割失败，可先筛选；添加目标点或框选后点击“重新分割”，也可手工补画后保存。':'鼠标拖动补画或擦除；多边形点击添加顶点，回车闭合，Esc 取消。');
  }catch(e){message(e.message,true)}finally{controls(false)}
}
function point(e){const r=$('paint').getBoundingClientRect();return{x:Math.max(0,Math.min($('mask').width-1,(e.clientX-r.left)*$('mask').width/r.width)),y:Math.max(0,Math.min($('mask').height-1,(e.clientY-r.top)*$('mask').height/r.height))}}
function tool(value){if(!maskEditable()||state.busy||state.jobActive||state.exportActive)return;state.tool=value;state.points=[];for(const b of document.querySelectorAll('[data-tool]'))b.classList.toggle('active',b.dataset.tool===value);schedule()}
for(const b of document.querySelectorAll('[data-tool]'))b.onclick=()=>tool(b.dataset.tool);
function view(value){state.view=value;for(const b of document.querySelectorAll('[data-view]'))b.classList.toggle('active',b.dataset.view===value);schedule()}
for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>view(b.dataset.view);
function stroke(a,b){mc.strokeStyle=state.tool==='erase'?'black':'white';mc.lineWidth=Number($('brush').value);mc.lineCap='round';mc.lineJoin='round';mc.beginPath();mc.moveTo(a.x,a.y);mc.lineTo(b.x,b.y);mc.stroke();mc.fillStyle=mc.strokeStyle;mc.beginPath();mc.arc(b.x,b.y,mc.lineWidth/2,0,Math.PI*2);mc.fill();dirty()}
let lastPoint=null;
$('paint').onpointerdown=e=>{if(!maskEditable()||state.busy||state.jobActive||state.exportActive||e.button!==0)return;e.preventDefault();if(state.view==='original')view('overlay');const p=point(e);if(state.tool==='samPositive'||state.tool==='samNegative'){state.samPoints.push({...p,label:state.tool==='samPositive'?1:0});promptCount();message('提示点已添加；点击“运行 SAM”更新分割。');return}if(state.tool==='samBox'){state.samStart=p;state.samBox=[p.x,p.y,p.x,p.y];$('paint').setPointerCapture(e.pointerId);schedule();return}if(state.tool.startsWith('poly')){state.points.push(p);schedule();return}snapshot();state.draw=true;lastPoint=p;$('paint').setPointerCapture(e.pointerId);stroke(p,p)};
$('paint').onpointermove=e=>{if(state.samStart){const p=point(e),a=state.samStart;state.samBox=[Math.min(a.x,p.x),Math.min(a.y,p.y),Math.max(a.x,p.x),Math.max(a.y,p.y)];schedule();return}if(!state.draw)return;const p=point(e);stroke(lastPoint,p);lastPoint=p};
function endStroke(){if(state.samStart){state.samStart=null;if(state.samBox[2]-state.samBox[0]<3||state.samBox[3]-state.samBox[1]<3)state.samBox=null;promptCount()}state.draw=false;lastPoint=null}
$('paint').onpointerup=endStroke;$('paint').onpointercancel=endStroke;
function finishPolygon(){if(!maskEditable()||state.busy||state.exportActive||state.jobActive)return;if(state.points.length<3){message('至少点击三个顶点再闭合轮廓。');return}snapshot();mc.beginPath();state.points.forEach((p,i)=>i?mc.lineTo(p.x,p.y):mc.moveTo(p.x,p.y));mc.closePath();mc.fillStyle=state.tool==='polyErase'?'black':'white';mc.fill();state.points=[];dirty()}
$('finishPoly').onclick=finishPolygon;
function undo(){if(state.busy)return;if(state.points.length){state.points.pop();schedule();return}if(!state.undo.length)return;state.redo.push(mc.getImageData(0,0,$('mask').width,$('mask').height));mc.putImageData(state.undo.pop(),0,0);dirty()}
function redo(){if(state.busy||!state.redo.length)return;state.undo.push(mc.getImageData(0,0,$('mask').width,$('mask').height));mc.putImageData(state.redo.pop(),0,0);dirty()}
$('undo').onclick=undo;$('redo').onclick=redo;
$('brush').oninput=()=>{$('brushLabel').textContent=$('brush').value};$('opacity').oninput=schedule;
$('head').onchange=()=>{if(state.meta)dirty(false)};$('notes').oninput=()=>{if(state.meta)dirty(false)};
function binaryPNG(){const data=mc.getImageData(0,0,$('mask').width,$('mask').height);for(let i=0;i<data.data.length;i+=4){const v=data.data[i]>127?255:0;data.data[i]=data.data[i+1]=data.data[i+2]=v;data.data[i+3]=255}mc.putImageData(data,0,0);return $('mask').toDataURL('image/png')}
try{$('autoNext').checked=localStorage.getItem('review.autoNext')==='true'}catch{}
$('autoNext').onchange=()=>{try{localStorage.setItem('review.autoNext',String($('autoNext').checked))}catch{}};
async function save(reviewed){
  if(!state.current||!state.meta||state.busy||state.jobActive||state.exportActive)return;
  if(state.meta.review_only){if(!reviewed)await decideResult(state.meta.decision||'pending');return}
  if(!maskEditable())return;
  if(state.points.length){message('请先闭合多边形，或按 Esc 取消后再保存。',true);return}
  if(reviewed&&$('head').value==='unknown'){message('请先记录头侧方向，再确认合格。',true);$('head').focus();return}
  const before=visibleRows(),index=before.findIndex(r=>r.id===state.current.id),nextRow=index>=0?before[index+1]:null;
  let saved=false;controls(true);
  try{
    const meta=await api('/api/save',{id:state.current.id,revision:state.meta.revision||0,mask:binaryPNG(),reviewed,head_side:$('head').value,notes:$('notes').value,sam_prediction_id:state.samPredictionId});
    state.meta={...state.meta,...meta};Object.assign(state.current,{status:meta.status,reviewed:meta.reviewed,revision:meta.revision,segmentation_error:''});state.dirty=false;state.maskDirty=false;
    if(workspaceData)for(const f of workspaceData.failures||[])if(f.relative_dir===state.current.relative_dir)f.failure_status='resolved';
    $('title').textContent='耳标 '+state.current.pig_id;readStats(state.meta);renderList();render();saved=true;
    message(reviewed?'已确认合格，已保存 trunk_mask.png 和叠加预览。':'修正进度已保存，尚未标记为合格。');
  }catch(e){message(e.message,true)}finally{controls(false)}
  if(saved&&reviewed&&$('autoNext').checked){if(nextRow)await openFrame(nextRow);else message('已确认保存；已到当前筛选列表的最后一张。')}
}
$('save').onclick=()=>save(false);$('approve').onclick=()=>save(true);
$('restore').onclick=async()=>{if(!state.current||state.busy)return;controls(true);try{const im=await loadImage(imageURL(state.current.id,'draft'));snapshot();mc.drawImage(im,0,0);state.points=[];dirty();message('已载入自动初稿；当前保存结果尚未改变，可撤销。')}catch(e){message(e.message,true)}finally{controls(false)}};
$('generate').onclick=async()=>{if(!state.current||state.busy)return;const row=state.current;controls(true);try{const meta=await api('/api/generate',{id:row.id});Object.assign(row,{status:meta.status,reviewed:false});state.dirty=false;controls(false);await openFrame(row)}catch(e){message(e.message,true)}finally{controls(false)}};
$('samClear').onclick=()=>{if(state.busy)return;state.samPoints=[];state.samBox=null;promptCount();message('提示已清空；当前分割保留。')};
$('samPredict').onclick=async()=>{if(!state.current||state.busy)return;if(!state.samPoints.some(p=>p.label===1)&&!state.samBox){message('请先在猪体内部添加目标点，或框选目标。',true);return}controls(true);message('SAM 正在计算，首次运行需要加载模型…');try{const result=await api('/api/sam_predict',{id:state.current.id,prompts:{points:state.samPoints.map(p=>[p.x,p.y]),labels:state.samPoints.map(p=>p.label),box:state.samBox}});const im=await loadImage(result.mask);snapshot();mc.drawImage(im,0,0);state.samPredictionId=result.prediction_id;dirty();view('overlay');message(`SAM 2.1 已更新画布（${result.model_info.seconds.toFixed(1)} 秒）；检查头耳尾腿，可用笔刷修正后保存。`)}catch(e){message(e.message,true)}finally{controls(false)}};
function step(delta){const rows=visibleRows(),index=rows.findIndex(r=>r.id===state.current?.id);const next=rows[index+delta];if(next)openFrame(next);else message(delta>0?'已到当前列表的最后一张。':'已到当前列表的第一张。')}
$('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);$('search').oninput=renderList;$('filter').onchange=renderList;
for(const [button,img] of [['rgbBtn','rgb'],['depthBtn','depth']])$(button).onclick=()=>{if(!state.current)return;$('largePreview').src=$(img).src;$('previewDialog').showModal()};$('closePreview').onclick=()=>$('previewDialog').close();
window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue=''}});
document.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)||document.querySelector('dialog[open]')||state.busy||state.jobActive)return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo();return}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();save(false);return}if(e.key==='Escape'){state.points=[];schedule()}else if(e.key==='Enter'&&state.points.length){e.preventDefault();finishPolygon()}else if(e.key.toLowerCase()==='b')tool('brush');else if(e.key.toLowerCase()==='e')tool('erase');else if(e.key==='ArrowLeft')step(-1);else if(e.key==='ArrowRight')step(1)});
