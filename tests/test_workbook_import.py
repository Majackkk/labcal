import copy
import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from xml.sax.saxutils import escape

import test_campaigns as campaign_fixtures
from labcal.adapters.spreadsheet import read_workbook, extract_candidates, column_name
from labcal.application.workbooks import import_workbook, get_workbook
from labcal.application.campaigns import create_campaign, campaign_directory, update_campaign
from labcal.application.console import console_state
from labcal.adapters.server import make_server
from labcal.errors import LabcalError
from labcal.util import file_hash, canonical_json, content_hash


def workbook(path, rows, merges=()):
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    body = []
    for number, row in enumerate(rows, 1):
        cells = ''.join(f'<c r="{column_name(col)}{number}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                        for col, value in enumerate(row, 1) if value is not None)
        body.append(f'<row r="{number}">{cells}</row>')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="方案" sheetId="1" r:id="r1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',f'<worksheet xmlns="{ns}"><sheetData>{"".join(body)}</sheetData><mergeCells>'+''.join(f'<mergeCell ref="{r}"/>' for r in merges)+'</mergeCells></worksheet>')


class WorkbookImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.file = Path(self.temp.name) / 'plan.xlsx'

    def tearDown(self): self.temp.cleanup()

    def extracted(self, rows, merges=()):
        workbook(self.file, rows, merges)
        return extract_candidates(read_workbook(self.file))[1]

    def test_seven_columns_and_missing_values_keep_zero_and_serial(self):
        rows = self.extracted([
            ['设备名称','厂家','设备型号','设备编号','校准/检定参数','允许误差 MPE 要求','不确定度要求'],
            ['模拟仪器','测试厂','MOD-X','0007','温度','0','U ≤ 0.2 ℃, k=2'],
            ['第二台',None,None,None,'长度',None,None]])
        self.assertEqual(rows[0]['fields'], {'equipment_name':'模拟仪器','manufacturer':'测试厂','model':'MOD-X','equipment_number':'0007','parameter':'温度','mpe':'0','uncertainty':'U ≤ 0.2 ℃, k=2'})
        self.assertIsNone(rows[1]['fields']['manufacturer']); self.assertIsNone(rows[1]['fields']['model'])
        self.assertIsNone(rows[1]['fields']['equipment_number']); self.assertIsNone(rows[1]['fields']['mpe']); self.assertIsNone(rows[1]['fields']['uncertainty'])
        self.assertEqual(rows[0]['field_sources']['equipment_number']['cell'],'D2')

    def test_sequence_and_unlabelled_model_are_not_invented_identity(self):
        rows = self.extracted([['设备名称','序号','校准参数'], ['模拟仪器 MOD-10','01','温度']])
        self.assertEqual(rows[0]['fields']['equipment_name'],'模拟仪器 MOD-10')
        self.assertIsNone(rows[0]['fields']['model']);self.assertIsNone(rows[0]['fields']['equipment_number'])

    def test_vertical_merged_identity_keeps_origin_and_does_not_fill_unmerged_blanks(self):
        rows = self.extracted([
            ['仪器名称','制造商','型号规格','出厂编号','检定参数'],
            ['模拟设备','测试厂','M1','009','参数甲'], [None,None,None,None,'参数乙'],
            ['另一设备',None,None,None,'参数丙']], ['A2:A3','B2:B3','C2:C3','D2:D3','A4:D4'])
        self.assertEqual(rows[1]['fields']['equipment_number'],'009');self.assertEqual(rows[1]['field_sources']['equipment_number']['cell'],'D2')
        self.assertIsNone(rows[2]['fields']['equipment_number']);self.assertIsNone(rows[2]['fields']['manufacturer'])

    def test_explicit_labels_above_table_supply_sheet_metadata(self):
        rows = self.extracted([
            ['设备名称：模拟温度计；厂家：测试厂；设备型号：M-42；设备编号：009'],
            ['校准／检定参数','允许误差','不确定度要求'], ['温度','±1 ℃',None]])
        self.assertEqual(rows[0]['fields']['manufacturer'],'测试厂');self.assertEqual(rows[0]['fields']['model'],'M-42');self.assertEqual(rows[0]['fields']['equipment_number'],'009')
        self.assertEqual(rows[0]['field_sources']['manufacturer']['origin'],'label')

    def test_inline_uncertainty_is_kept_as_candidate_source_text(self):
        rows = self.extracted([['设备名称','校准参数','计量依据'],['模拟仪器','质量','校准后评定测量不确定度 (U ≤ 0.10 mg, k=2)。'],['其他仪器','温度','测量不确定度评估'],['未明确仪器','长度','扩展不确定度满足要求']])
        self.assertIn('U ≤ 0.10 mg',rows[0]['fields']['uncertainty']);self.assertEqual(rows[0]['field_sources']['uncertainty']['cell'],'C2')
        self.assertIsNone(rows[1]['fields']['uncertainty'])
        self.assertIsNone(rows[2]['fields']['uncertainty'])

    def test_missing_parameter_does_not_drop_equipment_row(self):
        rows=self.extracted([['设备名称','设备编号','校准/检定参数','允许误差'],['模拟仪器','001',None,'±1 ℃']])
        self.assertEqual(len(rows),1);self.assertIsNone(rows[0]['fields']['parameter'])


class WorkbookArchiveTests(unittest.TestCase):
    setUp = campaign_fixtures.CampaignTests.setUp
    tearDown = campaign_fixtures.CampaignTests.tearDown
    imported = campaign_fixtures.CampaignTests.imported

    def test_workbench_upload_extracts_fields_and_creates_import_folder(self):
        server=make_server(self.ws,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            body={'lab_id':'LAB_A','campaign_id':self.c['id'],'filename':self.book.name,'file_base64':base64.b64encode(self.book.read_bytes()).decode()}
            client.request('POST','/api/workbooks',json.dumps(body),{'Content-Type':'application/json','X-Labcal-Token':server.token})
            response=client.getresponse();self.assertEqual(response.status,200);result=json.loads(response.read())['result']
            self.assertTrue((Path(result['import_directory'])/'原件.xlsx').exists())
            client.request('GET',f"/api/workbook?lab=LAB_A&campaign={self.c['id']}&id={result['workbook_id']}")
            response=client.getresponse();self.assertEqual(response.status,200);item=json.loads(response.read())['result']
            self.assertEqual(len(item['candidate_view'][0]['fields']),7)
            self.assertIsNone(item['candidate_view'][0]['fields']['equipment_number'])
            client.close()
        finally:server.shutdown();server.server_close();thread.join(timeout=5)
    def test_import_creates_read_only_original_and_fixed_extraction_in_scoped_folder(self):
        result=self.imported();directory=Path(result['import_directory'])
        self.assertTrue(directory.is_relative_to(campaign_directory(self.ws,'LAB_A',self.c)))
        self.assertEqual(file_hash(directory/'原件.xlsx'),file_hash(self.book))
        item=get_workbook(self.ws,'LAB_A',result['workbook_id'])
        self.assertEqual(json.loads((directory/'提取记录.json').read_text()),item['payload'])
        self.assertFalse((directory/'原件.xlsx').stat().st_mode & 0o222)
        self.assertEqual(self.imported()['import_directory'],str(directory))
        other=import_workbook(self.ws,'LAB_A',self.other['id'],self.book)
        self.assertNotEqual(other['import_directory'],str(directory))

    def test_year_change_moves_whole_import_folder_without_changing_hashes(self):
        result=self.imported(); old=Path(result['import_directory'])
        changed=update_campaign(self.ws,'LAB_A',self.c['id'],{'year':2025,'expected_record_hash':self.c['record_hash']})
        item=get_workbook(self.ws,'LAB_A',result['workbook_id'],self.c['id'])
        self.assertFalse(old.exists()); self.assertTrue(Path(item['import_directory']).is_relative_to(campaign_directory(self.ws,'LAB_A',changed)))
        self.assertEqual(file_hash(Path(item['import_directory'])/'原件.xlsx'),file_hash(self.book))

    def test_legacy_snapshot_is_projected_without_rewriting_stored_payload(self):
        result=self.imported();item=get_workbook(self.ws,'LAB_A',result['workbook_id'])
        legacy=copy.deepcopy(item['payload']);legacy['schema_version']='1.0';legacy['parser']='xlsx-stdlib/1.0'
        for row in legacy['candidates']: row.pop('fields');row.pop('field_sources')
        digest=content_hash(legacy);key='WB-'+digest[:24]
        with self.ws.lab_store('LAB_A').connect() as db:
            db.execute('INSERT INTO workbook_imports VALUES (?,?,?,?)',(key,canonical_json(legacy),digest,'TEST_ONLY'))
        viewed=get_workbook(self.ws,'LAB_A',key)
        self.assertEqual(viewed['payload_hash'],digest);self.assertNotIn('fields',viewed['payload']['candidates'][0])
        self.assertIn('fields',viewed['candidate_view'][0])

    def test_changed_archive_copy_is_not_silently_overwritten(self):
        result=self.imported();file=Path(result['import_directory'])/'原件.xlsx';file.chmod(0o644);file.write_bytes(b'test only corrupt archive')
        with self.assertRaises(LabcalError) as error:self.imported()
        self.assertEqual(error.exception.code,'WORKBOOK_ARCHIVE_CHANGED')
