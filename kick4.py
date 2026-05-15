#!/usr/bin/env python3
"""StreamVault v6 — by Mazen Aldeeb
   Native HLS.js player (no video.js wrapper bugs) · True mobile-first · Pro UI
"""
import os, re, time, json, base64, logging, threading
from urllib.parse import urljoin, urlparse, quote, unquote
from threading import Lock
from flask import Flask, request, jsonify, Response
import cloudscraper
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
    PW = True
except: PW = False

UA     = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
TOUT   = 14
CACHE  = 30
LEEWAY = 25
BG_INT = 45
PORT   = 5451

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
app = Flask(__name__)
sc  = cloudscraper.create_scraper(browser={"custom": UA})
sc.headers.update({"Accept":"application/json,*/*","Accept-Language":"en-US,en;q=0.9",
                   "Referer":"https://kick.com/","Origin":"https://kick.com"})

M3U8 = re.compile(r"(https?://[^\s'\"\\]+?\.m3u8[^\s'\"\\]*)", re.I)
PBK  = re.compile(r'(?:"playback_url"|playbackUrl)\s*[:=]\s*"([^"]+)"')
chs  = {}; chl = Lock(); T0 = time.time()

# ── Kick API ─────────────────────────────────────────────────
def kapi(path, p=None):
    try:
        r = sc.get(f"https://kick.com/api/v2/{path}", params=p, timeout=TOUT)
        r.raise_for_status(); return r.json()
    except Exception as e: logging.debug(f"kapi {path}: {e}"); return None

def ch_info(name):
    d = kapi(f"channels/{name}")
    if not d: return None
    user = d.get("user") or {}
    ls   = d.get("livestream") or d.get("current_livestream") or {}
    cats = (ls.get("categories") or []) if ls else []
    cat  = (cats[0].get("name","") if isinstance(cats[0],dict) else "") if cats else ""
    th   = ls.get("thumbnail") if ls else None
    thumb= (th.get("url","") if isinstance(th,dict) else th or "") if th else ""
    cr   = d.get("chatroom") or {}
    return {
        "name":name,
        "display_name": user.get("username",name) if isinstance(user,dict) else name,
        "avatar": user.get("profile_pic","") if isinstance(user,dict) else "",
        "banner": d.get("banner_image",""),
        "bio": d.get("channel_description",""),
        "followers": d.get("followersCount", d.get("followers_count",0)),
        "is_live": bool(ls),
        "viewer_count": ls.get("viewer_count",0) if ls else 0,
        "title": (ls.get("session_title") or ls.get("title","")) if ls else "",
        "category": cat,
        "language": d.get("language",""),
        "started_at": (ls.get("created_at") or ls.get("started_at","")) if ls else "",
        "thumbnail": thumb,
        "chatroom_id": cr.get("id") if isinstance(cr,dict) else None,
    }

def get_chat(name):
    try:
        r = sc.get(f"https://kick.com/api/v2/channels/{name}/messages", timeout=TOUT)
        r.raise_for_status(); data = r.json()
        raw = data.get("data",{}); 
        if isinstance(raw,dict): raw = raw.get("messages",[])
        if not isinstance(raw,list): raw = []
        out=[]
        for m in raw:
            s = m.get("sender") or {}
            if isinstance(s,str): s={"username":s}
            ident  = s.get("identity") or {}
            badges = ident.get("badges",[]) if isinstance(ident,dict) else []
            is_mod = any(b.get("type","") in ("moderator","broadcaster","channel_owner") for b in badges if isinstance(b,dict))
            is_sub = any(b.get("type","")=="subscriber" for b in badges if isinstance(b,dict))
            out.append({"id":str(m.get("id",time.time())),
                        "sender":s.get("username","?"),
                        "content":m.get("content",""),
                        "created_at":m.get("created_at",""),
                        "color":ident.get("color","") if isinstance(ident,dict) else "",
                        "is_mod":is_mod,"is_sub":is_sub})
        return out
    except Exception as e: logging.debug(f"chat {name}: {e}"); return []

def search(q="",cat="",page=1):
    try:
        if q:
            r=sc.get("https://kick.com/api/v2/search",params={"q":q,"type":"channels","page":page},timeout=TOUT)
            if r.ok:
                d=r.json(); raw=(d.get("channels") or {}).get("data",[]) if isinstance(d.get("channels"),dict) else d.get("channels",[])
                res=_norm(raw); 
                if res: return res
            info=ch_info(q.lower().replace(" ",""))
            return [_i2c(info)] if info else []
        if cat:
            r=sc.get(f"https://kick.com/api/v2/categories/{quote(cat)}/channels",params={"page":page,"limit":24},timeout=TOUT)
            if r.ok:
                d=r.json(); raw=d.get("channels",d.get("data",[]))
                return _norm(raw if isinstance(raw,list) else [])
        r=sc.get("https://kick.com/api/v2/channels",params={"page":page,"limit":24,"sort":"viewer_count"},timeout=TOUT)
        if r.ok:
            d=r.json(); raw=d if isinstance(d,list) else d.get("data",d.get("channels",[]))
            return _norm(raw)
    except Exception as e: logging.debug(f"search: {e}")
    return []

def _norm(raw):
    out=[]
    for c in (raw or []):
        if not isinstance(c,dict): continue
        slug=c.get("slug") or c.get("channel_slug") or c.get("user_username") or ""; 
        if not slug: continue
        ls=c.get("livestream") or {}
        cats=ls.get("categories",[]) if isinstance(ls,dict) else []
        cat=(cats[0].get("name","") if isinstance(cats[0],dict) else "") if cats else ""
        th=ls.get("thumbnail") if isinstance(ls,dict) else None
        thumb=(th.get("url","") if isinstance(th,dict) else th or "") if th else ""
        if not thumb:
            u=c.get("user") or {}; thumb=u.get("profile_pic","") if isinstance(u,dict) else ""
        out.append({"slug":slug,
                    "display_name":(c.get("user") or {}).get("username",slug) if isinstance(c.get("user"),dict) else slug,
                    "is_live":bool(ls) or c.get("is_live",False),
                    "viewer_count":ls.get("viewer_count",0) if isinstance(ls,dict) else 0,
                    "category":cat,
                    "title":(ls.get("session_title") or ls.get("title","")) if isinstance(ls,dict) else "",
                    "thumbnail":thumb})
    return out

def _i2c(i):
    return {"slug":i["name"],"display_name":i["display_name"],"is_live":i["is_live"],
            "viewer_count":i["viewer_count"],"category":i["category"],"title":i["title"],"thumbnail":i["thumbnail"]}

def get_clips(name,sort="view",period="all",cursor=""):
    try:
        p={"sort":sort,"time":period}
        if cursor: p["cursor"]=cursor
        r=sc.get(f"https://kick.com/api/v2/channels/{name}/clips",params=p,timeout=TOUT)
        r.raise_for_status(); d=r.json()
        obj=d.get("clips",d) if isinstance(d,dict) else {}
        lst=obj.get("data",[]) if isinstance(obj,dict) else (d if isinstance(d,list) else [])
        nxt=obj.get("next_cursor","") if isinstance(obj,dict) else ""
        out=[]
        for c in lst:
            if not isinstance(c,dict): continue
            th=c.get("thumbnail_url",c.get("thumbnail",""))
            if isinstance(th,dict): th=th.get("url","")
            out.append({"id":str(c.get("id","")),"title":c.get("title",c.get("clip_title","Untitled")),
                        "thumbnail":th,"duration":c.get("duration",0),
                        "views":c.get("views",c.get("view_count",0)),
                        "created_at":c.get("created_at",""),
                        "url":c.get("playback_url",c.get("clip_url",""))})
        return {"clips":out,"next_cursor":nxt}
    except Exception as e: logging.debug(f"clips {name}: {e}"); return {"clips":[],"next_cursor":""}

def get_vods(name,cursor=""):
    try:
        p={}
        if cursor: p["cursor"]=cursor
        r=sc.get(f"https://kick.com/api/v2/channels/{name}/videos",params=p,timeout=TOUT)
        r.raise_for_status(); d=r.json()
        obj=d.get("videos",d) if isinstance(d,dict) else {}
        lst=obj.get("data",[]) if isinstance(obj,dict) else (d if isinstance(d,list) else [])
        nxt=obj.get("next_cursor","") if isinstance(obj,dict) else ""
        out=[]
        for v in lst:
            if not isinstance(v,dict): continue
            th=v.get("thumbnail","")
            if isinstance(th,dict): th=th.get("url","")
            out.append({"id":str(v.get("id","")),"title":v.get("session_title",v.get("title","Untitled")),
                        "thumbnail":th,"duration":v.get("duration",0),
                        "views":v.get("views",v.get("view_count",0)),
                        "created_at":v.get("created_at",""),"url":v.get("source","")})
        return {"vods":out,"next_cursor":nxt}
    except Exception as e: logging.debug(f"vods {name}: {e}"); return {"vods":[],"next_cursor":""}

# ── HLS ──────────────────────────────────────────────────────
def hls_api(name):
    d=kapi(f"channels/{name}")
    if not d: return None
    ls=d.get("livestream") or d.get("current_livestream") or {}
    if ls and ls.get("playback_url"): return ls["playback_url"]
    if d.get("playback_url"): return d["playback_url"]
    for k in ("stream","current_stream","streamer_channel"):
        v=d.get(k)
        if isinstance(v,dict) and v.get("playback_url"): return v["playback_url"]
    return None

def hls_html(name):
    try:
        r=sc.get(f"https://kick.com/{name}",timeout=TOUT); r.raise_for_status()
        m=PBK.search(r.text)
        if m: return m.group(1)
        m=M3U8.search(r.text)
        if m: return m.group(1)
        soup=BeautifulSoup(r.text,"html.parser")
        for s in soup.find_all("script"):
            if s.string and "m3u8" in s.string:
                mm=M3U8.search(s.string)
                if mm: return mm.group(1)
    except Exception as e: logging.debug(f"html {name}: {e}")
    return None

def hls_pw(name):
    if not PW: return None
    try:
        with sync_playwright() as p:
            b=p.chromium.launch(headless=True,args=["--no-sandbox"])
            ctx=b.new_context(user_agent=UA); pg=ctx.new_page()
            found={"u":None}
            def on_r(resp):
                try:
                    u=resp.url
                    if ".m3u8" in u or "mpegurl" in resp.headers.get("content-type",""): found["u"]=u
                except: pass
            pg.on("response",on_r)
            pg.goto(f"https://kick.com/{name}",wait_until="networkidle",timeout=15000)
            pg.wait_for_timeout(2000); b.close(); return found["u"]
    except Exception as e: logging.debug(f"pw {name}: {e}")
    return None

def find_hls(name):
    for fn in (hls_api,hls_html,hls_pw):
        u=fn(name)
        if u and ".m3u8" in u: return u
    return None

def get_st(name):
    with chl:
        if name not in chs: chs[name]={"url":None,"exp":None,"t":0,"lk":Lock(),"vars":None,"vt":0}
        return chs[name]

def jwt_exp(u):
    try:
        if "token=" not in u: return None
        tok=u.split("token=",1)[1].split("&",1)[0]; parts=tok.split(".")
        if len(parts)<2: return None
        s=parts[1]+"="*(-len(parts[1])%4)
        pl=json.loads(base64.urlsafe_b64decode(s.encode()).decode() or "{}")
        return int(pl["exp"]) if "exp" in pl else None
    except: return None

def ensure_url(name,force=False):
    st=get_st(name); now=int(time.time())
    with st["lk"]:
        need=force or not st["url"]
        if st["exp"] and now>=st["exp"]-LEEWAY: need=True
        if not need and now-st["t"]<=CACHE: return st["url"]
        u=find_hls(name)
        if not u: return st["url"]
        st["url"],st["exp"],st["t"]=u,jwt_exp(u),now; st["vars"],st["vt"]=None,0; return st["url"]

def parse_vars(text,base):
    out=[]; lines=text.splitlines(); i=0
    while i<len(lines):
        l=lines[i].strip()
        if l.startswith("#EXT-X-STREAM-INF:"):
            a=l.split(":",1)[1]; bw=h=nm=None
            m=re.search(r"BANDWIDTH=(\d+)",a)
            if m: bw=int(m.group(1))
            m=re.search(r"RESOLUTION=\d+x(\d+)",a)
            if m: h=int(m.group(1))
            m=re.search(r'NAME="([^"]+)"',a)
            if m: nm=m.group(1)
            if i+1<len(lines):
                uri=lines[i+1].strip()
                if uri and not uri.startswith("#"): out.append({"bw":bw,"h":h,"name":nm,"uri":urljoin(base,uri)})
            i+=2; continue
        i+=1
    out.sort(key=lambda v:(v.get("h") or 0,v.get("bw") or 0),reverse=True); return out

def get_vars(name):
    st=get_st(name); now=int(time.time())
    with st["lk"]:
        if st["vars"] and now-st["vt"]<=60: return st["vars"]
    u=ensure_url(name)
    if not u: return []
    try:
        r=sc.get(u,timeout=TOUT)
        if r.status_code in(401,403): u=ensure_url(name,force=True); r=sc.get(u,timeout=TOUT)
        r.raise_for_status(); v=parse_vars(r.text,u)
        with st["lk"]: st["vars"],st["vt"]=v,now; return v
    except Exception as e: logging.debug(f"vars {name}: {e}"); return []

def bg():
    while True:
        time.sleep(BG_INT); now=int(time.time())
        with chl: names=list(chs.keys())
        for n in names:
            st=get_st(n)
            if st["url"] and st["exp"] and now>=st["exp"]-LEEWAY:
                try: ensure_url(n,force=True)
                except: pass

def norm(c):
    c=(c or "").strip().lower()
    if c.startswith("http"):
        try:
            seg=urlparse(c).path.strip("/").split("/")
            if seg and seg[0]: c=seg[0]
        except: pass
    return re.sub(r"[^a-z0-9_\-]","",c)[:64]

# ── HTML ─────────────────────────────────────────────────────
PAGE = r"""<!doctype html>
<html lang="ar" dir="ltr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="theme-color" content="#060d1a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<title>StreamVault</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   DESIGN SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
:root {
  /* colors */
  --bg:   #060d1a;
  --s1:   #0a1426;
  --s2:   #0f1d33;
  --s3:   #162540;
  --s4:   #1d2e4e;
  --s5:   #243659;
  --b1:rgba(255,255,255,.05);
  --b2:rgba(255,255,255,.09);
  --b3:rgba(255,255,255,.16);
  --g:    #5bff20;
  --g2:   #48d918;
  --gd:rgba(91,255,32,.16);
  --gd2:rgba(91,255,32,.06);
  --red:  #ff4160;
  --redb:rgba(255,65,96,.15);
  --gold: #ffc740;
  --sky:  #38c6ff;
  --purp: #a78bfa;
  --t1:   #d6ebfa;
  --t2:   #6e9ab5;
  --t3:   #36566e;
  --t4:   #1c3244;
  /* radius */
  --r4:4px;--r8:8px;--r12:12px;--r16:16px;--r24:24px;--rpill:999px;
  /* spacing */
  --hh:52px;
  --sbw:252px;
  --chw:290px;
  /* shadows */
  --sh1:0 2px 12px rgba(0,0,0,.4);
  --sh2:0 8px 32px rgba(0,0,0,.6);
  --sh3:0 16px 60px rgba(0,0,0,.8);
}

/* ━━━ RESET ━━━ */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html{height:100%;background:var(--bg);overscroll-behavior:none}
body{min-height:100%;font-family:'Outfit',system-ui,sans-serif;background:var(--bg);color:var(--t1);-webkit-font-smoothing:antialiased;overflow-x:hidden}
button{cursor:pointer;border:none;background:none;color:inherit;font-family:inherit;touch-action:manipulation;-webkit-user-select:none;user-select:none}
input,select,textarea{font-family:inherit;background:none;-webkit-appearance:none;appearance:none}
a{color:inherit;text-decoration:none}
img{display:block;max-width:100%}
::-webkit-scrollbar{width:3px;height:3px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--s5);border-radius:2px}
::-webkit-scrollbar-thumb:hover{background:var(--g)}

/* ━━━ APP SHELL ━━━ */
#app{display:flex;flex-direction:column;height:100dvh;min-height:0}

/* ━━━ TOPBAR ━━━ */
#bar {
  height:var(--hh);flex-shrink:0;
  background:var(--s1);border-bottom:1px solid var(--b2);
  display:flex;align-items:center;gap:8px;padding:0 10px;
  position:relative;z-index:200;
}
.logo{font-size:17px;font-weight:900;letter-spacing:-.8px;white-space:nowrap;flex-shrink:0;line-height:1}
.logo-g{background:linear-gradient(130deg,var(--g) 0%,#a5ff6b 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.logo-s{font-size:9px;font-weight:500;color:var(--t3);letter-spacing:2px;display:block;margin-top:-1px;-webkit-text-fill-color:var(--t3)}
.sbar{
  flex:1;min-width:0;max-width:420px;
  display:flex;align-items:center;
  background:var(--s3);border:1.5px solid var(--b2);
  border-radius:var(--rpill);overflow:hidden;
  transition:border-color .2s,box-shadow .2s;
}
.sbar:focus-within{border-color:var(--g);box-shadow:0 0 0 3px rgba(91,255,32,.12)}
.sbar input{
  flex:1;min-width:0;border:none;background:none;color:var(--t1);
  padding:9px 14px;font-size:13px;font-weight:500;
}
.sbar input::placeholder{color:var(--t3)}
.sbar .go{
  background:var(--g);color:#040c00;font-size:12px;font-weight:700;
  width:34px;height:34px;border-radius:50%;margin:2px;
  display:flex;align-items:center;justify-content:center;
  flex-shrink:0;transition:.15s;
}
.sbar .go:active,.sbar .go:hover{background:var(--g2);transform:scale(1.06)}
.acts{display:flex;gap:4px;margin-left:auto;align-items:center;flex-shrink:0}
.ab{
  display:flex;align-items:center;gap:5px;
  padding:6px 9px;border-radius:var(--r8);
  font-size:11.5px;font-weight:600;color:var(--t2);
  border:1.5px solid var(--b1);transition:.15s;
}
.ab:hover,.ab.on{background:var(--s3);border-color:var(--b2);color:var(--t1)}
.ab.on{color:var(--g);border-color:var(--gd)}
.ab .lbl{display:none}
@media(min-width:520px){.ab .lbl{display:inline}}
.qsel{background:var(--s3);border:1.5px solid var(--b1);color:var(--t2);padding:6px 10px;border-radius:var(--r8);font-size:11px;cursor:pointer;font-family:'Outfit',sans-serif}

/* ━━━ BODY ━━━ */
#body{flex:1;display:flex;min-height:0;overflow:hidden}

/* ━━━ SIDEBAR ━━━ */
#sb{
  width:var(--sbw);flex-shrink:0;
  background:var(--s1);border-right:1px solid var(--b1);
  display:flex;flex-direction:column;
  overflow:hidden;
}
.sb-inner{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:8px 6px}
.sb-sec-label{
  font-size:9.5px;font-weight:800;letter-spacing:2.2px;
  text-transform:uppercase;color:var(--t3);
  padding:8px 8px 5px;
}
.ch-row{
  display:flex;align-items:center;gap:8px;
  padding:7px 8px;border-radius:var(--r12);cursor:pointer;
  transition:background .12s;position:relative;
}
.ch-row:hover{background:var(--s3)}
.ch-row.active{background:var(--gd2);outline:1px solid rgba(91,255,32,.14)}
.av{
  width:33px;height:33px;border-radius:50%;flex-shrink:0;
  background:var(--s4);position:relative;overflow:hidden;
  display:flex;align-items:center;justify-content:center;
  font-size:12px;font-weight:700;color:var(--t2);
}
.av img{width:100%;height:100%;object-fit:cover;border-radius:50%}
.av-ring{
  position:absolute;inset:-2px;border-radius:50%;
  border:2.5px solid var(--red);display:none;
  animation:avring 2s ease-in-out infinite;
}
.av.live .av-ring{display:block}
@keyframes avring{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(1.06)}}
.ch-inf{flex:1;min-width:0}
.ch-nm{font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.3}
.ch-sub{font-size:10px;color:var(--t3);margin-top:1px;display:flex;gap:4px;align-items:center;overflow:hidden}
.ltag{background:var(--red);color:#fff;font-size:7.5px;font-weight:800;padding:1px 4px;border-radius:3px;letter-spacing:.5px;flex-shrink:0}
.vn{color:var(--g);font-weight:700;font-size:10px}
.del-btn{opacity:0;color:var(--t3);padding:3px 5px;border-radius:5px;font-size:10px;flex-shrink:0;transition:.12s}
.ch-row:hover .del-btn{opacity:1}
.del-btn:hover{color:var(--red);background:var(--redb)}
.sb-div{height:1px;background:var(--b1);margin:6px 8px}
.sb-link{
  display:flex;align-items:center;gap:7px;padding:7px 10px;
  border-radius:var(--r12);font-size:11.5px;color:var(--t2);
  transition:.12s;margin:1px 2px;
}
.sb-link:hover{background:var(--s3);color:var(--t1)}
.sb-link i{width:14px;text-align:center;font-size:12px}
#sb-foot{padding:8px 12px;border-top:1px solid var(--b1);font-size:9.5px;color:var(--t3);text-align:center;line-height:1.8;flex-shrink:0}

/* ━━━ CONTENT ━━━ */
#cnt{flex:1;display:flex;flex-direction:column;min-width:0;overflow:hidden}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   VIDEO — THE FIX
   No video.js wrapper, pure HTML5 video
   with HLS.js injected as source
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
#vwrap{
  flex-shrink:0;
  background:#000;
  position:relative;
  width:100%;
  /* Enforce 16:9 via aspect-ratio — works on ALL modern mobile browsers */
  aspect-ratio:16/9;
  max-height:56vw; /* fallback */
}
#vid{
  display:block;
  width:100%;
  height:100%;
  position:absolute;
  inset:0;
  object-fit:contain;
  background:#000;
}
/* Empty state overlay */
#vempty{
  position:absolute;inset:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:10px;
  background:var(--s2);color:var(--t3);z-index:2;
}
#vempty i{font-size:44px;color:var(--s5)}
#vempty p{font-size:13px;font-weight:500;color:var(--t2)}
#vempty small{font-size:11px;opacity:.6}
/* Loading state */
#vload{
  position:absolute;inset:0;display:none;flex-direction:column;
  align-items:center;justify-content:center;background:rgba(0,0,0,.7);
  z-index:3;gap:10px;
}
#vload.show{display:flex}
.vspinner{width:36px;height:36px;border:3px solid rgba(91,255,32,.2);border-top-color:var(--g);border-radius:50%;animation:vspin .7s linear infinite}
@keyframes vspin{to{transform:rotate(360deg)}}
#vload span{font-size:12px;color:var(--t2);font-weight:500}
/* Video controls overlay */
#vctrl{
  position:absolute;top:8px;right:8px;
  display:flex;gap:5px;z-index:5;
  opacity:0;transition:opacity .25s;
}
#vwrap:hover #vctrl,#vwrap:focus-within #vctrl{opacity:1}
/* Show controls on mobile always */
@media(max-width:768px){ #vctrl{opacity:1} }
.vc{
  width:32px;height:32px;border-radius:var(--r8);
  background:rgba(0,0,0,.65);backdrop-filter:blur(8px);
  border:1px solid rgba(255,255,255,.14);color:#fff;
  display:flex;align-items:center;justify-content:center;
  font-size:12px;transition:.15s;
}
.vc:hover,.vc:active{background:var(--g);color:#040c00;border-color:var(--g)}
/* Progress bar (custom) */
#vprog{
  position:absolute;bottom:0;left:0;right:0;height:3px;background:rgba(255,255,255,.15);cursor:pointer;z-index:4;
}
#vprog-fill{height:100%;background:var(--g);width:0;transition:width .5s linear;pointer-events:none}
/* Volume slider bottom */
#vbar{
  position:absolute;bottom:0;left:0;right:0;z-index:5;
  background:linear-gradient(transparent,rgba(0,0,0,.85));
  padding:20px 10px 8px;display:flex;align-items:center;gap:8px;
  opacity:0;transition:opacity .25s;
}
#vwrap:hover #vbar{opacity:1}
@media(max-width:768px){ #vbar{opacity:0!important} }/* native controls on mobile */
.vbar-btn{color:#fff;font-size:14px;padding:2px 5px;transition:.15s}
.vbar-btn:hover{color:var(--g)}
#vol-inp{width:70px;-webkit-appearance:none;appearance:none;height:3px;border-radius:2px;background:rgba(255,255,255,.3);cursor:pointer}
#vol-inp::-webkit-slider-thumb{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;background:var(--g);cursor:pointer}
.vbar-time{color:rgba(255,255,255,.7);font-size:11px;font-weight:600;margin-left:auto}

/* ━━━ INFO BAR ━━━ */
#ibar{background:var(--s1);border-bottom:1px solid var(--b1);padding:10px 12px;display:none}
#ibar.show{display:block}
.ib-r{display:flex;gap:9px;align-items:flex-start}
.ib-av{width:38px;height:38px;border-radius:50%;flex-shrink:0;background:var(--s4);overflow:hidden}
.ib-av img{width:100%;height:100%;object-fit:cover}
.ib-bd{flex:1;min-width:0}
.ib-nm{font-size:15px;font-weight:800;line-height:1.2}
.ib-tt{font-size:11.5px;color:var(--t2);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tags{display:flex;gap:5px;margin-top:6px;flex-wrap:wrap}
.tag{padding:3px 8px;border-radius:5px;font-size:10px;font-weight:700;display:inline-flex;align-items:center;gap:3px}
.tg-live{background:var(--red);color:#fff}
.tg-live::before{content:'';width:5px;height:5px;border-radius:50%;background:#fff;animation:tdot 1.1s ease infinite}
@keyframes tdot{0%,100%{opacity:1}50%{opacity:.2}}
.tg-v{background:var(--gd);color:var(--g);border:1px solid rgba(91,255,32,.2)}
.tg-c{background:var(--s3);color:var(--t2);border:1px solid var(--b1)}
.ibar-acts{display:flex;gap:5px;margin-top:8px;flex-wrap:wrap}

/* ━━━ BUTTONS ━━━ */
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 13px;border-radius:var(--r12);font-size:12px;font-weight:600;border:1.5px solid transparent;transition:.15s;cursor:pointer;font-family:'Outfit',sans-serif;white-space:nowrap}
.btn:active{transform:scale(.96)}
.b-g{background:var(--g);color:#040c00}.b-g:hover{background:var(--g2)}
.b-o{background:var(--s3);color:var(--t2);border-color:var(--b1)}.b-o:hover{border-color:var(--b2);color:var(--t1)}
.b-r{background:var(--redb);color:var(--red);border-color:rgba(255,65,96,.22)}.b-r:hover{background:rgba(255,65,96,.25)}
.b-sm{padding:5px 10px;font-size:11px}

/* ━━━ TABS ━━━ */
#tabs{flex-shrink:0;background:var(--s1);border-bottom:1px solid var(--b1);display:flex;overflow-x:auto;scrollbar-width:none}
#tabs::-webkit-scrollbar{display:none}
.tab{padding:10px 14px;font-size:12px;font-weight:600;color:var(--t3);border-bottom:2px solid transparent;transition:.15s;white-space:nowrap;display:flex;align-items:center;gap:5px;flex-shrink:0}
.tab:hover{color:var(--t1)}
.tab.on{color:var(--g);border-bottom-color:var(--g)}

/* ━━━ PANELS ━━━ */
#panels{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch}
.pnl{display:none;padding:12px}
.pnl.on{display:block;animation:pup .15s ease}
@keyframes pup{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}

/* Stats */
.sg{display:grid;grid-template-columns:repeat(auto-fill,minmax(105px,1fr));gap:8px;margin-bottom:12px}
.sc{background:var(--s2);border:1px solid var(--b1);border-radius:var(--r16);padding:12px;text-align:center;transition:.15s}
.sc:hover{border-color:var(--b2);transform:translateY(-2px);box-shadow:var(--sh1)}
.sv{font-size:21px;font-weight:900;color:var(--g);line-height:1.2}
.sl{font-size:9px;color:var(--t3);margin-top:3px;text-transform:uppercase;letter-spacing:1.3px;font-weight:700}
.cbox{background:var(--s2);border:1px solid var(--b1);border-radius:var(--r16);padding:12px;margin-bottom:10px}
.clbl{font-size:9.5px;font-weight:800;color:var(--t3);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:8px}
canvas#chart{display:block;width:100%!important;height:58px}
.ibox{background:var(--s2);border:1px solid var(--b1);border-radius:var(--r16);overflow:hidden}
.ir{display:flex;justify-content:space-between;align-items:center;padding:9px 12px;border-bottom:1px solid var(--b1);font-size:12px}
.ir:last-child{border:none}
.ik{color:var(--t3);font-weight:500}.iv{color:var(--t1);font-weight:600;text-align:right;max-width:60%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* Media grid */
.tbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.sl2{background:var(--s3);border:1.5px solid var(--b1);color:var(--t2);padding:6px 10px;border-radius:var(--r8);font-size:11px;cursor:pointer;font-family:'Outfit',sans-serif}
.mg{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.mc{background:var(--s2);border:1px solid var(--b1);border-radius:var(--r16);overflow:hidden;cursor:pointer;transition:.18s}
.mc:hover{border-color:var(--b2);transform:translateY(-3px);box-shadow:var(--sh2)}
.mc:active{transform:scale(.97)}
.mth{position:relative;aspect-ratio:16/9;background:var(--s3);overflow:hidden}
.mth img{width:100%;height:100%;object-fit:cover;transition:.3s}
.mc:hover .mth img{transform:scale(1.06)}
.mplay{position:absolute;inset:0;background:rgba(0,0,0,.42);display:flex;align-items:center;justify-content:center;opacity:0;transition:.2s}
.mplay i{font-size:28px;color:#fff;filter:drop-shadow(0 2px 8px rgba(0,0,0,.9))}
.mc:hover .mplay,.mc:active .mplay{opacity:1}
.mdur{position:absolute;bottom:5px;right:5px;background:rgba(0,0,0,.82);color:#fff;font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700}
.mbody{padding:8px 10px}
.mtt{font-size:11px;font-weight:500;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.mmeta{font-size:10px;color:var(--t3);margin-top:4px;display:flex;justify-content:space-between}
.lm{display:block;width:100%;padding:10px;margin-top:10px;text-align:center;border-radius:var(--r12);background:var(--s3);border:1.5px solid var(--b1);color:var(--t2);font-size:12px;font-weight:600;transition:.15s;cursor:pointer}
.lm:hover{border-color:var(--b2);color:var(--t1)}.lm.h{display:none}

/* Empty & spinner */
.empty{padding:30px 16px;text-align:center;color:var(--t3)}
.empty i{font-size:32px;display:block;margin-bottom:8px;color:var(--s5)}
.empty p{font-size:12px}
.sp{display:flex;justify-content:center;padding:18px}
.spin{width:22px;height:22px;border:2px solid var(--s5);border-top-color:var(--g);border-radius:50%;animation:vspin .65s linear infinite}

/* Channel cards */
.cg{display:grid;grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:8px}
.cc{background:var(--s2);border:1px solid var(--b1);border-radius:var(--r16);overflow:hidden;cursor:pointer;transition:.18s}
.cc:hover{border-color:var(--b2);transform:translateY(-3px);box-shadow:var(--sh2)}
.cc:active{transform:scale(.97)}
.cct{position:relative;aspect-ratio:16/9;background:var(--s3);overflow:hidden}
.cct img{width:100%;height:100%;object-fit:cover}
.cct .ni{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:24px;color:var(--t3)}
.cct .lb{position:absolute;top:5px;left:5px;background:var(--red);color:#fff;font-size:8px;font-weight:800;padding:2px 5px;border-radius:4px;letter-spacing:.5px}
.cct .vc{position:absolute;bottom:4px;right:5px;background:rgba(0,0,0,.82);color:#fff;font-size:9px;padding:1px 5px;border-radius:3px;border:none}
.ccb{padding:8px 10px}
.ccn{font-size:12px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ccc{font-size:10px;color:var(--t3);margin-top:2px}
.cct2{font-size:10px;color:var(--t2);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cat-row{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.cat-b{padding:6px 12px;border-radius:var(--rpill);font-size:11px;font-weight:600;border:1.5px solid var(--b1);color:var(--t3);transition:.15s;cursor:pointer}
.cat-b:hover{border-color:var(--b2);color:var(--t1)}
.cat-b.on{background:var(--gd2);border-color:rgba(91,255,32,.28);color:var(--g)}

/* Cards */
.card{background:var(--s2);border:1px solid var(--b1);border-radius:var(--r16);padding:13px;margin-bottom:10px}
.card-h{font-size:11px;font-weight:700;color:var(--t2);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.inp{background:var(--s3);border:1.5px solid var(--b1);color:var(--t1);padding:8px 11px;border-radius:var(--r12);font-size:12px;transition:.15s;font-family:'Outfit',sans-serif;outline:none}
.inp:focus{border-color:var(--g);box-shadow:0 0 0 3px rgba(91,255,32,.09)}
.code{background:var(--bg);border:1px solid var(--b1);border-radius:var(--r12);padding:10px 12px;font-family:'Courier New',monospace;font-size:10.5px;color:var(--g);word-break:break-all;line-height:1.7;display:none;margin-top:8px}
textarea.inp{width:100%;min-height:88px;resize:vertical;line-height:1.6}
.row{display:flex;gap:7px;flex-wrap:wrap;align-items:center}
.ns{font-size:11px;color:var(--g);margin-top:6px;display:none}

/* Schedule */
.shi{background:var(--s2);border:1px solid var(--b1);border-radius:var(--r16);padding:12px 14px;display:flex;align-items:center;gap:11px;margin-bottom:7px;transition:.15s}
.shi:hover{border-color:var(--b2)}
.sh-t{font-size:14px;font-weight:900;color:var(--g);min-width:62px}
.sh-b{flex:1}.sh-n{font-size:12px;font-weight:600}
.sh-s{font-size:10px;color:var(--t3);margin-top:2px}

/* ━━━ CHAT ━━━ */
#chat{
  width:var(--chw);flex-shrink:0;
  background:var(--s1);border-left:1px solid var(--b1);
  display:flex;flex-direction:column;min-height:0;overflow:hidden;
}
#ch-hd{padding:9px 10px;border-bottom:1px solid var(--b1);display:flex;align-items:center;gap:7px;flex-shrink:0}
.cldot{width:6px;height:6px;border-radius:50%;background:var(--t3);flex-shrink:0;transition:.3s}
.cldot.on{background:var(--red);animation:cldot 1.5s ease infinite}
@keyframes cldot{0%,100%{box-shadow:0 0 0 0 rgba(255,65,96,.5)}60%{box-shadow:0 0 0 5px rgba(255,65,96,0)}}
.chlbl{font-weight:700;font-size:12px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ib{width:26px;height:26px;border-radius:var(--r8);display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--t3);transition:.12s;flex-shrink:0}
.ib:hover{background:var(--s3);color:var(--t1)}
#ch-flt{padding:5px 7px;border-bottom:1px solid var(--b1);display:flex;gap:3px;flex-shrink:0;flex-wrap:wrap}
.ff{padding:3px 8px;border-radius:5px;font-size:10px;font-weight:600;border:1.5px solid var(--b1);color:var(--t3);transition:.12s;cursor:pointer}
.ff.on,.ff:hover{color:var(--g);border-color:rgba(91,255,32,.3);background:var(--gd2)}
#embox{padding:5px 7px;border-bottom:1px solid var(--b1);display:none;flex-wrap:wrap;gap:3px;flex-shrink:0}
#embox.on{display:flex}
.em{font-size:16px;cursor:pointer;padding:3px 4px;border-radius:4px;line-height:1;transition:.12s}
.em:hover,.em:active{background:var(--s3);transform:scale(1.3)}
.estr{font-size:9.5px;font-weight:700;cursor:pointer;padding:3px 7px;border-radius:4px;background:var(--s3);color:var(--t2);border:1px solid var(--b1);transition:.12s}
.estr:hover,.estr:active{color:var(--g);border-color:rgba(91,255,32,.3)}
#msgs{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:5px 3px;display:flex;flex-direction:column;gap:1px;min-height:0}
#msgs::-webkit-scrollbar{width:2px}
.msg{display:flex;gap:4px;padding:4px 6px;border-radius:6px;font-size:11.5px;line-height:1.45;align-items:flex-start;transition:.08s}
.msg:hover{background:rgba(255,255,255,.025)}
.msg.hl{background:rgba(91,255,32,.07);border-left:2px solid var(--g)}
.msg.mod{border-left:2px solid var(--sky)}
.mbgs{display:flex;gap:2px;align-items:center;flex-shrink:0;padding-top:1px}
.mbg{width:12px;height:12px;border-radius:3px;display:flex;align-items:center;justify-content:center;font-size:7px}
.mbg-m{background:rgba(56,198,255,.2);color:var(--sky)}
.mbg-s{background:rgba(167,139,250,.2);color:var(--purp)}
.muser{font-weight:700;flex-shrink:0}
.msep{color:var(--t3);font-size:10px;flex-shrink:0}
.mtxt{color:var(--t1);word-break:break-word;flex:1}
.mts{font-size:9px;color:var(--t3);align-self:flex-end;flex-shrink:0}
#ch-st{padding:3px 10px;font-size:10px;color:var(--t3);flex-shrink:0}
#ch-inp-w{padding:6px 7px;border-top:1px solid var(--b1);display:flex;gap:5px;flex-shrink:0}
.cinp{flex:1;min-width:0;background:var(--s3);border:1.5px solid var(--b1);color:var(--t1);padding:7px 11px;border-radius:var(--rpill);font-size:12px;transition:.15s;font-family:'Outfit',sans-serif;outline:none}
.cinp:focus{border-color:var(--g);box-shadow:0 0 0 2px rgba(91,255,32,.1)}
.csend{background:var(--g);color:#040c00;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0;transition:.15s}
.csend:hover,.csend:active{background:var(--g2);transform:scale(1.08)}

/* ━━━ TOAST ━━━ */
#toasts{position:fixed;top:58px;right:10px;z-index:9999;display:flex;flex-direction:column;gap:5px;max-width:270px;pointer-events:none}
.toast{background:var(--s3);border:1.5px solid var(--b2);border-radius:var(--r12);padding:10px 12px;display:flex;gap:8px;align-items:center;box-shadow:var(--sh3);animation:tin .22s cubic-bezier(.34,1.56,.64,1);pointer-events:all;font-size:12px;font-weight:500}
.toast.g{border-color:rgba(91,255,32,.3)}.toast.r{border-color:rgba(255,65,96,.3)}.toast.y{border-color:rgba(255,199,64,.3)}
@keyframes tin{from{transform:translateX(120%);opacity:0}to{transform:none;opacity:1}}
.ti{font-size:15px}.tm{flex:1;color:var(--t1);line-height:1.4}.tx{color:var(--t3);font-size:14px;padding:0 3px}
.tx:hover{color:var(--t1)}

/* ━━━ OVERLAYS (mobile drawers) ━━━ */
.ovl{display:none;position:fixed;inset:0;background:rgba(0,0,0,.68);z-index:290;backdrop-filter:blur(3px)}
.ovl.on{display:block}

/* ━━━ MODALS ━━━ */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.78);z-index:400;align-items:center;justify-content:center;backdrop-filter:blur(6px);padding:14px}
.modal.on{display:flex;animation:mfade .18s ease}
@keyframes mfade{from{opacity:0}to{opacity:1}}
.modal-box{background:var(--s2);border:1.5px solid var(--b2);border-radius:var(--r16);width:100%;max-width:440px;max-height:88vh;overflow-y:auto;animation:pup .18s ease}
.modal-hd{padding:14px 16px;border-bottom:1px solid var(--b1);display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;background:var(--s2);z-index:1}
.modal-hd h3{font-size:14px;font-weight:800}
.modal-hd .mx{color:var(--t3);font-size:17px;padding:2px 7px}
.modal-hd .mx:hover{color:var(--t1)}
.modal-body{padding:14px 16px}

/* Toggle */
.tog{width:38px;height:20px;border-radius:10px;background:var(--s5);position:relative;cursor:pointer;transition:.22s;border:none;flex-shrink:0}
.tog.on{background:var(--g)}
.tog::after{content:'';position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;transition:.22s;box-shadow:var(--sh1)}
.tog.on::after{left:20px}
.set-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--b1)}
.set-row:last-of-type{border:none}
.set-l{font-size:12.5px;font-weight:600}
.set-d{font-size:10px;color:var(--t3);margin-top:2px}

/* ━━━ RESPONSIVE ━━━ */
@media(max-width:900px){
  #sb{position:fixed;left:0;top:0;bottom:0;height:100dvh;z-index:300;transform:translateX(-100%);transition:transform .28s cubic-bezier(.4,0,.2,1)}
  #sb.on{transform:none;box-shadow:var(--sh3)}
  #chat{position:fixed;right:0;top:0;bottom:0;height:100dvh;z-index:300;transform:translateX(100%);transition:transform .28s cubic-bezier(.4,0,.2,1)}
  #chat.on{transform:none;box-shadow:var(--sh3)}
}
@media(max-width:600px){
  :root{--hh:48px}
  .logo-s{display:none}
  .sg{grid-template-columns:repeat(3,1fr)}
  .mg{grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:6px}
  .cg{grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:6px}
}
@media(max-width:360px){
  .mg{grid-template-columns:1fr 1fr}
  .cg{grid-template-columns:1fr 1fr}
  .sg{grid-template-columns:1fr 1fr}
}
/* Safe area for notched phones */
@supports(padding-bottom:env(safe-area-inset-bottom)){
  #bar{padding-left:max(10px,env(safe-area-inset-left));padding-right:max(10px,env(safe-area-inset-right))}
}
</style>
</head>
<body>
<div id="toasts"></div>
<div class="ovl" id="ov-sb" onclick="closeSb()"></div>
<div class="ovl" id="ov-ch" onclick="closeCh()"></div>

<div id="app">
<!-- TOPBAR -->
<header id="bar">
  <button class="ab" style="padding:6px 8px" onclick="toggleSb()"><i class="fas fa-bars"></i></button>
  <div class="logo"><span class="logo-g">StreamVault</span><span class="logo-s">BY MAZEN ALDEEB</span></div>
  <div class="sbar">
    <input id="inp" type="search" placeholder="Channel name or URL…" autocomplete="off" spellcheck="false">
    <button class="go" onclick="doPlay()"><i class="fas fa-play"></i></button>
  </div>
  <div class="acts">
    <select class="qsel" id="qsel"><option value="auto">Auto</option></select>
    <button class="ab" id="b-br" onclick="goBrowse()"><i class="fas fa-compass"></i><span class="lbl"> Browse</span></button>
    <button class="ab" onclick="openMulti()"><i class="fas fa-th-large"></i></button>
    <button class="ab" onclick="openSettings()"><i class="fas fa-sliders-h"></i></button>
    <button class="ab" id="b-ch" onclick="toggleCh()"><i class="fas fa-comments"></i><span class="lbl"> Chat</span></button>
  </div>
</header>

<div id="body">
<!-- SIDEBAR -->
<nav id="sb">
  <div class="sb-inner">
    <div class="sb-sec-label">Following</div>
    <div id="favs-list"></div>
    <button class="sb-link" onclick="goBrowse()"><i class="fas fa-plus-circle" style="color:var(--g)"></i> Browse Channels</button>
    <div class="sb-div"></div>
    <div class="sb-sec-label">Recent</div>
    <div id="hist-list"></div>
    <button class="sb-link" onclick="clrHist()"><i class="fas fa-trash-alt" style="color:var(--t3)"></i> Clear History</button>
  </div>
  <div id="sb-foot">StreamVault v6<br><span id="uptime">—</span></div>
</nav>

<!-- CONTENT -->
<div id="cnt">
  <!-- BROWSE PAGE -->
  <div id="pg-browse" style="display:none;flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:12px">
    <div class="cat-row" id="cat-row">
      <button class="cat-b on" data-c="" onclick="setCat(this,'')">🔥 Top Live</button>
      <button class="cat-b" data-c="just-chatting" onclick="setCat(this,'just-chatting')">💬 Just Chatting</button>
      <button class="cat-b" data-c="gaming" onclick="setCat(this,'gaming')">🎮 Gaming</button>
      <button class="cat-b" data-c="music" onclick="setCat(this,'music')">🎵 Music</button>
      <button class="cat-b" data-c="sports" onclick="setCat(this,'sports')">⚽ Sports</button>
      <button class="cat-b" data-c="irl" onclick="setCat(this,'irl')">📷 IRL</button>
      <button class="cat-b" data-c="crypto" onclick="setCat(this,'crypto')">💰 Crypto</button>
      <button class="cat-b" data-c="art" onclick="setCat(this,'art')">🎨 Art</button>
    </div>
    <div class="sp" id="br-spin" style="display:none"><div class="spin"></div></div>
    <div id="br-grid" class="cg"></div>
    <button id="br-more" class="lm h" onclick="browseMore()"><i class="fas fa-chevron-down"></i> Load More</button>
  </div>

  <!-- PLAYER PAGE -->
  <div id="pg-player" style="display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden">
    <!-- VIDEO -->
    <div id="vwrap">
      <div id="vempty">
        <i class="fas fa-satellite-dish"></i>
        <p>Enter a channel name to watch</p>
        <small>Supports kick.com streams</small>
      </div>
      <div id="vload"><div class="vspinner"></div><span id="vload-txt">Loading stream…</span></div>
      <!-- Native video element — works on ALL devices -->
      <video id="vid" playsinline
        style="display:none"
        x-webkit-airplay="allow"
        webkit-playsinline="true">
      </video>
      <div id="vctrl">
        <button class="vc" onclick="doPip()" title="PiP"><i class="fas fa-external-link-alt"></i></button>
        <button class="vc" onclick="doFull()" title="Fullscreen"><i class="fas fa-expand" id="fs-ico"></i></button>
        <button class="vc" onclick="doShot()" title="Screenshot"><i class="fas fa-camera"></i></button>
        <button class="vc" id="mute-btn" onclick="doMute()" title="Mute"><i class="fas fa-volume-up" id="vol-ico"></i></button>
      </div>
      <div id="vbar">
        <button class="vbar-btn" onclick="togglePlay()"><i class="fas fa-pause" id="play-ico"></i></button>
        <input type="range" id="vol-inp" min="0" max="1" step="0.05" value="1" oninput="setVol(this.value)">
        <span class="vbar-time" id="vtime">LIVE</span>
      </div>
    </div>

    <!-- INFO BAR -->
    <div id="ibar">
      <div class="ib-r">
        <div class="ib-av" id="ib-av"><img id="ib-aimg" style="display:none" alt=""></div>
        <div class="ib-bd">
          <div class="ib-nm" id="ib-nm">—</div>
          <div class="ib-tt" id="ib-tt">—</div>
          <div class="tags">
            <span class="tag tg-live">LIVE</span>
            <span class="tag tg-v" id="ib-v">— viewers</span>
            <span class="tag tg-c" id="ib-c">—</span>
          </div>
        </div>
      </div>
      <div class="ibar-acts">
        <button class="btn b-g b-sm" onclick="followNow()"><i class="fas fa-star"></i> Follow</button>
        <button class="btn b-o b-sm" onclick="doShot()"><i class="fas fa-camera"></i></button>
        <button class="btn b-o b-sm" onclick="cpM3u8()"><i class="fas fa-link"></i> M3U8</button>
        <button class="btn b-o b-sm" onclick="goTab('clips')"><i class="fas fa-film"></i> Clips</button>
      </div>
    </div>

    <!-- TABS -->
    <div id="tabs">
      <button class="tab on" data-t="stats" onclick="goTab('stats')"><i class="fas fa-chart-line"></i> Stats</button>
      <button class="tab" data-t="clips" onclick="goTab('clips')"><i class="fas fa-film"></i> Clips</button>
      <button class="tab" data-t="vods"  onclick="goTab('vods')"><i class="fas fa-video"></i> VODs</button>
      <button class="tab" data-t="sched" onclick="goTab('sched')"><i class="fas fa-calendar"></i> Schedule</button>
      <button class="tab" data-t="exp"   onclick="goTab('exp')"><i class="fas fa-download"></i> Export</button>
      <button class="tab" data-t="notes" onclick="goTab('notes')"><i class="fas fa-note-sticky"></i> Notes</button>
    </div>

    <!-- PANELS -->
    <div id="panels">
      <div class="pnl on" id="pnl-stats">
        <div class="sg">
          <div class="sc"><div class="sv" id="sv-v">—</div><div class="sl">Viewers</div></div>
          <div class="sc"><div class="sv" id="sv-f">—</div><div class="sl">Followers</div></div>
          <div class="sc"><div class="sv" id="sv-d">—</div><div class="sl">Duration</div></div>
          <div class="sc"><div class="sv" id="sv-q">—</div><div class="sl">Quality</div></div>
        </div>
        <div class="cbox"><div class="clbl">Viewer Trend (last 40 polls)</div><canvas id="chart"></canvas></div>
        <div class="ibox">
          <div class="ir"><span class="ik">Channel</span><span class="iv" id="ii-ch">—</span></div>
          <div class="ir"><span class="ik">Title</span><span class="iv" id="ii-t">—</span></div>
          <div class="ir"><span class="ik">Category</span><span class="iv" id="ii-c">—</span></div>
          <div class="ir"><span class="ik">Language</span><span class="iv" id="ii-l">—</span></div>
          <div class="ir"><span class="ik">Started</span><span class="iv" id="ii-s">—</span></div>
          <div class="ir"><span class="ik">Followers</span><span class="iv" id="ii-f">—</span></div>
        </div>
      </div>

      <div class="pnl" id="pnl-clips">
        <div class="tbar">
          <select class="sl2" id="cl-sort" onchange="loadClips(true)"><option value="view">Most Viewed</option><option value="recent">Recent</option><option value="oldest">Oldest</option></select>
          <select class="sl2" id="cl-per" onchange="loadClips(true)"><option value="all">All Time</option><option value="7d">7 Days</option><option value="30d">30 Days</option></select>
          <button class="btn b-o b-sm" onclick="loadClips(true)"><i class="fas fa-sync"></i></button>
        </div>
        <div id="cl-grid" class="mg"></div>
        <button id="cl-more" class="lm h" onclick="loadClips(false)"><i class="fas fa-chevron-down"></i> More</button>
      </div>

      <div class="pnl" id="pnl-vods">
        <div class="tbar"><button class="btn b-o b-sm" onclick="loadVods(true)"><i class="fas fa-sync"></i> Refresh</button></div>
        <div id="vd-grid" class="mg"></div>
        <button id="vd-more" class="lm h" onclick="loadVods(false)"><i class="fas fa-chevron-down"></i> More</button>
      </div>

      <div class="pnl" id="pnl-sched">
        <div id="sched-list"><div class="empty"><i class="fas fa-calendar-alt"></i><p>Play a channel to see schedule</p></div></div>
      </div>

      <div class="pnl" id="pnl-exp">
        <div class="card">
          <div class="card-h"><i class="fas fa-download" style="color:var(--g)"></i> Download / Stream</div>
          <div class="row">
            <select class="sl2" id="dl-q"><option value="auto">Best Quality</option></select>
            <select class="sl2" id="dl-fmt"><option value="m3u8">M3U8 File</option><option value="ffmpeg">FFmpeg</option><option value="vlc">VLC</option></select>
            <button class="btn b-g b-sm" onclick="doExp()"><i class="fas fa-download"></i> Get</button>
          </div>
          <div id="dl-info" style="font-size:11px;color:var(--t3);margin-top:7px"></div>
          <div id="dl-code" class="code"></div>
        </div>
        <div class="card">
          <div class="card-h"><i class="fas fa-share-alt" style="color:var(--sky)"></i> Share Link</div>
          <div class="row">
            <input id="share-inp" class="inp" style="flex:1;min-width:0" readonly placeholder="Play a channel first…">
            <button class="btn b-o b-sm" onclick="cpShare()"><i class="fas fa-copy"></i> Copy</button>
          </div>
        </div>
        <div class="card">
          <div class="card-h"><i class="fas fa-star" style="color:var(--gold)"></i> Export Following</div>
          <div class="row">
            <button class="btn b-o b-sm" onclick="expM3u()"><i class="fas fa-list"></i> M3U Playlist</button>
            <button class="btn b-o b-sm" onclick="expJson()"><i class="fas fa-code"></i> JSON</button>
          </div>
        </div>
      </div>

      <div class="pnl" id="pnl-notes">
        <div class="card">
          <div class="card-h"><i class="fas fa-note-sticky" style="color:var(--gold)"></i> Notes — <span id="notes-ch">—</span></div>
          <textarea id="note-ta" class="inp" placeholder="Timestamps, clips, links…"></textarea>
          <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">
            <button class="btn b-g b-sm" onclick="saveNote()"><i class="fas fa-save"></i> Save</button>
            <button class="btn b-o b-sm" onclick="expNotes()"><i class="fas fa-file-export"></i> Export All</button>
          </div>
          <div class="ns" id="nsaved">✓ Saved!</div>
        </div>
      </div>
    </div><!-- /panels -->
  </div><!-- /pg-player -->
</div><!-- /cnt -->

<!-- CHAT -->
<aside id="chat">
  <div id="ch-hd">
    <div class="cldot" id="chd"></div>
    <div class="chlbl" id="chlbl">CHAT</div>
    <button class="ib" onclick="togglePoll()" id="pbtn" title="Pause/Resume"><i class="fas fa-sync-alt"></i></button>
    <button class="ib" onclick="clrChat()" title="Clear chat"><i class="fas fa-trash"></i></button>
    <button class="ib" onclick="expChat()" title="Export chat"><i class="fas fa-download"></i></button>
    <button class="ib" onclick="closeCh()" title="Close" style="margin-left:2px"><i class="fas fa-times"></i></button>
  </div>
  <div id="ch-flt">
    <button class="ff on" id="ff-all" onclick="setCF('all')">All</button>
    <button class="ff" id="ff-mod" onclick="setCF('mod')">Mods</button>
    <button class="ff" id="ff-sub" onclick="setCF('sub')">Subs</button>
    <button class="ff" onclick="toggleEm()">😄 Emotes</button>
    <button class="ff" id="sl-btn" onclick="toggleScroll()" title="Scroll lock"><i class="fas fa-lock-open"></i></button>
  </div>
  <div id="embox">
    <span class="em" onclick="ins('😂')">😂</span><span class="em" onclick="ins('❤️')">❤️</span>
    <span class="em" onclick="ins('🔥')">🔥</span><span class="em" onclick="ins('👑')">👑</span>
    <span class="em" onclick="ins('💯')">💯</span><span class="em" onclick="ins('🎮')">🎮</span>
    <span class="em" onclick="ins('🏆')">🏆</span><span class="em" onclick="ins('👀')">👀</span>
    <span class="em" onclick="ins('🎉')">🎉</span><span class="em" onclick="ins('⚡')">⚡</span>
    <span class="em" onclick="ins('🐐')">🐐</span><span class="em" onclick="ins('🫡')">🫡</span>
    <span class="em" onclick="ins('😤')">😤</span><span class="em" onclick="ins('🗿')">🗿</span>
    <span class="estr" onclick="ins('GG')">GG</span>
    <span class="estr" onclick="ins('Pog')">Pog</span>
    <span class="estr" onclick="ins('KEKW')">KEKW</span>
    <span class="estr" onclick="ins('LUL')">LUL</span>
    <span class="estr" onclick="ins('OMEGALUL')">OMEGALUL</span>
    <span class="estr" onclick="ins('PauseChamp')">PauseChamp</span>
    <span class="estr" onclick="ins('monkaS')">monkaS</span>
    <span class="estr" onclick="ins('5Head')">5Head</span>
    <span class="estr" onclick="ins('EZ')">EZ</span>
    <span class="estr" onclick="ins('Copium')">Copium</span>
  </div>
  <div id="msgs"><div class="empty"><i class="fas fa-comment-slash"></i><p>Play a channel to load chat</p></div></div>
  <div id="ch-st">—</div>
  <div id="ch-inp-w">
    <input id="cinp" class="cinp" placeholder="Send a message…" onkeydown="if(event.key==='Enter')sendMsg()">
    <button class="csend" onclick="sendMsg()"><i class="fas fa-paper-plane"></i></button>
  </div>
</aside>
</div><!-- /body -->
</div><!-- /app -->

<!-- SETTINGS MODAL -->
<div class="modal" id="set-modal">
  <div class="modal-box">
    <div class="modal-hd"><h3>⚙️ Settings</h3><button class="mx" onclick="closeSet()">×</button></div>
    <div class="modal-body" id="set-body"></div>
  </div>
</div>

<!-- MULTI-STREAM MODAL -->
<div class="modal" id="ms-modal">
  <div class="modal-box" style="max-width:860px">
    <div class="modal-hd">
      <h3>📺 Multi-Stream</h3>
      <div style="display:flex;gap:5px;align-items:center">
        <button class="btn b-o b-sm" onclick="setML(1)">1×1</button>
        <button class="btn b-o b-sm" onclick="setML(2)">2×1</button>
        <button class="btn b-o b-sm" onclick="setML(4)">2×2</button>
        <button class="btn b-g b-sm" onclick="addSlot()"><i class="fas fa-plus"></i> Add</button>
        <button class="btn b-r b-sm" onclick="clrMS()"><i class="fas fa-times"></i></button>
        <button class="mx" onclick="closeMS()">×</button>
      </div>
    </div>
    <div style="padding:12px"><div id="ms-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:6px"></div></div>
  </div>
</div>

<!-- HLS.js CDN -->
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.7/dist/hls.min.js"></script>
<script>
'use strict';
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   GLOBAL STATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
const G = {
  ch: '',
  hls: null,           // HLS.js instance
  chatTimer: null,
  chatPaused: false,
  chatMsgs: [],
  chatFilter: 'all',
  scrollLock: false,
  infoTimer: null,
  vwHist: [],
  clNext: '',
  vdNext: '',
  brPage: 1,
  brCat: '',
  brQ: '',
  msHls: [],
  S: JSON.parse(localStorage.getItem('sv6_set') || '{"autoplay":true,"chatscroll":true,"lowlat":false,"notif":false}'),
  hl: (localStorage.getItem('sv6_hl') || '').split(',').map(w => w.trim()).filter(Boolean),
  favs: JSON.parse(localStorage.getItem('sv6_favs') || '[]'),
  hist: JSON.parse(localStorage.getItem('sv6_hist') || '[]'),
  notes: JSON.parse(localStorage.getItem('sv6_notes') || '{}'),
};

const vid = document.getElementById('vid');

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   NATIVE VIDEO PLAYER (HLS.js)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function loadStream(url) {
  const empt = document.getElementById('vempty');
  const load = document.getElementById('vload');
  const ltxt = document.getElementById('vload-txt');

  empt.style.display = 'none';
  vid.style.display = 'block';
  load.classList.add('show');
  ltxt.textContent = 'Loading stream…';

  // Destroy old HLS instance
  if (G.hls) { try { G.hls.destroy(); } catch {} G.hls = null; }

  if (Hls.isSupported()) {
    const hls = new Hls({
      lowLatencyMode: G.S.lowlat,
      enableWorker: true,
      backBufferLength: 30,
      maxBufferLength: G.S.lowlat ? 8 : 30,
      maxMaxBufferLength: G.S.lowlat ? 12 : 60,
      liveSyncDurationCount: G.S.lowlat ? 2 : 3,
      xhrSetup: xhr => { xhr.withCredentials = false; }
    });
    G.hls = hls;
    hls.loadSource(url);
    hls.attachMedia(vid);
    hls.on(Hls.Events.MANIFEST_PARSED, (e, data) => {
      load.classList.remove('show');
      vid.play().catch(() => {});
      // Populate quality selector
      const ql = document.getElementById('qsel');
      const dl = document.getElementById('dl-q');
      ql.innerHTML = '<option value="-1">Auto</option>';
      dl.innerHTML = '<option value="-1">Best Quality</option>';
      data.levels.forEach((lv, i) => {
        const lbl = lv.height ? lv.height + 'p' : Math.round((lv.bitrate || 0) / 1000) + 'k';
        [ql, dl].forEach(sel => {
          const o = document.createElement('option');
          o.value = i; o.textContent = lbl; sel.appendChild(o);
        });
      });
      if (data.levels.length) document.getElementById('sv-q').textContent = data.levels[0].height ? data.levels[0].height + 'p' : 'Auto';
    });
    hls.on(Hls.Events.ERROR, (e, d) => {
      if (d.fatal) {
        if (d.type === Hls.ErrorTypes.NETWORK_ERROR) {
          toast('🔄', 'Network error — retrying…', 'y');
          setTimeout(() => { if (G.ch) reloadStream(); }, 3000);
        } else {
          toast('⚠️', 'Stream error', 'r');
          load.classList.remove('show');
        }
      }
    });
    // Quality change from selector
    document.getElementById('qsel').onchange = function() {
      const v = parseInt(this.value);
      if (G.hls) G.hls.currentLevel = v;
    };
  } else if (vid.canPlayType('application/vnd.apple.mpegurl')) {
    // Native HLS (iOS Safari)
    vid.src = url;
    vid.load();
    vid.play().catch(() => {});
    load.classList.remove('show');
  } else {
    load.classList.remove('show');
    toast('⚠️', 'HLS not supported on this browser', 'r');
    return;
  }

  // Video events
  vid.onplaying = () => {
    load.classList.remove('show');
    document.getElementById('ibar').classList.add('show');
    updShare();
    document.getElementById('play-ico').className = 'fas fa-pause';
  };
  vid.onpause = () => { document.getElementById('play-ico').className = 'fas fa-play'; };
  vid.onwaiting = () => { load.classList.add('show'); ltxt.textContent = 'Buffering…'; };
  vid.oncanplay = () => { load.classList.remove('show'); };
  vid.ontimeupdate = () => {
    if (!isNaN(vid.duration) && vid.duration !== Infinity) {
      const pct = (vid.currentTime / vid.duration) * 100;
      document.getElementById('vprog-fill').style.width = pct + '%';
      document.getElementById('vtime').textContent = fmtT(vid.currentTime) + ' / ' + fmtT(vid.duration);
    }
  };
  vid.onfullscreenchange = () => {
    document.getElementById('fs-ico').className = document.fullscreenElement ? 'fas fa-compress' : 'fas fa-expand';
  };
}

function fmtT(s) { const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sc=Math.floor(s%60); return h?`${h}:${p2(m)}:${p2(sc)}`:`${m}:${p2(sc)}`; }

function reloadStream() {
  if (!G.ch) return;
  const q = document.getElementById('qsel').value;
  loadStream(`/hls/master.m3u8?c=${enc(G.ch)}&q=${enc(q === '-1' ? 'auto' : q)}&_=${Date.now()}`);
}

/* ━━━ VIDEO CONTROLS ━━━ */
function togglePlay() { if (vid.paused) vid.play().catch(()=>{}); else vid.pause(); }
function doMute() {
  vid.muted = !vid.muted;
  document.getElementById('vol-ico').className = vid.muted ? 'fas fa-volume-mute' : 'fas fa-volume-up';
  document.getElementById('vol-inp').value = vid.muted ? 0 : vid.volume;
}
function setVol(v) { vid.volume = parseFloat(v); vid.muted = parseFloat(v) === 0; document.getElementById('vol-ico').className = parseFloat(v) === 0 ? 'fas fa-volume-mute' : 'fas fa-volume-up'; }
async function doPip() { try { if (document.pictureInPictureElement) await document.exitPictureInPicture(); else await vid.requestPictureInPicture(); } catch { toast('⚠️', 'PiP not supported', 'y'); } }
function doFull() { if (!document.fullscreenElement) document.getElementById('vwrap').requestFullscreen().catch(()=>vid.requestFullscreen().catch(()=>{})); else document.exitFullscreen(); }
function doShot() {
  if (!vid.videoWidth) { toast('⚠️', 'No video to capture', 'y'); return; }
  const c = document.createElement('canvas'); c.width = vid.videoWidth; c.height = vid.videoHeight;
  c.getContext('2d').drawImage(vid, 0, 0);
  c.toBlob(b => { const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = `${G.ch || 'stream'}_${Date.now()}.png`; a.click(); toast('📸', 'Screenshot saved!', 'g'); }, 'image/png');
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   PLAY CHANNEL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function doPlay() {
  const raw = document.getElementById('inp').value.trim();
  if (!raw) { toast('⚠️', 'Enter a channel name', 'y'); return; }
  const ch = normCh(raw);
  if (!ch) { toast('⚠️', 'Invalid channel name', 'y'); return; }
  document.getElementById('inp').value = ch;
  playCh(ch);
}

async function playCh(ch) {
  if (!ch) return;
  showPlayer();
  if (G.ch && G.ch !== ch) saveNote(true);
  G.ch = ch;
  addHist(ch);
  loadNote(ch);
  updActiveSb(ch);
  localStorage.setItem('sv6_last', ch);
  document.getElementById('chlbl').textContent = ch.toUpperCase();
  document.getElementById('ib-nm').textContent = ch;
  document.getElementById('notes-ch').textContent = ch;
  document.getElementById('share-inp').value = '';

  // Tune backend (async, fire-and-forget)
  fetch('/api/tune?c=' + enc(ch)).catch(() => {});

  // Load stream
  const q = document.getElementById('qsel').value;
  loadStream(`/hls/master.m3u8?c=${enc(ch)}&q=${enc(q === '-1' ? 'auto' : q)}`);

  // Info + chat
  loadInfo(ch);
  startChat(ch);

  if (G.infoTimer) clearInterval(G.infoTimer);
  G.infoTimer = setInterval(() => { if (G.ch === ch) loadInfo(ch); }, 30000);

  toast('⚡', 'Loading ' + ch + '…', 'g');
}

function normCh(r) {
  r = (r || '').trim().toLowerCase();
  if (r.startsWith('http')) { try { const p = new URL(r).pathname.split('/').filter(Boolean); if (p.length) r = p[0]; } catch {} }
  return r.replace(/[^a-z0-9_\-]/g, '').slice(0, 64);
}

/* live search as you type */
let sT;
document.getElementById('inp').addEventListener('input', e => {
  clearTimeout(sT);
  const v = e.target.value.trim();
  if (v.length >= 2 && !v.startsWith('http')) {
    sT = setTimeout(() => { G.brQ = v; showBrowse(); doSearch(v, '', 1, false); }, 350);
  }
});
document.getElementById('inp').addEventListener('keydown', e => { if (e.key === 'Enter') doPlay(); });

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   STREAM INFO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
async function loadInfo(ch) {
  try {
    const r = await fetch('/api/stream_info?c=' + enc(ch));
    const d = await r.json();
    if (!d || d.error) return;
    const V = n => { n = n || 0; if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'; if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K'; return '' + n; };
    // Stats cards
    document.getElementById('sv-v').textContent = V(d.viewer_count);
    document.getElementById('sv-f').textContent = V(d.followers);
    if (d.started_at) { const m = Math.floor((Date.now() - new Date(d.started_at).getTime()) / 60000); document.getElementById('sv-d').textContent = m >= 60 ? `${Math.floor(m / 60)}h${m % 60}m` : m + 'm'; }
    // Info rows
    document.getElementById('ii-ch').textContent = d.display_name || ch;
    document.getElementById('ii-t').textContent  = d.title || '—';
    document.getElementById('ii-c').textContent  = d.category || '—';
    document.getElementById('ii-l').textContent  = d.language || '—';
    document.getElementById('ii-s').textContent  = d.started_at ? new Date(d.started_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '—';
    document.getElementById('ii-f').textContent  = V(d.followers);
    // Info bar
    document.getElementById('ib-nm').textContent = d.display_name || ch;
    document.getElementById('ib-tt').textContent = d.title || '';
    document.getElementById('ib-v').textContent  = V(d.viewer_count) + ' viewers';
    document.getElementById('ib-c').textContent  = d.category || '—';
    if (d.avatar) { const i = document.getElementById('ib-aimg'); i.src = d.avatar; i.style.display = 'block'; }
    updFavMeta(ch, d);
    // Chart
    G.vwHist.push(d.viewer_count || 0);
    if (G.vwHist.length > 40) G.vwHist.shift();
    drawChart();
  } catch(e) { console.warn('loadInfo:', e); }
}

function drawChart() {
  const cv = document.getElementById('chart');
  if (!cv) return;
  const W = cv.parentElement.clientWidth - 24, H = 58;
  cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d'), data = G.vwHist;
  ctx.clearRect(0, 0, W, H);
  if (data.length < 2) return;
  const mx = Math.max(...data, 1), toY = v => H - Math.round((v / mx) * H * .88), sx = W / (data.length - 1);
  const grd = ctx.createLinearGradient(0, 0, 0, H);
  grd.addColorStop(0, 'rgba(91,255,32,.26)'); grd.addColorStop(1, 'rgba(91,255,32,.01)');
  ctx.beginPath(); ctx.moveTo(0, toY(data[0]));
  for (let i = 1; i < data.length; i++) {
    const px = (i-1)*sx, py = toY(data[i-1]), x = i*sx, y = toY(data[i]);
    ctx.bezierCurveTo((px+x)/2, py, (px+x)/2, y, x, y);
  }
  ctx.strokeStyle = '#5bff20'; ctx.lineWidth = 2; ctx.stroke();
  ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath(); ctx.fillStyle = grd; ctx.fill();
  const lx = (data.length-1)*sx, ly = toY(data[data.length-1]);
  ctx.beginPath(); ctx.arc(lx, ly, 4, 0, Math.PI*2); ctx.fillStyle = '#5bff20'; ctx.fill();
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   CHAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function startChat(ch) {
  if (G.chatTimer) clearInterval(G.chatTimer);
  G.chatMsgs = []; G.chatPaused = false;
  document.getElementById('chd').classList.add('on');
  document.getElementById('chlbl').textContent = ch.toUpperCase();
  document.getElementById('msgs').innerHTML = '<div class="sp"><div class="spin"></div></div>';
  document.getElementById('ch-st').textContent = 'Connecting…';
  fetchChat(ch);
  G.chatTimer = setInterval(() => { if (!G.chatPaused && G.ch === ch) fetchChat(ch); }, 5000);
}

async function fetchChat(ch) {
  try {
    const r = await fetch('/api/chat?c=' + enc(ch));
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    const msgs = Array.isArray(d.messages) ? d.messages : [];
    if (!msgs.length) {
      if (!G.chatMsgs.length) { document.getElementById('msgs').innerHTML = '<div class="empty"><i class="fas fa-comment-slash"></i><p>No messages yet</p></div>'; document.getElementById('ch-st').textContent = 'Waiting…'; }
      return;
    }
    const seen = new Set(G.chatMsgs.map(m => m.id));
    const fresh = msgs.filter(m => !seen.has(m.id));
    if (!fresh.length) return;
    G.chatMsgs = [...G.chatMsgs, ...fresh].slice(-300);
    renderChat(fresh, true);
    document.getElementById('ch-st').textContent = `Live · ${msgs.length} msgs`;
  } catch { document.getElementById('ch-st').textContent = 'Chat unavailable'; }
}

function renderChat(msgs, append = false) {
  const box = document.getElementById('msgs');
  const hlSet = new Set(G.hl);
  const frag = document.createDocumentFragment(); let shown = 0;
  for (const m of msgs) {
    if (G.chatFilter === 'mod' && !m.is_mod) continue;
    if (G.chatFilter === 'sub' && !m.is_sub) continue;
    const div = document.createElement('div'); div.className = 'msg';
    const txt = (m.content || '').toLowerCase();
    if (hlSet.size && [...hlSet].some(w => w && txt.includes(w))) div.classList.add('hl');
    if (m.is_mod) div.classList.add('mod');
    const col = m.color || hashCol(m.sender || '');
    const ts = m.created_at ? new Date(m.created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '';
    div.innerHTML = `<div class="mbgs">${m.is_mod ? '<span class="mbg mbg-m" title="Mod">⚔</span>' : ''}${m.is_sub ? '<span class="mbg mbg-s" title="Sub">★</span>' : ''}</div><span class="muser" style="color:${col}">${esc(m.sender || '?')}</span><span class="msep">:</span><span class="mtxt">${esc(m.content || '')}</span><span class="mts">${ts}</span>`;
    frag.appendChild(div); shown++;
  }
  if (!append) { box.innerHTML = ''; if (!shown) { box.innerHTML = '<div class="empty"><i class="fas fa-comment-slash"></i><p>Nothing to show</p></div>'; return; } }
  box.appendChild(frag);
  if (G.S.chatscroll && !G.scrollLock) box.scrollTop = box.scrollHeight;
}

function hashCol(s) { let h = 0; for (let i = 0; i < s.length; i++) h = s.charCodeAt(i) + ((h << 5) - h); return `hsl(${Math.abs(h) % 360},55%,68%)`; }
function togglePoll() { G.chatPaused = !G.chatPaused; document.getElementById('pbtn').style.color = G.chatPaused ? 'var(--red)' : ''; document.getElementById('ch-st').textContent = G.chatPaused ? 'Paused' : 'Resumed'; if (!G.chatPaused && G.ch) fetchChat(G.ch); }
function clrChat() { G.chatMsgs = []; document.getElementById('msgs').innerHTML = '<div class="empty"><i class="fas fa-comment-slash"></i><p>Cleared</p></div>'; }
function expChat() { if (!G.chatMsgs.length) { toast('⚠️', 'No chat to export', 'y'); return; } dlTxt(`chat_${G.ch}.txt`, G.chatMsgs.map(m => `[${m.created_at || ''}] ${m.sender || '?'}: ${m.content || ''}`).join('\n')); toast('✅', 'Chat exported', 'g'); }
function setCF(f) { G.chatFilter = f; ['all','mod','sub'].forEach(x => document.getElementById('ff-' + x)?.classList.toggle('on', x === f)); renderChat(G.chatMsgs, false); }
function toggleEm() { document.getElementById('embox').classList.toggle('on'); }
function toggleScroll() { G.scrollLock = !G.scrollLock; document.getElementById('sl-btn').innerHTML = G.scrollLock ? '<i class="fas fa-lock" style="color:var(--g)"></i>' : '<i class="fas fa-lock-open"></i>'; }
function ins(e) { const i = document.getElementById('cinp'); i.value += (i.value ? ' ' : '') + e; i.focus(); }
function sendMsg() { const i = document.getElementById('cinp'), m = i.value.trim(); if (!m) return; renderChat([{id: Date.now()+'', sender:'You', content:m, created_at:new Date().toISOString(), color:'#5bff20', is_mod:false, is_sub:false}], true); i.value = ''; toast('💬', 'Sent (view-only)', 'g', 1600); }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   BROWSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
async function doSearch(q, cat, page, append = false) {
  const spin = document.getElementById('br-spin'), grid = document.getElementById('br-grid'), more = document.getElementById('br-more');
  spin.style.display = 'flex';
  if (!append) { grid.innerHTML = ''; more.classList.add('h'); }
  try {
    const p = new URLSearchParams(); if (q) p.set('q', q); if (cat) p.set('cat', cat); p.set('page', page);
    const r = await fetch('/api/browse?' + p); const d = await r.json();
    const chs = d.channels || [];
    renderChs(chs, append);
    G.brPage = page;
    if (chs.length >= 16) more.classList.remove('h'); else more.classList.add('h');
  } catch { if (!append) grid.innerHTML = '<div class="empty" style="grid-column:1/-1"><i class="fas fa-exclamation-circle"></i><p>Search failed</p></div>'; }
  spin.style.display = 'none';
}

function renderChs(list, append = false) {
  const grid = document.getElementById('br-grid');
  const V = n => { n = n || 0; if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K'; return '' + n; };
  if (!list.length && !append) { grid.innerHTML = '<div class="empty" style="grid-column:1/-1"><i class="fas fa-search"></i><p>No results found</p></div>'; return; }
  list.forEach(ch => {
    const slug = ch.slug || ''; if (!slug) return;
    const d = document.createElement('div'); d.className = 'cc';
    const imgH = ch.thumbnail ? `<img src="${esc(ch.thumbnail)}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=ni>📺</div>'">` : '<div class="ni">📺</div>';
    d.innerHTML = `<div class="cct">${imgH}${ch.is_live ? '<span class="lb">LIVE</span>' : ''}${ch.is_live && ch.viewer_count ? `<span class="vc" style="position:absolute;bottom:4px;right:5px;background:rgba(0,0,0,.82);color:#fff;font-size:9px;padding:1px 5px;border-radius:3px">👁 ${V(ch.viewer_count)}</span>` : ''}</div><div class="ccb"><div class="ccn">${esc(ch.display_name || slug)}</div>${ch.category ? `<div class="ccc">${esc(ch.category)}</div>` : ''}${ch.title ? `<div class="cct2">${esc(ch.title)}</div>` : ''}</div>`;
    d.addEventListener('click', () => { document.getElementById('inp').value = slug; playCh(slug); if (window.innerWidth < 900) showPlayer(); });
    grid.appendChild(d);
  });
}

function goBrowse() { showBrowse(); if (!document.getElementById('br-grid').children.length) doSearch('', G.brCat, 1, false); }
function browseMore() { doSearch(G.brQ, G.brCat, G.brPage + 1, true); }
function setCat(btn, cat) { G.brCat = cat; G.brQ = ''; G.brPage = 1; document.querySelectorAll('.cat-b').forEach(b => b.classList.toggle('on', b === btn)); doSearch('', cat, 1, false); }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   CLIPS & VODS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
async function loadClips(reset = true) {
  if (!G.ch) { toast('ℹ️', 'Play a channel first', 'y'); return; }
  if (reset) { G.clNext = ''; document.getElementById('cl-grid').innerHTML = ''; }
  const spin = `<div class="sp" id="cl-sp"><div class="spin"></div></div>`;
  document.getElementById('cl-grid').insertAdjacentHTML('beforeend', spin);
  document.getElementById('cl-more').classList.add('h');
  try {
    const p = new URLSearchParams({c: G.ch, sort: document.getElementById('cl-sort').value, period: document.getElementById('cl-per').value});
    if (G.clNext) p.set('cursor', G.clNext);
    const r = await fetch('/api/clips?' + p); const d = await r.json();
    document.getElementById('cl-sp')?.remove();
    const clips = d.clips || [];
    if (!clips.length && reset) document.getElementById('cl-grid').innerHTML = '<div class="empty" style="grid-column:1/-1"><i class="fas fa-film"></i><p>No clips found</p></div>';
    else { renderMedia('cl-grid', clips); G.clNext = d.next_cursor || ''; if (G.clNext) document.getElementById('cl-more').classList.remove('h'); }
  } catch { document.getElementById('cl-sp')?.remove(); }
}

async function loadVods(reset = true) {
  if (!G.ch) { toast('ℹ️', 'Play a channel first', 'y'); return; }
  if (reset) { G.vdNext = ''; document.getElementById('vd-grid').innerHTML = ''; }
  const spin = `<div class="sp" id="vd-sp"><div class="spin"></div></div>`;
  document.getElementById('vd-grid').insertAdjacentHTML('beforeend', spin);
  document.getElementById('vd-more').classList.add('h');
  try {
    const p = new URLSearchParams({c: G.ch}); if (G.vdNext) p.set('cursor', G.vdNext);
    const r = await fetch('/api/vods?' + p); const d = await r.json();
    document.getElementById('vd-sp')?.remove();
    const vods = d.vods || [];
    if (!vods.length && reset) document.getElementById('vd-grid').innerHTML = '<div class="empty" style="grid-column:1/-1"><i class="fas fa-video"></i><p>No VODs available</p></div>';
    else { renderMedia('vd-grid', vods); G.vdNext = d.next_cursor || ''; if (G.vdNext) document.getElementById('vd-more').classList.remove('h'); }
  } catch { document.getElementById('vd-sp')?.remove(); }
}

function renderMedia(gid, items) {
  const grid = document.getElementById(gid);
  const V = n => { n = n || 0; if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K'; return '' + n; };
  items.forEach(item => {
    const d = document.createElement('div'); d.className = 'mc';
    const dur = item.duration ? fmtD(item.duration) : '';
    const ago = item.created_at ? timeAgo(new Date(item.created_at)) : '';
    d.innerHTML = `<div class="mth">${item.thumbnail ? `<img src="${esc(item.thumbnail)}" loading="lazy" onerror="this.style.display='none'">` : ''}<div class="mplay"><i class="fas fa-play-circle"></i></div>${dur ? `<span class="mdur">${dur}</span>` : ''}</div><div class="mbody"><div class="mtt">${esc(item.title || 'Untitled')}</div><div class="mmeta"><span>${item.views ? V(item.views) + ' views' : ''}</span><span>${ago}</span></div></div>`;
    d.addEventListener('click', () => {
      if (!item.url) { toast('⚠️', 'URL not available', 'y'); return; }
      loadStream(item.url);
      toast('▶', ('Playing: ' + (item.title || '')).slice(0, 50), 'g');
    });
    grid.appendChild(d);
  });
}

function fmtD(s) { const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sc = s%60; return h ? `${h}:${p2(m)}:${p2(sc)}` : `${m}:${p2(sc)}`; }
function p2(n) { return String(n).padStart(2, '0'); }
function timeAgo(d) { const s = Math.floor((Date.now()-d)/1000); if (s<60) return 'just now'; if (s<3600) return Math.floor(s/60)+'m ago'; if (s<86400) return Math.floor(s/3600)+'h ago'; return Math.floor(s/86400)+'d ago'; }

/* ━━━ SCHEDULE ━━━ */
async function loadSched() {
  if (!G.ch) return;
  const el = document.getElementById('sched-list');
  el.innerHTML = '<div class="sp"><div class="spin"></div></div>';
  try {
    const r = await fetch('/api/schedule?c=' + enc(G.ch)); const d = await r.json();
    const items = d.schedule || [];
    if (!items.length) { el.innerHTML = '<div class="empty"><i class="fas fa-calendar-alt"></i><p>No schedule available</p></div>'; return; }
    el.innerHTML = items.map(ev => `<div class="shi"><div class="sh-t">${esc(ev.start_time||ev.time||'??:??')}</div><div class="sh-b"><div class="sh-n">${esc(ev.title||'Stream')}</div><div class="sh-s">${esc(ev.date||'')}${ev.category?' · '+ev.category:''}</div></div><button class="btn b-o b-sm" onclick="toast('🔔','Reminder set!','g')"><i class="fas fa-bell"></i></button></div>`).join('');
  } catch { el.innerHTML = '<div class="empty"><i class="fas fa-exclamation-circle"></i><p>Failed</p></div>'; }
}

/* ━━━ EXPORT ━━━ */
function updShare() { if (!G.ch) return; document.getElementById('share-inp').value = `${location.origin}/watch/${G.ch}`; }
function cpShare() { const v = document.getElementById('share-inp').value; if (!v) { toast('⚠️','Play a channel first','y'); return; } navigator.clipboard.writeText(v).then(() => toast('✅','Link copied!','g')); }
function cpM3u8() { if (!G.ch) { toast('⚠️','Play a channel first','y'); return; } navigator.clipboard.writeText(`${location.origin}/hls/master.m3u8?c=${enc(G.ch)}&q=auto`).then(() => toast('✅','M3U8 URL copied!','g')); }
function doExp() {
  if (!G.ch) { toast('⚠️','Play a channel first','y'); return; }
  const q = document.getElementById('dl-q').value, fmt = document.getElementById('dl-fmt').value;
  const url = `${location.origin}/hls/master.m3u8?c=${enc(G.ch)}&q=${enc(q === '-1' ? 'auto' : q)}`;
  const out = document.getElementById('dl-info'), code = document.getElementById('dl-code');
  code.style.display = 'none';
  if (fmt === 'm3u8') { dlTxt(`${G.ch}.m3u8`, `#EXTM3U\n#EXTINF:-1,${G.ch} Live\n${url}`); out.textContent = '✅ Saved as M3U8 file'; }
  else if (fmt === 'ffmpeg') { code.textContent = `ffmpeg -i "${url}" -c copy -t 7200 "${G.ch}_$(date +%s).ts"`; code.style.display = 'block'; out.textContent = 'Copy command → run in terminal'; }
  else { code.textContent = `vlc "${url}" --sout '#standard{access=file,mux=ts,dst=${G.ch}.ts}'`; code.style.display = 'block'; out.textContent = 'Copy command → run in terminal'; }
}
function expM3u() { if (!G.favs.length) { toast('⚠️','No favorites','y'); return; } dlTxt('following.m3u8', ['#EXTM3U',...G.favs.map(ch=>`#EXTINF:-1,${ch}\n${location.origin}/hls/master.m3u8?c=${enc(ch)}&q=auto`)].join('\n')); toast('✅','Exported','g'); }
function expJson() { dlTxt('following.json', JSON.stringify(G.favs, null, 2)); toast('✅','Exported','g'); }

/* ━━━ FAVORITES ━━━ */
function renderFavs() {
  const list = document.getElementById('favs-list');
  list.innerHTML = '';
  if (!G.favs.length) { list.innerHTML = '<div style="padding:7px 12px;font-size:10.5px;color:var(--t3)">No channels followed yet</div>'; return; }
  G.favs.forEach(ch => {
    const d = document.createElement('div'); d.className = 'ch-row' + (ch === G.ch ? ' active' : ''); d.id = 'fav-' + ch;
    d.innerHTML = `<div class="av" id="sav-${ch}">${ch.slice(0,2).toUpperCase()}<div class="av-ring"></div></div><div class="ch-inf"><div class="ch-nm">${esc(ch)}</div><div class="ch-sub" id="fsub-${ch}">—</div></div><button class="del-btn" onclick="event.stopPropagation();unfav('${esc(ch)}')" title="Unfollow">✕</button>`;
    d.addEventListener('click', () => { document.getElementById('inp').value = ch; playCh(ch); if (window.innerWidth <= 900) closeSb(); });
    list.appendChild(d);
    loadFavMeta(ch);
  });
}
async function loadFavMeta(ch) { try { const r = await fetch('/api/stream_info?c=' + enc(ch)); const d = await r.json(); if (d && !d.error) updFavMeta(ch, d); } catch {} }
function updFavMeta(ch, d) {
  const sub = document.getElementById('fsub-' + ch), av = document.getElementById('sav-' + ch);
  if (!sub || !av) return;
  const V = n => { n=n||0; if(n>=1e3)return (n/1e3).toFixed(1)+'K'; return ''+n; };
  if (d.is_live) { sub.innerHTML = `<span class="ltag">LIVE</span><span class="vn">${V(d.viewer_count)}</span>`; av.classList.add('live'); }
  else { sub.textContent = d.category || 'Offline'; av.classList.remove('live'); }
  if (d.avatar) { av.innerHTML = `<img src="${esc(d.avatar)}" alt="${esc(ch)}"><div class="av-ring"></div>`; if (d.is_live) av.classList.add('live'); }
}
function updActiveSb(ch) { document.querySelectorAll('.ch-row').forEach(e => e.classList.toggle('active', e.id === 'fav-' + ch)); }
function followNow() { if (!G.ch) { toast('⚠️','Play a channel first','y'); return; } addFav(G.ch); }
function addFav(ch) { if (!G.favs.includes(ch)) { G.favs.unshift(ch); localStorage.setItem('sv6_favs', JSON.stringify(G.favs)); renderFavs(); toast('⭐','Following '+ch,'g'); } else toast('ℹ️','Already following '+ch,'g'); }
function unfav(ch) { G.favs = G.favs.filter(f => f !== ch); localStorage.setItem('sv6_favs', JSON.stringify(G.favs)); renderFavs(); }

/* ━━━ HISTORY ━━━ */
function renderHist() {
  const list = document.getElementById('hist-list');
  list.innerHTML = '';
  if (!G.hist.length) { list.innerHTML = '<div style="padding:7px 12px;font-size:10.5px;color:var(--t3)">No history</div>'; return; }
  [...G.hist].reverse().slice(0, 20).forEach(e => {
    const d = document.createElement('div'); d.className = 'ch-row';
    d.innerHTML = `<div class="av" style="font-size:9px;background:var(--s4)">⏱</div><div class="ch-inf"><div class="ch-nm">${esc(e.ch)}</div><div class="ch-sub">${e.t}</div></div>`;
    d.addEventListener('click', () => { document.getElementById('inp').value = e.ch; playCh(e.ch); if (window.innerWidth <= 900) closeSb(); });
    list.appendChild(d);
  });
}
function addHist(ch) { G.hist = G.hist.filter(h => h.ch !== ch); G.hist.push({ch, t: new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}); if (G.hist.length > 50) G.hist.shift(); localStorage.setItem('sv6_hist', JSON.stringify(G.hist)); renderHist(); }
function clrHist() { G.hist = []; localStorage.removeItem('sv6_hist'); renderHist(); toast('🗑️','History cleared','g'); }

/* ━━━ NOTES ━━━ */
function loadNote(ch) { document.getElementById('note-ta').value = G.notes[ch] || ''; document.getElementById('notes-ch').textContent = ch; }
function saveNote(s = false) { if (!G.ch) return; const t = document.getElementById('note-ta').value; if (t === (G.notes[G.ch] || '')) return; G.notes[G.ch] = t; localStorage.setItem('sv6_notes', JSON.stringify(G.notes)); if (!s) { const el = document.getElementById('nsaved'); el.style.display = 'block'; setTimeout(() => el.style.display = 'none', 2000); } }
function expNotes() { dlTxt('notes.txt', Object.entries(G.notes).map(([ch,n]) => `=== ${ch} ===\n${n}`).join('\n\n')); toast('✅','Notes exported','g'); }

/* ━━━ TABS ━━━ */
function goTab(t) {
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  document.querySelectorAll('.pnl').forEach(p => p.classList.toggle('on', p.id === 'pnl-' + t));
  if (t === 'clips' && G.ch && !document.getElementById('cl-grid').children.length) loadClips(true);
  if (t === 'vods'  && G.ch && !document.getElementById('vd-grid').children.length) loadVods(true);
  if (t === 'sched') loadSched();
}

/* ━━━ PAGE SWITCH ━━━ */
function showPlayer() { document.getElementById('pg-browse').style.display = 'none'; document.getElementById('pg-player').style.display = 'flex'; document.getElementById('b-br').classList.remove('on'); }
function showBrowse() { document.getElementById('pg-player').style.display = 'none'; document.getElementById('pg-browse').style.display = 'block'; document.getElementById('b-br').classList.add('on'); }

/* ━━━ DRAWERS ━━━ */
function toggleSb() { const on = document.getElementById('sb').classList.toggle('on'); document.getElementById('ov-sb').classList.toggle('on', on); }
function closeSb()  { document.getElementById('sb').classList.remove('on'); document.getElementById('ov-sb').classList.remove('on'); }
function toggleCh() { const on = document.getElementById('chat').classList.toggle('on'); document.getElementById('ov-ch').classList.toggle('on', on); }
function closeCh()  { document.getElementById('chat').classList.remove('on'); document.getElementById('ov-ch').classList.remove('on'); }

/* ━━━ MULTI-STREAM ━━━ */
function openMulti() { document.getElementById('ms-modal').classList.add('on'); }
function closeMS()   { document.getElementById('ms-modal').classList.remove('on'); }
function setML(n) { document.getElementById('ms-grid').style.gridTemplateColumns = n === 1 ? '1fr' : '1fr 1fr'; }
async function addSlot() {
  const ch = prompt('Channel name:'); if (!ch || !ch.trim()) return;
  const name = normCh(ch.trim()); if (!name) return;
  await fetch('/api/tune?c=' + enc(name)).catch(() => {});
  const grid = document.getElementById('ms-grid'), idx = G.msHls.length, vid_id = 'ms-vid-' + idx;
  const slot = document.createElement('div');
  slot.style.cssText = 'background:#000;border-radius:12px;overflow:hidden;position:relative;aspect-ratio:16/9';
  slot.innerHTML = `<video id="${vid_id}" controls playsinline muted style="width:100%;height:100%;display:block;object-fit:contain;background:#000"></video><div style="position:absolute;top:5px;left:5px;background:rgba(0,0,0,.75);color:var(--g);font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px">${esc(name)}</div>`;
  grid.appendChild(slot);
  const v = document.getElementById(vid_id);
  if (Hls.isSupported()) {
    const h = new Hls({lowLatencyMode:true,enableWorker:true});
    G.msHls.push(h);
    h.loadSource(`/hls/master.m3u8?c=${enc(name)}&q=auto`);
    h.attachMedia(v);
    h.on(Hls.Events.MANIFEST_PARSED, () => v.play().catch(() => {}));
  } else { v.src = `/hls/master.m3u8?c=${enc(name)}&q=auto`; v.play().catch(() => {}); }
  toast('📺', name + ' added to multi-stream', 'g');
}
function clrMS() { G.msHls.forEach(h => { try { h.destroy(); } catch {} }); G.msHls = []; document.getElementById('ms-grid').innerHTML = ''; }

/* ━━━ SETTINGS ━━━ */
function openSettings() {
  const rows = [
    {k:'autoplay', l:'Auto-play last channel', d:'Resume last stream on page open'},
    {k:'chatscroll',l:'Chat auto-scroll',       d:'Scroll to new messages automatically'},
    {k:'lowlat',   l:'Low latency mode',         d:'Reduce buffer for less delay (may stutter)'},
    {k:'notif',    l:'Desktop notifications',    d:'Alert when a followed channel goes live'},
  ];
  document.getElementById('set-body').innerHTML =
    rows.map(r => `<div class="set-row"><div><div class="set-l">${r.l}</div><div class="set-d">${r.d}</div></div><button class="tog ${G.S[r.k] ? 'on' : ''}" id="st-${r.k}" onclick="togS('${r.k}')"></button></div>`).join('')
    + `<div style="margin-top:12px"><div class="set-l" style="margin-bottom:6px">Highlight keywords <span style="color:var(--t3);font-weight:400;font-size:11px">(comma-separated)</span></div><input id="hlinp" class="inp" style="width:100%;font-size:12px" placeholder="gg,nice,pog" value="${esc(G.hl.join(','))}"></div>`
    + `<div style="display:flex;gap:7px;margin-top:12px"><button class="btn b-g b-sm" onclick="saveSet()"><i class="fas fa-save"></i> Save</button><button class="btn b-r b-sm" onclick="rstAll()"><i class="fas fa-trash"></i> Reset All</button></div>`;
  document.getElementById('set-modal').classList.add('on');
}
function togS(k) { G.S[k] = !G.S[k]; document.getElementById('st-' + k)?.classList.toggle('on', G.S[k]); }
function saveSet() { G.hl = (document.getElementById('hlinp')?.value || '').split(',').map(w => w.trim()).filter(Boolean); localStorage.setItem('sv6_hl', G.hl.join(',')); localStorage.setItem('sv6_set', JSON.stringify(G.S)); closeSet(); toast('✅','Settings saved','g'); }
function closeSet() { document.getElementById('set-modal').classList.remove('on'); }
function rstAll() { if (!confirm('Reset all StreamVault data?')) return; ['sv6_favs','sv6_hist','sv6_notes','sv6_set','sv6_hl','sv6_last'].forEach(k => localStorage.removeItem(k)); location.reload(); }

/* ━━━ MODALS CLOSE ━━━ */
document.querySelectorAll('.modal').forEach(m => m.addEventListener('click', e => { if (e.target === m) m.classList.remove('on'); }));

/* ━━━ TOAST ━━━ */
function toast(ico, msg, type = 'g', dur = 3500) {
  const w = document.getElementById('toasts'), el = document.createElement('div');
  el.className = 'toast ' + type;
  el.innerHTML = `<span class="ti">${ico}</span><span class="tm">${esc(msg)}</span><button class="tx" onclick="this.parentElement.remove()">×</button>`;
  w.appendChild(el);
  if (dur > 0) setTimeout(() => el.remove?.(), dur);
}

/* ━━━ UPTIME ━━━ */
async function updUp() { try { const r = await fetch('/api/server_stats'); const d = await r.json(); const el = document.getElementById('uptime'); if (el) el.textContent = 'Up ' + d.uptime_human; } catch {} }

/* ━━━ UTILS ━━━ */
function esc(s) { if (!s) return ''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&#34;').replace(/'/g,'&#39;'); }
function enc(s) { return encodeURIComponent(s || ''); }
function dlTxt(n, t) { const a = document.createElement('a'); a.href = 'data:text/plain;charset=utf-8,' + encodeURIComponent(t); a.download = n; a.click(); }

/* ━━━ INIT ━━━ */
document.addEventListener('DOMContentLoaded', () => {
  renderFavs();
  renderHist();
  updUp();
  setInterval(updUp, 60000);
  setInterval(() => G.favs.forEach(ch => loadFavMeta(ch)), 90000);

  // URL routing
  const pm = location.pathname.match(/^\/watch\/(.+)/);
  if (pm) { const ch = decodeURIComponent(pm[1]); document.getElementById('inp').value = ch; playCh(ch); return; }

  // Auto-play
  if (G.S.autoplay) {
    const last = localStorage.getItem('sv6_last');
    if (last) { document.getElementById('inp').value = last; playCh(last); return; }
  }

  // Default: browse
  showBrowse();
  doSearch('', '', 1, false);
});
</script>
</body>
</html>"""

# ── Routes ───────────────────────────────────────────────────
@app.route("/")
def index(): return Response(PAGE, content_type="text/html; charset=utf-8")

@app.route("/watch/<path:name>")
def watch_page(name): return Response(PAGE, content_type="text/html; charset=utf-8")

@app.route("/<ch_name>")
def direct(ch_name):
    bad = ('.', 'api', 'hls', 'static', 'favicon', '_')
    if any(ch_name.startswith(b) for b in bad): return "Not found", 404
    return Response(PAGE, content_type="text/html; charset=utf-8")

@app.route("/api/tune")
def api_tune():
    n = norm(request.args.get("c",""))
    if not n: return jsonify({"ok":False}), 400
    force = request.args.get("force","0") in ("1","true")
    u = ensure_url(n, force=force)
    return jsonify({"ok": bool(u)})

@app.route("/api/variants")
def api_variants():
    n = norm(request.args.get("c",""))
    if not n: return jsonify([])
    return jsonify(get_vars(n))

@app.route("/api/stream_info")
def api_stream_info():
    n = norm(request.args.get("c",""))
    if not n: return jsonify({"error":"no_channel"}), 400
    try:
        d = ch_info(n)
        if not d: return jsonify({"error":"not_found"}), 404
        d["uptime"] = int(time.time()-T0)
        return jsonify(d)
    except Exception as e:
        logging.error(f"stream_info {n}: {e}"); return jsonify({"error":str(e)}), 500

@app.route("/api/clips")
def api_clips():
    n = norm(request.args.get("c",""))
    if not n: return jsonify({"clips":[],"next_cursor":""})
    return jsonify(get_clips(n, request.args.get("sort","view"),
                             request.args.get("period","all"),
                             request.args.get("cursor","")))

@app.route("/api/vods")
def api_vods():
    n = norm(request.args.get("c",""))
    if not n: return jsonify({"vods":[],"next_cursor":""})
    return jsonify(get_vods(n, request.args.get("cursor","")))

@app.route("/api/chat")
def api_chat():
    n = norm(request.args.get("c",""))
    if not n: return jsonify({"messages":[]})
    return jsonify({"messages": get_chat(n)})

@app.route("/api/schedule")
def api_schedule():
    n = norm(request.args.get("c",""))
    if not n: return jsonify({"schedule":[]})
    try:
        r = sc.get(f"https://kick.com/api/v2/channels/{n}/schedule", timeout=TOUT)
        r.raise_for_status(); d = r.json()
        return jsonify({"schedule": d if isinstance(d,list) else d.get("schedule",[])})
    except: return jsonify({"schedule":[]})

@app.route("/api/browse")
def api_browse():
    return jsonify({"channels": search(
        request.args.get("q","").strip(),
        request.args.get("cat","").strip(),
        int(request.args.get("page",1)))})

@app.route("/api/server_stats")
def api_server_stats():
    up = int(time.time()-T0); h,r=divmod(up,3600); m,s=divmod(r,60)
    with chl: active = len([k for k,v in chs.items() if v.get("url")])
    return jsonify({"uptime_seconds":up,"uptime_human":f"{h}h {m}m {s}s",
                    "active_channels":active,"total":len(chs),"playwright":PW})

@app.route("/hls/master.m3u8")
def hls_master():
    n = norm(request.args.get("c","")); q = request.args.get("q","auto")
    H = {"Content-Type":"application/vnd.apple.mpegurl; charset=utf-8",
         "Access-Control-Allow-Origin":"*","Cache-Control":"no-cache"}
    if not n: return "missing",400
    u = ensure_url(n)
    if not u: return "#EXTM3U\n#EXT-X-ERROR:NO_STREAM\n",200,H
    try:
        r = sc.get(u,timeout=TOUT)
        if r.status_code in(401,403): u=ensure_url(n,force=True); r=sc.get(u,timeout=TOUT)
        r.raise_for_status()
    except Exception as e: return f"#EXTM3U\n#EXT-X-ERROR:{e}\n",200,H
    variants = parse_vars(r.text,u)
    out = ["#EXTM3U","#EXT-X-VERSION:3"]
    def add(v):
        prx = f"/hls/seg?c={quote(n,safe='')}&u={quote(v['uri'],safe='')}"
        pts=[]
        if v.get("bw"): pts.append(f"BANDWIDTH={v['bw']}")
        if v.get("h"):  pts.append(f"RESOLUTION={int(v['h']*16/9)}x{v['h']}")
        if v.get("name"): pts.append(f'NAME="{v["name"]}"')
        out.append("#EXT-X-STREAM-INF:"+",".join(pts)); out.append(prx)
    if q and q!="auto":
        t=None
        try: t=int(q)
        except: pass
        chosen=None
        if t:
            bd=99999
            for v in variants:
                if v.get("h"):
                    dd=abs(v["h"]-t)
                    if dd<bd: bd=dd; chosen=v
        if not chosen and variants: chosen=variants[0]
        if chosen: add(chosen)
        else:
            for v in variants: add(v)
    else:
        for v in variants: add(v)
    return "\n".join(out)+"\n",200,H

@app.route("/hls/seg")
def hls_seg():
    n = norm(request.args.get("c","")); u = unquote(request.args.get("u",""))
    if not n or not u: return "missing",400
    for attempt in (1,2):
        try:
            r = sc.get(u,stream=True,timeout=30)
            if r.status_code in(401,403) and attempt==1: ensure_url(n,force=True); continue
            r.raise_for_status()
            ct = r.headers.get("Content-Type","application/octet-stream")
            def gen():
                for chunk in r.iter_content(65536):
                    if chunk: yield chunk
            return Response(gen(),content_type=ct,headers={"Access-Control-Allow-Origin":"*","Cache-Control":"no-cache"})
        except Exception as e:
            logging.debug(f"seg {attempt}: {e}"); time.sleep(.2)
    return Response(b"",status=502,headers={"Access-Control-Allow-Origin":"*"})

# ── Main ─────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=bg, daemon=True).start()
    print(f"""
╔═════════════════════════════════════════════╗
║  StreamVault v6  ·  by Mazen Aldeeb         ║
║  ➜  http://localhost:{PORT}                   ║
║  ➜  http://localhost:{PORT}/watch/xqc         ║
╚═════════════════════════════════════════════╝""")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
