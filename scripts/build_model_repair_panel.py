"""Read-only retained-input extraction and exact historical artifact replay.

No database/model initialization or production control is imported or called.
Output is a research panel, not a production qualification receipt.
"""
import argparse, collections, csv, datetime as dt, hashlib, json, math, pathlib, sqlite3, subprocess, sys, time
import numpy as np

MARKET_FEATURES=('return_1m','return_5m','return_15m','return_30m','return_60m','tick_speed_5m_per_second','quote_imbalance_60m','realized_volatility_60m')
IDENTITIES=('MARKET_ONLY','FULL','BROAD_FULL','NEWS_ONLY','NEWS_RESIDUAL','BROAD_NEWS_RESIDUAL')
ABS_TOL=1e-10
REL_TOL=1e-8
def canonical(x): return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def chash(x): return hashlib.sha256(canonical(x)).hexdigest()
def date(s): return dt.datetime.fromisoformat(s.replace('Z','+00:00')) if s else None
def near(a,b): return a is not None and b is not None and math.isclose(a,b,abs_tol=ABS_TOL,rel_tol=REL_TOL)
def identities(p): return {str(q):[q.stat().st_size,q.stat().st_mtime_ns] for q in (p,pathlib.Path(str(p)+'-wal'),pathlib.Path(str(p)+'-shm'))}
def require(condition,reason):
 if not condition:raise ValueError(reason)
def write(p,x): p.write_text(json.dumps(x,separators=(',',':'),allow_nan=False),encoding='utf-8')

def validate_ridge_identity(payload,expected_hash,*,expected_order=None,target_unit='U5',expected_version=None,recorded_version=None):
 if chash(payload)!=expected_hash:raise ValueError('ARTIFACT_HASH_MISMATCH')
 if payload.get('schema')!='xauusd.forward.ridge.v2':raise ValueError('ARTIFACT_SCHEMA_MISMATCH')
 if target_unit!='U5':raise ValueError('TARGET_UNIT_MISMATCH')
 if expected_version is not None and expected_version!=recorded_version:raise ValueError('MODEL_VERSION_MISMATCH')
 names=payload['feature_names']
 if expected_order is not None and list(expected_order)!=names:raise ValueError('FEATURE_ORDER_MISMATCH')
 if len(names)!=len(set(names)) or any(len(payload[k])!=len(names) for k in ('means','scales','coefficients')):raise ValueError('FEATURE_DIMENSION_MISMATCH')
 if not all(math.isfinite(float(v)) for k in ('means','scales','coefficients') for v in payload[k]):raise ValueError('NONFINITE_ARTIFACT')

def replay_exact_ridge(payload,features,expected_hash,*,expected_order=None,target_unit='U5',expected_version=None,recorded_version=None,legacy_scales=False):
 validate_ridge_identity(payload,expected_hash,expected_order=expected_order,target_unit=target_unit,expected_version=expected_version,recorded_version=recorded_version)
 values=[features[name] for name in payload['feature_names']]
 if any(v is None or not math.isfinite(float(v)) for v in values):raise ValueError('FEATURE_MISSING_OR_NONFINITE')
 if legacy_scales:
  if any(s==0 for s in payload['scales']):raise ValueError('HISTORICAL_ZERO_SCALE')
  return float((payload['intercept']+((np.asarray([values])-np.asarray(payload['means']))/np.asarray(payload['scales']))@np.asarray(payload['coefficients']))[0])
 from xauusd_forecaster.ridge import RidgeArtifact
 artifact=RidgeArtifact(tuple(payload['feature_names']),tuple(payload['means']),tuple(payload['scales']),tuple(payload['coefficients']),payload['intercept'],payload['alpha'],payload['training_dataset_hash'],payload.get('residual_std',0),payload.get('training_rows',0),payload.get('weighting_version'),payload.get('weight_summary'))
 return float(artifact.predict(np.asarray([values],dtype=np.float64))[0])

def read_checked_copy(path,expected_hash):
 if not path.is_file():raise ValueError('ARTIFACT_MISSING')
 if path.stat().st_size>2_000_000:raise ValueError('ARTIFACT_SIZE_EXCEEDED')
 try:payload=json.loads(path.read_bytes())
 except (ValueError,UnicodeError) as ex:raise ValueError('ARTIFACT_MALFORMED') from ex
 if chash(payload)!=expected_hash:raise ValueError('ARTIFACT_HASH_MISMATCH')
 return payload

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input-dir',type=pathlib.Path,required=True);ap.add_argument('--output-dir',type=pathlib.Path,required=True);ap.add_argument('--artifact-root',type=pathlib.Path,required=True);ap.add_argument('--artifact-inventory',type=pathlib.Path,required=True);ap.add_argument('--source-root',type=pathlib.Path,required=True);ap.add_argument('--snapshot',type=pathlib.Path,required=True);ap.add_argument('--input-sha256',required=True);ap.add_argument('--plan',type=pathlib.Path,required=True);ap.add_argument('--selected-inputs',type=pathlib.Path);ap.add_argument('--selected-sha256');a=ap.parse_args()
 o=a.output_dir
 if o.exists() and any(o.iterdir()):raise ValueError('OUTPUT_MUST_BE_EMPTY')
 if any(p.resolve().is_relative_to(o.resolve()) for p in (a.input_dir,a.snapshot,a.artifact_root,a.artifact_inventory,a.plan)):
  raise ValueError('OUTPUT_MUST_NOT_OWN_INPUT')
 o.mkdir(parents=True,exist_ok=True); x=json.loads((a.input_dir/'followup_input.json').read_text(encoding='utf-8'));f=json.loads((a.input_dir/'facts.json').read_text(encoding='utf-8'));d=a.snapshot;require(d.resolve()==pathlib.Path(f['database']).resolve(),'SNAPSHOT_PATH_MISMATCH');require(x['source_identity']==f['file_identity']==identities(d),'SNAPSHOT_IDENTITY_MISMATCH');require(identities(d)[str(d)+'-wal'][0]==0,'NONEMPTY_WAL_REQUIRES_ONLINE_SNAPSHOT')
 require(sha(a.input_dir/'followup_input.json')==a.input_sha256,'INPUT_DIGEST_MISMATCH');plan_sha256=sha(a.plan)
 extra=a.selected_inputs or o/'selected_inputs.json'
 if a.selected_inputs and (not a.selected_sha256 or sha(extra)!=a.selected_sha256):raise ValueError('SELECTED_INPUT_DIGEST_MISMATCH')
 if not extra.exists():
  db=sqlite3.connect(d.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN');queries=[]
  def q(sql):
   start=time.monotonic();db.set_progress_handler(lambda:int(time.monotonic()-start>30),10000);rows=[dict(r) for r in db.execute(sql)];queries.append({'sql':sql,'rows':len(rows),'elapsed_seconds':time.monotonic()-start});print(queries[-1],flush=True);return rows
  e={'outcomes':q('SELECT source_decision_id,recomputed_at,label_version,entry_event_time,entry_received_time,exit_event_time,exit_received_time,maximum_event_gap,maximum_receipt_gap,quote_coverage,ambiguity_state,reason_codes_json FROM derived_outcomes'),
     'market':q('SELECT source_decision_id,recomputed_at,features_json,reason_codes_json,source_evidence_hash FROM derived_market_snapshots'),
     'news':q('SELECT source_decision_id,recomputed_at,eligibility_version,features_json,output_hash,source_evidence_hash FROM derived_news_feature_snapshots'),
     'source_market':q('SELECT snapshot_hash,collected_at,source_event_time,source_received_time,source,features_json,reason_codes_json FROM market_snapshots')}
  db.rollback();db.close();require(identities(d)==f['file_identity'],'SNAPSHOT_CHANGED');e.update(queries=queries,input_identity=identities(d));write(extra,e)
 e=json.loads(extra.read_text(encoding='utf-8'));require(e['input_identity']==f['file_identity'],'SELECTED_SNAPSHOT_MISMATCH');out={r['source_decision_id']:r for r in x['outcomes']};out_extra={r['source_decision_id']:r for r in e['outcomes']};market={r['source_decision_id']:r for r in x['market']};mx={r['source_decision_id']:r for r in e['market']};source_market={r['snapshot_hash']:r for r in e['source_market']};news=collections.defaultdict(list)
 for n in e['news']: news[n['source_decision_id']].append(n)
 models={r['model_version']:dict(r) for r in x['models']};scores={(r['source_decision_id'],r['model_version']):r for r in x['scores']};clocks={r['decision_id'] for r in x['clocks']};generations=collections.defaultdict(dict);version_gen=collections.defaultdict(list)
 for v in f['versions']:
  for g in v['generation']:generations[g][v['model_identity']]=v['model_version'];version_gen[v['model_version']].append(g)
 activations=sorted(f['activations'],key=lambda a:date(a['activated_at']));arts={};inventory=[];artifact_by_path={}
 frozen_inventory={(i['source_path'],i['expected_canonical_hash']):i for i in json.loads(a.artifact_inventory.read_text(encoding='utf-8')) if i['status']=='EXACT_HASH_VERIFIED'}
 sys.path.insert(0,str(a.source_root));from xauusd_forecaster.ridge import RidgeArtifact
 def load_artifact(path,expected):
  key=(str(path),expected)
  if key in arts:return arts[key]
  reference=frozen_inventory.get(key)
  if reference is None:
   result={'source_path':str(path),'expected_canonical_hash':expected,'status':'UNKNOWN_NOT_IN_FROZEN_INVENTORY'};inventory.append(result);arts[key]=(None,result);return None,result
  p=pathlib.Path(reference['copy_path']);result={'source_path':str(path),'frozen_copy_source':str(p),'expected_canonical_hash':expected,'status':'UNKNOWN_MISSING'};payload=None
  if not p.is_absolute() or not p.resolve().is_relative_to(a.artifact_root.resolve()): result['status']='REJECTED_OUTSIDE_KNOWN_ARTIFACT_ROOT'
  elif p.exists():
   if p.stat().st_size>2_000_000:result['status']='REJECTED_SIZE'
   else:
    raw=p.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=reference['bytes_sha256']:raise ValueError('FROZEN_ARTIFACT_BYTES_MISMATCH')
    try:
     payload=read_checked_copy(p,expected);actual=chash(payload);result.update(bytes_sha256=hashlib.sha256(raw).hexdigest(),canonical_hash=actual,bytes=len(raw))
     if actual!=expected:result['status']='ARTIFACT_HASH_MISMATCH';payload=None
     else:
      dest=o/'artifacts'/result['bytes_sha256']/(p.name);dest.parent.mkdir(parents=True,exist_ok=True)
      if dest.exists():require(dest.read_bytes()==raw,'ARTIFACT_COPY_MISMATCH')
      else:dest.write_bytes(raw)
      result.update(status='EXACT_HASH_VERIFIED',copy_path=str(dest),schema=payload.get('schema'),feature_names=payload.get('feature_names'),alpha=payload.get('alpha'),training_rows=payload.get('training_rows'))
    except (ValueError,TypeError) as ex:result.update(status='MALFORMED_ARTIFACT',error=type(ex).__name__)
  inventory.append(result);arts[key]=(payload,result);artifact_by_path[str(p)]=(payload,result);return payload,result
 for m in models.values():
  payload,info=load_artifact(m['artifact_path'],m['artifact_hash']);m.update(artifact_copy=info.get('copy_path'),artifact_bytes_sha256=info.get('bytes_sha256'),artifact_status=info['status'],generation_ids=version_gen[m['model_version']])
  if payload and 'market_artifact_path' in payload:
   for component in ('market','news'):load_artifact(payload[component+'_artifact_path'],payload[component+'_artifact_hash'])
 rolling=collections.defaultdict(dict);raw_predictions=[]
 for p in x['predictions']:
  if p['model_identity'] not in IDENTITIES:continue
  m=models.get(p['model_version'])
  if not m or date(p['decision_time'])<=date(m['created_at']):continue
  k=p['source_decision_id'];prev=rolling[k].get(p['model_identity'])
  if prev is None or (date(m['created_at']),p['model_version'])>(date(models[prev['model_version']]['created_at']),prev['model_version']):rolling[k][p['model_identity']]=p
 def ridge(payload,features,market_component=False,legacy_scales=False):
  return replay_exact_ridge(payload,features,chash(payload),expected_order=MARKET_FEATURES if market_component else None,legacy_scales=legacy_scales)
 rows=[];replay_rows=[];counter=collections.Counter()
 for k,ps in sorted(rolling.items(),key=lambda kv:date(next(iter(kv[1].values()))['decision_time'])):
  t=next(iter(ps.values()))['decision_time'];ma=market.get(k,{});me=mx.get(k,{});mf=json.loads(me.get('features_json','{}'));oc=out.get(k,{});oe=out_extra.get(k,{});sm=source_market.get(ma.get('source_snapshot_hash'),{});sf=json.loads(sm.get('features_json','{}'));eligible=[act for act in activations if date(act['activated_at'])<date(t)];active=eligible[-1] if eligible else {};g=active.get('generation_id');mem=generations.get(g,{})
  valid=bool(k in clocks and ma.get('evidence_lane')=='LIVE_OOS' and oc.get('evidence_lane')=='LIVE_OOS' and oc.get('outcome_status')=='VALID' and ma.get('u5') and oe.get('exit_received_time'))
  available=max((v for v in (oe.get('recomputed_at'),oe.get('exit_received_time')) if v),key=date,default=None)
  row={'source_decision_id':k,'decision_time':t,'active_generation':g,'activated_at':active.get('activated_at'),'clock_complete':k in clocks,'valid':valid,'evidence_lane':ma.get('evidence_lane'),'market_health':ma.get('data_health'),'session_state':sf.get('market_session',sf.get('session_state','UNKNOWN')),'u5':ma.get('u5'),'u5_version':ma.get('u5_version'),'bid':ma.get('bid'),'ask':ma.get('ask'),'gross_midpoint_log':oc.get('gross_midpoint_direction_move'),'gross_target_u5':oc['gross_midpoint_direction_move']/ma['u5'] if oc.get('gross_midpoint_direction_move') is not None and ma.get('u5') else None,'long_log_return':oc.get('long_quote_return'),'short_log_return':oc.get('short_quote_return'),'entry_time':oe.get('entry_received_time'),'exit_time':oe.get('exit_received_time'),'label_available_at':available,'label_version':oe.get('label_version'),'outcome_status':oc.get('outcome_status'),'outcome_reasons':json.loads(oe.get('reason_codes_json','[]')),'market_features':{n:mf.get(n) for n in MARKET_FEATURES},'market_feature_order':list(MARKET_FEATURES),'market_snapshot_hash':ma.get('output_hash'),'source_snapshot_hash':ma.get('source_snapshot_hash'),'source_received_time':sm.get('source_received_time'),'market_computed_at':me.get('recomputed_at'),'source_collected_at':sm.get('collected_at'),'cost_contract':'commission-30usd-million-side;slippage-zero-ASSUMED;bidask-label-spread-included','predictions':{}}
  for identity,p in ps.items():
   m=models[p['model_version']];sc=scores.get((k,p['model_version']));recorded=p['predicted_direction_u5'];artifact,ainfo=load_artifact(m['artifact_path'],m['artifact_hash']);item={'predicted_u5':recorded,'recorded_residual_u5':p['predicted_news_residual_u5'],'recorded_action':p['recommended_action'],'effective_action':p['effective_action'],'model_version':p['model_version'],'generation':version_gen[p['model_version']],'created_at':m['created_at'],'prediction_created_at':p['created_at'],'training_cutoff':m['training_cutoff'],'active_match':mem.get(identity)==p['model_version'],'valid':bool(valid and sc and p['evidence_lane']=='LIVE_OOS'),'prediction_status':p['prediction_status'],'feature_hash':p['feature_snapshot_hash'],'ev_long_u5':p['ev_long_u5'],'ev_short_u5':p['ev_short_u5'],'artifact_hash':m['artifact_hash'],'artifact_bytes_sha256':ainfo.get('bytes_sha256'),'artifact_status':ainfo['status'],'replay_status':'NOT_RUN'}
   matching=[n for n in news[k] if (m['eligibility_version'] is None or n['eligibility_version']==m['eligibility_version']) and chash((ma.get('output_hash'),n['output_hash'],m['eligibility_version']))==p['feature_snapshot_hash']]
   binding='EXACT_CURRENT_TUPLE' if len(matching)==1 else 'UNKNOWN_HISTORICAL_OR_GATED_HASH'
   if not matching:
    matching=[n for n in news[k] if chash((ma.get('output_hash'),n['output_hash']))==p['feature_snapshot_hash']]
    if len(matching)==1:binding='EXACT_HISTORICAL_TWO_HASH_TUPLE'
   ns=matching[0] if len(matching)==1 else None
   item['feature_binding']=binding;item['news_pit']='UNKNOWN_EVENT_RECEIPTS_NOT_REPLAYED'
   replay=None
   if recorded is None:item['replay_status']='GATED_NO_NUMERIC_PREDICTION'
   elif artifact is None:item['replay_status']=ainfo['status']
   else:
    try:
     nf=json.loads(ns['features_json']) if ns else {};broad=identity in ('BROAD_NEWS_RESIDUAL','BROAD_FULL','NEWS_ONLY');exposed=float(nf.get('broad_news_event_count' if broad else 'news_event_count') or 0)>0
     item['news_exposed_from_features']=exposed;item['news_snapshot_computed_at']=ns.get('recomputed_at') if ns else None
     if identity=='MARKET_ONLY':replay=ridge(artifact,mf,True)
     elif ns is None:raise ValueError('NEWS_FEATURE_BINDING_UNAVAILABLE')
     elif identity in ('NEWS_RESIDUAL','BROAD_NEWS_RESIDUAL'):replay=ridge(artifact,nf) if exposed else 0.0
     elif identity=='NEWS_ONLY':replay=ridge(artifact,nf) if exposed else None
     else:
      components={}
      for c in ('market','news'):
       cp=load_artifact(artifact[c+'_artifact_path'],artifact[c+'_artifact_hash'])[0]
       cm=models.get(artifact[c+'_model_version'])
       if cp is None and cm and cm['artifact_hash']==artifact[c+'_artifact_hash']:
        cp=load_artifact(cm['artifact_path'],cm['artifact_hash'])[0];item[c+'_manifest_path_resolution']='EXACT_VERSION_AND_HASH_METADATA_RELOCATION'
       components[c]=cp
      if any(v is None for v in components.values()):raise ValueError('MANIFEST_COMPONENT_UNVERIFIED')
      for c in ('market','news'):
       version=artifact[c+'_model_version'];metadata=models.get(version)
       if not metadata or metadata['artifact_hash']!=artifact[c+'_artifact_hash']:raise ValueError('MANIFEST_COMPONENT_VERSION_MISMATCH')
      rp=ridge(components['market'],mf,True);rn=ridge(components['news'],nf) if exposed else 0.;replay=rp+rn;item.update(replayed_market_u5=rp,replayed_residual_u5=rn,manifest_generation=artifact.get('generation_id'))
     item['replay_status']='MATCH' if near(recorded,replay) else 'NUMERIC_MISMATCH'
     if item['replay_status']=='NUMERIC_MISMATCH':
      # Historical owner 99861c47^ used raw scales and always evaluated news.
      # Keep current result visible; matching old semantics is not an unknown
      # historical deployment identity magically becoming proved.
      if identity=='MARKET_ONLY':legacy=ridge(artifact,mf,True,True);always_current=replay
      elif identity in ('NEWS_RESIDUAL','BROAD_NEWS_RESIDUAL','NEWS_ONLY'):legacy=ridge(artifact,nf,False,True);always_current=ridge(artifact,nf)
      else:legacy=ridge(components['market'],mf,True,True)+ridge(components['news'],nf,False,True);always_current=ridge(components['market'],mf,True)+ridge(components['news'],nf)
      item.update(historical_raw_scale_replayed_u5=legacy,historical_raw_scale_match=near(recorded,legacy))
      if near(recorded,legacy):
       item['first_divergence']='HISTORICAL_ALWAYS_EVALUATE_NEWS_99861c47_PARENT' if near(recorded,always_current) else 'HISTORICAL_RAW_SCALE_SEMANTICS_99861c47_PARENT';item['replay_status']='MATCH_HISTORICAL_RIDGE_CONTRACT'
    except (ValueError,KeyError,TypeError) as ex:item['replay_status']='INPUT_OR_MANIFEST_UNRESOLVED';item['first_divergence']=str(ex)
   item['replayed_u5']=replay;item['absolute_error']=abs(recorded-replay) if recorded is not None and replay is not None else None;counter[identity+':'+item['replay_status']]+=1
   if item['valid']:
    expected=0.0 if item['recorded_action']=='WAIT' else row['long_log_return'] if item['recorded_action']=='LONG' else row['short_log_return']
    if not near(sc['value_quote_return'],expected) or not near(sc['target_direction_u5'],row['gross_target_u5']):raise ValueError('ACCEPTED_SCORE_LABEL_OR_UNIT_MISMATCH')
   row['predictions'][identity]=item;replay_rows.append({'source_decision_id':k,'decision_time':t,'identity':identity,**{field:item.get(field) for field in ('model_version','artifact_status','feature_binding','replay_status','first_divergence','predicted_u5','replayed_u5','absolute_error','historical_raw_scale_replayed_u5','historical_raw_scale_match')}})
  row['active_common']=all(i in row['predictions'] and row['predictions'][i]['active_match'] and row['predictions'][i]['valid'] for i in ('MARKET_ONLY','BROAD_NEWS_RESIDUAL','BROAD_FULL'));rows.append(row)
 panel={'schema':'offline-model-research-panel-v1','rows':rows,'models':models,'metadata':{'source_database':str(d),'baseline_historical_sha256':f['historical_backup_sha256'],'input_identity':identities(d),'followup_input_sha256':sha(a.input_dir/'followup_input.json'),'selected_inputs_sha256':sha(extra),'script_sha256':sha(pathlib.Path(__file__)),'abs_tolerance':ABS_TOL,'rel_tolerance':REL_TOL,'market_feature_order':list(MARKET_FEATURES),'label_availability_authority':'max(derived_outcomes.recomputed_at,exit_received_time); do not fit unless strictly before fit time','session_authority':'original market_snapshot feature field if retained; UNKNOWN is not CLOSED/OPEN','queries':e['queries'],'artifact_replay_counts':dict(counter),'artifact_inventory_counts':dict(collections.Counter(i['status'] for i in inventory)),'all_opportunities':len(rows),'valid_opportunities':sum(r['valid'] for r in rows),'active_common':sum(r['active_common'] for r in rows),'broker_pnl':'UNKNOWN_NO_FILL_RECORDS','news_full_event_pit':'UNKNOWN_NOT_REPLAYED','reference_cost_log':f['commission_log_cost_per_directional']}}
 panel['metadata']['owner_sha256']={name:sha(a.source_root/'xauusd_forecaster'/name) for name in ('ridge.py','training.py','inference_v2.py','execution_costs.py','executable_label.py')}
 panel['metadata']['source_git_sha']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=a.source_root,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).strip()
 panel['metadata']['python_version']=sys.version;panel['metadata']['numpy_version']=np.__version__
 panel['metadata']['experiment_plan_sha256']=plan_sha256
 panel['metadata']['frozen_artifact_inventory_sha256']=sha(a.artifact_inventory)
 write(o/'panel.json',panel);write(o/'artifact_inventory.json',inventory)
 with (o/'replay_rows.csv').open('w',newline='',encoding='utf-8') as stream:
  w=csv.DictWriter(stream,fieldnames=list(replay_rows[0]));w.writeheader();w.writerows(replay_rows)
 write(o/'replay_summary.json',{**panel['metadata'],'panel_sha256':sha(o/'panel.json'),'max_abs_error':max((r['absolute_error'] or 0 for r in replay_rows),default=0),'first_mismatches':[r for r in replay_rows if r['replay_status']=='NUMERIC_MISMATCH'][:10]});require(identities(d)==f['file_identity'],'SNAPSHOT_CHANGED');print(json.dumps(panel['metadata'],indent=2),flush=True)

if __name__=='__main__':main()
