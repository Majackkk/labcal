const test=require('node:test');const assert=require('node:assert/strict');
const {ColumnSizing}=require('../../src/labcal/web/columns.js');
test('popup column preferences cannot overwrite desk column preferences',()=>{
 assert.notEqual(ColumnSizing.scopeKey('diagnosis',true),ColumnSizing.scopeKey('diagnosis',false));
 assert.equal(ColumnSizing.scopeKey('diagnosis',false),'diagnosis:desk');
});
test('resizing changes the selected column and keeps hidden columns and neighbours',()=>{
 assert.deepEqual(ColumnSizing.resize([130,null,90,160],2,27),[130,null,117,160]);
});
test('drag limits prevent unusable widths',()=>{
 assert.deepEqual(ColumnSizing.resize([100,120],0,-500),[56,120]);
 assert.deepEqual(ColumnSizing.resize([100,120],1,5000),[100,1000]);
});
test('restored preferences reject corrupt, incompatible and nonnumeric widths',()=>{
 assert.equal(ColumnSizing.normalize([100,'200'],2),null);
 assert.equal(ColumnSizing.normalize([100,200],3),null);
 assert.equal(ColumnSizing.normalize([0,200],2),null);
 assert.deepEqual(ColumnSizing.normalize([100,null,200],3),[100,null,200]);
});
