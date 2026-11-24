"""Synthetic review copies: preserve source bytes and distinguish candidate issues."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
try:
    import fitz
except ImportError:
    fitz=None
from labcal.adapters.pdf import annotate_pdf
from labcal.util import file_hash
from labcal.errors import LabcalError


@unittest.skipUnless(fitz,'Requires optional PDF dependency')
class PDFCalloutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.original=self.root/'synthetic.pdf'
        with fitz.open() as doc:
            p=doc.new_page();p.insert_text((50,100),'SYNTHETIC REPORT 0.24 TEST ONLY');doc.save(self.original)

    def finding(self, number=1):
        return {'id':f'TEST-{number}','extraction_id':'EXT-TEST','title':'测试候选问题','message':'合成测试数字，仅用于工程验证。',
            'basis':'TEST ONLY，没有真实验收依据。','suggestion':'查看原页核对。','certainty':'pending','severity':'high',
            'location':{'page':1,'bbox':[.2,.2,.5,.3],'kind':'visual'}}

    def test_visible_cards_preserve_original_and_pending_status(self):
        before=file_hash(self.original);output=self.root/'review.pdf'
        annotate_pdf(self.original,output,[self.finding()],style='callouts')
        self.assertEqual(file_hash(self.original),before)
        with fitz.open(output) as d:
            text=d[0].get_text()
            self.assertIn('SYNTHETIC REPORT 0.24 TEST ONLY',text)
            self.assertIn('待核实',text);self.assertIn('测试候选问题',text)
            self.assertNotIn('已确认问题',text)
            self.assertEqual(len(list(d[0].annots())),1)
            self.assertGreater(d[0].rect.width,595)

    def test_rotated_original_uses_displayed_coordinates(self):
        rotated=self.root/'rotated.pdf'
        with fitz.open(self.original) as d:d[0].set_rotation(90);d.save(rotated)
        output=self.root/'review.pdf';annotate_pdf(rotated,output,[self.finding()],style='callouts')
        with fitz.open(output) as d:
            circles=[x['rect'] for x in d[0].get_drawings() if x['fill'] and x['rect'].width<10 and x['rect'].height<10]
            # Existing annotation icons may also contain small circular shapes.
            target=fitz.Rect(842*.2,595*.2,842*.5,595*.3)
            self.assertTrue(any(r.x0>=target.x1+3.5 and abs((r.y0+r.y1)/2-595*.25)<1 for r in circles))

    def test_many_cards_get_continuation_pages_and_full_note_text(self):
        output=self.root/'review.pdf';findings=[self.finding(i) for i in range(12)]
        for f in findings:f['message']='TEST工程验证的长问题。'*40
        annotate_pdf(self.original,output,findings,style='callouts')
        with fitz.open(output) as d:
            self.assertGreater(len(d),1)
            notes=[a.info for p in d for a in p.annots() or [] if a.type[0]==fitz.PDF_ANNOT_TEXT]
            self.assertEqual(len(notes),12)
            self.assertTrue(all(findings[0]['message'] in n['content'] for n in notes))

    def test_b3_diagonal_edit_preserves_text_and_has_no_strikeout(self):
        with fitz.open(self.original) as d:
            r=d[0].search_for('0.24')[0];w,h=d[0].rect.width,d[0].rect.height
        finding=self.finding()
        finding['location']={'page':1,'bbox':[r.x0/w,r.y0/h,r.x1/w,r.y1/h],
            'kind':'text','quote':'0.24','markup':{'action':'replace','condition':'TEST ONLY verify original records'}}
        output=self.root/'b3.pdf';before=file_hash(self.original)
        result=annotate_pdf(self.original,output,[finding],style='callouts')
        self.assertEqual(result['renderer'],'b3-uncertainty-basis-4')
        self.assertEqual(file_hash(self.original),before)
        with fitz.open(output) as d:
            p=d[0];text=p.get_text()
            self.assertIn('0.24',text)
            for label in ('修改建议','核实要求','原因','待核实'):self.assertIn(label,text)
            self.assertNotIn('已确认问题',text)
            self.assertFalse(any(a.type[0]==fitz.PDF_ANNOT_STRIKE_OUT for a in p.annots()))
            lines=[a for a in p.annots() if a.type[0]==fitz.PDF_ANNOT_LINE]
            self.assertEqual(len(lines),1)
            a,b=lines[0].vertices
            self.assertLess(a[0],b[0]);self.assertGreater(a[1],b[1])
            self.assertAlmostEqual(a[0],r.x0,delta=.1);self.assertAlmostEqual(b[1],r.y0,delta=.1)
            dots=[x['rect'] for x in p.get_drawings() if x.get('fill') and 4<x['rect'].width<6 and 4<x['rect'].height<6]
            self.assertEqual(len(dots),1)
            self.assertFalse(dots[0].intersects(r+(-3.5,-3.5,3.5,3.5)))
            with fitz.open(self.original) as original:
                self.assertFalse(any(dots[0].intersects(fitz.Rect(w[:4])) for w in original[0].get_text('words')))

    def test_crowded_target_moves_dot_into_added_gutter(self):
        from labcal.adapters.pdf_callouts import _leader_origin, DOT_RADIUS
        with fitz.open(self.original) as d:
            p=d[0];target=fitz.Rect(40,80,280,105)
            x,y=_leader_origin(p,target,p.rect.width,p.rect.height,[p.rect],[],fitz)
            self.assertGreater(x-DOT_RADIUS,p.rect.width)

    def uncertainty_finding(self):
        finding = self.finding()
        finding['location']['markup'] = dict(action='clarify', topic='uncertainty', uncertainty_basis=dict(
            role='supplementary_check', application='TEST ONLY: apply stated rule to matching E and U, not to display division.',
            references=[dict(document='TEST-DOC:2030 Synthetic guideline', clause='6.2.2',
                quote='TEST ONLY accepted interval', source='TEST synthetic original and status list',
                status='valid', checked_on='2030-01-01')]))
        return finding

    def test_uncertainty_clause_and_original_quote_survive_visible_cards_and_notes(self):
        finding = self.uncertainty_finding(); before = file_hash(self.original)
        output = self.root/'clause-review.pdf'
        annotate_pdf(self.original,output,[finding],style='callouts')
        with fitz.open(output) as d:
            visible=''.join(p.get_text() for p in d)
            notes='\n'.join(a.info.get('content','') for p in d for a in p.annots() or [])
            for value in ['TEST-DOC:2030','6.2.2','TEST ONLY accepted interval','2030-01-01']:
                self.assertIn(value,visible); self.assertIn(value,notes)
            self.assertIn('补充核查',visible)
        self.assertEqual(file_hash(self.original),before)

    def test_uncertainty_missing_clause_stale_status_and_invalid_date_block_export(self):
        import copy
        from labcal.domain.report_markup import validate_markup_location
        for change in ['missing_basis','missing_clause','blank_quote','invalid_status','invalid_date']:
            f=self.uncertainty_finding(); markup=f['location']['markup']; ref=markup['uncertainty_basis']['references'][0]
            if change=='missing_basis':markup.pop('uncertainty_basis')
            if change=='missing_clause':ref.pop('clause')
            if change=='blank_quote':ref['quote']='   '
            if change=='invalid_status':ref['status']='withdrawn'
            if change=='invalid_date':ref['checked_on']='2030-02-30'
            with self.subTest(change=change), self.assertRaises(LabcalError):validate_markup_location(f['location'])

    def test_long_uncertainty_evidence_is_not_truncated(self):
        finding=self.uncertainty_finding()
        finding['location']['markup']['uncertainty_basis']['application']='TEST ONLY long application. '*200+'\nFINAL EVIDENCE END'
        output=self.root/'long-evidence.pdf';annotate_pdf(self.original,output,[finding],style='callouts')
        with fitz.open(output) as d:
            self.assertGreater(len(d),1)
            self.assertIn('FINAL EVIDENCE END',''.join(p.get_text() for p in d))

    def test_dimension_colour_is_independent_of_action_and_certainty(self):
        from labcal.domain.assessment import DIMENSIONS, rgb
        from labcal.adapters.pdf_callouts import _card_parts
        for dimension in DIMENSIONS:
            f = self.finding(); f['dimension'] = dimension
            for action in ('replace', 'flag'):
                f['location'].update(quote='TEST', markup={'action': action})
                card = _card_parts(f, 302, fitz, 650)[0]
                self.assertEqual(card['color'], rgb(dimension))
                self.assertIn(DIMENSIONS[dimension]['label'], ''.join(card['titles']))
        f['location']['markup']['dimension'] = 'not-a-layer'
        with self.assertRaises(LabcalError): annotate_pdf(self.original,self.root/'invalid.pdf',[f])

    def test_scanned_target_dot_avoids_nearby_ink(self):
        from labcal.adapters.pdf_callouts import _leader_origin, DOT_RADIUS
        source=self.root/'scan.pdf'
        with fitz.open(self.original) as d:
            r=d[0].search_for('0.24')[0];pix=d[0].get_pixmap(matrix=fitz.Matrix(2,2))
            with fitz.open() as scan:
                p=scan.new_page();p.insert_image(p.rect,stream=pix.tobytes('png'));scan.save(source)
        with fitz.open(source) as d:
            p=d[0];x,y=_leader_origin(p,r,p.rect.width,p.rect.height,[r],[],fitz)
            dot=fitz.Rect(x-DOT_RADIUS,y-DOT_RADIUS,x+DOT_RADIUS,y+DOT_RADIUS)
            self.assertFalse(dot.intersects(r))
            pixels=p.get_pixmap(clip=dot,colorspace=fitz.csGRAY)
            self.assertGreater(min(pixels.samples),190)

    def test_flag_and_insert_do_not_cross_out_reported_results(self):
        for action in ('flag','insert','clarify'):
            f=self.finding();f['location']['markup']={'action':action}
            output=self.root/(action+'.pdf');annotate_pdf(self.original,output,[f],style='callouts')
            with fitz.open(output) as d:
                self.assertFalse(any(a.type[0] in {fitz.PDF_ANNOT_LINE,fitz.PDF_ANNOT_STRIKE_OUT} for a in d[0].annots()))

    def test_invalid_edit_target_cannot_mark_a_table_or_unrelated_text(self):
        f=self.finding();f['location'].update(quote='not the original',markup={'action':'replace'},kind='document')
        with self.assertRaises(LabcalError):annotate_pdf(self.original,self.root/'document.pdf',[f],style='callouts')
        f['location']['kind']='text'
        with self.assertRaises(LabcalError):annotate_pdf(self.original,self.root/'broad.pdf',[f],style='callouts')
        with fitz.open(self.original) as d:r=d[0].search_for('0.24')[0];w,h=d[0].rect.width,d[0].rect.height
        f['location']['bbox']=[r.x0/w,r.y0/h,r.x1/w,r.y1/h]
        with self.assertRaises(LabcalError):annotate_pdf(self.original,self.root/'mismatch.pdf',[f],style='callouts')

    def test_very_long_card_is_readable_on_continuations_without_losing_reason(self):
        f=self.finding();f['message']='TEST LONG REASON '*1000+'END_OF_REASON'
        output=self.root/'long.pdf';result=annotate_pdf(self.original,output,[f],style='callouts')
        with fitz.open(output) as d:
            self.assertGreater(len(d),1)
            combined=''.join(''.join(p.get_text().split()) for p in d)
            self.assertIn('END_OF_REASON',combined)
            for p in d:
                self.assertEqual(p.rect.height,842)
                for word in p.get_text('words'):
                    self.assertGreaterEqual(word[1],0)
                    self.assertLessEqual(word[3],p.rect.height)
            self.assertTrue(all(m['source_page']==1 for m in result['page_map']))

    def test_source_widget_appearance_is_retained_in_the_copy(self):
        original=self.root/'widget.pdf'
        with fitz.open(self.original) as d:
            w=fitz.Widget();w.field_name='TEST_ONLY_STAMP';w.field_type=fitz.PDF_WIDGET_TYPE_TEXT
            w.rect=fitz.Rect(50,130,300,160);w.field_value='EXISTING WIDGET APPEARANCE';w.text_fontsize=11
            d[0].add_widget(w);d.save(original)
        output=self.root/'review.pdf';annotate_pdf(original,output,[],style='callouts')
        with fitz.open(output) as d:self.assertIn('EXISTING WIDGET APPEARANCE',d[0].get_text())

    def test_existing_output_and_unknown_style_are_rejected(self):
        output=self.root/'review.pdf';output.write_bytes(b'keep existing output')
        with self.assertRaises(LabcalError):annotate_pdf(self.original,output,[],style='callouts')
        with self.assertRaises(LabcalError):annotate_pdf(self.original,self.root/'other.pdf',[],style='unknown')
        self.assertEqual(output.read_bytes(),b'keep existing output')


if __name__=='__main__':unittest.main()
