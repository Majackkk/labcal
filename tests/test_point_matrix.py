"""Synthetic point tables: parsing ambiguity must never turn into acceptance."""
import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from labcal.domain.point_matrix import plan_points, certificate_points, align_points, point_key, numeric
from labcal.application.workspace_catalog import classify_storage, catalog, archive_contents, storage_overview
from labcal.application.laboratories import create_laboratory
from labcal.application.console import console_state
from labcal.application.campaigns import create_campaign
from labcal.adapters.storage import Workspace
from labcal.adapters.server import make_server
from labcal.cli import parser, execute
from labcal.errors import LabcalError


def req(text, parameter='试验力示值误差', mpe='相对误差 ≤ ±0.5%'):
    return {'id':'R1','parameter':parameter,'raw':{'values':{'conditions':text}},'fields':{'mpe':mpe},'plan_anchor':"'sheet'!A2:G2"}


def extraction(rows, heading='力值校准'):
    return {'id':'EXT-TEST','payload':{'pages':[{'page':1,'lines':[{'id':'L1','page':1,'bbox':[.1,.1,.6,.12],'text':heading}],
        'tables':[{'bbox':[.1,.15,.9,.9],'rows':rows,'state':'candidate'}]}]}}


def force_table():
    return extraction([
        ['项目','仪器示值','实测值','示值相对误差','示值允许误差','重复性','重复性允许误差'],
        ['压力','N','N','%','%','%','%'],
        [None,'200','199.8','0.10','±1.0','0.00','≤1.0'],
        ['拉力','200','199.7','0.15','±1.0','0.02','≤1.0'],
        [None,'400','399.5','0.13','±1.0','0.01','≤1.0'],
        ['不确定度',None,None,None,'U = 0.46% (k=2)',None,None]])


class PointMatrixTests(unittest.TestCase):
    def test_component_lists_keep_capacity_and_notes_out_of_points(self):
        rows=plan_points([req('1、传感器 A（500 N / 50 kg）：校准点为 10, 20, 200 N；\n2、传感器 B（5 kN / 500 kg）：校准点为 200, 1000 N')])
        self.assertEqual([r['point'] for r in rows],['10','20','200','200','1000'])
        self.assertIn('传感器 A',rows[0]['component']);self.assertIn('传感器 B',rows[-1]['component'])

    def test_gauge_length_and_explanatory_notes_are_not_calibration_points(self):
        rows=plan_points([req('标距 50.0 mm 工况下：\n0.025, 0.050 mm\n*(重点考核 115mm)*','引伸计')])
        self.assertEqual([r['point'] for r in rows],['0.025','0.050'])
        self.assertIn('50.0',rows[0]['condition']);self.assertIn('115mm',rows[0]['note'])

    def test_ranges_percentages_and_unknown_prose_remain_unresolved(self):
        for text in ['0～500 N','10-500 N','1J、2J 各摆锤的 10%、20% 常用点','检查承重面']:
            rows=plan_points([req(text)])
            self.assertEqual(rows[0]['parse_state'],'unresolved');self.assertEqual(rows[0]['point'],'')

    def test_zero_negative_and_exponent_are_preserved(self):
        rows=plan_points([req('-20, 0, 2e1 ℃','温度示值误差')])
        self.assertEqual([r['point'] for r in rows],['-20','0','2e1'])
        self.assertEqual(point_key('0','℃'),point_key('0.0','°C'))

    def test_exact_unit_normalization_does_not_match_nearby_or_incompatible_points(self):
        self.assertEqual(point_key('1','kN'),point_key('1000.0','N'))
        self.assertNotEqual(point_key('1000','N'),point_key('1001','N'))
        self.assertNotEqual(point_key('1','mm'),point_key('1','N'))
        self.assertIsNone(numeric('1,2'));self.assertEqual(numeric('1,000'),numeric('1000'))

    def test_merged_conditions_units_and_table_uncertainty_are_candidate(self):
        before=force_table(); original=copy.deepcopy(before);rows,warnings=certificate_points(before)
        self.assertEqual(len(rows),3);self.assertFalse(warnings)
        self.assertEqual([r['condition'] for r in rows],['压力','拉力','拉力'])
        self.assertEqual(rows[0]['measured'],'199.8 N');self.assertEqual(rows[0]['error'],'0.10 %')
        self.assertEqual(rows[0]['repeatability'],'0.00 %');self.assertEqual(rows[0]['mpe'],'±1.0 %')
        self.assertEqual(rows[0]['uncertainty_scope'],'table');self.assertEqual(rows[0]['state'],'candidate')
        self.assertEqual(before,original)

    def test_duplicate_point_branches_are_visible_and_never_arbitrarily_chosen(self):
        observations,_=certificate_points(force_table())
        plans=plan_points([req('传感器 A：校准点为 200, 300 N')])
        rows=align_points(plans,observations)
        self.assertEqual([r['signal'] for r in rows],['ambiguous','ambiguous','plan_only','report_only'])
        self.assertTrue(all(r['status']=='pending' for r in rows))
        self.assertIn('双方标注的 MPE 不同',rows[0]['alerts'])

    def test_competing_limits_are_not_silently_resolved(self):
        plans=plan_points([req('5 mm/min','横梁移动速度','速度相对误差 MPE ≤ ±1.0%\n*(实测按 0.5 级控制在 ±0.5%)*')])
        row=align_points(plans,[])[0]
        self.assertIn('方案有多种限值表述',row['alerts']);self.assertEqual(row['status'],'pending')

    def test_unpointed_result_is_not_invented_as_zero(self):
        rows,_=certificate_points(extraction([['同轴度示值误差','允许误差','不确定度'],['%','%','U rel(k=2)'],['12.8','≤15','3.5%']],'同轴度校准'))
        self.assertEqual(rows[0]['point'],'');self.assertEqual(rows[0]['error'],'12.8 %')
        self.assertEqual(align_points([],rows)[0]['signal'],'report_only')

    def test_same_point_in_another_quantity_does_not_match(self):
        observations,_=certificate_points(force_table())
        rows=align_points(plan_points([req('200 N','扭矩')]),observations)
        self.assertEqual(rows[0]['signal'],'plan_only')

    def test_non_numeric_results_are_not_guessed(self):
        rows,notes=certificate_points(extraction([['校准点','实测值'],['N','N'],['见附件','符合']]))
        self.assertEqual(rows,[]);self.assertTrue(notes)

    def test_temperature_characteristics_stay_separate_at_same_setpoint(self):
        rows,_=certificate_points(extraction([['序号','设定温度/(℃)','上偏差/(℃)','下偏差/(℃)','温度波动度/(℃)','温度均匀度/(℃)'],
                                              ['No.1','80.0','-0.44','-1.07','0.24','0.63']], '温度校准'))
        self.assertEqual(len(rows),4);self.assertEqual(len({r['family'] for r in rows}),4)
        self.assertTrue(all(r['point']=='80.0' and r['unit']=='℃' for r in rows))
        matrix=align_points(plan_points([req('80 ℃','温度均匀度')]),rows)
        self.assertEqual(matrix[0]['certificate']['error'],'0.63 ℃')
        self.assertEqual(matrix[0]['signal'],'paired')

    def test_split_uncertainty_cell_and_pressure_unit_are_preserved(self):
        rows,_=certificate_points(extraction([['标准值（bar）','示值（bar）','示值误差（bar）','不确定度（k=2）',None],
                                              ['0.30','0.30','0.00','U =','0.10%FS']], '压力校准'))
        self.assertEqual(rows[0]['uncertainty'],'U = 0.10%FS')
        self.assertEqual(rows[0]['unit'],'bar');self.assertEqual(rows[0]['error'],'0.00 bar')

    def test_partial_parsing_keeps_unresolved_requirement_visible(self):
        rows=plan_points([req('10, 20 N\n50～500 N')])
        self.assertEqual(len(rows),3);self.assertEqual(rows[-1]['parse_state'],'unresolved')

    def test_formal_component_and_condition_are_not_discarded(self):
        rows=plan_points([{'id':'R1','parameter':'力值','points':['0','10'],'unit':'N','component':'sensor-A','mode':'压缩',
                           'conditions':{'temperature':'20 ℃'},'limit':{'upper':'1','unit':'N'}}])
        self.assertEqual(rows[0]['component'],'sensor-A');self.assertIn('压缩',rows[0]['condition'])
        self.assertIn('20 ℃',rows[0]['condition']);self.assertIn('上限：1',rows[0]['mpe'])

    def test_duplicate_semantic_columns_are_reported_not_overwritten(self):
        rows,notes=certificate_points(extraction([['校准点','正行程示值误差','反行程示值误差'],['N','%','%'],['200','0.2','0.4']]))
        self.assertEqual(rows,[]);self.assertIn('重复结果或通道列',notes[0])

    def test_single_report_result_cannot_uniquely_cover_two_plan_scopes(self):
        left=req('5 mm/min','低速区速度');right=req('5 mm/min','高速区速度');right['id']='R2'
        observations,_=certificate_points(extraction([['校准点','实测值','示值误差'],['mm/min','mm/min','%'],['5','4.99','0.2']],'速度校准'))
        rows=align_points(plan_points([left,right]),observations)
        self.assertTrue(all(r['signal']=='ambiguous' for r in rows))

    def test_nominal_standard_and_instrument_indication_are_distinct(self):
        rows,_=certificate_points(extraction([['标准值(kPa)','仪器示值(kPa)','示值误差(kPa)'],['100','99.8','-0.2']],'压力校准'))
        self.assertEqual(rows[0]['point'],'100');self.assertEqual(rows[0]['measured'],'99.8 kPa')

    def test_upper_and_lower_deviation_do_not_match_each_other(self):
        rows,_=certificate_points(extraction([['设定温度/(℃)','上偏差/(℃)'],['80','-0.4']],'温度校准'))
        matrix=align_points(plan_points([req('80 ℃','温度下偏差')]),rows)
        self.assertEqual(matrix[0]['signal'],'plan_only')


class WorkspaceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.w=Workspace(Path(self.temp.name));self.w.initialize()
        for key in ['LAB_A','ARCHIVE_A']:create_laboratory(self.w,key,key,test_only=True)

    def test_roles_are_explicit_not_guessed_from_name_and_preserve_data(self):
        self.assertEqual(len(catalog(self.w)[0]),2)
        classify_storage(self.w,'ARCHIVE_A','sample_archive','Synthetic archive fixture')
        labs,archives=catalog(self.w)
        self.assertEqual([l['id'] for l in labs],['LAB_A']);self.assertEqual(archives[0]['id'],'ARCHIVE_A')
        self.assertEqual(len(self.w.labs()),2)
        self.assertTrue((self.w.lab_store('ARCHIVE_A').directory/'lab.sqlite').is_file())
        self.assertEqual(len(console_state(self.w)['laboratories']),1)
        self.assertEqual(archive_contents(self.w,'ARCHIVE_A')['total'],0)
        with self.assertRaises(LabcalError):archive_contents(self.w,'LAB_A')
        with self.assertRaises(LabcalError):console_state(self.w,'ARCHIVE_A')

    def test_unclassified_old_space_is_not_assumed_to_be_a_laboratory(self):
        with self.w.lab_store('ARCHIVE_A').connect() as db:db.execute("DELETE FROM meta WHERE key='workspace_role'")
        labs,archives=catalog(self.w)
        self.assertEqual([l['id'] for l in labs],['LAB_A']);self.assertEqual(archives[0]['storage_role'],'unclassified')

    def test_unified_cli_rejects_regular_business_writes_to_archives(self):
        classify_storage(self.w,'ARCHIVE_A','sample_archive','Synthetic archive fixture')
        args=parser().parse_args(['--workspace',str(self.w.root),'equipment','add','--lab','ARCHIVE_A','--asset-code','TEST','--name','Synthetic'])
        with self.assertRaises(LabcalError) as error: execute(args)
        self.assertEqual(error.exception.code,'ARCHIVE_READ_ONLY')

    def test_storage_map_checks_real_database_and_selected_campaign(self):
        campaign=create_campaign(self.w,'LAB_A','Synthetic year',year=2030)
        info=storage_overview(self.w,'LAB_A',campaign['id'])
        self.assertEqual(info['integrity'],'ok');self.assertEqual(info['foreign_key_errors'],[])
        self.assertIn('/2030/',info['directory']);self.assertTrue({'资料输入','中间处理','方案输出','诊断输出'}.issubset({m['module'] for m in info['modules']}))
        with self.assertRaises(LabcalError):storage_overview(self.w,'ARCHIVE_A',campaign['id'])

    def test_archive_http_access_is_read_only_and_not_a_business_context(self):
        classify_storage(self.w,'ARCHIVE_A','migration_archive','Synthetic archive fixture')
        server=make_server(self.w,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        client=http.client.HTTPConnection('127.0.0.1',server.server_port)
        try:
            client.request('GET','/api/state');response=client.getresponse();data=json.loads(response.read())['result']
            self.assertEqual([l['id'] for l in data['laboratories']],['LAB_A'])
            client.request('GET','/api/archive?lab=ARCHIVE_A');response=client.getresponse();self.assertEqual(response.status,200);response.read()
            client.request('POST','/api/campaigns',json.dumps({'lab_id':'ARCHIVE_A','name':'must not create'}),{'X-Labcal-Token':server.token,'Content-Type':'application/json'})
            response=client.getresponse();self.assertEqual(json.loads(response.read())['error']['code'],'ARCHIVE_READ_ONLY')
            client.request('GET','/api/state?lab=ARCHIVE_A');response=client.getresponse();self.assertEqual(json.loads(response.read())['error']['code'],'LAB_NOT_FOUND')
        finally:client.close();server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
