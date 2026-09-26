# -*- coding: utf-8 -*-
"""
네이버 블로그 모니터링 (프로그램형 뷰어)
  python blog_watch.py      blogs.txt 의 블로그를 확인 → 블로그뷰어.html 갱신 후 열기
블로그 추가/삭제: blogs.txt 에 "아이디, 이름" 한 줄씩
표시 기간 변경: 아래 DAYS 숫자
"""
import html, json, os, re, sqlite3, sys, time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

DAYS = 30   # 최근 며칠 이내 글을 뷰어에 표시할지
os.chdir(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def load_blogs():
    out = []
    for line in open("blogs.txt", encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        bid, _, name = [x.strip() for x in line.partition(",")]
        out.append((bid, name or bid))
    return out


def fetch(bid):
    req = urllib.request.Request("https://rss.blog.naver.com/%s.xml" % bid, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    items = []
    for it in root.iter("item"):
        g = lambda t: (it.findtext(t) or "").strip()
        try:
            dt = parsedate_to_datetime(g("pubDate")).strftime("%Y-%m-%d %H:%M")
        except Exception:
            dt = g("pubDate")
        desc = re.sub(r"<[^>]+>", " ", html.unescape(g("description")))
        items.append(dict(link=g("link").split("?")[0], title=html.unescape(g("title")), date=dt,
                          cat=html.unescape(g("category")), desc=re.sub(r"\s+", " ", desc).strip()[:300]))
    return items


# ── 특이·주의 예규/판례 별점 (제목·요약의 표현으로 판단하는 규칙 기반) ──
RULING = re.compile(r"예규|유권해석|해석|판례|판결|결정례|심판|심사|불복|대법|행정법원|고등법원|사전-\d|서면-\d|기준-\d|법규과|법령해석|재산세제과|부동산납세과|질의회신|조심\d")
STRONG = [  # 3점: 기존 해석과 충돌·변경·문제 제기
    (r"≠|기존\s*(유권)?해석(과|을|이)?\s*(다|달|반대|뒤집)|해석\s*변경|예규\s*변경|변경(된|전)\s*예규|뒤집|번복", "기존 해석과 다름/변경"),
    (r"잘못된\s*(해석|유권해석)|해석이\s*맞나요|문제점|불합리|모순|위법|무효", "해석에 대한 문제 제기"),
    (r"대법원|대법\d", "대법원 판례"),
]
MID = [  # 2점: 결론이 불리하거나 함정
    (r"불가능|불가\b|불가\)|적용\s*(불가|안\s*됨|되지\s*않|배제)|못\s*받|안\s*됩니다|수\s*없습니다|아닙니다|해당하지\s*않|해당\s*안|인정\s*(안|되지)|부인", "불리한 결론"),
    (r"추징|가산세|중과|과세됩니다|토해내|비과세\s*배제", "추징·중과 위험"),
    (r"함정|주의|조심|유의|놓치|실수|착각|오해|반드시|꼭\s*확인", "주의 환기"),
    (r"법원\s*판례|판결|행정법원|고등법원|조세심판원|심판례", "법원·심판 판단"),
]
WEAK = [  # 1점: 특이·예외·사실관계 의존
    (r"예외|특이|이례|의외|처음|최초|신설|새로\s*나온|최신", "특이·신규"),
    (r"사실관계(에\s*따라|를\s*봐야)|상황에\s*따라|다를\s*수|케이스별", "사실관계에 따라 결론 달라짐"),
    (r"심판|불복|심사청구|이의신청|경정청구|승소|인용", "불복·쟁송 사례"),
]


BOILER = re.compile(r"\[?공지\]?\s*본 포스팅은.*|본 포스팅은 신뢰할 수 있는.*|안녕하세요[^.]{0,40}(세무사|민셈|세금지킴이)[^.]{0,30}입니다\.?")


def star_of(title, desc):
    desc = BOILER.sub(" ", desc or "")          # 블로그마다 반복되는 인사말·면책 공지는 판단에서 제외
    t = (title or "") + " " + desc
    if not (RULING.search(t) or re.search(r"여부|특례|비과세|감면|중과|공제|과세|적용|판정", title or "")
            or any(re.search(rx, title or "") for rx, _ in STRONG + MID)):
        return -1, ""                       # 예규·판례 관련 글이 아니면 별점 표시 안 함
    why, lv = [], []
    for grp, pt in ((STRONG, 3), (MID, 2), (WEAK, 1)):
        for rx, label in grp:
            if re.search(rx, title or "") or re.search(rx, desc or ""):
                why.append(label)
                lv.append(pt)
    sc = max(lv) if lv else 0
    if sc and sc < 3 and len(lv) >= 2:     # 서로 다른 신호가 겹치면 한 단계 올림
        sc += 1
    return min(3, sc), ", ".join(dict.fromkeys(why))


def opendb():
    con = sqlite3.connect("seen.db")
    con.execute("CREATE TABLE IF NOT EXISTS seen(link TEXT PRIMARY KEY, blog TEXT, title TEXT, date TEXT, first_seen TEXT)")
    cols = [r[1] for r in con.execute("PRAGMA table_info(seen)")]
    for c in ("name", "cat", "descr"):
        if c not in cols:
            con.execute("ALTER TABLE seen ADD COLUMN %s TEXT" % c)
    return con


def main(open_browser=True, fetch_new=True):
    con = opendb()
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    first_run = con.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0
    blogs = load_blogs()
    errors, new_cnt = [], 0
    for bid, name in (blogs if fetch_new else []):
        try:
            items = fetch(bid)
        except Exception as e:
            errors.append("%s(%s): %s" % (name, bid, str(e)[:60]))
            print("  %-16s 실패" % name)
            continue
        for it in items:
            exists = con.execute("SELECT 1 FROM seen WHERE link=?", (it["link"],)).fetchone()
            if not exists:
                new_cnt += 1
            con.execute("""INSERT INTO seen(link,blog,title,date,first_seen,name,cat,descr) VALUES(?,?,?,?,?,?,?,?)
                           ON CONFLICT(link) DO UPDATE SET title=excluded.title, date=excluded.date,
                           name=excluded.name, cat=excluded.cat, descr=excluded.descr""",
                        (it["link"], bid, it["title"], it["date"], stamp, name, it["cat"], it["desc"]))
        print("  %-16s 확인 (%d건)" % (name, len(items)))
        time.sleep(0.5)
    con.commit()

    last_fetch = con.execute("SELECT MAX(first_seen) FROM seen").fetchone()[0]
    cutoff = (now - timedelta(days=DAYS)).strftime("%Y-%m-%d %H:%M")
    order = {bid: i for i, (bid, _) in enumerate(blogs)}
    names = {bid: n for bid, n in blogs}
    rows = con.execute("""SELECT blog, title, date, cat, descr, link, first_seen FROM seen
                          WHERE date >= ? ORDER BY date DESC""", (cutoff,)).fetchall()
    posts = []
    for b, t, d, c, s, u, fs in rows:
        if b not in names:
            continue
        is_new = (not first_run) and fs == (stamp if fetch_new else last_fetch)
        st, why = star_of(t, s)
        posts.append(dict(b=b, n=names[b], t=t or "", d=d or "", c=c or "", s=s or "", u=u, new=is_new, st=st, why=why))
    blog_list = [dict(id=bid, n=n) for bid, n in blogs]

    page = TEMPLATE.replace("__DATA__", json.dumps(dict(posts=posts, blogs=blog_list, days=DAYS,
                                                           cutoff=cutoff[:10], updated=stamp, errors=errors, static=bool(os.environ.get("STATIC_SITE")),
                                                           first=first_run), ensure_ascii=False).replace("</", "<\\/"))
    if os.environ.get("STATIC_SITE"):                  # 깃허브 자동 실행: site/index.html 로 출력
        os.makedirs("site", exist_ok=True)
        out = os.path.abspath(os.path.join("site", "index.html"))
        open(out, "w", encoding="utf-8").write(page)
    else:
        out = os.path.abspath("블로그뷰어.html")
        open(out, "w", encoding="utf-8").write(page)
        os.makedirs("결과", exist_ok=True)
        open(os.path.join("결과", "블로그뷰어_%s.html" % now.strftime("%Y%m%d_%H%M")), "w", encoding="utf-8").write(page)
    print("\n최근 %d일 글 %d건, 이번에 새로 올라온 글 %d건 → %s" % (DAYS, len(posts), 0 if first_run else new_cnt, out))
    if open_browser and (new_cnt or errors or first_run or "--open" in sys.argv):
        try:
            os.startfile(out)
        except Exception:
            pass
    return dict(total=len(posts), new=0 if first_run else new_cnt, errors=errors)


TEMPLATE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>블로그 모니터</title>
<meta name="theme-color" content="#1f3a5f"><meta name="robots" content="noindex,nofollow">
<style>
*{box-sizing:border-box}html,body{height:100%}body{margin:0;font-family:'Malgun Gothic',sans-serif;color:#222;background:#eef1f5;display:flex;flex-direction:column}
header{background:#1f3a5f;color:#fff;padding:8px 14px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
header h1{font-size:16px;margin:0}header .sub{font-size:12px;opacity:.8}
header .sp{margin-left:auto}header #q{padding:6px 10px;border-radius:6px;border:0;width:240px;font-size:13px}
header label{font-size:12px}header button{padding:6px 12px;border:0;border-radius:6px;background:#fff;color:#1f3a5f;font-weight:bold;cursor:pointer}
.err{background:#fde8e7;color:#b3261e;padding:6px 14px;font-size:12px}
#grid{flex:1;min-height:0;display:grid;grid-template-columns:repeat(6,1fr);grid-auto-rows:minmax(0,1fr);gap:0;border-top:1px solid #333;border-left:1px solid #333;background:#fff}
.card{border-right:1px solid #333;border-bottom:1px solid #333;display:flex;flex-direction:column;min-height:0;min-width:0}
.card .hd{padding:6px 8px;background:#f3f6fa;border-bottom:1px solid #d5dbe3;display:flex;align-items:center;gap:6px}
.card .hd a{font-weight:bold;font-size:14px;color:#1f3a5f;text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card .hd .c{margin-left:auto;font-size:11px;color:#888;white-space:nowrap}
.card.me .hd{background:#1f3a5f}.card.me .hd a{color:#fff}.card.me .hd .c{color:#cfd8e3}
.card .hd .nb{background:#e8453c;color:#fff;border-radius:9px;font-size:10px;padding:1px 6px}
.card .ls{flex:1;overflow-y:auto;padding:2px 0}
.it{display:flex;gap:6px;padding:4px 8px;font-size:12.5px;line-height:1.35;text-decoration:none;color:#222;border-bottom:1px dotted #eee}
.it:hover{background:#eef4ff}
.stars{color:#f5a623;font-size:11px;letter-spacing:-1px;white-space:nowrap}.stars.s0{color:#c9ced6}
.it .d{flex:0 0 38px;color:#888;font-size:11.5px;padding-top:1px}
.it .t{flex:1;min-width:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.it.new .t{font-weight:bold}.it.new .d{color:#e8453c;font-weight:bold}
.none{color:#aaa;font-size:12px;padding:10px}
.hl{background:#fff3a8}
#list{flex:1;overflow:auto;padding:10px 22px 30px}
#list .day{font-size:13px;font-weight:bold;color:#1f3a5f;margin:14px 0 6px;border-bottom:2px solid #c9d3e0;padding-bottom:3px}
#list .row{display:flex;gap:10px;align-items:baseline;background:#fff;border-radius:6px;padding:8px 12px;margin:4px 0;text-decoration:none;color:#222;box-shadow:0 1px 2px rgba(0,0,0,.05)}
#list .row:hover{background:#eef4ff}#list .who{flex:0 0 110px;background:#6b7c93;color:#fff;border-radius:4px;font-size:11px;padding:2px 6px;text-align:center}
#list .me .who{background:#e8453c}#list .tm{flex:0 0 40px;color:#888;font-size:12px}#list .tt{font-size:14px}#list .cat{color:#999;font-size:12px;margin-left:6px}
#list .new .tt{font-weight:bold}


@media(max-width:700px){
 html,body{height:auto}body{display:block}
 header{position:sticky;top:0;z-index:5}
 #grid{overflow:visible!important;grid-auto-rows:auto}
 .card{min-height:0}.card .ls{overflow:visible;flex:none}
 .more{display:block;margin-top:auto;width:100%;border:0;border-top:1px solid #e3e7ed;background:#f7f9fc;color:#1f3a5f;font-size:13px;padding:9px;cursor:pointer}
 #list{padding:8px 10px 30px}
 #list .row{flex-wrap:wrap;gap:4px 8px;padding:8px 10px}
 #list .who{flex:0 0 auto;padding:2px 8px}
 #list .tm{flex:0 0 auto}
 #list .tt{flex:1 1 100%;font-size:14px;line-height:1.45}
}
</style></head><body>
<header><h1>블로그 모니터</h1><div class="sub" id="sub"></div>
<div class="sp"></div><button id="vw" style="background:#3d5a80;color:#fff">날짜순 보기</button><label><input type="checkbox" id="nw"> 새 글만</label><label><input type="checkbox" id="st2"> ★★ 이상만</label>
<input id="q" placeholder="검색 (제목·요약·카테고리)"><button id="rf" style="display:none">⟳ 새로고침</button><button id="mb" style="display:none">📱 휴대폰</button></header>
<div id="mbox" style="display:none;background:#fffbe6;border-bottom:1px solid #e6d58a;padding:10px 14px;font-size:13px;line-height:1.7"></div>
<div id="err"></div><div id="grid"></div><div id="list" style="display:none"></div>
<script>
const DATA=__DATA__;const P=DATA.posts;let q='',onlyNew=false,only2=false;
function stars(p){if(p.st<0)return '';return '<span class="stars s'+p.st+'" title="'+esc(p.why||'특이사항 표현 없음')+'">'+'★'.repeat(p.st)+'☆'.repeat(3-p.st)+'</span> '}
function ok(p){return (!onlyNew||p.new)&&(!only2||p.st>=2)&&(!q||(p.t+p.s+p.c).includes(q))}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]))}
document.getElementById('sub').textContent='최근 '+DATA.days+'일 · 갱신 '+DATA.updated;
if(DATA.errors.length)document.getElementById('err').innerHTML='<div class="err">확인 실패: '+DATA.errors.map(esc).join(' / ')+'</div>';
let view='grid';const OPEN={};
function draw(){document.getElementById('grid').style.display=view=='grid'?'grid':'none';document.getElementById('list').style.display=view=='list'?'block':'none';
if(view=='list')return drawList();
// 빈칸 없는 배치: 창 너비로 칸 수를 정하고, 남는 칸만큼 앞쪽 블로그(내 블로그부터)를 세로 2칸으로 늘림
const n=DATA.blogs.length, W=window.innerWidth, g=document.getElementById('grid');
const mobile=W<700;
let cols=Math.max(2,Math.min(7,Math.floor(W/185)));
if(n<=14&&W>=1300)cols=Math.ceil(n/2);
const rows=Math.ceil(n/cols), span=mobile?0:Math.min(rows*cols-n, cols-1), wide=rows>=2;
g.style.gridTemplateColumns='repeat('+cols+',minmax(0,1fr))';
if(mobile){g.style.gridTemplateRows='';g.style.overflow='';}else{g.style.gridTemplateRows='repeat('+rows+',minmax(230px,1fr))';g.style.overflow='auto';}
let h='';
DATA.blogs.forEach((b,bi)=>{const all=P.filter(p=>p.b==b.id);const nn=all.filter(p=>p.new).length;
const L0=all.filter(ok);const lim=mobile&&!OPEN[b.id]?5:1e9;const L=L0.slice(0,lim);const rest=L0.length-L.length;
h+='<div class="card'+(bi==0?' me':'')+'" style="'+(wide&&bi<span?'grid-row:span 2':'')+'"><div class="hd"><a target="_blank" href="https://blog.naver.com/'+b.id+'" title="블로그 열기">'+esc(b.n)+'</a>'+(nn?'<span class="nb">'+nn+'</span>':'')+'<span class="c">'+all.length+'건</span></div><div class="ls">';
h+=L.length?L.map(p=>'<a class="it'+(p.new?' new':'')+'" target="_blank" href="'+p.u+'" title="'+esc(p.d+'\n'+p.t+(p.c?'\n['+p.c+']':'')+(p.why?'\n★ '+p.why:'')+'\n\n'+p.s)+'"><span class="d">'+p.d.slice(5,10)+'</span><span class="t">'+stars(p)+esc(p.t)+'</span></a>').join(''):'<div class="none">'+(q||onlyNew?'해당 글 없음':'최근 '+DATA.days+'일 글 없음')+'</div>';
if(mobile&&(rest>0||OPEN[b.id]&&L0.length>5))h+='<button class="more" onclick="OPEN[\''+b.id+'\']=!OPEN[\''+b.id+'\'];draw()">'+(OPEN[b.id]?'접기 ▲':'+'+rest+'건 더보기 ▼')+'</button>';
h+='</div></div>'});
document.getElementById('grid').innerHTML=h}
function drawList(){let d='',h='';const me=DATA.blogs.length?DATA.blogs[0].id:'';
P.filter(ok).forEach(p=>{const dy=p.d.slice(0,10);if(dy!=d){h+='<div class="day">'+dy+'</div>';d=dy}
h+='<a class="row'+(p.new?' new':'')+(p.b==me?' me':'')+'" target="_blank" href="'+p.u+'" title="'+esc(p.s)+'"><span class="who">'+esc(p.n)+'</span><span class="tm">'+p.d.slice(11)+'</span><span class="tt">'+(p.new?'<b style="color:#e8453c">NEW </b>':'')+stars(p)+esc(p.t)+(p.c?'<span class="cat">['+esc(p.c)+']</span>':'')+'</span></a>'});
document.getElementById('list').innerHTML=h||'<div style="text-align:center;color:#999;padding:50px">해당 글 없음</div>'}
document.getElementById('vw').onclick=e=>{view=view=='grid'?'list':'grid';e.target.textContent=view=='grid'?'날짜순 보기':'블로그별 보기';draw()};
document.getElementById('q').oninput=e=>{q=e.target.value.trim();draw()};
window.addEventListener('resize',()=>{clearTimeout(window._rt);window._rt=setTimeout(draw,150)});
document.getElementById('nw').onchange=e=>{onlyNew=e.target.checked;draw()};
document.getElementById('st2').onchange=e=>{only2=e.target.checked;draw()};
draw();
if(location.protocol.startsWith('http')&&!DATA.static){const rf=document.getElementById('rf');rf.style.display='inline-block';
rf.onclick=async()=>{rf.disabled=true;rf.textContent='확인 중…';try{await fetch('/refresh');}catch(e){}location.reload()};
setInterval(()=>fetch('/ping').catch(()=>{}),20000);
if(location.hostname=='127.0.0.1'){const mb=document.getElementById('mb');mb.style.display='inline-block';
mb.onclick=async()=>{const box=document.getElementById('mbox');if(box.style.display=='block'){box.style.display='none';return}
const r=await (await fetch('/info')).json();
box.innerHTML='<b>휴대폰에서 아래 주소로 접속하세요</b> (한 번 접속하면 이후 주소 끝 ?k=… 없이도 열림)<br>'+
(r.urls.length?r.urls.map(u=>esc(u.kind)+': <b style="user-select:all">'+esc(u.url)+'</b>').join('<br>'):'네트워크 주소를 찾지 못했습니다')+
(r.server?'':'<br><span style="color:#b3261e">※ 지금은 PC 창 모드입니다. 창을 닫으면 휴대폰에서도 안 열립니다. 7번 파일로 상시 실행을 켜두세요.</span>');
box.style.display='block'}}}
</script></body></html>"""


if __name__ == "__main__":
    main()
