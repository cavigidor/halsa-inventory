"""M8-CP3 Excel Upload Center — the Turkish owner UI ("Dosya Yükleme"), shared by the dashboard tab and the fixed
`/yukleme` page (reachable even before any dashboard was published).

Static markup + vanilla JS only. The page embeds NO data: everything comes from the owner-only API after the session
check, and every value is inserted as text (never as HTML). The report type of each file is chosen by the owner from
a list (it is never preselected from the file name); empty dates / years are sent as unknown.
"""
from __future__ import annotations

TAB_ID = "up"
TAB_LABEL = "Dosya Yükleme"

CSS = r"""
#up .up-intro{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:6px 0 12px;font-size:13px}
#up .up-intro p{margin:4px 0}
#up .up-flow{display:flex;flex-wrap:wrap;gap:6px;list-style:none;padding:0;margin:8px 0 2px;font-size:12px}
#up .up-flow li{border:1px solid var(--line);border-radius:999px;padding:3px 10px;background:#f6f7f8}
#up .up-flow li[data-on="1"]{background:var(--ink);color:#fff;border-color:var(--ink)}
#up .up-state{border-radius:10px;padding:10px 13px;margin:8px 0 12px;font-weight:600;font-size:13px;overflow-wrap:anywhere;background:var(--card);border:1px solid var(--line)}
#up .up-state.err{background:#fdf3f3;border-color:#f3c4c4;color:#b42318}
#up .up-state.ok{background:#effaf3;border-color:#bfe3cf;color:#1a7f4b}
#up .up-banner{border-radius:10px;padding:10px 13px;margin:8px 0;font-weight:600;font-size:13px;overflow-wrap:anywhere;border:1px solid var(--line)}
#up .up-banner.err{background:#fdf3f3;border-color:#f3c4c4;color:#b42318}#up .up-banner.ok{background:#effaf3;border-color:#bfe3cf;color:#1a7f4b}
#up .up-viewer{background:var(--warn-bg);border:1px solid #f5d9a8;border-radius:10px;padding:10px 13px;font-size:13px;margin:6px 0 12px}
#up .up-viewer a{color:#1f5fbf;font-weight:600}
#up h3{font-size:14px;font-weight:800;margin:16px 0 6px}
#up .up-card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:0 0 12px;min-width:0}
#up .up-muted{font-size:12px;color:var(--muted);margin:4px 0;overflow-wrap:anywhere}
#up .up-drop{display:block;border:2px dashed #b8bfcc;border-radius:12px;padding:18px 14px;text-align:center;background:#fafbfc;cursor:pointer;font-weight:600}
#up .up-drop.over{border-color:#2563eb;background:#eef4ff}
#up .up-drop input{display:block;margin:10px auto 0;max-width:100%}
#up .up-files{list-style:none;padding:0;margin:10px 0 0}
#up .up-file{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:0 0 10px;background:var(--card);min-width:0}
#up .up-file[data-ok="0"]{border-color:#f5d9a8}
#up .up-fhead{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline;justify-content:space-between}
#up .up-fname{font-weight:700;overflow-wrap:anywhere;min-width:0}
#up .up-fmeta{font-size:12px;color:var(--muted)}
#up .up-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:8px 12px;margin:8px 0 0;align-items:end}
#up label.up-f{display:flex;flex-direction:column;gap:3px;font-size:12px;font-weight:700;color:var(--muted);min-width:0}
#up select,#up input[type=date],#up input[type=number]{font:inherit;font-size:14px;border:1px solid #b8bfcc;border-radius:8px;padding:7px 9px;background:var(--card);color:var(--ink);min-height:38px;max-width:100%;width:100%}
#up .up-hint{font-size:12px;color:var(--muted);margin:6px 0 0}
#up .up-btn{border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:13px;font-weight:600;padding:8px 12px;border-radius:8px;cursor:pointer;min-height:38px}
#up .up-btn.pri{background:var(--ink);color:#fff;border-color:var(--ink)}
#up .up-btn.go{background:#1a7f4b;color:#fff;border-color:#1a7f4b}
#up .up-btn.warn{color:#b42318}
#up .up-btn:disabled{opacity:.5;cursor:not-allowed}
#up .up-btn:focus-visible,#up .up-drop:focus-within{outline:2px solid #2563eb;outline-offset:2px}
#up .up-acts{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
#up .up-bar{height:10px;border-radius:999px;background:#eef0f2;overflow:hidden;margin:8px 0 4px}
#up .up-bar>span{display:block;height:100%;background:#2563eb;width:0;transition:width .3s}
#up .up-prog{font-size:13px;font-weight:600}
#up .up-list{list-style:none;padding:0;margin:4px 0}
#up .up-list li{padding:6px 0;border-bottom:1px solid var(--line);font-size:13px;overflow-wrap:anywhere}
#up .up-list li:last-child{border-bottom:0}
#up .up-err li{color:#b42318}
#up .up-warn li{color:#92400e}
#up .up-chip{display:inline-block;font-size:11px;font-weight:700;border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin:2px 4px 2px 0;white-space:nowrap}
#up .up-chip.ok{color:#1a7f4b;border-color:#bfe3cf}#up .up-chip.no{color:#b42318;border-color:#f3c4c4}
#up .up-chip.unk{color:#b45309;border-color:#f5d9a8}
#up .up-kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:3px 14px;font-size:13px;margin:6px 0}
#up .up-kv b{color:var(--muted);font-weight:600;margin-right:4px}
#up .up-src{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:0 0 8px;min-width:0}
#up .up-src h4{margin:0 0 4px;font-size:14px;overflow-wrap:anywhere}
#up .up-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,120px),1fr));gap:8px;margin:6px 0}
#up .up-tile{border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:var(--card)}
#up .up-tile .n{font-size:18px;font-weight:800;font-variant-numeric:tabular-nums}
#up .up-tile .l{font-size:12px;color:var(--muted)}
#up .up-sec{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) minmax(0,1fr);gap:4px 10px;font-size:13px;padding:5px 0;border-bottom:1px solid var(--line)}
#up .up-sec.head{font-weight:800;font-size:12px;color:var(--muted)}
#up .up-notice{background:#eef2ff;border:1px solid #c7d2fe;color:#3730a3;border-radius:8px;padding:8px 11px;font-weight:700;font-size:13px;margin:8px 0}
#up .up-confirm{background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:10px 12px;margin:8px 0}
#up .up-ok{color:#1a7f4b;font-weight:700}
#up [hidden]{display:none!important}
@media(max-width:600px){#up .up-acts .up-btn{flex:1 1 100%}#up .up-sec{grid-template-columns:minmax(0,1fr)}#up .up-sec.head{display:none}}
"""

BODY = """
<h2 class="sec">Dosya Yükleme</h2>
<div class="up-intro" role="note">
<p>DinamikCRM'den aldığınız Excel raporlarını buradan yükleyin. Sistem dosyaları önce kendi kurallarıyla doğrular ve bir önizleme gösterir; pano ancak siz <b>Değişiklikleri Yayınla</b> dediğinizde güncellenir. DinamikCRM'e hiçbir bağlantı yoktur ve yapay zekâ kullanılmaz.</p>
<ol class="up-flow" aria-label="Adımlar"><li data-k="select">1. Dosyaları seçin</li><li data-k="declare">2. Rapor bilgilerini girin</li><li data-k="validate">3. Doğrulayın</li><li data-k="preview">4. Önizlemeyi inceleyin</li><li data-k="publish">5. Yayınlayın</li></ol>
</div>
<div class="up-state" role="status" aria-live="polite" hidden></div>
<p class="up-viewer" hidden>Dosya yükleme ve yayınlama yalnızca yönetici girişiyle yapılabilir. Görüntüleme ve finans görüntüleme oturumları dosya yükleyemez. <a href="/giris">Yönetici girişi</a></p>
<section class="up-owner" hidden>
<div class="up-card up-published"><h3>Şu anda yayında</h3><ul class="up-list up-pub"></ul></div>
<div class="up-card up-select">
<h3>1. Dosyaları seçin</h3>
<label class="up-drop">Excel dosyalarını buraya bırakın veya seçin (.xlsx, birden fazla seçilebilir)<input type="file" class="up-input" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" multiple></label>
<p class="up-muted up-limits"></p>
<ul class="up-files"></ul>
<p class="up-hint">Her dosya için rapor türünü siz seçersiniz; dosya adına bakılmaz. Tarih veya yılı bilmiyorsanız boş bırakın: “bilinmiyor” olarak kaydedilir ve ilgili analizler doğrulanmış sayılmaz.</p>
<div class="up-acts"><button type="button" class="up-btn pri up-validate" disabled>Doğrula ve önizle</button><button type="button" class="up-btn warn up-discard" hidden>Bu yüklemeden vazgeç</button></div>
</div>
<div class="up-card up-job" hidden aria-live="polite"><p class="up-prog"></p><div class="up-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"><span></span></div><p class="up-muted up-elapsed"></p></div>
<div class="up-card up-preview" hidden></div>
<div class="up-card up-history"><h3>Son yüklemeler</h3><ul class="up-list up-hist"></ul></div>
</section>
"""

JS = r"""
(function(){
'use strict';
const root=document.getElementById('up'); if(!root) return;
const tabBtn=document.querySelector('.tab[data-t="up"]'), standalone=document.body.dataset.uploadPage==='1';
const POLL_MS=window.__UP_POLL_MS||1500;
const MSG={file:'Dosya Yükleme için siteyi tarayıcıda sunucu adresinden açın.',offline:'Sunucuya ulaşılamadı. İnternet bağlantınızı kontrol edip tekrar deneyin.',
  fail:'Dosya Yükleme yüklenemedi',auth:'Oturum sona erdi; yeniden yönetici girişi yapın.',noSlot:'Her dosya için rapor türünü seçin.',
  twice:'Aynı rapor türü için iki dosya seçildi; her rapor türü için bir dosya yükleyin.',notXlsx:'Yalnızca .xlsx dosyaları yüklenebilir: ',
  tooBig:'Dosya çok büyük: ',uploading:'Yükleniyor',uploaded:'Yüklendi',validating:'Doğrulama başladı; bu sayfayı açık tutabilir veya daha sonra dönebilirsiniz.',
  published:'Yayınlandı. Pano güncellendi; yeni verileri görmek için sayfayı yenileyin.',unchanged:'Değişiklik yoktu: yüklenen dosyalar ve beyanlar yayındakiyle aynı. Hiçbir şey yeniden yayınlanmadı.',
  kept:'Önceki veriler değişmeden kullanılmaya devam ediyor.',confirm:'Bu işlem panoyu yüklediğiniz raporlarla güncelleyecek. Doğrulanmamış analizler yine gizli kalır. Yayınlansın mı?',
  none:'Henüz yüklenmiş bir dosya yok.',dup:'Bu dosya bu yüklemede zaten var.'};
const st={started:false,seq:0,data:null,batch:null,rows:[],poll:null,busy:false,confirm:false};
const $=s=>root.querySelector(s);
function el(t,c,x){const e=document.createElement(t); if(c) e.className=c; if(x!=null) e.textContent=String(x); return e;}
function show(n,on){if(n) n.hidden=!on;}
function state(t,k){const n=$('.up-state'); n.textContent=t||''; n.className='up-state'+(k?' '+k:''); show(n,!!t);}
function num(v){return (v==null||!Number.isFinite(Number(v)))?'—':Number(v).toLocaleString('tr-TR');}
function mb(b){return b<1048576?Math.max(1,Math.round(b/1024)).toLocaleString('tr-TR')+' KB':(b/1048576).toLocaleString('tr-TR',{maximumFractionDigits:1})+' MB';}
const TMPL={approved_template:'onaylı DinamikCRM şablonu',single_identity_header:'tek ürün kodu başlığı',identity_header_missing:'ürün kodu başlığı yok',identity_header_ambiguous:'ürün kodu başlığı belirsiz (onaylı şablon değil)'};
const AY=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
function day(iso){const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso||'')); return m?(+m[3])+' '+AY[+m[2]-1]+' '+m[1]:'—';}
function when(iso){if(!iso) return '—'; const d=new Date(iso); if(isNaN(d)) return day(iso); return d.getDate()+' '+AY[d.getMonth()]+' '+d.getFullYear()+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
function flow(k){root.querySelectorAll('.up-flow li').forEach(li=>{li.dataset.on=li.dataset.k===k?'1':'0';});}
async function api(path,opt){
  let r; try{r=await fetch(path,opt||{headers:{'Accept':'application/json'}});}catch(e){const x=new Error('offline'); x.offline=true; throw x;}
  let b=null; try{b=await r.json();}catch(e){b=null;}
  if(!r.ok){const x=new Error('HTTP '+r.status); x.status=r.status; x.body=b; x.code=b&&b.detail&&b.detail.error_code; throw x;} return b;}
function post(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json','X-StockAgent':'1'},body:JSON.stringify(body||{})});}
function errText(e){if(e.offline) return MSG.offline; if(e.status===401) return MSG.auth;
  const d=e.body&&e.body.detail; const m=d&&(d.message||(Array.isArray(d)?'Girilen bilgiler geçersiz.':null)); return m||(MSG.fail+(e.status?' (HTTP '+e.status+')':'')+'.');}
async function session(force){try{const a=window.StockAgentAccess; return a?await a.session(force):null;}catch(e){return null;}}

async function reveal(){const s=await session(); if(tabBtn) tabBtn.hidden=!(s&&s.role==='owner');}
function start(){if(st.started) return; st.started=true; if(location.protocol==='file:'){state(MSG.file,'err'); return;} load();}
async function load(){
  const my=++st.seq; const s=await session(true); const role=s&&s.role;
  if(role!=='owner'){root.dataset.access=role||'viewer'; show($('.up-owner'),false); show($('.up-viewer'),true); return;}
  root.dataset.access='owner'; show($('.up-viewer'),false);
  try{const d=await api('/api/uploads'); if(my!==st.seq) return; st.data=d; render(d);}
  catch(e){if(my===st.seq){if(e.status===401||e.status===403){show($('.up-owner'),false); show($('.up-viewer'),true);} else state(errText(e),'err');}}
}
function render(d){
  show($('.up-owner'),true); state('');
  $('.up-limits').textContent='En fazla '+d.limits.max_files+' dosya · dosya başına en fazla '+mb(d.limits.max_bytes)+' · yalnızca .xlsx';
  const pub=$('.up-pub'); pub.replaceChildren();
  if(!(d.published||[]).length) pub.append(el('li','up-muted','Yayında bir rapor yok.'));
  (d.published||[]).forEach(p=>{const li=el('li'); li.dataset.slot=p.slot; li.append(el('b',null,p.slot_label+': '),document.createTextNode(p.label||'—'));
    const dec=Object.entries(p.declared||{}).filter(([k,v])=>v!=null).map(([k,v])=>/^\d{4}-\d{2}-\d{2}$/.test(String(v))?day(v):String(v));
    li.append(el('span','up-fmeta',' · '+(dec.length?'beyan: '+dec.join(', '):'tarih beyan edilmedi')+(p.origin==='upload'||p.origin==='explicit'?'':' · ayar/klasör seçimi')));
    pub.append(li);});
  st.batch=d.batch; st.rows=rowsFrom(d.batch);
  drawFiles(); drawHistory(d.history||[]);
  const b=d.batch;
  const live=d.job&&(d.job.state==='queued'||d.job.state==='running');   // a just-finished job is not resumed
  if(live){ if(!st.poll) poll(d.job.job_id); }
  else if(!st.poll) drawJob(null);
  drawPreview(b);
  flow(!b||!b.files.length?'select':(b.status==='ready'||b.status==='invalid'?'preview':(b.status==='publishing'?'publish':'declare')));
  if(b&&b.message&&!d.job) state(b.message,(b.status==='published'||b.status==='unchanged')?'ok':(b.status==='ready'?'':'err'));
}
function rowsFrom(b){if(!b) return []; const a=b.assignments||{};
  return b.files.map(f=>{const x=a[f.file_id]||{}; return {file_id:f.file_id,label:f.label,size:f.size,sha:f.sha256,slot:x.slot||'',decl:Object.assign({},x.declarations||{})};});}
function editable(){const b=st.batch; return !b||['draft','ready','invalid'].includes(b.status);}
function slotDef(k){return (st.data.slots||[]).find(s=>s.key===k);}
function drawFiles(){
  const ul=$('.up-files'); ul.replaceChildren(); const ed=editable()&&!st.busy;
  st.rows.forEach(r=>{
    const li=el('li','up-file'); li.dataset.id=r.file_id; li.dataset.ok=r.slot?'1':'0';
    const h=el('div','up-fhead'); h.append(el('span','up-fname',r.label),el('span','up-fmeta',mb(r.size)+' · parmak izi '+r.sha));
    li.append(h);
    const g=el('div','up-grid');
    const lab=el('label','up-f','Rapor türü'); const sel=el('select','up-slot'); sel.disabled=!ed;
    const o0=el('option',null,'— Rapor türünü seçin —'); o0.value=''; sel.append(o0);
    (st.data.slots||[]).forEach(s=>{const o=el('option',null,s.label); o.value=s.key; sel.append(o);});
    sel.value=r.slot||''; sel.addEventListener('change',()=>{r.slot=sel.value; r.decl={}; invalidate(); drawFiles();});
    lab.append(sel); g.append(lab);
    const sd=r.slot&&slotDef(r.slot);
    if(sd){(sd.fields||[]).forEach(f=>{
      const l=el('label','up-f',f.label); let inp;
      if(f.type==='date'){inp=el('input'); inp.type='date'; inp.value=r.decl[f.key]||'';}
      else if(f.type==='year'){inp=el('input'); inp.type='number'; inp.min='2000'; inp.max='2099'; inp.step='1'; inp.placeholder='YYYY'; inp.value=r.decl[f.key]||'';}
      else{inp=el('select'); [['','Bilinmiyor'],['true','Evet'],['false','Hayır']].forEach(([v,t])=>{const o=el('option',null,t); o.value=v; inp.append(o);});
        inp.value=r.decl[f.key]===true?'true':(r.decl[f.key]===false?'false':'');}
      inp.className='up-decl'; inp.dataset.f=f.key; inp.disabled=!ed;
      const upd=()=>{const v=inp.value; r.decl[f.key]=f.type==='bool'?(v===''?null:v==='true'):(v===''?null:v); invalidate();};
      inp.addEventListener('input',upd); inp.addEventListener('change',upd);
      l.append(inp); g.append(l);});}
    li.append(g);
    if(sd) li.append(el('p','up-hint',sd.hint+' Boş bırakılan alanlar “bilinmiyor” olarak kaydedilir.'));
    if(ed){const rm=el('button','up-btn up-remove','Kaldır'); rm.type='button'; rm.addEventListener('click',()=>removeFile(r)); li.append(el('div','up-acts')); li.lastChild.append(rm);}
    ul.append(li);});
  if(!st.rows.length) ul.append(el('li','up-muted',MSG.none));
  $('.up-validate').disabled=!ed||!st.rows.length||st.rows.some(r=>!r.slot);
  show($('.up-discard'),!!st.batch&&ed&&!!st.rows.length);
  $('.up-input').disabled=!ed;
}
function invalidate(){const p=$('.up-preview'); if(p.dataset.batch){p.dataset.stale='1'; const n=p.querySelector('.up-pubbox'); if(n) n.hidden=true;}
  $('.up-validate').disabled=!st.rows.length||st.rows.some(r=>!r.slot);}
async function ensureBatch(){
  if(st.batch&&editable()) return st.batch;
  const b=await post('/api/uploads',{}); st.batch=b; return b;}
function uploadOne(batch,file,row){return new Promise(async(resolve,reject)=>{
  const s=await session(); const x=new XMLHttpRequest();
  x.open('POST','/api/uploads/'+encodeURIComponent(batch.batch_id)+'/files');
  x.setRequestHeader('X-StockAgent','1'); x.setRequestHeader('Content-Type','application/octet-stream');
  x.setRequestHeader('X-Upload-Name',encodeURIComponent(file.name)); if(s&&s.csrf_token) x.setRequestHeader('X-CSRF-Token',s.csrf_token);
  x.upload.onprogress=e=>{if(e.lengthComputable){row.pct=Math.round(e.loaded*100/e.total); row.node.textContent=file.name+' — '+MSG.uploading+' %'+row.pct;}};
  x.onload=()=>{let b=null; try{b=JSON.parse(x.responseText);}catch(e){} if(x.status>=200&&x.status<300) resolve(b); else {const er=new Error('HTTP '+x.status); er.status=x.status; er.body=b; reject(er);}};
  x.onerror=()=>{const er=new Error('offline'); er.offline=true; reject(er);}; x.send(file);});}
async function addFiles(list){
  if(!editable()||st.busy) return; const files=[...list]; if(!files.length) return;
  const max=st.data.limits.max_bytes, msgs=[];
  const ok=files.filter(f=>{if(!/\.xlsx$/i.test(f.name)){msgs.push(MSG.notXlsx+f.name); return false;} if(f.size>max){msgs.push(MSG.tooBig+f.name+' ('+mb(f.size)+')'); return false;} return true;});
  if(!ok.length){state(msgs.join(' · '),'err'); return;}
  st.busy=true; drawFiles(); const box=$('.up-files'); const prog=[];
  ok.forEach(f=>{const li=el('li','up-file up-progress',f.name+' — '+MSG.uploading); box.append(li); prog.push({node:li});});
  try{const b=await ensureBatch(); let i=0;
    for(const f of ok){const p=prog[i++]; try{const r=await uploadOne(b,f,p); p.node.textContent=f.name+' — '+(r&&r.duplicate_in_batch?MSG.dup:MSG.uploaded);}
      catch(e){msgs.push(f.name+': '+errText(e)); p.node.textContent=f.name+' — '+errText(e);}}
  }catch(e){msgs.push(errText(e));}
  st.busy=false; const keep=st.rows.map(r=>({id:r.file_id,slot:r.slot,decl:r.decl}));
  await load(); keep.forEach(k=>{const r=st.rows.find(x=>x.file_id===k.id); if(r&&!r.slot){r.slot=k.slot; r.decl=k.decl;}}); drawFiles();
  if(msgs.length) state(msgs.join(' · '),'err');
}
async function removeFile(r){try{const b=await post('/api/uploads/'+st.batch.batch_id+'/files/'+r.file_id+'/remove',{}); st.rows=st.rows.filter(x=>x.file_id!==r.file_id); st.batch=b; $('.up-preview').hidden=true; drawFiles();}catch(e){state(errText(e),'err');}}
async function validate(){
  const seen={}; for(const r of st.rows){if(!r.slot){state(MSG.noSlot,'err'); return;} if(seen[r.slot]){state(MSG.twice,'err'); return;} seen[r.slot]=1;}
  const asg={}; st.rows.forEach(r=>{const sd=slotDef(r.slot); const d={}; (sd.fields||[]).forEach(f=>{const v=r.decl[f.key]; d[f.key]=(v===''||v===undefined)?null:v;}); asg[r.file_id]={slot:r.slot,declarations:d};});
  try{$('.up-validate').disabled=true; const j=await post('/api/uploads/'+st.batch.batch_id+'/validate',{assignments:asg}); state(MSG.validating); flow('validate'); poll(j.job_id);}
  catch(e){state(errText(e),'err'); drawFiles();}
}
function drawJob(j){const box=$('.up-job'); if(!j){show(box,false); return;} show(box,true);
  const pct=j.steps?Math.round(Math.min(j.step,j.steps)*100/j.steps):0;
  box.dataset.state=j.state; box.dataset.kind=j.kind;
  $('.up-prog').textContent=(j.kind==='publish'?'Yayın: ':'Doğrulama: ')+j.stage_label+(j.state==='running'||j.state==='queued'?' ('+j.step+'/'+j.steps+')':'');
  const bar=box.querySelector('.up-bar>span'); bar.style.width=(j.state==='done'?100:pct)+'%'; box.querySelector('.up-bar').setAttribute('aria-valuenow',String(j.state==='done'?100:pct));
  $('.up-elapsed').textContent=j.elapsed_seconds!=null?('Geçen süre: '+j.elapsed_seconds+' sn'+(j.state==='running'?' · büyük satış dosyasında birkaç dakika sürebilir':'')):'';}
function poll(id){clearTimeout(st.poll);
  const tick=async()=>{let j; try{j=await api('/api/uploads/jobs/'+encodeURIComponent(id));}catch(e){state(errText(e),'err'); st.poll=setTimeout(tick,POLL_MS*2); return;}
    drawJob(j); root.dataset.job=j.state;
    if(j.state==='queued'||j.state==='running'){st.poll=setTimeout(tick,POLL_MS); return;}
    st.poll=null; await load(); drawJob(j);
    if(j.kind==='publish'){if(j.state==='done'){const b=j.batch; state(b.status==='unchanged'?MSG.unchanged:MSG.published,'ok'); flow('publish'); showReload();}
      else state((j.message||'Yayın başarısız.')+' '+MSG.kept,'err');}
    else if(j.state!=='done') state(j.message||'Doğrulama başarısız.','err');
  }; tick();}
function showReload(){const p=$('.up-preview'); let b=p.querySelector('.up-reload'); if(!b){b=el('button','up-btn pri up-reload','Panoyu yenile'); b.type='button'; b.addEventListener('click',()=>location.reload()); p.append(b);} show(p,true);}
function kv(pairs){const g=el('div','up-kv'); pairs.forEach(([k,v])=>{const d=el('div'); d.append(el('b',null,k+':'),document.createTextNode(v==null||v===''?'—':String(v))); g.append(d);}); return g;}
function list(cls,items,txt){const u=el('ul','up-list '+cls); items.forEach(i=>u.append(el('li',null,txt(i)))); return u;}
function drawPreview(b){
  const box=$('.up-preview'); box.replaceChildren(); delete box.dataset.stale;
  const p=b&&b.preview; if(!p){show(box,false); delete box.dataset.batch; return;}
  box.dataset.batch=b.batch_id; box.dataset.status=b.status; box.dataset.noop=p.noop?'1':'0'; show(box,true);
  box.append(el('h3',null,'4. Önizleme — yayından önce kontrol edin'));
  box.append(el('p','up-notice',p.notice));
  const t=el('div','up-tiles'); const tile=(k,n,l)=>{const d=el('div','up-tile'); d.dataset.k=k; d.append(el('div','n',num(n)),el('div','l',l)); t.append(d);};
  tile('errors',p.errors.length,'Hata'); tile('warnings',p.warnings.length,'Uyarı'); tile('missing',p.missing.length,'Eksik bilgi');
  tile('vkm_new',p.vkm.counts.new+p.vkm.counts.returns,'Yeni / geri gelen VK sorunu'); tile('vkm_resolved',p.vkm.counts.resolved,'Çözülecek VK sorunu');
  tile('withheld',p.sections.metrics_withheld,'Gizli kalan analiz ('+p.sections.metrics_total+' içinden)'); box.append(t);
  if(p.noop) box.append(el('p','up-banner ok up-noop','Değişiklik yok: yüklenen dosyalar ve beyanlar şu anda yayındakiyle aynı. Yayınlamak hiçbir şeyi değiştirmez.'));
  if(p.errors.length){box.append(el('h3',null,'Hatalar (yayını engeller)')); box.append(list('up-err',p.errors,e=>e.message));}
  if(p.warnings.length){box.append(el('h3',null,'Uyarılar')); box.append(list('up-warn',p.warnings,e=>e.message));}
  if(p.missing.length){box.append(el('h3',null,'Eksik kaynak bilgileri')); box.append(list('up-warn up-missing',p.missing,e=>e.message));}
  box.append(el('h3',null,'Kaynaklar, beyanlar, sayfalar ve sütunlar'));
  (p.sources||[]).forEach(s=>{const c=el('div','up-src'); c.dataset.slot=s.slot;
    c.append(el('h4',null,s.slot_label+' — '+s.label+(s.carried?' (yayındaki rapor, değişmeden devralınır)':'')));
    c.append(kv((s.declarations||[]).map(d=>[d.label,d.value==null?'bilinmiyor (beyan edilmedi)':(/^\d{4}-\d{2}-\d{2}$/.test(String(d.value))?day(d.value):(d.value===true?'Evet':(d.value===false?'Hayır':d.value)))])));
    c.append(kv([['Sayfalar',(s.sheets||[]).join(', ')||'—'],['Veri satırı',s.data_rows!=null?num(s.data_rows):(s.counts&&s.counts.installments!=null?num(s.counts.installments)+' taksit':'—')],
      ['Düzen',s.layout_supported===true?'Destekleniyor':(s.layout_supported===false?'DESTEKLENMİYOR':(s.state==='ready'?'Okundu':(s.state==='failed'?'Okunamadı':'—')))],['Parmak izi',s.sha256]]));
    if((s.columns||[]).length){const w=el('p','up-cols'); w.append(el('b',null,'Zorunlu sütunlar: '));
      s.columns.forEach(col=>{const ch=el('span','up-chip '+(col.found===true?'ok':(col.found===false?'no':'unk')),(col.found===false?'✗ ':'✓ ')+col.name); ch.dataset.found=String(col.found); w.append(ch);}); c.append(w);}
    if(s.template) c.append(el('p','up-muted','Stok şablonu: '+(TMPL[s.template.status]||s.template.status||'—')+(s.template.status==='approved_template'?' (ürün kodu sütunu '+(s.template.identity_column||'A')+')':'')));
    if(s.master_sheet===false) c.append(el('p','up-muted','“liste” (müşteri ana listesi) sayfası bulunamadı.'));
    if((s.issue_codes||[]).length) c.append(el('p','up-muted','İçe aktarma kodları: '+s.issue_codes.join(', ')));
    box.append(c);});
  if((p.reconciliation||[]).length){box.append(el('h3',null,'Mutabakat durumu'));
    box.append(list('up-recon',p.reconciliation,r=>r.label+': toplamlar '+r.totals+(r.footer?' · rapor toplam satırları: '+r.footer:'')+(r.joins?' · '+r.joins:'')+(r.join_unmatched!=null?' · eşleşmeyen: '+num(r.join_unmatched):'')+(r.duplicates!=null?' · yinelenen: '+num(r.duplicates):'')+' · eşleme: '+r.mapping));}
  box.append(el('h3',null,'Veri Kontrol üzerindeki etkisi'));
  const vc=p.vkm.counts; box.append(el('p','up-muted','Yeni: '+num(vc.new)+' · Geri gelen: '+num(vc.returns)+' · Devam eden: '+num(vc.still_present)+' · Çözülecek: '+num(vc.resolved)+' · Bilinmiyor (denetlenemedi / dosya yok): '+num(vc.not_checked+vc.source_missing)));
  (p.vkm.sources||[]).forEach(s=>{if(s.unchanged) return; const items=s.issues.filter(i=>i.outcome!=='still_present'); if(!items.length) return;
    const d=el('div','up-src up-vksrc'); d.dataset.key=s.key; d.append(el('h4',null,s.label)); d.append(list('up-vk',items,i=>i.outcome_label+' — '+i.title+(i.counts&&i.counts.length?' ('+i.counts.map(c=>c.label+': '+num(c.value)).join(', ')+')':''))); box.append(d);});
  box.append(el('h3',null,'Pano bölümlerine etkisi'));
  const hd=el('div','up-sec head'); hd.append(el('span',null,'Bölüm'),el('span',null,'Şu an'),el('span',null,'Yayından sonra')); box.append(hd);
  (p.sections.tabs||[]).forEach(r=>{const row=el('div','up-sec'); row.dataset.metric=r.metric; row.dataset.after=r.candidate; row.append(el('span',null,r.title),el('span',null,r.current_label),el('span',null,r.candidate_label)); box.append(row);});
  box.append(el('p','up-muted',p.sections.note));
  if(p.as_of&&(p.as_of.current||p.as_of.candidate)) box.append(kv([['Şu anki veri tarihi',day(p.as_of.current)],['Yayından sonra',p.as_of.candidate?day(p.as_of.candidate):'değişmez'],['Not',p.as_of.note]]));
  if((p.sections.withheld_titles||[]).length){const det=el('details'); det.append(el('summary',null,'Gizli kalan analizler ('+p.sections.metrics_withheld+')')); det.append(list('up-withheld',p.sections.withheld_titles,m=>m.title)); box.append(det);}
  const pb=el('div','up-pubbox'); pb.hidden=!b.publishable; box.append(pb);
  if(b.publishable){pb.append(el('h3',null,'5. Yayınlama'));
    const go=el('button','up-btn go up-publish','Değişiklikleri Yayınla'); go.type='button'; const acts=el('div','up-acts'); acts.append(go); pb.append(acts);
    const cf=el('div','up-confirm'); cf.hidden=true; cf.append(el('p',null,MSG.confirm)); const yes=el('button','up-btn go up-yes','Evet, yayınla'), no=el('button','up-btn up-no','İptal'); yes.type=no.type='button';
    const ca=el('div','up-acts'); ca.append(yes,no); cf.append(ca); pb.append(cf);
    go.addEventListener('click',()=>{cf.hidden=false; go.disabled=true; yes.focus();}); no.addEventListener('click',()=>{cf.hidden=true; go.disabled=false;});
    yes.addEventListener('click',async()=>{yes.disabled=true; no.disabled=true; try{const j=await post('/api/uploads/'+b.batch_id+'/publish',{preview_token:b.preview_token}); flow('publish'); state('Yayın başladı…'); poll(j.job_id);}catch(e){state(errText(e)+(e.code==='stale'?'':' '+MSG.kept),'err'); await load();}});}
  else if(b.status==='invalid') box.append(el('p','up-banner err','Hatalar düzeltilmeden yayın yapılamaz. Dosyayı veya beyanı düzeltip yeniden doğrulayın.'));
}
function drawHistory(h){const ul=$('.up-hist'); ul.replaceChildren(); if(!h.length){ul.append(el('li','up-muted','Henüz yükleme yapılmadı.')); return;}
  h.forEach(x=>{const li=el('li'); li.dataset.status=x.status; li.append(el('b',null,when(x.created_at)+' — '+x.status_label));
    if((x.reports||[]).length) li.append(el('span','up-fmeta',' · '+x.reports.map(r=>r.slot_label+': '+(r.label||'—')).join(', ')));
    ul.append(li);});}
async function discard(){if(!st.batch) return; try{await post('/api/uploads/'+st.batch.batch_id+'/discard',{}); state('Yükleme iptal edildi; yayındaki veriler değişmedi.'); await load();}catch(e){state(errText(e),'err');}}
const input=$('.up-input'); input.addEventListener('change',()=>{addFiles(input.files); input.value='';});
const drop=$('.up-drop'); ['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault(); drop.classList.add('over');}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault(); drop.classList.remove('over');}));
drop.addEventListener('drop',e=>{if(e.dataTransfer&&e.dataTransfer.files) addFiles(e.dataTransfer.files);});
$('.up-validate').addEventListener('click',validate); $('.up-discard').addEventListener('click',discard);
if(tabBtn){tabBtn.addEventListener('click',start); reveal();}
if(standalone) start();
})();
"""


def panel() -> tuple[str, str]:
    """(tab button, panel) for the dashboard. The tab button is hidden until the page's session check confirms the
    owner; every API call is owner-only on the server regardless."""
    btn = f'<button class="tab" data-t="{TAB_ID}" hidden>{TAB_LABEL}</button>'
    block = (f'\n<!--UP-TAB-START--><div id="{TAB_ID}" class="panel"><style>{CSS}</style>{BODY}<script>{JS}</script>'
             '</div><!--UP-TAB-END-->')
    return btn, block


def page() -> str:
    """The fixed `/yukleme` page: the same panel, the shared access bar and login link. No data inside."""
    import dashboard_builder as db
    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Dosya Yükleme — Şirket Panosu</title>
<style>
:root{{--ink:#16191d;--muted:#6b7480;--line:#e6e8ec;--paper:#fbfbf9;--card:#fff;--warn:#b45309;--warn-bg:#fef6e7;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);
font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.45}}
.wrap{{max-width:1080px;margin:0 auto;padding:22px 16px 60px}}
header{{border-bottom:2px solid var(--ink);padding-bottom:12px;margin-bottom:16px}}
h1{{font-size:24px;font-weight:800;margin:4px 0 2px}}.sub a{{color:#1f5fbf;font-weight:600}}
.sec{{font-size:13px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;margin:0 0 6px;padding-bottom:8px;border-bottom:1px solid var(--line)}}
{db._ACCESS_CSS}{CSS}</style></head><body data-upload-page="1"><div class="wrap">
<header><h1>Dosya Yükleme</h1><div class="sub"><a href="/">← Panoya dön</a></div>
<div class="acc-bar" id="halsa-access" hidden></div></header>
<div class="acc-msg" id="halsa-access-msg" role="alert" hidden></div>
<script>{db._ACCESS_JS}</script>
<div id="{TAB_ID}" class="panel on">{BODY}</div>
<script>{JS}</script></div></body></html>"""


__all__ = ["panel", "page", "CSS", "BODY", "JS", "TAB_ID", "TAB_LABEL"]
