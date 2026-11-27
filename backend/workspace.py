"""Browser-selected datasets and one background SAM batch at a time."""
from __future__ import annotations

import copy
import csv
import io
import json
from pathlib import Path
import secrets
import shutil
import tempfile
import threading

from .core import atomic_bytes, atomic_json, discover, stamp, validate_output, input_mode
from .geometry import RGB_VERSION, calibration_metadata
from .records import SUCCESS
from .dataset import Dataset
from .paths import PROJECT_ROOT


EXPORT_SCOPES = {'keep', 'keep_reviewed', 'reject', 'unsure', 'pending', 'all'}


def folder_name(value):
    if not isinstance(value,str) or value in {'.','..',''} or value!=value.strip() or any(c in value for c in '/\\\0') or value.startswith('.'):
        raise ValueError('请填写一个普通成果文件夹名，不要包含路径分隔符')
    return value


def copy_results_tree(source, target):
    """Copy regular result files; never follow links into another dataset."""
    if source.is_symlink():
        raise ValueError(f'成果目录包含符号链接，无法导出：{source}')
    target.mkdir()
    for entry in source.iterdir():
        if entry.is_symlink():
            raise ValueError(f'成果目录包含符号链接，无法导出：{entry}')
        if entry.is_dir():
            copy_results_tree(entry, target/entry.name)
        elif entry.is_file():
            shutil.copy2(entry, target/entry.name)
        else:
            raise ValueError(f'成果包含不支持的文件：{entry}')


def directory(value):
    if not isinstance(value,str) or not value.strip():
        raise ValueError('请选择文件夹')
    path=Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f'文件夹不存在：{path}')
    return path


class Workspace:
    def __init__(self, state_dir, initial_output=None, model_factory=None, processor=None):
        self.state_dir=Path(state_dir).resolve()
        self.settings_file=self.state_dir/'settings.json'
        self.settings={}
        self.startup_warning=''
        self.dataset=None
        self.lock=threading.RLock()
        self.model_lock=threading.Lock()
        self.model=None
        self.model_factory=model_factory
        self.processor=processor
        self.job=None
        self.worker=None
        self.export_job=None
        self.export_worker=None
        self.stop_event=threading.Event()
        if self.settings_file.is_file():
            try:
                self.settings=json.loads(self.settings_file.read_text())
            except (OSError,ValueError):
                self.startup_warning='上次目录记录无法读取，请重新选择成果目录'
        candidate=initial_output or self.settings.get('last_output')
        if candidate:
            try:
                self.open_results(str(candidate), remember=True)
            except (OSError,ValueError,KeyError,TypeError) as e:
                self.startup_warning=f'未恢复上次成果：{e}'

    def _remember(self):
        self.state_dir.mkdir(parents=True,exist_ok=True)
        atomic_json(self.settings_file,self.settings)

    def _idle(self):
        if self.export_job and self.export_job['status']=='running':
            raise ValueError('正在导出筛选成果，请等待导出完成后再编辑或切换目录')
        if self.job and self.job['status'] in {'running','stopping'}:
            raise ValueError('分割任务正在运行，请等待完成或先点击停止后再切换目录')

    def require_dataset(self, dataset_id):
        if self.dataset is None:
            raise ValueError('请先打开成果目录，或开始新的分割任务')
        if dataset_id!=self.dataset.dataset_id:
            raise ValueError('成果目录已切换，请刷新页面，避免把操作保存到其他目录')
        return self.dataset

    def listing(self):
        from .sam_backend import ready
        from .native_picker import supported
        with self.lock:
            data=self.dataset.listing() if self.dataset else {
                'frames':[],'pigs':0,'source':None,'output':None,'dataset_id':None,
                'backend':'sam2','sam_ready':ready()}
            return {**data,'job':copy.deepcopy(self.job),'export_job':copy.deepcopy(self.export_job),
                    'native_picker':supported(),'startup_warning':self.startup_warning,
                    'last_export_parent':self.settings.get('last_export_parent',''),
                    'last_source':self.settings.get('last_source',''),
                    'last_input_mode':self.settings.get('last_input_mode','infrared'),
                    'last_parent':self.settings.get('last_parent',str(PROJECT_ROOT.parent / 'outputs'))}

    def directories(self, value=None):
        folder=directory(value or str(Path.home()))
        entries=[]
        for p in folder.iterdir():
            try:
                if not p.name.startswith('.') and not p.is_symlink() and p.is_dir():
                    entries.append({'name':p.name,'path':str(p.resolve())})
            except OSError:
                continue
        return {'path':str(folder),'parent':str(folder.parent),
                'directories':sorted(entries,key=lambda e:e['name'].casefold()),
                'is_results':(folder/'dataset.json').is_file()}

    def scan(self, value, mode='infrared'):
        mode=input_mode(mode)
        source=directory(value)
        rows=discover(source,mode)
        if not rows:
            raise ValueError('没有找到可用采集：RGB 模式需要 color.png；红外模式需要 infrared.npy、infrared.png 和 depth_mm.npy')
        if mode=='rgb':
            for row in rows:
                try:calibration_metadata(row['source_dir'],mode)
                except ValueError as exc:raise ValueError(f"{row['relative_dir']}：{exc}") from exc
        return {'source':str(source),'input_mode':mode,'frames':len(rows),'pigs':len({r['pig_id'] for r in rows}),
                'geometry_checked':mode=='rgb'}

    def open_results(self, value, source_override=None, remember=True):
        output=directory(value)
        manifest=output/'dataset.json'
        if not manifest.is_file():
            raise ValueError('该目录没有 dataset.json，请选择分割成果的根目录，而不是某头猪或某次采集目录')
        data=json.loads(manifest.read_text())
        mode=input_mode(data.get('input_mode','infrared'))
        source=directory(source_override or self.settings.get('source_overrides',{}).get(str(output)) or data['source'])
        source,output=validate_output(source,output)
        if self.state_dir==source or source in self.state_dir.parents or self.state_dir in source.parents:
            raise ValueError('原始目录与工作台进度目录必须相互独立')
        available={r['relative_dir']:r for r in discover(source,mode)}
        saved=data.get('frames')
        if saved==[] and (output/'segmentation_failures.json').is_file():
            raise ValueError('该目录仅包含失败报告，没有分割图像；请查阅其中的筛选记录.csv 或分割失败记录.csv')
        if not isinstance(saved,list) or not saved:
            raise ValueError('成果清单中没有采集记录')
        rows=[]
        ids=set()
        for entry in saved:
            rel=entry['relative_dir']
            if not isinstance(rel,str) or rel not in available:
                raise ValueError(f'源数据缺失或相对路径无效：{rel}；源目录移动后可填写新的原始目录')
            target=(output/rel).resolve()
            if output not in target.parents or target==output:
                raise ValueError('成果路径异常')
            row={**available[rel],'id':entry['id']}
            if not isinstance(row['id'],str) or row['id'] in ids:
                raise ValueError('成果清单包含无效或重复采集编号')
            ids.add(row['id'])
            rows.append(row)
        dataset=Dataset(source,output,rows=rows,mode=mode)
        with self.lock:
            self._idle()
            self.dataset=dataset
            self.job=None
            job_file=output/'batch_job.json'
            if job_file.is_file():
                saved_job=json.loads(job_file.read_text())
                if saved_job.get('status') in {'running','stopping'}:
                    saved_job.update(status='interrupted',current='上次服务已结束；已完成成果保留。未完成采集可在工作台逐张分割，或新建任务。')
                saved_job.update(output=str(output),source=str(source))
                self.job=saved_job
            self.startup_warning=''
            if remember:
                self.settings.update(last_output=str(output),last_source=str(source))
                if source_override:
                    self.settings.setdefault('source_overrides',{})[str(output)]=str(source)
                self._remember()
            return self.listing()

    def start(self, payload):
        from .sam_backend import ready
        mode=input_mode(payload.get('input_mode','infrared'))
        source=directory(payload.get('source'))
        parent=directory(payload.get('parent'))
        name=folder_name(payload.get('name',''))
        output=parent/name
        if output.is_symlink():
            raise ValueError('成果目录不能是符号链接')
        source,output=validate_output(source,output)
        if source==self.state_dir or source in self.state_dir.parents or self.state_dir in source.parents:
            raise ValueError('原始目录与工作台进度目录必须相互独立')
        if output==self.state_dir or output in self.state_dir.parents or self.state_dir in output.parents:
            raise ValueError('成果目录与工作台进度目录必须相互独立')
        rows=discover(source,mode)
        if not rows:
            raise ValueError('原始目录没有所选模式的数据；RGB 需要 color.png，红外需要红外和深度文件')
        if not ready():
            raise ValueError('本机SAM权重或官方代码缺失，请先恢复模型文件')
        with self.lock:
            self._idle()
            if output.exists() and (not output.is_dir() or any(output.iterdir())):
                raise ValueError('成果目录已有文件。请换一个文件夹名；查看已有成果请使用“打开成果目录”')
            output.mkdir(parents=False,exist_ok=True)
            atomic_json(output/'dataset.json',{'source':str(source),'output':str(output),'backend':'sam2',
                        'frames':rows,'created_at':stamp(),'workflow':'browser_batch','input_mode':mode,
                        'geometry_version':RGB_VERSION if mode=='rgb' else None})
            self.dataset=Dataset(source,output,rows=rows,mode=mode)
            self.settings.update(last_source=str(source),last_output=str(output),last_parent=str(parent),last_input_mode=mode)
            self._remember()
            self.stop_event.clear()
            self.job={'id':secrets.token_hex(8),'status':'running','done':0,'success':0,'failed':0,
                      'total':len(rows),'current':'正在加载SAM模型','output':str(output),
                      'source':str(source),'input_mode':mode,'started_at':stamp(),'errors':[]}
            self._persist_job()
            self.worker=threading.Thread(target=self._run,args=(self.dataset,),daemon=False)
            self.worker.start()
            return self.listing()

    def get_model(self):
        with self.model_lock:
            if self.model is None:
                if self.model_factory:
                    self.model=self.model_factory()
                else:
                    from .sam_backend import SAMPredictor
                    self.model=SAMPredictor()
            return self.model

    def _persist_job(self):
        atomic_json(Path(self.job['output'])/'batch_job.json',self.job)

    def _run(self, dataset):
        try:
            from .sam_batch import segment_frame
            model=self.get_model()
            for row in dataset.rows:
                if self.stop_event.is_set():
                    break
                with self.lock:
                    self.job['current']=f"猪 {row['pig_id']} / {row['capture_id']}"
                try:
                    if self.processor:
                        self.processor(row,dataset.output,model)
                    else:
                        segment_frame(row,dataset.output,model,mode=dataset.input_mode)
                    with self.lock:
                        self.job['success']+=1
                except Exception as e:
                    with self.lock:
                        dataset.record_failure(row,e)
                        self.job['failed']+=1
                        self.job['errors'].append({'relative_dir':row['relative_dir'],'error':str(e)})
                with self.lock:
                    self.job['done']+=1
                    self._persist_job()
            with self.lock:
                self.job['status']='completed' if self.job['done']==self.job['total'] else 'stopped'
                self.job['current']='分割结束' if self.job['status']=='completed' else '已停止；已完成成果保留'
        except Exception as e:
            with self.lock:
                for row in dataset.rows:
                    if dataset.metadata(row)['status'] not in SUCCESS:
                        dataset.record_failure(row,e,stage='batch')
                self.job['status']='failed'
                self.job['current']=str(e)
                self.job['error']=str(e)
        finally:
            with self.lock:
                self.job['finished_at']=stamp()
                self._persist_job()
                atomic_json(dataset.output/'summary.json',{
                    'model':'SAM 2.1 Hiera Base Plus','requested':len(dataset.rows),
                    'masks':self.job['success'],'errors':self.job['errors'],
                    'status':self.job['status'],'reviewed':0,
                    'source':str(dataset.source),'output':str(dataset.output),'input_mode':dataset.input_mode,
                    'error':self.job.get('error'),
                    'algorithm_note':'每帧临时计算GrabCut候选供SAM单点/框提示；不依赖旧成果目录；所有结果待人工复核'})

    def stop(self):
        with self.lock:
            if self.job and self.job['status']=='running':
                self.stop_event.set()
                self.job['status']='stopping'
                self.job['current']='正在停止，当前一张处理完成后结束'
            return copy.deepcopy(self.job)

    def start_export(self, payload):
        with self.lock:
            self._idle()
            dataset=self.require_dataset(payload.get('dataset_id'))
            parent=directory(payload.get('parent'))
            target=parent/folder_name(payload.get('name',''))
            if target.is_symlink() or target.exists():
                raise ValueError('导出目录已存在，请换一个文件夹名，避免覆盖已有成果')
            _,target=validate_output(dataset.source,target)
            validate_output(dataset.output,target)
            validate_output(self.state_dir,target)
            scope=payload.get('scope','keep')
            if scope not in EXPORT_SCOPES:
                raise ValueError('导出范围无效')
            selected=[]
            for row in dataset.rows:
                meta=dataset.metadata(row)
                if meta.get('status') not in {'draft','corrected','reviewed'}:
                    continue
                decision=meta.get('decision','pending')
                if scope=='all' or (scope=='keep_reviewed' and decision=='keep' and meta.get('reviewed')) or decision==scope:
                    selected.append((copy.deepcopy(row),copy.deepcopy(meta)))
            failures=dataset.failure_records()
            if not selected and not failures:
                raise ValueError('该范围没有可导出的分割成果，请先筛选或调整导出范围')
            self.export_job={'id':secrets.token_hex(8),'dataset_id':dataset.dataset_id,
                             'status':'running','done':0,'total':len(selected),'current':'准备复制分割成果',
                             'output':str(target),'scope':scope,'failure_records':len(failures),'started_at':stamp()}
            self.export_worker=threading.Thread(target=self._export,args=(dataset,target,selected),daemon=False)
            self.export_worker.start()
            return {'export_job':copy.deepcopy(self.export_job)}

    def _export(self, dataset, target, selected):
        stage=None
        try:
            stage=Path(tempfile.mkdtemp(prefix='.itof-export-',dir=target.parent))
            for row,meta in selected:
                rel=row['relative_dir']
                dest=stage/rel
                if stage not in dest.resolve().parents:
                    raise ValueError('导出成果的相对路径异常')
                source=dataset.dest(row)
                # Export the saved mask selected by the editor, including its revision history.
                mask='trunk_mask_working.png' if meta['status'] in {'corrected','reviewed'} else 'trunk_mask_draft.png'
                if not (source/mask).is_file():
                    raise ValueError(f'分割掩码缺失：{rel}/{mask}')
                dest.parent.mkdir(parents=True,exist_ok=True)
                copy_results_tree(source,dest)
                atomic_json(dest/'segmentation.json',{**meta,**row})
                with self.lock:
                    self.export_job['done']+=1
                    self.export_job['current']=rel
            manifest=json.loads((dataset.output/'dataset.json').read_text())
            manifest.update(source=str(dataset.source),output=str(target),frames=[row for row,_ in selected],
                            created_at=stamp(),workflow='filtered_export',exported_from=str(dataset.output))
            atomic_json(stage/'dataset.json',manifest)
            failures=dataset.failure_records()
            atomic_json(stage/'segmentation_failures.json',{'schema_version':1,'source_output':str(dataset.output),
                                                          'records':failures})
            atomic_bytes(stage/'分割失败记录.csv',self.failures_csv(dataset))
            atomic_bytes(stage/'筛选记录.csv',self.csv(dataset.dataset_id,rows=[row for row,_ in selected],output=target))
            atomic_json(stage/'export.json',{**self.export_job,'status':'completed','finished_at':stamp(),
                                            'exported_from':str(dataset.output),'source':str(dataset.source),
                                            'note':'保留原始数据引用；筛选保留不代表掩码已经确认。附全批次失败历史，筛选 CSV 标明哪些成果实际导出。'})
            if target.exists() or target.is_symlink():
                raise ValueError('导出目录已被创建，请换一个文件夹名后重试')
            stage.rename(target)
            stage=None
            with self.lock:
                self.export_job.update(status='completed',current='导出完成',finished_at=stamp())
                self.settings['last_export_parent']=str(target.parent)
                try:
                    self._remember()
                except OSError:
                    self.export_job['warning']='成果已导出，但未能记住本次导出位置'
        except Exception as e:
            with self.lock:
                self.export_job.update(status='failed',error=str(e),finished_at=stamp())
        finally:
            if stage is not None:
                shutil.rmtree(stage,ignore_errors=True)

    def failures_csv(self,dataset):
        result=io.StringIO();writer=csv.writer(result)
        writer.writerow(['耳标','采集时间','相机','相机型号','相对目录','失败阶段','失败原因','失败时间','处理状态','源目录','筛选','备注','头侧方向'])
        for f in dataset.failure_records():
            writer.writerow([f.get('pig_id',''),f.get('capture_id',''),f.get('camera_label','未识别'),
                             f.get('camera_name',''),f['relative_dir'],f['stage'],f['error'],f.get('at',''),
                             '已补充分割' if f['failure_status']=='resolved' else '未解决',f.get('source_dir',''),
                             {'keep':'保留','reject':'淘汰','unsure':'待定','pending':'未筛选'}.get(f.get('decision','pending')),
                             f.get('notes',''),{'left':'左侧','right':'右侧','up':'上侧','down':'下侧'}.get(f.get('head_side'),'尚未记录')])
        return ('\ufeff'+result.getvalue()).encode('utf-8')

    def csv(self, dataset_id, rows=None, output=None):
        with self.lock:
            dataset=self.require_dataset(dataset_id)
            result=io.StringIO()
            writer=csv.writer(result)
            writer.writerow(['耳标','采集时间','筛选','分割状态','备注','源目录','成果目录','分割输入',
                             '相机','相机型号','相对目录','失败原因','失败时间','失败处理状态','成果已导出','头侧方向'])
            chosen=dataset.rows if rows is None else rows
            selected={r['relative_dir'] for r in chosen}
            report_rows=list(chosen)
            failures=dataset.failure_records()
            by_rel={r['relative_dir']:r for r in dataset.rows}
            for f in failures:
                rel=f['relative_dir']
                if rel and rel not in {r['relative_dir'] for r in report_rows}:
                    report_rows.append(by_rel.get(rel,f))
            for row in report_rows:
                current=by_rel.get(row['relative_dir'])
                history=[f for f in failures if f['relative_dir']==row['relative_dir']]
                m=dataset.metadata(current) if current else {**row,'status':'failed' if any(f['failure_status']=='failed' for f in history) else 'resolved'}
                writer.writerow([row.get('pig_id',''),row.get('capture_id',''),
                                 {'keep':'保留','reject':'淘汰','unsure':'待定','pending':'未筛选'}.get(m.get('decision','pending')),
                                 m['status'],m.get('notes',''),row.get('source_dir',''),
                                 str(Path(output)/row['relative_dir']) if output and row['relative_dir'] in selected else (str(dataset.dest(current)) if current else ''),
                                 'RGB' if dataset.input_mode=='rgb' else '红外',row.get('camera_label','未识别'),
                                 row.get('camera_name',''),row['relative_dir'],'；'.join(dict.fromkeys(f['error'] for f in history)),
                                 '；'.join(dict.fromkeys(f.get('at','') for f in history)),
                                 ('已补充分割' if all(f['failure_status']=='resolved' for f in history) else '未解决') if history else '',
                                 ('是' if row['relative_dir'] in selected else '否（仅失败记录）') if output else '',
                                 {'left':'左侧','right':'右侧','up':'上侧','down':'下侧'}.get(m.get('head_side'),'尚未记录')])
            for f in failures:
                if not f['relative_dir']:
                    writer.writerow(['','','','batch_failed','','','','RGB' if dataset.input_mode=='rgb' else '红外',
                                     '','','',f['error'],f.get('at',''),'批次异常','否（仅失败记录）' if output else '',''])
            return ('\ufeff'+result.getvalue()).encode('utf-8')
