"""Synthetic offline artifact-boundary regressions; never opens production paths."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

path=Path(__file__).resolve().parents[1]/'scripts'/'build_model_repair_panel.py'
spec=importlib.util.spec_from_file_location('offline_replay_contract',path)
replay=importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)

@pytest.fixture
def artifact():
 return {'schema':'xauusd.forward.ridge.v2','feature_names':['first','second'],'means':[1.0,2.0],'scales':[2.0,4.0],'coefficients':[.2,-.3],'intercept':.1,'alpha':100.0,'training_dataset_hash':'synthetic-known-input','residual_std':.2,'training_rows':20}

def test_same_real_ridge_artifact_input_is_deterministic(artifact):
 digest=replay.chash(artifact);features={'second':6.,'first':3.}
 first=replay.replay_exact_ridge(artifact,features,digest,expected_order=['first','second'],expected_version='frozen-v1',recorded_version='frozen-v1')
 assert first==pytest.approx(0.0,abs=1e-14)
 assert replay.replay_exact_ridge(artifact,features,digest)==first

@pytest.mark.parametrize('kind,reason',[('hash','ARTIFACT_HASH_MISMATCH'),('order','FEATURE_ORDER_MISMATCH'),('unit','TARGET_UNIT_MISMATCH'),('version','MODEL_VERSION_MISMATCH'),('dimension','FEATURE_DIMENSION_MISMATCH')])
def test_identity_family_rejected_before_prediction(artifact,kind,reason):
 payload=copy.deepcopy(artifact);expected=replay.chash(payload);kwargs={}
 if kind=='hash':payload['intercept']+=1
 if kind=='order':kwargs['expected_order']=['second','first']
 if kind=='unit':kwargs['target_unit']='USD'
 if kind=='version':kwargs.update(expected_version='v1',recorded_version='v2')
 if kind=='dimension':payload['scales'].pop();expected=replay.chash(payload)
 with pytest.raises(ValueError,match=reason):replay.replay_exact_ridge(payload,{'first':3,'second':6},expected,**kwargs)

def test_historical_nearzero_scale_is_an_explicit_different_contract(artifact):
 artifact['means']=[0.,0.];artifact['scales']=[1e-15,1.];artifact['coefficients']=[.2,0.];artifact['intercept']=0.
 digest=replay.chash(artifact);features={'first':1e-15,'second':0.}
 assert replay.replay_exact_ridge(artifact,features,digest)==pytest.approx(2e-16,abs=1e-25)
 assert replay.replay_exact_ridge(artifact,features,digest,legacy_scales=True)==pytest.approx(.2)
 # Historical semantics are only an explicit offline replay, not runtime fallback.
 assert replay.replay_exact_ridge(artifact,features,digest)<1e-12

@pytest.mark.parametrize('kind,reason',[('missing','ARTIFACT_MISSING'),('malformed','ARTIFACT_MALFORMED'),('tampered','ARTIFACT_HASH_MISMATCH')])
def test_missing_or_unverified_artifact_is_never_a_numeric_pass(tmp_path,artifact,kind,reason):
 path=tmp_path/'model.json';expected=replay.chash(artifact)
 if kind=='malformed':path.write_text('<html>not model bytes</html>')
 if kind=='tampered':artifact['intercept']+=.5;path.write_text(json.dumps(artifact))
 with pytest.raises(ValueError,match=reason):replay.read_checked_copy(path,expected)

def test_exact_checked_copy_preserves_bytes(artifact,tmp_path):
 path=tmp_path/'model.json';raw=json.dumps(artifact,indent=2).encode();path.write_bytes(raw)
 assert replay.read_checked_copy(path,replay.chash(artifact))==artifact
 assert path.read_bytes()==raw

def test_digest_reader_enforces_research_root_before_file_access():
 with pytest.raises(ValueError,match='OUTSIDE_OFFLINE_RESEARCH_ROOT'):
  replay.sha(Path.home()/'rejected-research-input.json')
