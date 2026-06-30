"""In-browser bbox 標註編輯器(零依賴原生 Canvas)+ Streamlit 整合。

為什麼有這個檔
--------------
工作台目前只能「看」框、要編輯就「啟動桌面工具」(X-AnyLabeling/LabelMe/ISAT)。
這支讓使用者**直接在瀏覽器裡畫/改/刪框**,存回影像旁的 ``<image>.json`` sidecar
(與 AI 預標 _run_ai_items / seeder 同一份契約)。純原生 Canvas,不引入任何前端套件。

互動(以「AI 先出框 → 人再微調」為核心)
* 8 把手縮放(4 角 + 4 邊),拖任一把手只動該邊/角、對邊釘住;Alt 拖把手=以中心對稱縮放。
* 免先選取即可抓把手;框內點擊選「最小面積」框、同點重複點/Alt+點可循環選重疊框;空白拖曳=畫新框。
* 方向鍵微調位置(Shift ×10)、Alt+方向鍵微調大小;Del 刪、Esc 取消。
* 復原/重做:Ctrl+Z / Ctrl+Y(或 Ctrl+Shift+Z)。存檔:💾 / Ctrl+S(有未存提示與離開警告)。
* 縮放:滾輪 / +、- / f、0 全圖 / z 對選取框;高倍清晰像素 + 上下限。
* 類別:1-9 快速鍵 + 搜尋框(10~80 類);類別圖例可顯示/隱藏/單看某類、標籤開關、十字輔助線、層級前後。

兩塊:
* :func:`editor_html` — 自含編輯器 HTML(影像 + 既有框 + 類別清單注入 + 唯讀測試探針)。
* :func:`render_canvas_editor` — Streamlit 整合;隱藏 text_input(nonce)接存檔。
  **存檔採合併**:非矩形形狀(polygon/mask)與每框 score 原樣保留,只覆寫矩形。

⚠️ 瀏覽器端 JS 互動以 headless 瀏覽器(Playwright)驅動 ``window.__m012canvas`` 探針/api 驗證;
Python 端 :func:`canvas_boxes_to_sidecar` / :func:`_img_data_url` 有單元測試。
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

# 隱藏控制項的哨兵字串(JS 在 parent document 用它找對應 widget)。
# ⚠️ 不可含 Markdown 特殊字元：st.button 的標籤會被當 Markdown 算繪，
# `__x__` 會變成粗體並吃掉前後底線，使按鈕 textContent 與此字串對不上、JS 找不到按鈕。
_PAYLOAD_LABEL = "m012CanvasPayloadBridge"
_SAVE_LABEL = "m012CanvasSaveBridge"

_MAX_ENCODE_DIM = 2048                       # 編碼 JPEG 的最大邊長(座標空間仍用原圖尺寸)
_DATAURL_CACHE: dict[str, tuple[str, int, int]] = {}   # (path|mtime|maxdim) → (data_url, w, h)


def _img_data_url(image_path: str) -> tuple[str, int, int]:
    """影像 → (data URL, 原圖 width, 原圖 height)。

    大圖只把**編碼用的 JPEG raster** 縮到 ``_MAX_ENCODE_DIM`` 以利瀏覽器渲染,但回傳的
    ``(w, h)`` 一律是**原圖尺寸** —— 編輯器以此為座標空間,canvas 會把較小的 raster 拉伸到
    ``iw*scale`` 繪製,故框座標完全不受影響。結果以 (path, mtime, maxdim) 快取,跨 rerun 不重編。
    """
    try:
        mtime = os.stat(image_path).st_mtime_ns
    except OSError:
        mtime = 0
    key = f"{image_path}|{mtime}|{_MAX_ENCODE_DIM}"
    hit = _DATAURL_CACHE.get(key)
    if hit is not None:
        return hit

    from PIL import Image, ImageOps
    import io
    img = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    w, h = img.size                          # 原圖尺寸 = 座標空間(不可變)
    enc = img
    if max(w, h) > _MAX_ENCODE_DIM:
        s = _MAX_ENCODE_DIM / float(max(w, h))
        enc = img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    buf = io.BytesIO()
    enc.save(buf, format="JPEG", quality=90)
    out = (f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}", w, h)
    if len(_DATAURL_CACHE) > 64:             # 粗略上限,避免長工作階段無限增長
        _DATAURL_CACHE.clear()
    _DATAURL_CACHE[key] = out
    return out


def editor_html(image_data_url: str, boxes: list[dict], classes: list[str],
                img_w: int, img_h: int) -> str:
    """自含的編輯器 HTML(canvas + JS)。存檔時把框 JSON 回灌 parent 的隱藏 widget。"""
    cfg = json.dumps({
        "img": image_data_url, "boxes": boxes, "classes": classes,
        "iw": img_w, "ih": img_h,
        "payloadLabel": _PAYLOAD_LABEL, "saveLabel": _SAVE_LABEL,
    })
    # 純字串 + .replace 注入 cfg(非 f-string,所以 JS 單括號照原樣保留)。
    return """
<style>
  html,body{height:100%;margin:0;padding:0;}
  #wrap{display:flex;flex-direction:column;height:100%;box-sizing:border-box;
        padding:4px 4px 0;font-family:sans-serif;user-select:none;-webkit-user-select:none;}
  #bar{display:flex;gap:5px;align-items:center;flex-wrap:wrap;margin-bottom:6px;flex:0 0 auto;}
  #bar button{font-size:12px;cursor:pointer;border:1px solid #cbd5e1;border-radius:4px;background:#fff;padding:2px 7px;}
  #bar button.on{background:#2563eb;color:#fff;border-color:#2563eb;}
  #clsWrap{position:relative;display:inline-block;}
  #clsSearch{font-size:12px;padding:2px 6px;border:1px solid #cbd5e1;border-radius:4px;width:128px;}
  #clsMenu{position:absolute;top:100%;left:0;z-index:30;min-width:150px;max-height:220px;overflow:auto;
           background:#fff;border:1px solid #cbd5e1;border-radius:4px;box-shadow:0 4px 12px rgba(0,0,0,.18);display:none;}
  #clsMenu div{padding:3px 8px;cursor:pointer;font-size:12px;display:flex;align-items:center;gap:6px;}
  #clsMenu div:hover,#clsMenu div.active{background:#eff6ff;}
  #holder{position:relative;flex:1 1 auto;min-height:0;display:flex;align-items:center;justify-content:center;
          background:#0f172a;border:1px solid #cbd5e1;border-radius:6px;overflow:hidden;}
  #cv{display:block;cursor:crosshair;touch-action:none;outline:none;box-shadow:0 0 0 1px rgba(255,255,255,.25);border-radius:2px;}
  #readout{position:absolute;top:6px;left:6px;font:11px/1.4 ui-monospace,monospace;color:#e2e8f0;
           background:rgba(15,23,42,.72);padding:3px 6px;border-radius:4px;pointer-events:none;white-space:pre;}
  #legend{position:absolute;top:6px;right:6px;max-height:62%;overflow:auto;font-size:11px;color:#e2e8f0;
          background:rgba(15,23,42,.8);padding:4px 5px;border-radius:4px;pointer-events:auto;display:none;}
  #legend .row{display:flex;align-items:center;gap:5px;padding:1px 0;white-space:nowrap;}
  #legend .sw{width:10px;height:10px;border-radius:2px;display:inline-block;flex:0 0 auto;}
  #legend button{font-size:10px;padding:0 3px;border:1px solid #475569;border-radius:3px;background:#1e293b;color:#e2e8f0;cursor:pointer;}
  #legend button.on{background:#2563eb;border-color:#2563eb;}
  #sw_sel{flex:1;}
  #hint{font-size:11px;color:#94a3b8;margin:4px 0;flex:0 0 auto;}
</style>
<div id="wrap">
  <div id="bar">
    <span style="font-size:12px;color:#475569;">類別:</span>
    <span id="palette"></span>
    <span id="clsWrap"><input id="clsSearch" placeholder="搜尋類別… (↵指派)" autocomplete="off"><div id="clsMenu"></div></span>
    <span style="flex:1"></span>
    <button id="undo" title="Ctrl+Z 復原">↶</button>
    <button id="redo" title="Ctrl+Y / Ctrl+Shift+Z 重做">↷</button>
    <button id="fitbtn" title="f / 0 全圖">⤢</button>
    <button id="labelsBtn" title="l 標籤開關">標籤</button>
    <button id="crossBtn" title="x 十字輔助線">十字</button>
    <button id="sweepBtn" title="刪除過小的框">掃小框</button>
    <span id="dirtyDot" title="尚未存檔" style="display:none;color:#f59e0b;font-weight:bold;">●</span>
    <button id="save" style="background:#2563eb;color:#fff;border:0;padding:4px 12px;border-radius:4px;" title="Ctrl+S">💾 存檔</button>
    <span id="status" style="font-size:12px;color:#16a34a;"></span>
  </div>
  <div id="holder"><canvas id="cv" tabindex="0"></canvas><div id="readout"></div><div id="legend"></div></div>
  <div id="hint">把手改大小(Alt=中心對稱)·框內點選最小框(重複點/Alt+點循環重疊)·空白拖曳畫新框·方向鍵移動/Alt改大小·Del刪·Ctrl+Z/Y復原重做·Tab或[ ]切框·+/-/f/z縮放·l標籤 x十字·Ctrl+S存檔</div>
</div>
<script>
const CFG = __CFG__;
(function(){
  const $ = id => document.getElementById(id);
  const cv = $('cv'), ctx = cv.getContext('2d'), holder = $('holder');
  const readout = $('readout'), legend = $('legend'), statusEl = $('status'), dirtyDot = $('dirtyDot');
  const clsSearch = $('clsSearch'), clsMenu = $('clsMenu');
  const labelsBtn = $('labelsBtn'), crossBtn = $('crossBtn');
  const dpr = window.devicePixelRatio || 1;
  const img = new Image();
  const MIN = 3, TOL = 8;                    // 最小框邊(影像px)、把手命中容差(螢幕px)
  const clamp = (v,lo,hi)=>Math.min(Math.max(v,lo),hi);
  let boxes = CFG.boxes.map(b => ({label:b.label, x:+b.x, y:+b.y, w:+b.w, h:+b.h,
                                   score:(b.score==null?null:+b.score)}));
  let classes = CFG.classes.length ? CFG.classes : Array.from(new Set(boxes.map(b=>b.label))).filter(Boolean);
  if(!classes.length) classes = ['object'];
  let curClass = classes[0];
  const colors = ['#ef4444','#3b82f6','#22c55e','#f59e0b','#a855f7','#06b6d4','#ec4899','#84cc16','#f97316','#14b8a6'];
  const colorOf = name => colors[Math.max(0, classes.indexOf(name)) % colors.length];

  let scale = 1, ox = 0, oy = 0, baseScale = 1, fitted = false, cssW = 0, cssH = 0;
  let sel = -1, drag = null, panning = false, spaceDown = false;
  const hist = [], redo = [];
  let dirty = false;
  const hidden = new Set(); let soloClass = null, labelsOff = false, crosshair = false;
  let mouseImg = null, lastClickImg = null, cycleIdx = 0;
  const ZMIN = ()=>baseScale*0.5, ZMAX = ()=>baseScale*40;

  const isVisibleLabel = l => soloClass ? l===soloClass : !hidden.has(l);
  const isVisible = i => isVisibleLabel(boxes[i].label);
  function updateDirtyUI(){ dirtyDot.style.display = dirty ? 'inline' : 'none'; }
  function markDirty(){ dirty = true; updateDirtyUI(); }
  const snapshot = ()=>{ hist.push(JSON.stringify(boxes)); redo.length = 0; };
  function undo(){ if(hist.length){ redo.push(JSON.stringify(boxes)); boxes = JSON.parse(hist.pop());
    if(sel>=boxes.length) sel=-1; markDirty(); renderLegend(); draw(); } }
  function redoFn(){ if(redo.length){ hist.push(JSON.stringify(boxes)); boxes = JSON.parse(redo.pop());
    if(sel>=boxes.length) sel=-1; markDirty(); renderLegend(); draw(); } }

  // ── 8 把手:角 + 邊。HE=該把手會移動的邊;HC=對應游標。x1/y1/x2/y2 邊模型。──────────
  const HE = {tl:{l:1,t:1}, tr:{r:1,t:1}, bl:{l:1,b:1}, br:{r:1,b:1}, t:{t:1}, b:{b:1}, l:{l:1}, r:{r:1}};
  const HC = {tl:'nwse-resize', br:'nwse-resize', tr:'nesw-resize', bl:'nesw-resize',
              l:'ew-resize', r:'ew-resize', t:'ns-resize', b:'ns-resize'};
  function handlePoints(b){ const x1=b.x,y1=b.y,x2=b.x+b.w,y2=b.y+b.h,mx=(x1+x2)/2,my=(y1+y2)/2;
    return {tl:[x1,y1],tr:[x2,y1],bl:[x1,y2],br:[x2,y2],t:[mx,y1],b:[mx,y2],l:[x1,my],r:[x2,my]}; }
  function handleAt(b,m){ const ti=TOL/scale, x1=b.x,y1=b.y,x2=b.x+b.w,y2=b.y+b.h;
    const nX1=Math.abs(m.x-x1)<=ti, nX2=Math.abs(m.x-x2)<=ti, nY1=Math.abs(m.y-y1)<=ti, nY2=Math.abs(m.y-y2)<=ti;
    const inX=m.x>=x1-ti&&m.x<=x2+ti, inY=m.y>=y1-ti&&m.y<=y2+ti;
    if(nX1&&nY1) return 'tl'; if(nX2&&nY1) return 'tr'; if(nX1&&nY2) return 'bl'; if(nX2&&nY2) return 'br';
    if(nX1&&inY) return 'l'; if(nX2&&inY) return 'r'; if(nY1&&inX) return 't'; if(nY2&&inX) return 'b'; return null; }
  function hitBox(b,m){ return m.x>=b.x && m.x<=b.x+b.w && m.y>=b.y && m.y<=b.y+b.h; }
  function applyEdges(d,m){
    let x1=d.x1,y1=d.y1,x2=d.x2,y2=d.y2;
    if(d.sym){                                // Alt:以中心對稱
      if(d.h.l){ x1=m.x; x2=d.cx+(d.cx-m.x); } if(d.h.r){ x2=m.x; x1=d.cx-(m.x-d.cx); }
      if(d.h.t){ y1=m.y; y2=d.cy+(d.cy-m.y); } if(d.h.b){ y2=m.y; y1=d.cy-(m.y-d.cy); }
    } else {
      if(d.h.l) x1=m.x; if(d.h.r) x2=m.x; if(d.h.t) y1=m.y; if(d.h.b) y2=m.y;
    }
    x1=clamp(x1,0,CFG.iw); x2=clamp(x2,0,CFG.iw); y1=clamp(y1,0,CFG.ih); y2=clamp(y2,0,CFG.ih);
    const b=boxes[d.i]; b.x=Math.min(x1,x2); b.y=Math.min(y1,y2); b.w=Math.abs(x2-x1); b.h=Math.abs(y2-y1);
  }

  // ── 版面:canvas 依「可用空間 ∩ 影像長寬比」放到最大,影像填滿 canvas。────────────
  function layout(){
    const availW = holder.clientWidth, availH = holder.clientHeight;
    if(availW<=0 || availH<=0) return;
    baseScale = Math.min(availW/CFG.iw, availH/CFG.ih);
    cssW = Math.max(1, CFG.iw*baseScale); cssH = Math.max(1, CFG.ih*baseScale);
    cv.style.width = cssW+'px'; cv.style.height = cssH+'px';
    cv.width = Math.round(cssW*dpr); cv.height = Math.round(cssH*dpr);
    if(!fitted){ scale = baseScale; ox = 0; oy = 0; }
    draw();
  }
  function resetFit(){ fitted = false; layout(); }
  const toImg = (px,py) => ({x:(px-ox)/scale, y:(py-oy)/scale});
  const toScr = (ix,iy) => ({x:ix*scale+ox, y:iy*scale+oy});
  const clampZoom = ()=>{ scale = clamp(scale, ZMIN(), ZMAX()); };
  function zoomAt(cx,cy,f){ const ni=toImg(cx,cy); scale*=f; clampZoom(); fitted=true;
    const np=toScr(ni.x,ni.y); ox+=cx-np.x; oy+=cy-np.y; draw(); }
  function zoomToSel(){ if(sel<0) return; const b=boxes[sel], pad=1.15;
    scale = clamp(Math.min(cssW/(b.w*pad), cssH/(b.h*pad)), ZMIN(), ZMAX()); fitted=true;
    ox = cssW/2 - (b.x+b.w/2)*scale; oy = cssH/2 - (b.y+b.h/2)*scale; draw(); }

  function draw(){
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.imageSmoothingEnabled = false;        // 高倍縮放保留清晰像素
    ctx.clearRect(0,0,cssW,cssH);
    if(img.complete && img.naturalWidth) ctx.drawImage(img, ox, oy, CFG.iw*scale, CFG.ih*scale);
    boxes.forEach((b,i)=>{
      if(!isVisible(i)) return;
      const p=toScr(b.x,b.y), wS=b.w*scale, hS=b.h*scale, col=colorOf(b.label);
      ctx.save();
      ctx.globalAlpha = (sel>=0 && i!==sel) ? 0.45 : 1;     // 選取時其餘變淡
      if(i===sel){ ctx.globalAlpha=0.12; ctx.fillStyle=col; ctx.fillRect(p.x,p.y,wS,hS); ctx.globalAlpha=1; }
      ctx.lineWidth = i===sel?3:2; ctx.strokeStyle=col; ctx.strokeRect(p.x,p.y,wS,hS);
      if(!labelsOff){ ctx.fillStyle=col; const tag=b.label+(i===sel?' ✦':''); ctx.font='12px sans-serif';
        const tw=ctx.measureText(tag).width+8; ctx.fillRect(p.x,p.y-16,tw,16);
        ctx.fillStyle='#fff'; ctx.fillText(tag,p.x+4,p.y-4); }
      ctx.restore();
      if(i===sel){ const hs=handlePoints(b); ctx.fillStyle='#fff'; ctx.strokeStyle='#1e293b'; ctx.lineWidth=1.5;
        for(const k in hs){ const s=toScr(hs[k][0],hs[k][1]); ctx.fillRect(s.x-5,s.y-5,10,10); ctx.strokeRect(s.x-5,s.y-5,10,10); } }
    });
    if(crosshair && mouseImg){ const s=toScr(mouseImg.x,mouseImg.y); ctx.save();
      ctx.strokeStyle='rgba(255,255,255,.65)'; ctx.lineWidth=1; ctx.beginPath();
      ctx.moveTo(s.x,0); ctx.lineTo(s.x,cssH); ctx.moveTo(0,s.y); ctx.lineTo(cssW,s.y); ctx.stroke(); ctx.restore(); }
    updateReadout();
  }
  function updateReadout(){
    let t = 'zoom '+Math.round(scale/baseScale*100)+'%';
    if(sel>=0){ const b=boxes[sel];
      t = 'x '+Math.round(b.x)+'  y '+Math.round(b.y)+'  w '+Math.round(b.w)+'  h '+Math.round(b.h)+'   '+t; }
    if(mouseImg) t += '   ('+Math.round(mouseImg.x)+','+Math.round(mouseImg.y)+')';
    readout.textContent = t;
  }

  // ── 類別:1-9 快速鍵(前 12 顯示)+ 搜尋下拉(支援 10~80 類)──────────────────────
  function palette(){
    const el = $('palette'); el.innerHTML='';
    classes.slice(0,12).forEach((c,i)=>{
      const b=document.createElement('button');
      b.textContent=(i<9?('['+(i+1)+'] '):'')+c;
      b.style.cssText='margin:1px;border:2px solid '+colorOf(c)+';border-radius:4px;padding:2px 8px;cursor:pointer;background:'+
        (c===curClass?colorOf(c):'#fff')+';color:'+(c===curClass?'#fff':'#334155')+';font-size:12px;';
      b.onmousedown=ev=>{ ev.preventDefault(); assignClass(c); cv.focus(); };
      el.appendChild(b);
    });
    if(classes.length>12){ const s=document.createElement('span'); s.style.cssText='font-size:11px;color:#94a3b8;margin-left:2px;';
      s.textContent='+'+(classes.length-12)+' 用搜尋'; el.appendChild(s); }
  }
  function assignClass(c){ curClass=c; if(sel>=0){ snapshot(); boxes[sel].label=c; markDirty(); }
    palette(); renderLegend(); draw(); }
  function renderClsMenu(filter){
    const f=(filter||'').toLowerCase(); clsMenu.innerHTML='';
    const ms=classes.filter(c=>c.toLowerCase().indexOf(f)>=0).slice(0,300);
    ms.forEach((c,idx)=>{ const d=document.createElement('div'); if(idx===0&&f) d.className='active';
      d.innerHTML='<span class="sw" style="width:10px;height:10px;border-radius:2px;background:'+colorOf(c)+'"></span>'+c;
      d.onmousedown=ev=>{ ev.preventDefault(); assignClass(c); clsSearch.value=''; clsMenu.style.display='none'; cv.focus(); };
      clsMenu.appendChild(d); });
    clsMenu.style.display = ms.length ? 'block':'none';
  }
  clsSearch.addEventListener('input', ()=>renderClsMenu(clsSearch.value));
  clsSearch.addEventListener('focus', ()=>renderClsMenu(clsSearch.value));
  clsSearch.addEventListener('blur', ()=>setTimeout(()=>{ clsMenu.style.display='none'; }, 150));
  clsSearch.addEventListener('keydown', e=>{
    if(e.key==='Enter'){ const f=clsSearch.value.toLowerCase(); const m=classes.find(c=>c.toLowerCase().indexOf(f)>=0);
      if(m) assignClass(m); clsSearch.value=''; clsMenu.style.display='none'; cv.focus(); e.preventDefault(); }
    else if(e.key==='Escape'){ clsSearch.value=''; clsMenu.style.display='none'; cv.focus(); e.preventDefault(); }
  });

  // ── 類別圖例(右上):顯示/隱藏、單看某類 ─────────────────────────────────────────
  function renderLegend(){
    const counts={}; boxes.forEach(b=>counts[b.label]=(counts[b.label]||0)+1);
    const names=Object.keys(counts);
    if(!names.length){ legend.style.display='none'; legend.innerHTML=''; return; }
    legend.style.display='block'; legend.innerHTML='';
    names.forEach(name=>{ const row=document.createElement('div'); row.className='row';
      const vis=isVisibleLabel(name);
      const sw=document.createElement('span'); sw.className='sw'; sw.style.background=colorOf(name);
      const lab=document.createElement('span'); lab.id='sw_sel'; lab.textContent=name+' ('+counts[name]+')';
      if(!vis){ lab.style.opacity='.5'; lab.style.textDecoration='line-through'; }
      const eye=document.createElement('button'); eye.textContent=vis?'👁':'🚫'; eye.title='顯示/隱藏';
      eye.onclick=()=>{ if(hidden.has(name)) hidden.delete(name); else hidden.add(name); renderLegend(); draw(); };
      const solo=document.createElement('button'); solo.textContent='S'; solo.title='只看這類';
      if(soloClass===name) solo.className='on';
      solo.onclick=()=>{ soloClass = soloClass===name?null:name; renderLegend(); draw(); };
      row.appendChild(sw); row.appendChild(lab); row.appendChild(eye); row.appendChild(solo); legend.appendChild(row); });
  }

  // ── 選取循環 / 層級 / 掃小框 ─────────────────────────────────────────────────────
  function visIdx(){ const a=[]; for(let i=0;i<boxes.length;i++) if(isVisible(i)) a.push(i); return a; }
  function cycleSel(dir){ const a=visIdx(); if(!a.length){ sel=-1; draw(); return; }
    let p=a.indexOf(sel); p = p<0 ? (dir>0?0:a.length-1) : (p+dir+a.length)%a.length; sel=a[p]; draw(); }
  function bringToFront(){ if(sel<0) return; snapshot(); const b=boxes.splice(sel,1)[0]; boxes.push(b);
    sel=boxes.length-1; markDirty(); renderLegend(); draw(); }
  function sendToBack(){ if(sel<0) return; snapshot(); const b=boxes.splice(sel,1)[0]; boxes.unshift(b);
    sel=0; markDirty(); renderLegend(); draw(); }
  function sweepTiny(){ snapshot(); boxes=boxes.filter(b=>b.w>=MIN && b.h>=MIN); sel=-1; markDirty(); renderLegend(); draw(); }

  function cursorFor(m){
    if(spaceDown) return panning?'grabbing':'grab';
    if(sel>=0){ const k=handleAt(boxes[sel],m); if(k) return HC[k]; }
    for(let i=boxes.length-1;i>=0;i--){ if(!isVisible(i)) continue; const k=handleAt(boxes[i],m); if(k) return HC[k]; }
    for(let i=boxes.length-1;i>=0;i--){ if(isVisible(i) && hitBox(boxes[i],m)) return 'move'; }
    return 'crosshair';
  }

  // ── 指標互動 ──────────────────────────────────────────────────────────────────
  cv.addEventListener('pointerdown', e=>{
    if(e.button!==0 && e.pointerType==='mouse') return;
    cv.focus(); try{ cv.setPointerCapture(e.pointerId); }catch(_){}
    const r=cv.getBoundingClientRect(), m=toImg(e.clientX-r.left, e.clientY-r.top);
    e.preventDefault();
    if(spaceDown){ panning={px:e.clientX,py:e.clientY,ox,oy}; return; }
    // 1) 把手(已選優先,再任一框);Alt 拖把手 = 中心對稱縮放
    let hi=-1,hk=null;
    if(sel>=0){ const k=handleAt(boxes[sel],m); if(k){ hi=sel; hk=k; } }
    if(hk===null){ for(let i=boxes.length-1;i>=0;i--){ if(!isVisible(i)) continue; const k=handleAt(boxes[i],m); if(k){ hi=i; hk=k; break; } } }
    if(hk!==null){ sel=hi; snapshot(); const b=boxes[hi];
      drag={mode:'resize',i:hi,h:HE[hk],x1:b.x,y1:b.y,x2:b.x+b.w,y2:b.y+b.h,
            sym:e.altKey,cx:b.x+b.w/2,cy:b.y+b.h/2,moved:false}; palette(); draw(); return; }
    // 2) 框內:選「最小面積」框;同點重複/Alt+點循環重疊框,然後移動
    const cand=[]; for(let i=0;i<boxes.length;i++){ if(isVisible(i) && hitBox(boxes[i],m)) cand.push(i); }
    if(cand.length){
      cand.sort((a,b)=>(boxes[a].w*boxes[a].h)-(boxes[b].w*boxes[b].h));
      const same = lastClickImg && Math.abs(lastClickImg.x-m.x)<3/scale && Math.abs(lastClickImg.y-m.y)<3/scale;
      cycleIdx = (same || e.altKey) ? (cycleIdx+1)%cand.length : 0;
      sel=cand[cycleIdx]; lastClickImg=m;
      snapshot(); drag={mode:'move',i:sel,dx:m.x-boxes[sel].x,dy:m.y-boxes[sel].y,moved:false};
      palette(); renderLegend(); draw(); return;
    }
    lastClickImg=null; cycleIdx=0;
    // 3) 空白 → 新框
    snapshot(); boxes.push({label:curClass,x:m.x,y:m.y,w:0,h:0,score:null}); sel=boxes.length-1;
    drag={mode:'new',i:sel,h:{r:1,b:1},x1:m.x,y1:m.y,x2:m.x,y2:m.y,moved:false}; renderLegend(); draw();
  });
  cv.addEventListener('pointermove', e=>{
    const r=cv.getBoundingClientRect();
    mouseImg = toImg(e.clientX-r.left, e.clientY-r.top);
    if(panning){ ox=panning.ox+(e.clientX-panning.px); oy=panning.oy+(e.clientY-panning.py); draw(); return; }
    if(!drag){ cv.style.cursor=cursorFor(mouseImg); if(crosshair) draw(); else updateReadout(); return; }
    drag.moved=true;
    if(drag.mode==='move'){ const b=boxes[drag.i];
      b.x=clamp(mouseImg.x-drag.dx,0,CFG.iw-b.w); b.y=clamp(mouseImg.y-drag.dy,0,CFG.ih-b.h); }
    else applyEdges(drag, mouseImg);
    draw();
  });
  function endDrag(e){
    if(e){ try{ cv.releasePointerCapture(e.pointerId); }catch(_){} }
    if(drag){ const b=boxes[drag.i];
      if(drag.mode==='new'){ if(b.w<MIN||b.h<MIN){ boxes.splice(drag.i,1); sel=-1; } else markDirty(); }
      else if(drag.mode==='resize'){ if(b.w<MIN)b.w=MIN; if(b.h<MIN)b.h=MIN;
        if(b.x+b.w>CFG.iw)b.x=Math.max(0,CFG.iw-b.w); if(b.y+b.h>CFG.ih)b.y=Math.max(0,CFG.ih-b.h);
        if(drag.moved) markDirty(); }
      else if(drag.moved) markDirty();
      renderLegend(); draw();
    }
    drag=null; panning=false;
  }
  cv.addEventListener('pointerup', endDrag);
  cv.addEventListener('pointercancel', endDrag);
  cv.addEventListener('pointerleave', ()=>{ mouseImg=null; if(crosshair) draw(); });
  cv.addEventListener('wheel', e=>{ e.preventDefault(); const r=cv.getBoundingClientRect();
    zoomAt(e.clientX-r.left, e.clientY-r.top, e.deltaY<0?1.1:0.9); }, {passive:false});

  // ── 鍵盤微調 ──────────────────────────────────────────────────────────────────
  function nudgePos(dx,dy){ if(sel<0) return; snapshot(); const b=boxes[sel];
    b.x=clamp(b.x+dx,0,CFG.iw-b.w); b.y=clamp(b.y+dy,0,CFG.ih-b.h); markDirty(); draw(); }
  function nudgeSize(dx,dy){ if(sel<0) return; snapshot(); const b=boxes[sel];
    const x2=clamp(b.x+b.w+dx,b.x+MIN,CFG.iw), y2=clamp(b.y+b.h+dy,b.y+MIN,CFG.ih); b.w=x2-b.x; b.h=y2-b.y; markDirty(); draw(); }
  function toggleLabels(){ labelsOff=!labelsOff; labelsBtn.classList.toggle('on',labelsOff); draw(); }
  function toggleCross(){ crosshair=!crosshair; crossBtn.classList.toggle('on',crosshair); draw(); }

  document.addEventListener('keydown', e=>{
    const ae=document.activeElement;
    const typing = ae && (ae.tagName==='INPUT' || ae.tagName==='TEXTAREA' || ae.isContentEditable);
    if(typing){                                // 類別搜尋輸入時:只放行 Esc(失焦)與 Ctrl+S(存檔)
      if(e.key==='Escape'){ ae.blur(); cv.focus(); }
      else if((e.ctrlKey||e.metaKey) && (e.key==='s'||e.key==='S')){ e.preventDefault(); doSave(); }
      return;
    }
    if(e.code==='Space'){ spaceDown=true; cv.style.cursor='grab'; }
    if((e.ctrlKey||e.metaKey) && (e.key==='y'||e.key==='Y')){ redoFn(); e.preventDefault(); return; }
    if((e.ctrlKey||e.metaKey) && e.shiftKey && (e.key==='z'||e.key==='Z')){ redoFn(); e.preventDefault(); return; }
    if((e.ctrlKey||e.metaKey) && !e.shiftKey && (e.key==='z'||e.key==='Z')){ undo(); e.preventDefault(); return; }
    if((e.ctrlKey||e.metaKey) && (e.key==='s'||e.key==='S')){ e.preventDefault(); doSave(); return; }
    if((e.ctrlKey||e.metaKey) && e.key===']'){ bringToFront(); e.preventDefault(); return; }
    if((e.ctrlKey||e.metaKey) && e.key==='['){ sendToBack(); e.preventDefault(); return; }
    if(e.key==='Escape'){ if(drag){ undo(); drag=null; } else sel=-1; draw(); e.preventDefault(); return; }
    if(sel>=0 && e.key.indexOf('Arrow')===0){ e.preventDefault(); const step=e.shiftKey?10:1;
      const dx=e.key==='ArrowRight'?step:e.key==='ArrowLeft'?-step:0, dy=e.key==='ArrowDown'?step:e.key==='ArrowUp'?-step:0;
      if(e.altKey) nudgeSize(dx,dy); else nudgePos(dx,dy); return; }
    if((e.key==='Delete'||e.key==='Backspace') && sel>=0){ snapshot(); boxes.splice(sel,1); sel=-1; markDirty(); renderLegend(); draw(); e.preventDefault(); return; }
    if(e.key==='Tab'){ cycleSel(e.shiftKey?-1:1); e.preventDefault(); return; }
    if(e.key===']'){ cycleSel(1); e.preventDefault(); return; }
    if(e.key==='['){ cycleSel(-1); e.preventDefault(); return; }
    if(e.key==='+'||e.key==='='){ zoomAt(cssW/2,cssH/2,1.1); e.preventDefault(); return; }
    if(e.key==='-'){ zoomAt(cssW/2,cssH/2,0.9); e.preventDefault(); return; }
    if(e.key==='f'||e.key==='F'||e.key==='0'){ resetFit(); e.preventDefault(); return; }
    if(e.key==='z'||e.key==='Z'){ zoomToSel(); e.preventDefault(); return; }
    if(e.key==='l'||e.key==='L'){ toggleLabels(); e.preventDefault(); return; }
    if(e.key==='x'||e.key==='X'){ toggleCross(); e.preventDefault(); return; }
    if(e.key>='1' && e.key<='9'){ const i=+e.key-1; if(i<classes.length) assignClass(classes[i]); e.preventDefault(); return; }
  });
  document.addEventListener('keyup', e=>{ if(e.code==='Space'){ spaceDown=false; cv.style.cursor='crosshair'; } });
  window.addEventListener('beforeunload', e=>{ if(dirty){ e.preventDefault(); e.returnValue=''; } });

  // ── 存檔(💾 / Ctrl+S):回灌 parent 的隱藏 Streamlit widget,帶 nonce ──────────────
  function doSave(){
    const payload = JSON.stringify({nonce: Date.now(),
      boxes: boxes.map(b=>({label:b.label,x:b.x,y:b.y,w:b.w,h:b.h,score:b.score}))});
    try{
      const pd = window.parent.document;
      const ta = pd.querySelector('input[aria-label="'+CFG.payloadLabel+'"]') || pd.querySelector('textarea[aria-label="'+CFG.payloadLabel+'"]');
      if(ta){
        const proto = ta.tagName==='INPUT' ? window.parent.HTMLInputElement.prototype : window.parent.HTMLTextAreaElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto,'value').set;
        ta.focus({preventScroll:true}); setter.call(ta, payload);
        ta.dispatchEvent(new Event('input',{bubbles:true})); ta.dispatchEvent(new Event('change',{bubbles:true})); ta.blur();
        statusEl.style.color='#16a34a'; statusEl.textContent='已送出存檔…'; dirty=false; updateDirtyUI();
      } else { statusEl.style.color='#dc2626'; statusEl.textContent='找不到存檔橋接欄位'; }
    }catch(err){ statusEl.style.color='#dc2626'; statusEl.textContent='存檔失敗:'+err; }
  }

  $('undo').onclick=undo; $('redo').onclick=redoFn; $('fitbtn').onclick=resetFit; $('save').onclick=doSave;
  labelsBtn.onclick=toggleLabels; crossBtn.onclick=toggleCross; $('sweepBtn').onclick=sweepTiny;

  function resizeFrame(){ try{ const fe=window.frameElement;
    const vh=(window.parent && window.parent.innerHeight) || window.innerHeight || 700;
    if(fe){ fe.style.height = Math.max(420, Math.round(vh*0.82))+'px'; } }catch(e){} }

  // 測試/E2E 探針:唯讀 state() + 命令式 api(讓無穩定快捷鍵的功能也能被 headless 確定性驅動)。
  try{ window.__m012canvas = {
    state:()=>({boxes:boxes.map(b=>({...b})), sel, scale, ox, oy, cssW, cssH, dirty,
      canUndo:hist.length>0, canRedo:redo.length>0, hidden:[...hidden], solo:soloClass,
      labelsOff, crosshair, baseScale, curClass, zoomPct:Math.round(scale/baseScale*100)}),
    api:{ undo, redo:redoFn, save:doSave, fit:resetFit, zoomToSel, cycleSel,
      toggleHidden:l=>{ if(hidden.has(l)) hidden.delete(l); else hidden.add(l); renderLegend(); draw(); },
      solo:l=>{ soloClass = soloClass===l?null:l; renderLegend(); draw(); },
      toggleLabels, toggleCrosshair:toggleCross, bringToFront, sendToBack, sweepTiny,
      setClass:assignClass, order:()=>boxes.map(b=>b.label) } };
  }catch(_){}

  img.onload=()=>{ palette(); renderLegend(); layout(); };
  img.src=CFG.img;
  if(window.ResizeObserver){ new ResizeObserver(()=>layout()).observe(holder); }
  window.addEventListener('resize', ()=>{ resizeFrame(); layout(); });
  resizeFrame();
})();
</script>
""".replace("__CFG__", cfg)


def render_canvas_editor(image_path: str, classes: list[str], *, key: str,
                         height: int = 720) -> bool:
    """在 Streamlit 渲染編輯器;存檔時寫回 sidecar。回傳 True 表示本次有存檔。

    需要 streamlit(由呼叫端 module_012 提供)。隱藏 widget 接住瀏覽器端回灌。
    存檔採**合併**:既有 sidecar 的非矩形形狀(polygon/mask)原樣保留,只覆寫矩形,
    每框的 score(信心值)亦沿用,避免在頻繁寫回時悄悄丟失既有標註內容。
    """
    import streamlit as st
    import streamlit.components.v1 as components

    from plugins.labeling.domain.adapters.canvas_boxes import (
        canvas_boxes_to_sidecar,
        sidecar_to_canvas_boxes,
    )

    sidecar_path = Path(image_path).with_suffix(".json")
    existing = []
    keep_shapes: list[dict] = []   # 非矩形形狀(編輯器不碰),存檔時原樣保留
    if sidecar_path.exists():
        try:
            _sc = json.loads(sidecar_path.read_text(encoding="utf-8"))
            existing = sidecar_to_canvas_boxes(_sc)
            keep_shapes = [s for s in _sc.get("shapes", [])
                           if s.get("shape_type") != "rectangle"]
        except Exception:
            existing, keep_shapes = [], []

    data_url, iw, ih = _img_data_url(image_path)

    payload = st.text_input(_PAYLOAD_LABEL, key=f"{key}_payload",
                            label_visibility="collapsed")
    saved = False
    _nonce_key = f"{key}_last_nonce"
    if payload:
        try:
            data = json.loads(payload)
            _nonce = data.get("nonce")
            _boxes = data.get("boxes", [])
            # 只在看到「新的」nonce 時寫檔，避免每次 rerun 重複寫。
            if _nonce and st.session_state.get(_nonce_key) != _nonce:
                st.session_state[_nonce_key] = _nonce
                sidecar = canvas_boxes_to_sidecar(
                    _boxes, Path(image_path).name, iw, ih, keep_shapes=keep_shapes)
                sidecar_path.write_text(
                    json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
                saved = True
                st.toast(f"已存檔 {len(sidecar['shapes'])} 個框 → {sidecar_path.name}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"存檔失敗:{exc}")

    # 把回灌用的 text_area / button 在 parent document 收起來(沿用 012 hideGhosts 慣例)。
    components.html(
        "<script>(function(){var d=window.parent.document;function h(){"
        "d.querySelectorAll('input[aria-label=\"" + _PAYLOAD_LABEL + "\"]').forEach("
        "function(t){var w=t.closest('[data-testid=\"stTextInput\"]');"
        "if(w)w.style.cssText='position:absolute;left:-9999px;height:0;overflow:hidden;';});}"
        "h();new MutationObserver(h).observe(d.body,{childList:true,subtree:true});})();</script>",
        height=0)

    components.html(editor_html(data_url, existing, classes, iw, ih), height=height)
    return saved
