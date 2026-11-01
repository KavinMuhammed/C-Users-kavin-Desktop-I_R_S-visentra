"""Real extraction through API to immutable, authorized server PDF."""
import hashlib
from sqlalchemy import select
from p11_fixtures import URL, create, user, signoff
from test_phase1_recovery import image_bytes, selectable_pdf
from pdf_assertions import pdf_text
from visentra_api.db import SessionLocal
from visentra_api.models import TextEvidence
from visentra_api.storage import storage
import pytest


@pytest.mark.parametrize('kind', ['image', 'pdf'])
def test_real_evidence_correction_and_authorized_pdf(api, api2, kind):
    user(api,'ocr-report'); user(api2,'foreign-report'); ins=create(api)
    path=f"{URL}/{ins['id']}/text-evidence"
    data=image_bytes() if kind=='image' else selectable_pdf()
    ev=api.post(path,files={'file':('source.png' if kind=='image' else 'source.pdf',data)},data={'document_revision':'DOC-7'}).json()
    assert ev['status']=='completed' and ev['original_text'].strip()
    assert api.patch(path+'/'+ev['id'],json={'text':'Confirmed transcription 123','confirmed':True},headers={'If-Match':str(ev['revision'])}).status_code==200
    assert signoff(api,ins['id']).status_code==200
    result=api.post(f"{URL}/{ins['id']}/reports");assert result.status_code==201,result.text
    report=result.json();pdf=api.get(report['download_url']);assert pdf.status_code==200
    text=pdf_text(pdf.content)
    for value in ['Extracted text evidence','Original extracted text','Human-corrected text','Confirmed transcription 123','DOC-7',ev['sha256'],ev['id'],'Page 1','not an AI defect finding']:
        assert value in text,value
    assert ' '.join(ev['original_text'].split()) in text
    assert hashlib.sha256(pdf.content).hexdigest()==report['sha256']
    assert api2.get(report['download_url']).status_code==404
    assert api2.post(f"{URL}/{ins['id']}/reports").status_code==404
    assert api.patch(path+'/'+ev['id'],json={'text':'late','confirmed':True},headers={'If-Match':'2'}).status_code==409
    again=api.post(f"{URL}/{ins['id']}/reports").json();assert again['id']==report['id']


def test_ocr_source_corruption_blocks_report_and_can_retry(api):
    user(api,'ocr-integrity');ins=create(api);path=f"{URL}/{ins['id']}/text-evidence"
    data=selectable_pdf();ev=api.post(path,files={'file':('source.pdf',data)}).json()
    assert signoff(api,ins['id']).status_code==200
    with SessionLocal() as db:key=db.get(TextEvidence,ev['id']).object_key
    storage.put(key,b'corrupt','application/pdf')
    failed=api.post(f"{URL}/{ins['id']}/reports")
    assert failed.status_code==409 and failed.json()['error']['code']=='INTEGRITY_FAILED'
    storage.put(key,data,'application/pdf')
    assert api.post(f"{URL}/{ins['id']}/reports").status_code==201


# ------------------------------------------------------------------ Phase C: extracted text is searchable, inside the workspace only
def test_inspection_search_finds_extracted_and_corrected_text_but_not_across_workspaces(api, api2):
    from test_phase1_recovery import selectable_pdf
    user(api, "pc-search-a"); user(api2, "pc-search-b")
    ins = create(api)
    other = create(api)                                         # same owner, no evidence: must not match
    path = f"{URL}/{ins['id']}/text-evidence"
    ev = api.post(path, files={"file": ("source.pdf", selectable_pdf())}).json()
    assert ev["status"] == "completed" and ev["original_text"].strip()
    word = max(ev["original_text"].split(), key=len).strip(".,:;").lower()
    hits = api.get(URL, params={"q": word}).json()
    ids = [i["id"] for i in hits["items"]]
    assert ins["id"] in ids and other["id"] not in ids
    assert api.patch(f"{path}/{ev['id']}", json={"text": "Confirmed transcription ZX-4471", "confirmed": True},
                     headers={"If-Match": str(ev["revision"])}).status_code == 200
    assert [i["id"] for i in api.get(URL, params={"q": "zx-4471"}).json()["items"]] == [ins["id"]]
    assert ins["id"] in [i["id"] for i in api.get(URL, params={"q": word}).json()["items"]]   # original text is still searchable
    # another account cannot find it through search, by original or corrected text
    assert api2.get(URL, params={"q": "zx-4471"}).json()["items"] == []
    assert ins["id"] not in [i["id"] for i in api2.get(URL, params={"q": word}).json()["items"]]
    # LIKE wildcards in the query are literal
    assert ins["id"] not in [i["id"] for i in api.get(URL, params={"q": "zx_4471"}).json()["items"]]
