const test=require('node:test');
const assert=require('node:assert/strict');
const {ReportScan}=require('../../src/labcal/web/scan.js');
function fixture(){
  function node(){const flags=new Set();return {hidden:true,textContent:'',attributes:{},setAttribute(k,v){this.attributes[k]=v;},classList:{toggle(k,v){v?flags.add(k):flags.delete(k);},contains(k){return flags.has(k);}}};}
  const nodes=Object.fromEntries(['line','caption','canvas','progress','status'].map(k=>[k,node()]));
  let id=0;const timers=new Map(),clock={setTimeout(fn){timers.set(++id,fn);return id;},clearTimeout(k){timers.delete(k);}};
  const scan=new ReportScan(nodes,clock);return {nodes,scan,timers};
}
test('fast diagnostics show processing, then completion while results can already be read',()=>{
  const {nodes,scan,timers}=fixture(),token=scan.start();
  assert.equal(nodes.line.hidden,false);assert.equal(nodes.canvas.attributes['aria-busy'],'true');
  scan.complete(token);
  assert.equal(nodes.line.hidden,false);assert.equal(nodes.progress.hidden,false);
  assert.match(nodes.caption.textContent,/诊断完成/);assert.equal(nodes.canvas.attributes['aria-busy'],'false');
  timers.values().next().value();assert.equal(nodes.line.hidden,true);assert.equal(nodes.progress.hidden,true);
});
test('intake polling does not hide or replace an active diagnosis effect',()=>{
  const {nodes,scan}=fixture(),token=scan.start('对照中');
  scan.intake('');assert.equal(nodes.line.hidden,false);assert.equal(nodes.caption.textContent,'对照中');
  scan.intake('另一份报告识别中');assert.equal(nodes.caption.textContent,'对照中');
  scan.complete(token);scan.intake('');assert.match(nodes.caption.textContent,/诊断完成/);
});
test('failed diagnosis clears the effect and has no completion timer',()=>{
  const {nodes,scan,timers}=fixture(),token=scan.start();scan.fail(token);
  assert.equal(nodes.line.hidden,true);assert.equal(nodes.progress.hidden,true);assert.equal(timers.size,0);
  assert.equal(nodes.canvas.attributes['aria-busy'],'false');
});
test('context change and stale completion callback cannot alter a later diagnosis',()=>{
  const {nodes,scan,timers}=fixture(),first=scan.start();scan.complete(first);
  const stale=timers.values().next().value;scan.reset();const second=scan.start('新批次诊断');
  stale();scan.complete(first);scan.fail(first);
  assert.equal(nodes.caption.textContent,'新批次诊断');assert.equal(nodes.line.hidden,false);
  scan.complete(second);assert.match(nodes.caption.textContent,/诊断完成/);
});
test('OCR processing has the same scan effect but does not claim a diagnostic task',()=>{
  const {nodes,scan}=fixture();scan.intake('正在识别第2页');
  assert.equal(nodes.line.hidden,false);assert.equal(nodes.progress.hidden,true);
  scan.intake('');assert.equal(nodes.line.hidden,true);
});
test('browser timer globals retain their required receiver during completion cleanup',()=>{
  const fs=require('node:fs'),vm=require('node:vm'),{nodes}=fixture();
  const context=vm.createContext({nodes});
  vm.runInContext('var callback; function setTimeout(fn){if(this!==globalThis)throw new TypeError("Illegal invocation");callback=fn;return 1;} function clearTimeout(){if(this!==globalThis)throw new TypeError("Illegal invocation");callback=null;}',context);
  vm.runInContext(fs.readFileSync(require.resolve('../../src/labcal/web/scan.js'),'utf8'),context);
  vm.runInContext('const effect=new ReportScan(nodes);const token=effect.start();effect.complete(token);effect.reset();',context);
  assert.equal(nodes.line.hidden,true);assert.equal(nodes.progress.hidden,true);
});
