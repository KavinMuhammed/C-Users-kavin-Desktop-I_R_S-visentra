"""Numeric failures and screening failures must not become successful empty/relevant results."""
import numpy as np
import pytest
import torch

from p11_fixtures import create, jpeg, user
from test_phase13 import model_dir  # noqa: F401
from test_final_audit import _ready_engine
from visentra_api.inspection import analysis as AN
from visentra_ml.detection.domain_gate import Gate
from visentra_ml.detection.model import postprocess
from visentra_ml.detection.output import InvalidModelOutput


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_raw_probability_is_not_empty_success(value):
    raw = torch.zeros(1, 1, 8)
    raw[..., 4] = value
    with pytest.raises(InvalidModelOutput):
        postprocess(raw, 3, score_floor=.001, nms_iou=.65, max_det=100)


def test_invalid_class_shape_is_rejected():
    with pytest.raises(InvalidModelOutput):
        postprocess(torch.zeros(1, 1, 7), 3, score_floor=.001, nms_iou=.65, max_det=100)


def test_real_engine_invalid_tensor_persists_failure(api, model_dir, monkeypatch):
    user(api, "p2-invalid")
    eng = _ready_engine()
    monkeypatch.setattr(eng.predictor.model, "forward", lambda x: torch.full((1, 1, 8), float("nan")))
    result = create(api, jpeg(seed=211))
    assert result["last_error_code"] == "INVALID_MODEL_OUTPUT"
    assert result["detection"]["state"] == "failed"
    assert not result["detection"]["completed"] and not result["predictions"]
    saved = api.get('/api/v1/inspections/' + result['id']).json()
    assert saved['last_error_code'] == 'INVALID_MODEL_OUTPUT'
    assert eng._slot.acquire(timeout=.1)
    eng._slot.release()


def test_nan_gate_evidence_cannot_reject_image():
    g = Gate.__new__(Gate)
    ev = {k: float('nan') for k in ('p', 'airplane', 'p_crop_max', 'airplane_crop_max', 'coco_top_score')}
    with pytest.raises(ValueError, match='invalid domain gate'):
        g.decide(ev)


def test_nan_features_are_explicit_failure():
    with pytest.raises(ValueError, match='invalid domain gate features'):
        Gate.__new__(Gate).probability(np.full((6, 1232), np.nan))


def test_incomplete_gate_checksum_manifest(tmp_path):
    (tmp_path / 'SHA256SUMS').write_text('')
    with pytest.raises(ValueError, match='incomplete'):
        Gate(tmp_path)


def test_gate_runner_reports_numeric_error(tmp_path):
    runner = AN.GateRunner('test', tmp_path)
    runner.state = 'ready'
    class BrokenGate:
        def check(self, im):
            return Gate.__new__(Gate).probability(np.full((6, 1232), np.nan))
    runner.g = BrokenGate()
    verdict = runner.check_bytes(jpeg())
    assert verdict['state'] == 'error'
    assert verdict['in_domain'] is True  # keeps the inspection available for human review


@pytest.mark.parametrize('reference,expected', [('INS-2026-000017', 'uncertain'), ('INS-2026-000005', 'relevant')])
def test_original_incident_real_models_isolated_persistence(api, monkeypatch, reference, expected):
    import hashlib
    import sqlite3
    from pathlib import Path
    from visentra_api.config import settings
    root = Path(__file__).resolve().parents[1]
    with sqlite3.connect((root/'data/visentra.db').as_uri() + '?mode=ro', uri=True) as source:
        key, sha = source.execute('SELECT m.object_key,m.sha256 FROM inspection_media m JOIN inspections i ON i.id=m.inspection_id WHERE i.ref=? ORDER BY m.ordinal', (reference,)).fetchone()
    data = (root/'data/objects'/key).read_bytes()
    assert hashlib.sha256(data).hexdigest() == sha
    monkeypatch.setattr(settings, 'detection_models_dir', root/'models/detection')
    monkeypatch.setattr(settings, 'inspection_model', 'VT-DET-0002')
    monkeypatch.setattr(settings, 'domain_gate_dir', root/'models/gate')
    monkeypatch.setattr(settings, 'domain_gate', 'VT-GATE-0002')
    AN.reset()
    try:
        user(api, 'p2-real')
        result = create(api, data)
        assert result['detection']['relevance'] == expected
        assert result['detection']['state'] == 'completed_zero'
        assert result['analysis']['status'] == 'succeeded'
        saved = api.get('/api/v1/inspections/' + result['id']).json()
        assert saved['media'][0]['sha256'] == sha
        assert saved['detection']['gate']['gate_id'] == 'VT-GATE-0002'
        from visentra_api.db import SessionLocal
        from visentra_api.models import AnalysisRun
        with SessionLocal() as db:
            run = db.query(AnalysisRun).filter_by(inspection_id=result['id']).one()
            assert run.model_checksum == AN.engine()[0].weights_sha256
    finally:
        AN.reset()


@pytest.mark.parametrize('failure_state', ['failed', 'error'])
def test_screening_failure_is_never_relevant(api, model_dir, monkeypatch, failure_state):
    user(api, 'p2-gate-' + failure_state)
    class FailedGate:
        def check_bytes(self, data):
            return {'state': failure_state, 'in_domain': True, 'reason': 'screening unavailable'}
    monkeypatch.setattr(AN, 'gate', lambda: (FailedGate(), None))
    result = create(api, jpeg(seed=212))
    assert result['detection']['relevance'] == 'failed'
    assert result['detection']['screening_failed'] is True
    assert result['detection']['completed'] is True
    assert 'confirm' in result['detection']['relevance_note']
