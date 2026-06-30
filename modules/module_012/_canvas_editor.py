"""In-browser bbox 標註編輯器(零依賴原生 Canvas)+ Streamlit 整合。

為什麼有這個檔
--------------
工作台目前只能「看」框、要編輯就「啟動桌面工具」(X-AnyLabeling/LabelMe/ISAT)。
這支讓使用者**直接在瀏覽器裡畫/改/刪框**,存回影像旁的 ``<image>.json`` sidecar
(與 AI 預標 _run_ai_items / seeder 同一份契約)。純原生 Canvas,不引入 Konva/任何
前端套件,適合內網/離線。

互動(以「AI 先出框 → 人再微調」為核心)
* 8 個把手縮放:4 角 + 4 邊,拖任一把手只動該邊/角,對邊釘住(x1/y1/x2/y2 邊模型)。
* 免先選取即可抓把手;框內拖曳=移動;空白拖曳=畫新框。
* 方向鍵微調位置(±1,Shift ±10);Alt+方向鍵微調大小(右/下邊 ±1,Shift ±10)。
* Del 刪、Ctrl+Z 復原、Esc 取消(進行中的拖曳/取消選取)、數字鍵 1-9 切類別、
  滾輪縮放、空白鍵拖曳平移、⤢ 全圖復位。
* 幾何即時 clamp 進影像;縮太小不刪框(夾到最小邊),只有「新畫的誤點框」會丟。

兩塊:
* :func:`editor_html` — 回傳自含的編輯器 HTML(影像 + 既有框 + 類別清單注入)。
* :func:`render_canvas_editor` — Streamlit 整合:讀既有 sidecar→框、注入編輯器、用
  隱藏 text_input(nonce)接住存檔,經 ``canvas_boxes_to_sidecar`` 寫回 sidecar。
  **存檔採合併**:非矩形形狀(polygon/mask)與每框 score 原樣保留,只覆寫矩形。

⚠️ 瀏覽器端 JS ↔ Streamlit 的回灌橋,需在實際 app 裡 E2E 驗證(本檔的 Python 端
:func:`canvas_boxes_to_sidecar` 寫檔邏輯已有單元測試;互動以 headless 瀏覽器測)。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

# 隱藏控制項的哨兵字串(JS 在 parent document 用它找對應 widget)。
# ⚠️ 不可含 Markdown 特殊字元：st.button 的標籤會被當 Markdown 算繪，
# `__x__` 會變成粗體並吃掉前後底線，使按鈕 textContent 與此字串對不上、JS 找不到按鈕。
_PAYLOAD_LABEL = "m012CanvasPayloadBridge"
_SAVE_LABEL = "m012CanvasSaveBridge"


def _img_data_url(image_path: str) -> tuple[str, int, int]:
    """影像 → (data URL, width, height)。縮到合理上限以利瀏覽器渲染。"""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    w, h = img.size
    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}", w, h


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
        padding:4px 4px 0;font-family:sans-serif;
        user-select:none;-webkit-user-select:none;}
  #bar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px;flex:0 0 auto;}
  /* holder 撐滿剩餘空間；canvas 在其中置中、依影像長寬比放到最大(影像佔滿 canvas)。 */
  #holder{flex:1 1 auto;min-height:0;display:flex;align-items:center;justify-content:center;
          background:#0f172a;border:1px solid #cbd5e1;border-radius:6px;overflow:hidden;}
  #cv{display:block;cursor:crosshair;touch-action:none;box-shadow:0 0 0 1px rgba(255,255,255,.25);border-radius:2px;}
  #hint{font-size:11px;color:#94a3b8;margin:4px 0;flex:0 0 auto;}
</style>
<div id="wrap">
  <div id="bar">
    <span style="font-size:12px;color:#475569;">類別(數字鍵切換):</span>
    <span id="palette"></span>
    <span style="flex:1"></span>
    <button id="undo" title="Ctrl+Z">↶ Undo</button>
    <button id="fitbtn" title="重設縮放/置中">⤢ 全圖</button>
    <button id="save" style="background:#2563eb;color:#fff;border:0;padding:4px 12px;border-radius:4px;">💾 存檔</button>
    <span id="status" style="font-size:12px;color:#16a34a;"></span>
  </div>
  <div id="holder"><canvas id="cv"></canvas></div>
  <div id="hint">拖把手改大小(4角+4邊)·框內拖曳移動·空白拖曳畫新框·方向鍵微調位置(Shift×10)·Alt+方向鍵改大小·Del 刪·Ctrl+Z 復原·Esc 取消·滾輪縮放·空白鍵平移·⤢ 全圖。</div>
</div>
<script>
const CFG = __CFG__;
(function(){
  const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
  const holder = document.getElementById('holder');
  const dpr = window.devicePixelRatio || 1;
  const img = new Image();
  const MIN = 3;                              // 最小框邊(影像 px),縮太小夾到此值而非刪框
  const TOL = 8;                              // 把手命中容差(螢幕 px)
  const clamp = (v,lo,hi)=>Math.min(Math.max(v,lo),hi);
  let boxes = CFG.boxes.map(b => ({label:b.label, x:+b.x, y:+b.y, w:+b.w, h:+b.h,
                                   score:(b.score==null?null:+b.score)}));
  let classes = CFG.classes.length ? CFG.classes : Array.from(new Set(boxes.map(b=>b.label))).filter(Boolean);
  if(!classes.length) classes = ['object'];
  let curClass = classes[0];
  const colors = ['#ef4444','#3b82f6','#22c55e','#f59e0b','#a855f7','#06b6d4','#ec4899','#84cc16'];
  const colorOf = name => colors[Math.max(0, classes.indexOf(name)) % colors.length];

  let scale = 1, ox = 0, oy = 0;            // 影像→畫面 縮放 + 平移(CSS px)
  let baseScale = 1, fitted = false;        // baseScale=全圖縮放;fitted=使用者是否手動縮放過
  let cssW = 0, cssH = 0;                   // canvas 顯示尺寸(CSS px)
  let sel = -1, drag = null, panning = false, spaceDown = false;
  const hist = [];
  const snapshot = () => hist.push(JSON.stringify(boxes));
  const undo = () => { if(hist.length){ boxes = JSON.parse(hist.pop()); if(sel>=boxes.length) sel=-1; draw(); } };

  // ── 8 把手:角 + 邊。HE=該把手會移動的邊;HC=對應游標。x1/y1/x2/y2 邊模型 → 單一
  //    數學路徑涵蓋全部把手,且「拖過對邊自動翻轉」與正規化天然成立。────────────────
  const HE = {tl:{l:1,t:1}, tr:{r:1,t:1}, bl:{l:1,b:1}, br:{r:1,b:1}, t:{t:1}, b:{b:1}, l:{l:1}, r:{r:1}};
  const HC = {tl:'nwse-resize', br:'nwse-resize', tr:'nesw-resize', bl:'nesw-resize',
              l:'ew-resize', r:'ew-resize', t:'ns-resize', b:'ns-resize'};
  function handlePoints(b){ const x1=b.x,y1=b.y,x2=b.x+b.w,y2=b.y+b.h,mx=(x1+x2)/2,my=(y1+y2)/2;
    return {tl:[x1,y1],tr:[x2,y1],bl:[x1,y2],br:[x2,y2],t:[mx,y1],b:[mx,y2],l:[x1,my],r:[x2,my]}; }
  function handleAt(b,m){                       // 回傳把手 key 或 null(m 為影像座標;容差用螢幕 px)
    const ti=TOL/scale, x1=b.x,y1=b.y,x2=b.x+b.w,y2=b.y+b.h;
    const nX1=Math.abs(m.x-x1)<=ti, nX2=Math.abs(m.x-x2)<=ti, nY1=Math.abs(m.y-y1)<=ti, nY2=Math.abs(m.y-y2)<=ti;
    const inX=m.x>=x1-ti&&m.x<=x2+ti, inY=m.y>=y1-ti&&m.y<=y2+ti;
    if(nX1&&nY1) return 'tl'; if(nX2&&nY1) return 'tr'; if(nX1&&nY2) return 'bl'; if(nX2&&nY2) return 'br';
    if(nX1&&inY) return 'l'; if(nX2&&inY) return 'r'; if(nY1&&inX) return 't'; if(nY2&&inX) return 'b';
    return null;
  }
  function hitBox(b,m){ return m.x>=b.x && m.x<=b.x+b.w && m.y>=b.y && m.y<=b.y+b.h; }
  function applyEdges(d,m){                      // resize/new:被拖的邊跟游標,正規化 min/max + clamp 進圖
    let x1=d.x1,y1=d.y1,x2=d.x2,y2=d.y2;
    if(d.h.l) x1=m.x; if(d.h.r) x2=m.x; if(d.h.t) y1=m.y; if(d.h.b) y2=m.y;
    x1=clamp(x1,0,CFG.iw); x2=clamp(x2,0,CFG.iw); y1=clamp(y1,0,CFG.ih); y2=clamp(y2,0,CFG.ih);
    const b=boxes[d.i]; b.x=Math.min(x1,x2); b.y=Math.min(y1,y2); b.w=Math.abs(x2-x1); b.h=Math.abs(y2-y1);
  }

  // canvas 依「可用空間 ∩ 影像長寬比」放到最大 → 影像填滿整個 canvas(不留內邊)。
  // 量測 holder 的實際大小(非 img.onload 當下的暫時值),並由 ResizeObserver 在版面
  // 安定/視窗縮放時重算,根治「iframe 初次量測太窄→影像縮成一小塊」的競態。
  function layout(){
    const availW = holder.clientWidth, availH = holder.clientHeight;
    if(availW <= 0 || availH <= 0) return;
    baseScale = Math.min(availW / CFG.iw, availH / CFG.ih);
    cssW = Math.max(1, CFG.iw * baseScale);
    cssH = Math.max(1, CFG.ih * baseScale);
    cv.style.width = cssW + 'px'; cv.style.height = cssH + 'px';
    cv.width = Math.round(cssW * dpr); cv.height = Math.round(cssH * dpr);
    if(!fitted){ scale = baseScale; ox = 0; oy = 0; }  // 未手動縮放前一律貼齊全圖
    draw();
  }
  function resetFit(){ fitted = false; layout(); }
  const toImg = (px,py) => ({x:(px-ox)/scale, y:(py-oy)/scale});
  const toScr = (ix,iy) => ({x:ix*scale+ox, y:iy*scale+oy});

  function draw(){
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,cssW,cssH);
    if(img.complete && img.naturalWidth) ctx.drawImage(img, ox, oy, CFG.iw*scale, CFG.ih*scale);
    boxes.forEach((b,i)=>{
      const p = toScr(b.x,b.y), wS=b.w*scale, hS=b.h*scale, col=colorOf(b.label);
      if(i===sel){ ctx.save(); ctx.globalAlpha=0.12; ctx.fillStyle=col; ctx.fillRect(p.x,p.y,wS,hS); ctx.restore(); }
      ctx.lineWidth = i===sel?3:2; ctx.strokeStyle = col;
      ctx.strokeRect(p.x, p.y, wS, hS);
      ctx.fillStyle = col;
      const tag = b.label + (i===sel?' ✦':'');
      ctx.font='12px sans-serif';
      const tw = ctx.measureText(tag).width+8;
      ctx.fillRect(p.x, p.y-16, tw, 16);
      ctx.fillStyle='#fff'; ctx.fillText(tag, p.x+4, p.y-4);
      if(i===sel){   // 8 個把手(固定螢幕大小 10px),讓可抓點一目了然
        const hs=handlePoints(b);
        ctx.fillStyle='#fff'; ctx.strokeStyle='#1e293b'; ctx.lineWidth=1.5;
        for(const k in hs){ const s=toScr(hs[k][0],hs[k][1]); ctx.fillRect(s.x-5,s.y-5,10,10); ctx.strokeRect(s.x-5,s.y-5,10,10); }
      }
    });
  }

  function palette(){
    const el = document.getElementById('palette'); el.innerHTML='';
    classes.forEach((c,i)=>{
      const b = document.createElement('button');
      b.textContent = (i<9?('['+(i+1)+'] '):'')+c;
      b.style.cssText = 'margin:1px;border:2px solid '+colorOf(c)+';border-radius:4px;'+
        'padding:2px 8px;cursor:pointer;background:'+(c===curClass?colorOf(c):'#fff')+
        ';color:'+(c===curClass?'#fff':'#334155')+';font-size:12px;';
      b.onclick=()=>{ curClass=c; if(sel>=0){snapshot();boxes[sel].label=c;} palette(); draw(); };
      el.appendChild(b);
    });
  }

  // 滑過時的游標提示:把手→對應縮放游標、框內→移動、空白→畫框/平移。
  function cursorFor(m){
    if(spaceDown) return panning ? 'grabbing' : 'grab';
    if(sel>=0){ const k=handleAt(boxes[sel],m); if(k) return HC[k]; }
    for(let i=boxes.length-1;i>=0;i--){ const k=handleAt(boxes[i],m); if(k) return HC[k]; }
    for(let i=boxes.length-1;i>=0;i--){ if(hitBox(boxes[i],m)) return 'move'; }
    return 'crosshair';
  }

  // Pointer Events + setPointerCapture:按下後把後續 move/up 鎖到 canvas,
  // 游標移出畫布或移動很快也照樣跟住 → 拖曳穩定。
  cv.addEventListener('pointerdown', e=>{
    if(e.button!==0 && e.pointerType==='mouse') return;   // 只認左鍵
    try{ cv.setPointerCapture(e.pointerId); }catch(_){}
    const r=cv.getBoundingClientRect(), m=toImg(e.clientX-r.left, e.clientY-r.top);
    e.preventDefault();
    if(spaceDown){ panning={px:e.clientX,py:e.clientY,ox,oy}; return; }
    // 1) 先抓「已選框」的把手,再抓「任一框」的把手(免先選取即可改大小)。角優先於邊。
    let hi=-1, hk=null;
    if(sel>=0){ const k=handleAt(boxes[sel],m); if(k){ hi=sel; hk=k; } }
    if(hk===null){ for(let i=boxes.length-1;i>=0;i--){ const k=handleAt(boxes[i],m); if(k){ hi=i; hk=k; break; } } }
    if(hk!==null){ sel=hi; snapshot(); const b=boxes[hi];
      drag={mode:'resize',i:hi,h:HE[hk],x1:b.x,y1:b.y,x2:b.x+b.w,y2:b.y+b.h}; palette(); draw(); return; }
    // 2) 框內 → 移動
    for(let i=boxes.length-1;i>=0;i--){ if(hitBox(boxes[i],m)){ sel=i; snapshot();
      drag={mode:'move',i,dx:m.x-boxes[i].x,dy:m.y-boxes[i].y}; palette(); draw(); return; } }
    // 3) 空白 → 從錨點畫新框(等同從左上角拖 BR)
    snapshot(); boxes.push({label:curClass,x:m.x,y:m.y,w:0,h:0,score:null}); sel=boxes.length-1;
    drag={mode:'new',i:sel,h:{r:1,b:1},x1:m.x,y1:m.y,x2:m.x,y2:m.y}; draw();
  });
  cv.addEventListener('pointermove', e=>{
    const r=cv.getBoundingClientRect();
    if(panning){ ox=panning.ox+(e.clientX-panning.px); oy=panning.oy+(e.clientY-panning.py); draw(); return; }
    const m=toImg(e.clientX-r.left, e.clientY-r.top);
    if(!drag){ cv.style.cursor=cursorFor(m); return; }
    if(drag.mode==='move'){ const b=boxes[drag.i];
      b.x=clamp(m.x-drag.dx, 0, CFG.iw-b.w); b.y=clamp(m.y-drag.dy, 0, CFG.ih-b.h); }
    else { applyEdges(drag, m); }              // resize / new
    draw();
  });
  function endDrag(e){
    if(e){ try{ cv.releasePointerCapture(e.pointerId); }catch(_){} }
    if(drag){ const b=boxes[drag.i];
      if(drag.mode==='new'){ if(b.w<MIN||b.h<MIN){ boxes.splice(drag.i,1); sel=-1; } }
      else if(drag.mode==='resize'){            // 縮太小夾到最小邊,不刪框;再 clamp 進圖
        if(b.w<MIN) b.w=MIN; if(b.h<MIN) b.h=MIN;
        if(b.x+b.w>CFG.iw) b.x=Math.max(0,CFG.iw-b.w);
        if(b.y+b.h>CFG.ih) b.y=Math.max(0,CFG.ih-b.h);
      }
      draw();
    }
    drag=null; panning=false;
  }
  cv.addEventListener('pointerup', endDrag);
  cv.addEventListener('pointercancel', endDrag);
  cv.addEventListener('wheel', e=>{ e.preventDefault();
    const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
    const f=e.deltaY<0?1.1:0.9, ni=toImg(mx,my); scale*=f; fitted=true;  // 手動縮放後不再自動貼齊
    const np=toScr(ni.x,ni.y); ox+=mx-np.x; oy+=my-np.y; draw();
  }, {passive:false});

  // 方向鍵微調:位置(Arrow ±1,Shift ±10)/ 大小(Alt+Arrow 改右-下邊 ±1,Shift ±10)。
  function nudgePos(dx,dy){ if(sel<0) return; snapshot(); const b=boxes[sel];
    b.x=clamp(b.x+dx,0,CFG.iw-b.w); b.y=clamp(b.y+dy,0,CFG.ih-b.h); draw(); }
  function nudgeSize(dx,dy){ if(sel<0) return; snapshot(); const b=boxes[sel];
    const x2=clamp(b.x+b.w+dx, b.x+MIN, CFG.iw), y2=clamp(b.y+b.h+dy, b.y+MIN, CFG.ih);
    b.w=x2-b.x; b.h=y2-b.y; draw(); }
  document.addEventListener('keydown', e=>{
    if(e.code==='Space'){ spaceDown=true; cv.style.cursor='grab'; }
    if(e.key==='Escape'){ if(drag){ undo(); drag=null; } else { sel=-1; } draw(); e.preventDefault(); return; }
    if(sel>=0 && e.key.indexOf('Arrow')===0){
      e.preventDefault();
      const step=e.shiftKey?10:1;
      const dx=e.key==='ArrowRight'?step:e.key==='ArrowLeft'?-step:0;
      const dy=e.key==='ArrowDown'?step:e.key==='ArrowUp'?-step:0;
      if(e.altKey) nudgeSize(dx,dy); else nudgePos(dx,dy);
      return;
    }
    if((e.key==='Delete'||e.key==='Backspace') && sel>=0){ snapshot(); boxes.splice(sel,1); sel=-1; draw(); e.preventDefault(); }
    if(e.ctrlKey && (e.key==='z'||e.key==='Z')){ undo(); e.preventDefault(); }
    if(e.key>='1' && e.key<='9'){ const i=+e.key-1; if(i<classes.length){ curClass=classes[i]; if(sel>=0){snapshot();boxes[sel].label=curClass;} palette(); draw(); } }
  });
  document.addEventListener('keyup', e=>{ if(e.code==='Space'){ spaceDown=false; cv.style.cursor='crosshair'; } });
  document.getElementById('undo').onclick=undo;
  document.getElementById('fitbtn').onclick=resetFit;

  // ── 存檔:把框回灌 parent 的隱藏 Streamlit widget(沿用 012 的 components.html 慣例)──
  document.getElementById('save').onclick=()=>{
    // payload 帶 nonce：後端看到新 nonce 才寫檔（免隱藏按鈕、免時序競態）。
    const payload = JSON.stringify({nonce: Date.now(), boxes: boxes.map(b=>({label:b.label,x:b.x,y:b.y,w:b.w,h:b.h,score:b.score}))});
    try{
      const pd = window.parent.document;
      const ta = pd.querySelector('input[aria-label="'+CFG.payloadLabel+'"]') || pd.querySelector('textarea[aria-label="'+CFG.payloadLabel+'"]');
      if(ta){
        const proto = ta.tagName==='INPUT' ? window.parent.HTMLInputElement.prototype : window.parent.HTMLTextAreaElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto,'value').set;
        ta.focus({preventScroll:true});
        setter.call(ta, payload);
        ta.dispatchEvent(new Event('input',{bubbles:true}));
        ta.dispatchEvent(new Event('change',{bubbles:true}));
        ta.blur();   // blur 才會把值 commit 給 Streamlit 後端
        document.getElementById('status').textContent='已送出存檔…';
      } else {
        document.getElementById('status').style.color='#dc2626';
        document.getElementById('status').textContent='找不到存檔橋接欄位';
      }
    }catch(err){ document.getElementById('status').style.color='#dc2626'; document.getElementById('status').textContent='存檔失敗:'+err; }
  };

  // 讓 iframe 盡量吃滿瀏覽器視窗高度(components.html 預設固定高 → 影像被壓小)。
  function resizeFrame(){
    try{
      const fe = window.frameElement;
      const vh = (window.parent && window.parent.innerHeight) || window.innerHeight || 700;
      if(fe){ fe.style.height = Math.max(420, Math.round(vh*0.82)) + 'px'; }
    }catch(e){}
  }

  // 測試/E2E 用的唯讀狀態探針(無副作用):驗證拖曳/縮放/微調是否真的改了框座標。
  try{ window.__m012canvas = { state:()=>({boxes:boxes.map(b=>({...b})), sel, scale, ox, oy, cssW, cssH}) }; }catch(_){}

  img.onload=()=>{ palette(); layout(); };
  img.src=CFG.img;
  // 版面安定 / 視窗縮放即重算(根治「初次量測太窄」競態);手動縮放後保留使用者視圖。
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
