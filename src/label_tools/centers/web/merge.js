'use strict';
const MERGE = {pending: [], invalid: [], busy: false};
const MERGE_ZH = document.documentElement.lang.startsWith('zh');
function mergeText(en, zh) { return MERGE_ZH ? zh : en; }
function mergeError() {
  return mergeText('Merge points outside foreground; repair or remove groups: ', '合并点已落到前景外，请修正前景或解除这些合并组：') + MERGE.invalid.join(', ');
}
function mergeGuard() {
  if (MERGE.busy || MERGE.pending.length) {
    notice(mergeText('Confirm or cancel the pending merge first.', '请先确认或取消当前合并点。'));
    return true;
  }
  return false;
}
function mergeClick(point) {
  if (MERGE.busy) return;
  const i = MERGE.pending.findIndex(p => Math.hypot((p[0]-point.x)*S.scale, (p[1]-point.y)*S.scale) < 12);
  if (i >= 0) MERGE.pending.splice(i, 1);
  else if (MERGE.pending.length < 256) MERGE.pending.push([point.x, point.y]);
  mergePanels(); draw();
}
function mergeRemoveLast() { MERGE.pending.pop(); mergePanels(); draw(); }
function mergeCancel() { MERGE.pending = []; notice(); mergePanels(); draw(); }
function mergePanels() {
  if (!S.doc) return;
  const pending = MERGE.pending.length;
  $('merge-actions').hidden = S.mode !== 'merge' && !pending;
  $('merge-pending-count').textContent = pending;
  $('merge-apply').disabled = pending < 2 || MERGE.busy;
  $('merge-cancel').disabled = !pending || MERGE.busy;
  const groups = S.doc.merges || [];
  $('merge-count').textContent = groups.length;
  const list = $('merge-list');
  list.replaceChildren();
  for (const group of groups) {
    const row = document.createElement('div');
    row.className = 'merge-row' + (MERGE.invalid.includes(group.id) ? ' invalid' : '');
    const focus = document.createElement('button');
    focus.className = 'merge-focus';
    focus.textContent = 'M' + group.id + ' · ' + group.points.length + mergeText(' points', ' 点');
    focus.title = mergeText('Locate this merge group', '定位这组合并点');
    focus.onclick = () => {
      const [x,y] = group.points[0]; S.x=S.w/2-x*S.scale; S.y=S.h/2-y*S.scale; draw();
    };
    const remove = document.createElement('button');
    remove.className = 'icon';
    remove.title = mergeText('Remove merge group M', '解除合并组 M') + group.id;
    remove.setAttribute('aria-label', remove.title);
    const icon = document.createElement('i'); icon.dataset.lucide = 'unlink'; remove.append(icon);
    remove.onclick = () => {
      if (S.loading || mergeGuard()) return;
      history(); S.doc.merges = groups.filter(g => g.id !== group.id); changed();
    };
    row.append(focus, remove); list.append(row);
  }
  lucide.createIcons();
}
function drawMergePoints() {
  if (!S.doc) return;
  const groups = ($('show-points').checked ? S.doc.merges || [] : []).map(g => ({...g, pending:false}));
  if (MERGE.pending.length) groups.push({id:'?', points:MERGE.pending, pending:true});
  ctx.save();
  ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);
  for (const group of groups) {
    const color = MERGE.invalid.includes(group.id) ? '#ff6555' : group.pending ? '#ffdc79' : '#ffb458';
    for (let i=0; i<group.points.length; i++) {
      const [px,py] = group.points[i], x=S.x+px*S.scale, y=S.y+py*S.scale;
      if (x < -35 || y < -35 || x > S.w+35 || y > S.h+35) continue;
      ctx.strokeStyle=color; ctx.lineWidth=2;
      ctx.fillStyle='#3d2919'; ctx.beginPath(); ctx.rect(x-5,y-5,10,10);ctx.fill();ctx.stroke();
      const label='M'+group.id+'.'+(i+1);
      ctx.font='11px sans-serif';ctx.strokeStyle='#201c17';ctx.lineWidth=3;
      ctx.strokeText(label,x+10,y+16);ctx.fillStyle=color;ctx.fillText(label,x+10,y+16);
    }
  }
  ctx.restore();
}
async function commitMerge() {
  if (S.loading || MERGE.busy || MERGE.pending.length < 2) return;
  const candidate=clone(S.doc), version=S.version;
  candidate.merges=candidate.merges || [];
  const id=Math.max(0,...candidate.merges.map(g=>g.id))+1;
  candidate.merges.push({id,points:clone(MERGE.pending)});
  MERGE.busy=true;document.body.classList.add('loading');mergePanels();
  try {
    const result=await api('/api/preview',{case:current().id,target:S.target,doc:candidate});
    if (version !== S.version) throw Error(mergeText('Annotation changed; retry merge.', '标注发生变化，请重新确认合并。'));
    if ((result.invalid_merges || []).includes(id)) throw Error(mergeText('Each merge point must be inside foreground.', '每个合并点都必须位于前景实例内部。'));
    const info=(result.merge_info || []).find(g=>g.id===id);
    if (!info || info.merged_count < 1) throw Error(mergeText('Select at least two different instances.', '请至少点选两个不同的实例。'));
    history();S.doc.merges=candidate.merges;MERGE.pending=[];notice();changed();
  } catch(e) { notice(e.message || String(e)); }
  finally {MERGE.busy=false;document.body.classList.remove('loading');mergePanels();draw();}
}
document.addEventListener('DOMContentLoaded', () => {
  $('merge-apply').onclick=commitMerge;
  $('merge-cancel').onclick=mergeCancel;
  window.addEventListener('keydown', e => {
    if (['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName) || S.loading || MERGE.busy) return;
    if (e.key==='Escape' && MERGE.pending.length) { e.preventDefault();mergeCancel(); }
    if (e.key==='Enter' && MERGE.pending.length) { e.preventDefault();commitMerge(); }
  });
});
