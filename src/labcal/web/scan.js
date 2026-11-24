"use strict";
// One owner for the original-page effect: diagnosis takes priority over intake polling.
class ReportScan {
  constructor(nodes, clock={setTimeout:(fn,ms)=>setTimeout(fn,ms),clearTimeout:id=>clearTimeout(id)}) {
    this.nodes=nodes;this.clock=clock;this.owner=null;this.serial=0;this.timer=null;this.intakeLabel="";
  }
  start(label="正在对照方案与证书") {
    this.reset();const token=++this.serial;this.owner={token,phase:"running",label};this.render();return token;
  }
  complete(token) {
    if(this.owner?.token!==token)return;
    this.owner={token,phase:"complete",label:"诊断完成 · 结果已更新"};this.render();
    // Completion feedback stays briefly visible; results are already available, no artificial work delay.
    this.timer=this.clock.setTimeout(()=>{if(this.owner?.token===token){this.owner=null;this.timer=null;this.render();}},1200);
  }
  fail(token) { if(this.owner?.token===token)this.reset(); }
  reset() {
    if(this.timer!==null)this.clock.clearTimeout(this.timer);
    this.timer=null;this.owner=null;this.intakeLabel="";this.render();
  }
  intake(label) {this.intakeLabel=label||"";this.render();}
  render() {
    const {line,caption,canvas,progress,status}=this.nodes;
    const label=this.owner?.label||this.intakeLabel,complete=this.owner?.phase==="complete";
    line.hidden=caption.hidden=!label;caption.textContent=label;
    canvas.classList.toggle("scan-complete",complete);
    canvas.setAttribute("aria-busy",String(this.owner?.phase==="running"||!!this.intakeLabel));
    progress.hidden=!this.owner;progress.classList.toggle("complete",complete);
    status.textContent=this.owner?(complete?"诊断完成":"正在诊断…"):"";
  }
}
if(typeof module!=="undefined")module.exports={ReportScan};
