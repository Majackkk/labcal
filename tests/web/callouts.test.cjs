const test=require('node:test');
const assert=require('node:assert/strict');
const {CalloutLayout}=require('../../src/labcal/web/callouts.js');
test('cards never overlap, including equal anchors and more cards than page height',()=>{
 const items=Array.from({length:35},(_,id)=>({id,y:id%3*100,height:70+id%4*20}));
 const placed=CalloutLayout.place(items,500);
 assert.equal(placed.length,35);
 for(let i=1;i<placed.length;i++)assert.ok(placed[i].top>=placed[i-1].top+placed[i-1].height+10);
 assert.ok(placed[0].top>=0);assert.ok(placed.at(-1).top>500);
});
test('active notes respect extraction, page, and resolved/dismissed issues',()=>{
 const f=(id,extraction_id,page)=>({id,extraction_id,location:{page}});
 assert.deepEqual(CalloutLayout.active([f('a','one',1),f('b','one',1),f('c','two',1),f('d','one',2)],{b:{action:'dismissed'}},'one',1).map(x=>x.id),['a']);
});
test('high severity or an AI author does not turn a candidate into a confirmed error',()=>{
 const f={id:'a',certainty:'pending',severity:'high',author:'AI'};
 assert.equal(CalloutLayout.confirmed(f,{}),false);
 assert.equal(CalloutLayout.confirmed(f,{a:{action:'confirmed'}}),true);
});
