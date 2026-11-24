const test=require('node:test');
const assert=require('node:assert/strict');
const {DiagnosisState}=require('../../src/labcal/web/diagnosis-ui.js');
const {CalloutLayout}=require('../../src/labcal/web/callouts.js');
test('a matched point is not automatically a passing result',()=>{
 assert.equal(DiagnosisState.conclusion({signal:'paired',status:'pending',decision_state:'not_started'}),'尚未开展');
 assert.equal(DiagnosisState.conclusion({signal:'paired',status:'failed',decision_state:'concluded'}),'不符合');
});
test('system prerequisites and file-level items never create error dots',()=>{
 assert.equal(DiagnosisState.isContent({code:'BASELINE_NOT_BOUND'}),false);
 assert.equal(DiagnosisState.isContent({code:'INSTITUTION_SCOPE_PENDING'}),false);
 const f=(id,category,kind)=>({id,category,extraction_id:'e',location:{page:1,kind}});
 assert.deepEqual(CalloutLayout.active([f('a','precondition','text'),f('b','content_issue','document'),f('c','content_issue','visual')],{},'e',1).map(f=>f.id),['c']);
});
