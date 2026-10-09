// Execute the production preview and radio handlers against a small SVG DOM fixture.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const script = fs.readFileSync(require('node:path').join(__dirname, '../ui/assets/editor.js'), 'utf8');
const extract = (start, end) => script.slice(script.indexOf(start), script.indexOf(end, script.indexOf(start)));
class Node {
  constructor() { this.attrs = {}; this.dataset = {}; this.style = {}; this.children = []; this.classes=new Set(); this.classList = {contains:key=>this.classes.has(key),toggle:(key,on)=>on?this.classes.add(key):this.classes.delete(key),remove:key=>this.classes.delete(key)}; }
  setAttribute(k,v) {this.attrs[k]=String(v);}
  getAttribute(k) {return this.attrs[k];}
  removeAttribute(k) {delete this.attrs[k];}
  appendChild(n) {n.parent=this;this.children.push(n);}
  remove() {this.parent.children=this.parent.children.filter(n=>n!==this);}
  querySelector(selector) {
    if (selector.startsWith('rect')) return this.rect;
    if (selector === ':scope > [data-box-order-label]' || selector === '[data-box-order-label]') return this.label;
    if (selector === '[data-unknown-mark]') return this.children.find(n=>n.dataset.unknownMark);
    return null;
  }
}
const group = new Node();group.rect=new Node();group.label=new Node();group.dataset.boxId='1';
Object.entries({x:10,y:20,width:30,height:40}).forEach(([k,v])=>group.rect.setAttribute(k,v));
const chip=new Node();chip.dataset={assignedBoxId:'1',character:'永'};const box={bbox:[10,20,40,60],status:'intact',unknown:false,unavailable_font:false,expert_prediction:false,suspicious:false};
const context = {localBoxes:{'1':box},groupFor:()=>group,props:{value:{step:4}},
  selectedIds:new Set(['1']), annotationColor:'#22d3ee', element:{querySelector:()=>chip,querySelectorAll:selector=>selector.startsWith('.annotation-canvas')?[group]:selector==='[data-order-chip]'?[]:[chip]}, document:{createElementNS:()=>new Node()},
  activeBoxId:'1',syncExternalControls:()=>{},renderSuspiciousPreview:()=>{},assert};
vm.createContext(context);
vm.runInContext(extract('const statusColor =', 'const applyAnnotationColor =') +
  extract('const renderLocalStatus =', 'const updateCanvasLabels ='), context);
const run = code => vm.runInContext(code,context);
const radioHandlers=script.slice(script.indexOf("  const input = event.target.closest('#status-radio input');"),
  script.indexOf("\n});",script.indexOf("  const input = event.target.closest('#status-radio input');")));
run(`function radio(selector,value) { const event={target:{closest:s=>s===selector?{value}:null}}; ${radioHandlers} }`);
run("renderLocalStatus('1','intact',false)");
assert.equal(group.rect.attrs.stroke,'#22c55e');assert.equal(group.label.attrs.fill,'#ffffff');
run("radio('#unavailable-font-radio input','True')");
assert.equal(group.label.attrs.fill,'#ec4899');assert.equal(group.rect.attrs['fill-opacity'],'.20');
assert.equal(box.status,'intact');
run("radio('#expert-prediction-radio input','True')");
assert.equal(box.status,'damaged');assert.equal(group.rect.attrs.stroke,'#facc15');assert.equal(group.label.attrs.fill,'#ec4899');
run("radio('#status-radio input','intact')");
assert.equal(box.status,'intact');assert.equal(group.rect.attrs.stroke,'#facc15');
run("radio('#unavailable-font-radio input','False')");
assert.equal(group.label.attrs.fill,'#facc15');assert.equal(chip.style.color,'#facc15');
run("radio('#status-radio input','damaged'); radio('#unknown-radio input','True')");
assert.equal(box.expert_prediction,false);assert.equal(box.unavailable_font,false);
assert.equal(group.rect.attrs.stroke,'#ef4444');assert.equal(group.label.attrs.fill,'#ffffff');
assert.equal(group.querySelector('[data-unknown-mark]').textContent,'?');
assert.equal(group.querySelector('[data-unknown-mark]').attrs['font-size'],'24');
assert.equal(group.querySelector('[data-unknown-mark]').attrs['font-weight'],'700');
// Review coordinates differ from original bbox after crop/resize.
Object.entries({x:2,y:3,width:15,height:20}).forEach(([k,v])=>group.rect.setAttribute(k,v));
run("renderLocalStatus('1','damaged',true)");
assert.equal(group.querySelector('[data-unknown-mark]').attrs.x,'9.5');
assert.equal(group.querySelector('[data-unknown-mark]').attrs.y,'13');
assert.equal(group.querySelector('[data-unknown-mark]').attrs['font-size'],'12');
run("radio('#unavailable-font-radio input','True')");
assert.equal(box.unknown,false);assert.equal(group.querySelector('[data-unknown-mark]'),undefined);
run("radio('#expert-prediction-radio input','True'); radio('#expert-prediction-radio input','False')");
assert.equal(box.status,'damaged');assert.equal(box.unavailable_font,true);
group.rect.dataset.missing='1';
run("radio('#expert-prediction-radio input','True'); renderLocalStatus('1','damaged',true)");
assert.equal(box.expert_prediction,false);assert.equal(box.unavailable_font,false);assert.equal(box.unknown,false);
// Regression: hydration/selection must preserve 20% pink in both screens.
delete group.rect.dataset.missing;
for (const step of [4,7]) {
  context.props.value.step=step;
  for (const [font,expert,unknown,suspicious,missing,status,stroke,label,fill,opacity] of [
    [false,false,false,false,false,'intact','#22c55e','#ffffff','#22c55e','.04'],
    [false,false,false,false,false,'damaged','#ef4444','#ffffff','#ef4444','.04'],
    [true,false,false,false,false,'intact','#22c55e','#ec4899','#ec4899','.20'],
    [true,false,false,false,false,'damaged','#ef4444','#ec4899','#ec4899','.20'],
    [false,true,false,false,false,'intact','#facc15','#facc15','#facc15','.04'],
    [false,true,false,false,false,'damaged','#facc15','#facc15','#facc15','.04'],
    [true,true,false,false,false,'intact','#facc15','#ec4899','#ec4899','.20'],
    [false,false,true,false,false,'damaged','#ef4444','#ffffff','#ef4444','.04'],
    [true,true,false,true,false,'damaged','#facc15','#ec4899','#ec4899','.20'],
    [false,false,false,true,false,'intact','#22c55e','#ffffff','#facc15','.20'],
    [false,false,false,false,true,'intact','#22c55e','#ffffff','#e5e7eb','.30'],
    [false,false,false,true,true,'damaged','#ef4444','#ffffff','#e5e7eb','.30'],
  ]) {
    Object.assign(box,{status,unavailable_font:font,expert_prediction:expert,unknown,suspicious});
    group.classList.toggle('suspicious-region',suspicious);
    if(missing) group.rect.dataset.missing='1'; else delete group.rect.dataset.missing;
    run("renderLocalStatus('1',localBoxes['1'].status,localBoxes['1'].unknown); renderSelection(false)");
    assert.equal(group.rect.attrs.fill,fill);
    assert.equal(group.rect.attrs['fill-opacity'],opacity);
    assert.equal(group.rect.attrs.stroke,stroke);
    assert.equal(group.label.attrs.fill,label);
    assert.equal(Boolean(group.querySelector('[data-unknown-mark]')),unknown);
  }
}
delete group.rect.dataset.missing;
group.classList.toggle('suspicious-region',false);
// Reordering/label refresh must preserve special label color and text size.
context.props.value.width=100;context.props.value.height=100;
box.order=1;
vm.runInContext(extract('const updateCanvasLabels =','const hydrateLocalState ='),context);
for (const [font,expert,color] of [[true,true,'#ec4899'],[false,true,'#facc15'],[false,false,'#ffffff']]) {
  context.props.value.step=4;box.unavailable_font=font;box.expert_prediction=expert;
  run('updateCanvasLabels(); renderSelection(false)');
  assert.equal(group.label.attrs.fill,color);
  assert.equal(group.label.attrs['font-size'],'9');
}
// Physical palette changes must be scoped to the geometry editor (step 3).
assert.match(extract('const applyAnnotationColor =','const readAnnotationColor ='), /props.value.step !== 3/);
console.log('PASS: radio handlers, all status visuals after selection in steps 4/7, label refresh and cropped/scaled question mark');

// MISS lines span 60% of the box, with the same geometry policy as Python.
vm.runInContext(extract('const missMarkLines =', 'const drawPreview ='), context);
assert.deepEqual(JSON.parse(run('JSON.stringify(missMarkLines([10,20,40,60]))')),
  [[16,28,34,52],[34,28,16,52]]);
assert.deepEqual(JSON.parse(run('JSON.stringify(missMarkLines([2,3,17,23]))')),
  [[5,7,14,19],[14,7,5,19]]);
console.log('PASS: MISS mark size before and after crop/resize');

// Exercise the actual box True/False handler, including disabled MISS/excluded cases.
const suspiciousHandler = extract("  const suspicious = event.target.closest('#suspicious-radio", "  const input = event.target.closest('#status-radio input');");
context.element.querySelector = () => chip;
context.props.value.step = 4;
run(`function toggleSuspicious(checked) { const event={target:{closest:()=>({value:checked?'True':'False'})}}; ${suspiciousHandler} }`);
box.unknown=true; box.status='damaged';
run('toggleSuspicious(true)');
assert.equal(box.suspicious,true); assert.equal(box.unknown,false);
run("radio('#unknown-radio input','True')");
assert.equal(box.suspicious,false);
for (const kind of ['missing','excluded']) {
  chip.classList.toggle(kind,true);
  run('toggleSuspicious(true)');
  assert.equal(box.suspicious,false);
  chip.classList.toggle(kind,false);
}
console.log('PASS: Suspicious box True/False and Unknown/MISS/excluded rules');
// Production renderer resolves duplicate character cards through their current box.
const secondGroup = new Node(); secondGroup.rect = new Node(); secondGroup.label = new Node(); secondGroup.dataset.boxId = '2';
const secondChip = new Node(); secondChip.dataset = {assignedBoxId:'2',character:'永'};
chip.dataset.character = '永'; chip.dataset.assignedBoxId = '1';
context.localBoxes['2'] = {bbox:[50,20,80,60],status:'intact',unknown:false,unavailable_font:false,expert_prediction:false,suspicious:false};
box.unknown=false; box.suspicious=true;
context.groupFor = id => id === '1' ? group : secondGroup;
context.element.querySelectorAll = selector => selector.startsWith('.annotation-canvas') ? [group,secondGroup]
  : selector === '[data-order-chip]' ? [chip,secondChip] : [];
context.element.querySelector = selector => selector === '.status-legend' ? null
  : selector.includes('="1"') ? [chip,secondChip].find(c=>c.dataset.assignedBoxId==='1')
  : [chip,secondChip].find(c=>c.dataset.assignedBoxId==='2');
context.refreshMissingMark = (g,b,missing) => {g.rect.dataset.missing = missing ? '1' : '0';};
context.applyAnnotationColor = ()=>{};
vm.runInContext(extract('function renderSuspiciousPreview()', 'const orderRows ='),context);
run('renderSuspiciousPreview()');
assert.equal(chip.classList.contains('suspicious'),true);
chip.dataset.assignedBoxId='2'; secondChip.dataset.assignedBoxId='1';
run('renderSuspiciousPreview()');
assert.equal(chip.classList.contains('suspicious'),false);
assert.equal(secondChip.classList.contains('suspicious'),true);
assert.equal(box.suspicious,true); assert.equal(context.localBoxes['2'].suspicious,false);
secondChip.dataset.character='MISS';
run('renderSuspiciousPreview()');
assert.equal(box.suspicious,false);
assert.equal(secondChip.classList.contains('suspicious'),false);
assert.equal(group.rect.attrs['fill-opacity'],'.30');
console.log('PASS: duplicate card reassignment preserves box flag; MISS clears flag and overlay');
// The visible radio selection follows box state immediately, including True -> False.
const suspiciousInputs = ['False','True'].map(value => ({value,checked:value==='False',disabled:true,
  click() { suspiciousInputs.forEach(input => {input.checked=input===this;}); }}));
const radioContext = {root:{querySelectorAll:()=>suspiciousInputs},syncingStatusControl:false,box,chip};
vm.createContext(radioContext);
vm.runInContext(extract('const syncSuspiciousControl =', 'const syncExternalControls ='),radioContext);
chip.dataset.character='永'; chip.dataset.assignedBoxId='1'; box.suspicious=true;
vm.runInContext('syncSuspiciousControl(box,chip)',radioContext);
assert.equal(suspiciousInputs[1].checked,true);
assert.equal(suspiciousInputs[1].disabled,false);
box.suspicious=false; // Unknown=True clears the box flag in the production handler above.
vm.runInContext('syncSuspiciousControl(box,chip)',radioContext);
assert.equal(suspiciousInputs[0].checked,true);
assert.equal(suspiciousInputs[1].checked,false);
for (const kind of ['missing','excluded']) {
  chip.classList.toggle(kind,true);
  vm.runInContext('syncSuspiciousControl(box,chip)',radioContext);
  assert.equal(suspiciousInputs[0].checked,true);
  assert.equal(suspiciousInputs.every(input=>input.disabled),true);
  chip.classList.toggle(kind,false);
}
console.log('PASS: visible Suspicious True/False selection follows box state and disables MISS/excluded');
// Special assigned characters update live status and flags through the renderer.
chip.dataset.assignedBoxId='1'; secondChip.dataset.assignedBoxId='2';
chip.dataset.character='□'; secondChip.dataset.character='永';
Object.assign(box,{status:'intact',unknown:false,unavailable_font:true,expert_prediction:true,suspicious:true});
run('renderSuspiciousPreview()');
assert.equal(box.status,'damaged'); assert.equal(box.unknown,true);
assert.equal(box.unavailable_font,false); assert.equal(box.expert_prediction,false); assert.equal(box.suspicious,false);
assert.equal(group.querySelector('[data-unknown-mark]').textContent,'?');
chip.dataset.character='@';
run('renderSuspiciousPreview()');
assert.equal(box.unavailable_font,true); assert.equal(box.unknown,false);
assert.equal(group.label.attrs.fill,'#ec4899');
assert.equal(group.querySelector('[data-unknown-mark]'),undefined);
chip.dataset.character='永'; secondChip.dataset.character='□';
run('renderSuspiciousPreview()');
assert.equal(context.localBoxes['2'].unknown,true);
assert.equal(context.localBoxes['2'].status,'damaged');
// An excluded symbol has no receiving box and changes no status.
chip.dataset.character='@'; delete chip.dataset.assignedBoxId; chip.classList.toggle('excluded',true);
box.unavailable_font=false;
run('renderSuspiciousPreview()');
assert.equal(box.unavailable_font,false);
console.log('PASS: live square/@ assignment rules, reassignment and excluded symbols');
// Sorting is independent of source mismatch approval and keeps draft box orders.
const sortButton = {disabled:true,title:''};
const summaryHost = {querySelectorAll:()=>[{textContent:'2'},{textContent:'4'},{textContent:''}],querySelector:()=>null};
const sortContext = {props:{value:{step:3,contentVerified:true,characterCount:4,mismatchConfirmed:false}},
  localBoxes:{a:{order:1},b:{order:2}},selectedIds:new Set(),isDirty:false,
  mismatchConfirmationInvalidated:true,
  root:{querySelector:selector=>selector==='#validation-summary-host'?summaryHost:selector.includes('#sort-boxes')?sortButton:null},
  element:{querySelectorAll:()=>[]}};
vm.createContext(sortContext);
vm.runInContext(extract('const canEditReadingOrder =', 'const cloneBoxes ='),sortContext);
assert.equal(vm.runInContext('canEditReadingOrder()',sortContext),true);
vm.runInContext('updateValidationSummary()',sortContext);
assert.equal(sortButton.disabled,false); assert.equal(sortButton.title,'');
assert.equal(sortContext.localBoxes.a.order,1); assert.equal(sortContext.localBoxes.b.order,2);
sortContext.props.value.contentVerified=false;
assert.equal(vm.runInContext('canEditReadingOrder()',sortContext),false);
sortContext.props.value.contentVerified=true; sortContext.localBoxes={};
assert.equal(vm.runInContext('canEditReadingOrder()',sortContext),false);
console.log('PASS: sorting allows unconfirmed source mismatch and preserves draft orders');
