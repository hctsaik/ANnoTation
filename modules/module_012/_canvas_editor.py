"""In-browser bbox 標註編輯器(零依賴原生 Canvas)+ Streamlit 整合。

為什麼有這個檔
--------------
工作台目前只能「看」框、要編輯就「啟動桌面工具」(X-AnyLabeling/LabelMe/ISAT)。
這支讓使用者**直接在瀏覽器裡畫/改/刪框**,存回影像旁的 ``<image>.json`` sidecar
(與 AI 預標 _run_ai_items / seeder 同一份契約)。純原生 Canvas,不引入 Konva/任何
前端套件,適合內網/離線。

兩塊:
* :func:`editor_html` — 回傳自含的編輯器 HTML(影像 + 既有框 + 類別清單注入)。
  鍵盤:1-9 設類別、Del 刪、Ctrl+Z undo、滾輪縮放、空白鍵拖曳平移。
* :func:`render_canvas_editor` — Streamlit 整合:讀既有 sidecar→框、注入編輯器、用
  隱藏 text_area + 按鈕(瀏覽器端 JS 回灌,沿用 012 既有 components.html 慣例)接住
  存檔,經 ``canvas_boxes_to_sidecar`` 寫回 sidecar。

⚠️ 瀏覽器端 JS ↔ Streamlit 的回灌橋,需在實際 app 裡 E2E 驗證(本檔的 Python 端
:func:`canvas_boxes_to_sidecar` 寫檔邏輯已有單元測試)。
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
<div id="wrap" style="font-family:sans-serif;">
  <div id="bar" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px;">
    <span style="font-size:12px;color:#475569;">類別(數字鍵切換):</span>
    <span id="palette"></span>
    <span style="flex:1"></span>
    <button id="undo" title="Ctrl+Z">↶ Undo</button>
    <button id="save" style="background:#2563eb;color:#fff;border:0;padding:4px 12px;border-radius:4px;">💾 存檔</button>
    <span id="status" style="font-size:12px;color:#16a34a;"></span>
  </div>
  <canvas id="cv" style="border:1px solid #cbd5e1;border-radius:4px;cursor:crosshair;width:100%;"></canvas>
  <div style="font-size:11px;color:#94a3b8;margin-top:4px;">
    拖曳空白處＝畫新框；點框選取後可拖曳/拉角縮放；Del 刪；滾輪縮放；空白鍵+拖曳平移。
  </div>
</div>
<script>
const CFG = __CFG__;
(function(){
  const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
  const img = new Image();
  let boxes = CFG.boxes.map(b => ({label:b.label, x:+b.x, y:+b.y, w:+b.w, h:+b.h}));
  let classes = CFG.classes.length ? CFG.classes : Array.from(new Set(boxes.map(b=>b.label))).filter(Boolean);
  if(!classes.length) classes = ['object'];
  let curClass = classes[0];
  const colors = ['#ef4444','#3b82f6','#22c55e','#f59e0b','#a855f7','#06b6d4','#ec4899','#84cc16'];
  const colorOf = name => colors[Math.max(0, classes.indexOf(name)) % colors.length];

  let scale = 1, ox = 0, oy = 0;            // 視圖縮放/平移
  let sel = -1, drag = null, panning = false, spaceDown = false;
  const hist = [];
  const snapshot = () => hist.push(JSON.stringify(boxes));
  const undo = () => { if(hist.length){ boxes = JSON.parse(hist.pop()); sel=-1; draw(); } };

  function fit(){
    const cssW = cv.clientWidth || 800;
    scale = cssW / CFG.iw; ox = 0; oy = 0;
    cv.width = cssW; cv.height = Math.round(CFG.ih * scale);
  }
  const toImg = (px,py) => ({x:(px-ox)/scale, y:(py-oy)/scale});
  const toScr = (ix,iy) => ({x:ix*scale+ox, y:iy*scale+oy});

  function draw(){
    ctx.setTransform(1,0,0,1,0,0);
    ctx.clearRect(0,0,cv.width,cv.height);
    ctx.drawImage(img, ox, oy, CFG.iw*scale, CFG.ih*scale);
    boxes.forEach((b,i)=>{
      const p = toScr(b.x,b.y);
      ctx.lineWidth = i===sel?3:2; ctx.strokeStyle = colorOf(b.label);
      ctx.strokeRect(p.x, p.y, b.w*scale, b.h*scale);
      ctx.fillStyle = colorOf(b.label);
      const tag = b.label + (i===sel?' ✦':'');
      ctx.font='12px sans-serif';
      const tw = ctx.measureText(tag).width+8;
      ctx.fillRect(p.x, p.y-16, tw, 16);
      ctx.fillStyle='#fff'; ctx.fillText(tag, p.x+4, p.y-4);
      if(i===sel){ // 角控制點
        const c = toScr(b.x+b.w, b.y+b.h);
        ctx.fillStyle='#fff'; ctx.strokeStyle='#1e293b';
        ctx.fillRect(c.x-5,c.y-5,10,10); ctx.strokeRect(c.x-5,c.y-5,10,10);
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

  function hitCorner(b,m){ const c={x:b.x+b.w,y:b.y+b.h}; return Math.abs(m.x-c.x)<8/scale && Math.abs(m.y-c.y)<8/scale; }
  function hitBox(b,m){ return m.x>=b.x && m.x<=b.x+b.w && m.y>=b.y && m.y<=b.y+b.h; }

  cv.addEventListener('mousedown', e=>{
    const r=cv.getBoundingClientRect(), m=toImg(e.clientX-r.left, e.clientY-r.top);
    if(spaceDown){ panning={px:e.clientX,py:e.clientY,ox,oy}; return; }
    if(sel>=0 && hitCorner(boxes[sel],m)){ snapshot(); drag={mode:'resize',i:sel}; return; }
    for(let i=boxes.length-1;i>=0;i--){ if(hitBox(boxes[i],m)){ sel=i; snapshot(); drag={mode:'move',i,dx:m.x-boxes[i].x,dy:m.y-boxes[i].y}; palette(); draw(); return; } }
    snapshot(); boxes.push({label:curClass,x:m.x,y:m.y,w:0,h:0}); sel=boxes.length-1; drag={mode:'new',i:sel}; draw();
  });
  cv.addEventListener('mousemove', e=>{
    const r=cv.getBoundingClientRect();
    if(panning){ ox=panning.ox+(e.clientX-panning.px); oy=panning.oy+(e.clientY-panning.py); draw(); return; }
    if(!drag) return;
    const m=toImg(e.clientX-r.left, e.clientY-r.top), b=boxes[drag.i];
    if(drag.mode==='new'||drag.mode==='resize'){ b.w=m.x-b.x; b.h=m.y-b.y; }
    else if(drag.mode==='move'){ b.x=m.x-drag.dx; b.y=m.y-drag.dy; }
    draw();
  });
  window.addEventListener('mouseup', ()=>{
    if(drag){ const b=boxes[drag.i]; if(b.w<0){b.x+=b.w;b.w=-b.w;} if(b.h<0){b.y+=b.h;b.h=-b.h;}
      if(b.w<3||b.h<3){ boxes.splice(drag.i,1); sel=-1; } draw(); }
    drag=null; panning=false;
  });
  cv.addEventListener('wheel', e=>{ e.preventDefault();
    const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
    const f=e.deltaY<0?1.1:0.9, ni=toImg(mx,my); scale*=f;
    const np=toScr(ni.x,ni.y); ox+=mx-np.x; oy+=my-np.y; draw();
  }, {passive:false});

  document.addEventListener('keydown', e=>{
    if(e.code==='Space'){ spaceDown=true; cv.style.cursor='grab'; }
    if((e.key==='Delete'||e.key==='Backspace') && sel>=0){ snapshot(); boxes.splice(sel,1); sel=-1; draw(); e.preventDefault(); }
    if(e.ctrlKey && (e.key==='z'||e.key==='Z')){ undo(); e.preventDefault(); }
    if(e.key>='1' && e.key<='9'){ const i=+e.key-1; if(i<classes.length){ curClass=classes[i]; if(sel>=0){snapshot();boxes[sel].label=curClass;} palette(); draw(); } }
  });
  document.addEventListener('keyup', e=>{ if(e.code==='Space'){ spaceDown=false; cv.style.cursor='crosshair'; } });
  document.getElementById('undo').onclick=undo;

  // ── 存檔:把框回灌 parent 的隱藏 Streamlit widget(沿用 012 的 components.html 慣例)──
  document.getElementById('save').onclick=()=>{
    // payload 帶 nonce：後端看到新 nonce 才寫檔（免隱藏按鈕、免時序競態）。
    const payload = JSON.stringify({nonce: Date.now(), boxes: boxes.map(b=>({label:b.label,x:b.x,y:b.y,w:b.w,h:b.h}))});
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

  img.onload=()=>{ fit(); palette(); draw(); };
  img.src=CFG.img;
  window.addEventListener('resize', ()=>{ fit(); draw(); });
})();
</script>
""".replace("__CFG__", cfg)


def render_canvas_editor(image_path: str, classes: list[str], *, key: str,
                         height: int = 640) -> bool:
    """在 Streamlit 渲染編輯器;存檔時寫回 sidecar。回傳 True 表示本次有存檔。

    需要 streamlit(由呼叫端 module_012 提供)。隱藏 widget 接住瀏覽器端回灌。
    """
    import streamlit as st
    import streamlit.components.v1 as components

    from plugins.labeling.domain.adapters.canvas_boxes import (
        canvas_boxes_to_sidecar,
        sidecar_to_canvas_boxes,
    )

    sidecar_path = Path(image_path).with_suffix(".json")
    existing = []
    if sidecar_path.exists():
        try:
            existing = sidecar_to_canvas_boxes(
                json.loads(sidecar_path.read_text(encoding="utf-8")))
        except Exception:
            existing = []

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
                sidecar = canvas_boxes_to_sidecar(_boxes, Path(image_path).name, iw, ih)
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
