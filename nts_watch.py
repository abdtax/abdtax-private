# -*- coding: utf-8 -*-
"""
국세법령정보시스템 새 해석례 모니터 (등록일 기준 최근 30일)
  - 사전답변 / 질의회신 / 과세기준자문 / 고시서면질의 / 법제처 해석례 / 세법해석정비
  - 깃허브 Actions(STATIC_SITE=1) → site/nts.html,  PC → nts_watch.html
  - 누르면 사실관계·질의내용(판례는 판결문 전문) 표시, 원문 페이지로 바로 이동
  - 추가 설치 필요 없음 (파이썬 기본 모듈만 사용)
사용:
  python nts_watch.py            새 해석례 확인 후 화면 파일 생성
  python nts_watch.py --open     생성 후 브라우저로 열기 (PC)
  python nts_watch.py --probe    첫 시험용: 사이트 응답 형식을 nts_probe.txt로 저장
"""
import json, os, re, sys, time, random, html, urllib.request, urllib.parse, http.cookiejar
from datetime import datetime, timedelta

DAYS = 30                      # 표시 기간 (등록일 기준)
DELAY = 1.5                    # 요청 간격(초) - 사이트 부담 최소화
BASE = "https://taxlaw.nts.go.kr"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TYPES = {"01": "사전답변", "02": "질의회신", "03": "과세기준자문", "04": "고시서면질의"}
PREC = {"05": "과세적부", "06": "이의신청", "07": "심사청구", "08": "심판청구", "09": "판례", "10": "헌재"}
SEEN = "nts_seen.json"
DETAIL = "nts_detail.json"      # 본문(사실관계·질의내용·판결문) 저장 - 한 번 받은 문서는 다시 받지 않음
MAX_DETAIL = 500               # 한 번 실행에 새로 받을 본문 최대 건수
STATIC = os.environ.get("STATIC_SITE") == "1"
OUT = os.path.join("site", "nts.html") if STATIC else "nts_watch.html"
OUT_JS = os.path.join(os.path.dirname(OUT), "nts_detail.js")
SAVEBOX = os.environ.get("SAVEBOX_URL", "https://13-125-115-202.sslip.io/savebox")   # 보관함 서버 (PC·휴대폰 공유)
DATE_FMTS = ["%Y%m%d", "%Y-%m-%d", "%Y.%m.%d"]
BUDGET = int(os.environ.get("NTS_BUDGET", "360"))   # 한 번 실행 최대 시간(초). 넘으면 받은 것까지만 화면 생성
DEADLINE = time.time() + BUDGET


# ---------------------------------------------------------------- 사이트 접속
class Client:
    def __init__(self):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.op.addheaders = [("User-Agent", UA), ("X-Requested-With", "XMLHttpRequest"),
                              ("Referer", BASE + "/qt/USEQTA001M.do")]
        self.last, self.fails, self.down = 0, 0, False
        try:
            self.op.open(BASE + "/qt/USEQTA001M.do?ntstDcmClCd=02", timeout=30).read()
        except Exception:
            pass

    def action(self, aid, param):
        wait = DELAY * random.uniform(0.9, 1.3) - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        if self.down:
            raise RuntimeError("사이트 응답 없음 (연속 실패로 이번 실행은 중단)")
        if time.time() > DEADLINE:
            raise RuntimeError("실행 시간 %d분 초과 (나머지는 다음 실행 때)" % (BUDGET // 60))
        body = urllib.parse.urlencode({"actionId": aid, "paramData": json.dumps(param, ensure_ascii=False)}).encode()
        err = None
        for i in range(2):
            try:
                left = max(5, min(30, DEADLINE - time.time()))
                r = self.op.open(BASE + "/action.do", data=body, timeout=left)
                self.last = time.time()
                d = json.loads(r.read().decode("utf-8"))
                if d.get("status") == "SUCCESS":
                    self.fails = 0
                    return d["data"][aid]
                err = "응답 상태 %s" % d.get("status")
            except Exception as e:
                err = str(e)[:120]
            if time.time() > DEADLINE:
                break
            time.sleep(3)
        self.fails += 1
        if self.fails >= 3:
            self.down = True       # 연속 3번 실패 = 사이트 장애로 보고 나머지 요청 생략
        raise RuntimeError(err)


def list_param(cl, start, view, sdt="", edt="", coll="question,question_gr"):
    return {"startCount": start, "viewCount": view, "schDtBase": "DCM_RGT_DTM", "bltnStrtDt": sdt, "bltnEndDt": edt,
            "collectionName": coll, "dcmClCdCtl": ["001_" + cl], "exclVcbCtl": [],
            "icldVcbCtl": [], "ntstTlawClCdList": [], "sortField": "DOC_ID/DESC"}


def total_of(data, cl):
    for c in data["top"][0]["categoryMap"]["SUB_ID_CATEGORY"]:
        if c["name"] == "001_" + cl:
            return int(c["count"])
    return 0


def ymd(v):
    """여러 날짜 표기를 YYYY-MM-DD로"""
    s = re.sub(r"\D", "", str(v or ""))[:8]
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if len(s) == 8 else ""


def pick_reg(d):
    """목록 항목에서 등록일 찾기 (필드명이 확인되지 않은 경우 대비해 후보 순서대로)"""
    for k in ("FRS_RGT_DTM", "FRST_RGT_DTM", "DCM_RGT_DTM", "NTST_DCM_RGT_DT"):
        if ymd(d.get(k)):
            return ymd(d.get(k))
    return ""


def pick_prod(d):
    for k in ("NTST_DCM_RGT_DT", "DCM_RGT_DTM"):
        if ymd(d.get(k)):
            return ymd(d.get(k))
    return ""


def clean(t):
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", t or "")
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"[ \t ]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


# ---------------------------------------------------------------- 수집
def fetch_all(cli, start_d, end_d, probe=None):
    items, totals, notes = [], {}, []
    # 1) 사전·질의·기준·고시: 문서ID 내림차순(최신 등록 순)으로 받아 등록일(FRS_RGT_DTM) 30일 이내만 사용
    #    (사이트의 날짜 검색 조건은 생산일 기준이라 쓰지 않음)
    sdt = start_d.strftime("%Y-%m-%d")
    groups = [(cl, n, "rul", "question,question_gr") for cl, n in TYPES.items()] + \
             [(cl, n, "prec", "precedent,precedent_gr") for cl, n in PREC.items()]
    for cl, name, kind, coll in groups:
        page, got, older = 1, 0, 0
        while page <= 5:
            try:
                d = cli.action("ASIPDI002PR01", list_param(cl, page, 500, coll=coll))
            except Exception as e:
                notes.append("%s 확인 실패: %s" % (name, e))
                break
            if page == 1:
                totals[name] = total_of(d, cl)
            body = d.get("body") or []
            for k, it in enumerate(body):
                x = it.get("dcm") or {}
                if probe is not None and page == 1 and k == 0:
                    probe.append("[%s] 첫 항목: 등록 %s / 생산 %s / 수정 %s / %s" % (name, x.get("FRS_RGT_DTM"), x.get("NTST_DCM_RGT_DT"),
                                 x.get("LST_ALT_DTM"), x.get("NTST_DCM_DSCM_CNTN")))
                reg = pick_reg(x)
                if reg and reg < sdt:
                    older += 1
                    continue
                mod = ymd(x.get("LST_ALT_DTM"))
                items.append({"id": str(x.get("DOC_ID") or x.get("DOCID")), "type": name, "kind": kind,
                              "no": (x.get("NTST_DCM_DSCM_CNTN") or "").strip(), "tax": x.get("NTST_TLAW_CL_NM") or "",
                              "t": clean(x.get("TTL")), "g": clean(x.get("GIST_CNTN")), "r": clean(x.get("CNTN"))[:3000],
                              "reg": reg, "prod": pick_prod(x), "mod": mod if mod and reg and mod > reg else ""})
            got += len(body)
            if not body or older >= 50 or got >= totals.get(name, 0):
                break          # 30일 이전 문서가 충분히 나오면 중단
            page += 1
        if probe is not None:
            probe.append("  %s: 최근 %d일 %d건 (받은 %d건)" % (name, DAYS, sum(1 for x in items if x["type"] == name), got))
    # 2) 법제처 해석례
    try:
        d = cli.action("ASIBGE004MR03", {"pageIndex": "1", "searchCondition": "title", "recordCountPerPage": "300",
                                          "ntstDcmClCdList": ["41"]})
        for x in d.get("dvoList") or []:
            reg = ymd(x.get("ntstDcmRgtDt"))
            if reg and reg >= start_d.strftime("%Y-%m-%d"):
                items.append({"id": str(x.get("ntstDcmId")), "type": "법제처해석례", "no": (x.get("ntstDcmDscmCntn") or "").strip(),
                              "tax": "", "t": clean(x.get("ntstDcmTtl")), "g": clean(x.get("ntstDcmGistCntn")),
                              "r": clean(x.get("ntstDcmCntn"))[:3000], "reg": reg, "prod": ""})
        totals["법제처해석례"] = int(d.get("recordCount") or len(d.get("dvoList") or []))
    except Exception as e:
        notes.append("법제처 해석례 확인 실패: %s" % e)
    # 3) 세법해석정비 (유지·삭제 공지)
    try:
        d = cli.action("ASIQTF001MR01", {"pageIndex": 1, "ntstItrpMntcClCd": "01", "bltnStrtDt": "", "bltnEndDt": "",
                                          "cntsPrtsNo": "", "stttInfpClCdList": ["01", "06"], "recordCountPerPage": 300})
        for x in d.get("itlMntcDVOList") or []:
            reg = ymd(x.get("frsRgtDtm"))
            if reg and reg >= start_d.strftime("%Y-%m-%d"):
                g = "유지: %s\n삭제: %s" % ((x.get("mntnCase") or "").strip(" ,"), (x.get("dltCase") or "").strip(" ,"))
                items.append({"id": "M" + str(x.get("ntstItrpMntcId")), "type": "세법해석정비", "no": "",
                              "tax": x.get("ntstTlawClNm") or "", "t": clean(x.get("ntstItrpMntcTtl")),
                              "g": g, "r": clean(x.get("ntstItrpMntcCntn"))[:3000], "reg": reg, "prod": ""})
        totals["세법해석정비"] = int(d.get("recordCount") or 0)
    except Exception as e:
        notes.append("세법해석정비 확인 실패: %s" % e)
    return items, totals, notes


def probe_prec(cli, out):
    """판례·결정례 화면의 호출 이름(actionId)을 찾기 위한 조사 (첫 시험용)"""
    def get(u):
        try:
            return cli.op.open(u if u.startswith("http") else BASE + u, timeout=30).read().decode("utf-8", "ignore")
        except Exception as e:
            out.append("  읽기 실패 %s: %s" % (u, str(e)[:80]))
            return ""
    pages, seen_js = [], set()
    for start in ("/", "/index.do", "/qt/USEQTJ001M.do"):
        h = get(start)
        for m in re.finditer(r'href="([^"#]+?\.do[^"]*)"[^>]*>([^<]{0,40})', h):
            u, txt = m.group(1), m.group(2)
            if any(k in txt for k in ("판례", "결정", "심판", "심사")) or re.search(r"/(pd|pr|jd|dc|cs)/", u):
                pages.append((u, txt.strip()))
    pages = list(dict.fromkeys(pages))[:15]
    out.append("\n[판례 관련 메뉴 후보]")
    out += ["  %s  (%s)" % p for p in pages]
    ids = {}
    for u, _ in pages:
        h = get(u)
        srcs = re.findall(r'<script[^>]+src="([^"]+)"', h) + [u]
        for sj in srcs:
            if sj in seen_js or "jquery" in sj.lower():
                continue
            seen_js.add(sj)
            t = h if sj == u else get(sj)
            for m in re.finditer(r"(collectionName|dcmClCdCtl|SUB_ID)[^\n]{0,160}", t):
                out.append("  [%s] %s" % (sj[-40:], re.sub(r"\s+", " ", t[max(0, m.start() - 120):m.end()])[:320]))
            for m in re.finditer(r"AS[A-Z]{3,5}\d{3}[A-Z]{2}\d{2}", t):
                ctx = re.sub(r"\s+", " ", t[max(0, m.start() - 150):m.end() + 250])
                ids.setdefault(m.group(0), (u, sj, ctx))
            time.sleep(DELAY)
    out.append("\n[호출 이름 후보 %d개]" % len(ids))
    for k, (u, sj, ctx) in ids.items():
        out.append("● %s  (화면 %s / 파일 %s)\n   %s" % (k, u, sj, ctx[:400]))


# ---------------------------------------------------------------- 본문 (사실관계·질의내용·판결문)
def clean_text(h):
    t = re.sub(r"data:image/[^\"')\s]+", "", h or "")
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", t)
    t = re.sub(r"(?i)</?(span|font|b|strong|a|i|u|em|sup|sub)\b[^>]*>", "", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


HEAD = r"(?:^|\n)\s*(?:[0-9]+|[가-하]|[IVX]+|[①-⑩])?\s*[.)]?\s*"
SECT = [("f", r"(?:사실\s*관계|질의\s*배경|사실관계\s*및\s*쟁점)"),
        ("q", r"(?:질의\s*내용|질의\s*요지|질의\s*사항|신청\s*내용)"),
        ("a", r"(?:회신\s*내용|회\s*신|답변\s*내용|답\s*변)"),
        ("l", r"(?:관련\s*법령|관계\s*법령|관련\s*규정)")]


def split_sections(text):
    marks = []
    for key, pat in SECT:
        m = re.search(HEAD + pat + (r"\s*[:：]?\s*(?=\n|$)" if key == "a" else r"\s*[:：]?"), text)
        if m:
            marks.append((m.start(), m.end(), key))
    marks.sort()
    out = {}
    for i, (s0, e, key) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out.setdefault(key, text[e:end].strip())
    return out


def fetch_detail(cli, x):
    d = cli.action("ASIQTB002PR01", {"dcmDVO": {"ntstDcmId": x["id"]}})
    h = "\n".join(v.get("dcmFleByte") or "" for v in (d.get("dcmHwpEditorDVOList") or []) if v.get("dcmFleTy") == "html")
    text = clean_text(h)
    if len(text) < 30:
        return {"x": "", "err": "본문이 한글파일·스캔 이미지로만 되어 있음 → 원문 열기로 확인"}
    sec = split_sections(text)
    r = {"f": sec.get("f", "")[:8000], "q": sec.get("q", "")[:4000]}
    if x.get("kind") == "prec" or not (r["f"] or r["q"]):
        r["x"] = text[:30000] + (" …(이하 원문에서 계속)" if len(text) > 30000 else "")
    return r


def tidy_prec(r):
    """판결문: '주 문'처럼 띄어 쓴 제목을 붙이고, 주문(결론)을 따로 뽑음"""
    x = r.get("x") or ""
    if not x:
        return r
    x = re.sub(r"(?m)^\s*((?:[가-힣] ){1,6}[가-힣])\s*$", lambda m: m.group(1).replace(" ", ""), x)
    x = re.sub(r"(?m)^[ \t]+", "", x)
    r["x"] = x
    m = re.search(r"(?m)^주문\s*\n(.+?)(?=\n(?:청구취지|항소취지|상고취지|이유|신청취지|심판청구취지)\s*\n|\Z)", x, re.S)
    if m and not r.get("o"):
        r["o"] = m.group(1).strip()[:2000]
    return r


def load_json(path, empty):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return empty


def fetch_details(cli, items, probe=None):
    """새로 뜬 문서만 본문을 받아 저장 (이미 받은 문서는 다시 요청하지 않음)"""
    det = load_json(DETAIL, {})
    todo = [x for x in items if not x["id"].startswith("M") and x["id"] not in det]
    todo.sort(key=lambda x: x["reg"], reverse=True)
    fail = 0
    for n, x in enumerate(todo[:MAX_DETAIL]):
        try:
            det[x["id"]] = fetch_detail(cli, x)
            det[x["id"]]["at"] = datetime.now().strftime("%Y-%m-%d")
        except Exception as e:
            fail += 1
            if fail >= 10 and fail > n // 2:
                break          # 계속 실패하면 중단 (다음 실행 때 다시 시도)
        if probe is not None and n < 2:
            probe.append("[본문 시험] %s %s → %s" % (x["type"], x["no"], json.dumps(det.get(x["id"]), ensure_ascii=False)[:600]))
        if (n + 1) % 50 == 0:
            print("  본문 %d / %d" % (n + 1, min(len(todo), MAX_DETAIL)))
    ids = {x["id"] for x in items}
    det = {k: tidy_prec(v) for k, v in det.items() if k in ids}          # 목록에서 빠진(30일 지난) 문서 정리
    with open(DETAIL, "w", encoding="utf-8") as f:
        json.dump(det, f, ensure_ascii=False)
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write("window.DT=" + json.dumps(det, ensure_ascii=False).replace("</", "<\\/") + ";")
    notes = []
    if len(todo) > MAX_DETAIL:
        notes.append("본문 %d건은 다음 실행 때 이어서 받습니다" % (len(todo) - MAX_DETAIL))
    if fail:
        notes.append("본문 %d건 받기 실패 (다음 실행 때 다시 시도)" % fail)
    return notes


# ---------------------------------------------------------------- 기록
def load_seen():
    try:
        with open(SEEN, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"first": {}, "totals": {}, "runs": []}


def save_seen(s):
    with open(SEEN, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)


# ---------------------------------------------------------------- 화면
PAGE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<meta name="theme-color" content="#1f3a5f"><title>새 해석례·판례 모니터</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:'Malgun Gothic',sans-serif;color:#222;background:#eef1f5}
header{background:#1f3a5f;color:#fff;padding:10px 14px;display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;position:sticky;top:0;z-index:5}
header h1{font-size:16px;margin:0}header .m{font-size:12px;color:#cfd8e3}header a{color:#cfd8e3;font-size:12px}
.bar{background:#fff;padding:8px 14px;display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;border-bottom:1px solid #d9dee6}
.bar input[type=search]{flex:1 1 180px;min-width:140px;padding:7px 9px;border:1px solid #c9d0da;border-radius:6px;font-size:14px}
.bar select{padding:6px;border:1px solid #c9d0da;border-radius:6px;font-size:13px}
.chip{border:1px solid #c9d0da;background:#fff;color:#1f3a5f;border-radius:14px;padding:4px 10px;font-size:12.5px;cursor:pointer}
.chip.on{background:#1f3a5f;color:#fff;border-color:#1f3a5f}
.tabs{display:flex;background:#fff;border-bottom:1px solid #d9dee6}.tab{flex:1;text-align:center;padding:10px 6px;font-size:14px;font-weight:bold;color:#6b7c93;cursor:pointer;border-bottom:3px solid transparent}.tab.on{color:#1f3a5f;border-bottom-color:#1f3a5f}.tab .n{font-weight:normal;font-size:12px;margin-left:4px}
.warn{background:#fff4d6;color:#7a5200;padding:7px 14px;font-size:12.5px;border-bottom:1px solid #f0d58c}
main{max-width:1100px;margin:0 auto;padding:6px 12px 40px}
.day{font-size:13px;font-weight:bold;color:#1f3a5f;margin:14px 0 6px;border-bottom:2px solid #c9d3e0;padding-bottom:3px}
.row{background:#fff;border-radius:6px;padding:9px 12px;margin:5px 0;box-shadow:0 1px 2px rgba(0,0,0,.05);cursor:pointer}
.row:hover{background:#f5f8ff}.top{display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center}
.ty{color:#fff;border-radius:4px;font-size:11px;padding:1px 6px}.tx{background:#eef1f5;color:#445;border-radius:4px;font-size:11px;padding:1px 6px}
.nw{background:#e8453c;color:#fff;border-radius:9px;font-size:10.5px;padding:1px 6px;font-weight:bold}
.tt{font-weight:bold;font-size:14px;margin:4px 0 2px;line-height:1.4}.no{color:#777;font-size:12px}
.dt{color:#888;font-size:11.5px;margin-left:auto;white-space:nowrap}
.g{font-size:13px;color:#333;line-height:1.5;margin-top:3px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.row.open .g{display:block;-webkit-line-clamp:none}.more{display:none;margin-top:6px;border-top:1px dashed #d4d9e1;padding-top:6px;font-size:12.5px;line-height:1.55;color:#333;white-space:pre-wrap}
.row.open .more{display:block}details{margin:4px 0}summary{color:#1f3a5f;font-weight:bold;cursor:pointer}.more b{color:#1f3a5f}.lk{display:inline-block;margin-top:6px;font-size:12px;color:#1f3a5f}
.none{text-align:center;color:#999;padding:50px}
.acts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.acts button{border:1px solid #c9d0da;background:#fff;color:#1f3a5f;border-radius:6px;padding:5px 10px;font-size:12.5px;cursor:pointer}
.acts button.sv{background:#fff7e0;border-color:#f0c14b;color:#8a5a00}.star{color:#f0a500;font-size:13px}
.memo{width:100%;margin-top:6px;min-height:52px;border:1px solid #c9d0da;border-radius:6px;padding:6px;font:13px 'Malgun Gothic',sans-serif}
.exp{margin-left:auto}.exp button{border:0;background:#1f3a5f;color:#fff;border-radius:6px;padding:6px 10px;font-size:12.5px;cursor:pointer}
.sb{background:#f4f7fb;border-bottom:1px solid #d9dee6;padding:7px 14px;font-size:12.5px;color:#445;display:none;align-items:center;flex-wrap:wrap;gap:6px 10px}.sb.on{display:flex}.sb b{color:#1f3a5f}.sb button{border:1px solid #c9d0da;background:#fff;color:#1f3a5f;border-radius:6px;padding:4px 9px;font-size:12px;cursor:pointer}.sb .ok{color:#2e7d32;font-weight:bold}.sb .bad{color:#e8453c;font-weight:bold}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#222;color:#fff;padding:8px 14px;border-radius:18px;font-size:13px;display:none;z-index:9}
@media(max-width:600px){header h1{font-size:15px}.dt{margin-left:0;width:100%}.tt{font-size:13.5px}}
</style></head><body>
<header><h1>새 해석례·판례 모니터</h1><span class="m">등록일 기준 최근 __DAYS__일 · 확인 __RUN__</span></header>
__WARN__
<div class="tabs"><div class="tab on" data-k="rul">해석례<span class="n" id="n_rul"></span></div><div class="tab" data-k="prec">판례·결정례<span class="n" id="n_prec"></span></div><div class="tab" data-k="saved">★ 보관함<span class="n" id="n_saved"></span></div></div>
<div class="sb" id="sb"></div>
<div class="bar">
<span class="chip on" data-p="0">전체 __DAYS__일</span><span class="chip" data-p="14">2주</span><span class="chip" data-p="7">1주</span><span class="chip" data-p="-1">오늘 새로 뜸</span>
<select id="ty"><option value="">유형 전체</option></select><select id="tx"><option value="">세목 전체</option></select>
<input type="search" id="q" placeholder="제목·요지·사실관계·사건번호 검색"><span class="exp" id="exp" style="display:none"><button id="csv">엑셀(CSV)로 저장</button> <button id="cpall">전체 복사</button></span>
</div>
<main id="list"></main><div id="toast"></div>
<script src="nts_detail.js"></script>
<script>
const D=__DATA__,TODAY="__TODAY__",DT=window.DT||{};
const LINK=x=>x.id[0]=='M'?'':'https://taxlaw.nts.go.kr/'+(x.kind=='prec'?'pd/USEPDA002P':'qt/USEQTA002P')+'.do?ntstDcmId='+x.id;
D.forEach(x=>{const d=DT[x.id]||{};x.f=d.f||'';x.q=d.q||'';x.o=d.o||'';x.x=d.x||'';x.err=d.err||''});
const COL={"사전답변":"#2e7d32","질의회신":"#1f3a5f","과세기준자문":"#7b3fa0","고시서면질의":"#6b7c93","법제처해석례":"#b35c00","세법해석정비":"#e8453c","과세적부":"#5d4037","이의신청":"#6d6d2e","심사청구":"#8a6d00","심판청구":"#b35c00","판례":"#0b6e6e","헌재":"#4a148c"};
const SB='__SAVEBOX__';
const LS={g(k,d){try{const v=localStorage.getItem(k);return v==null?d:JSON.parse(v)}catch(e){return d}},s(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
let SV=LS.g('nts_saved',{}),SQ=LS.g('nts_sbq',[]),SKEY=LS.g('nts_sbkey',''),SST='',SLAST='';
const DEV=(/Mobi|Android|iPhone/.test(navigator.userAgent)?'mobile':'pc');
function keep(){LS.s('nts_saved',SV)}
async function api(path,body){const o={method:body?'POST':'GET',headers:{'X-Key':SKEY,'X-Dev':DEV}};if(body){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}
const r=await fetch(SB+path,o);if(r.status==401){SST='key';throw new Error('열쇠 오류')}if(!r.ok)throw new Error('서버 '+r.status);return r.json()}
function sbBar(){const e=$('#sb');if(!e)return;e.classList.toggle('on',K=='saved');
let h;if(!SKEY)h='<span class="bad">● 이 기기에만 저장 중</span><span>PC·휴대폰 공유와 영구 보관을 하려면 서버 보관함에 연결하세요.</span><button id="sbc">보관함 연결</button>';
else if(SST=='key')h='<span class="bad">● 열쇠가 맞지 않습니다</span><button id="sbc">열쇠 다시 입력</button>';
else if(SST=='off')h='<span class="bad">● 서버 연결 안 됨</span><span>이 기기에 임시 저장 중'+(SQ.length?' (서버 반영 대기 '+SQ.length+'건)':'')+'</span><button id="sbr">다시 연결</button>';
else if(SST=='ok')h='<span class="ok">● 서버 보관함 연결됨</span><span>PC·휴대폰 공유 · '+Object.keys(SV).length+'건 · 동기화 '+SLAST+'</span><button id="sbr">새로고침</button><button id="sbx">연결 해제</button>';
else h='<span>서버 보관함 확인 중…</span>';
e.innerHTML=h;const c=$('#sbc'),r=$('#sbr'),x=$('#sbx');
if(c)c.onclick=()=>{const k=(prompt('보관함 열쇠를 입력하세요 (서버 설치 때 받은 값)')||'').trim();if(k){SKEY=k;LS.s('nts_sbkey',k);SST='';sbBar();sync(true)}};
if(r)r.onclick=()=>sync();if(x)x.onclick=()=>{if(confirm('이 기기의 서버 연결을 해제할까요? (서버 보관함 내용은 그대로 남습니다)')){SKEY='';LS.s('nts_sbkey','');SST='';sbBar()}}}
function queue(op){SQ=SQ.filter(q=>!(q.id==op.id&&(op.a!='memo'||q.a=='memo')));SQ.push(op);LS.s('nts_sbq',SQ);flush()}
let FL=false;async function flush(){if(!SKEY||FL)return;FL=true;try{while(SQ.length){const q=SQ[0];
if(q.a=='put')await api('/put',{id:q.id,item:q.item});else if(q.a=='del')await api('/del',{id:q.id});else if(q.a=='memo')await api('/memo',{id:q.id,memo:q.memo});
SQ.shift();LS.s('nts_sbq',SQ)}if(SST!='ok'){SST='ok';SLAST=new Date().toTimeString().slice(0,5)}}catch(e){if(SST!='key')SST='off'}FL=false;sbBar()}
async function sync(first){if(!SKEY){sbBar();return}try{
if(!LS.g('nts_sbinit',false)){const loc=Object.values(SV);const j=await api('/bulk',{items:loc});LS.s('nts_sbinit',true);if(loc.length)toast('이 기기 보관함 '+loc.length+'건을 서버와 합쳤습니다')}
await flush();if(SST=='key')throw 0;const j=await api('/list');const n={};j.items.forEach(x=>{if(String(x.id).startsWith('blog:'))return;n[x.id]=x});
SQ.forEach(q=>{if(q.a=='put')n[q.id]=Object.assign({},q.item,{id:q.id});if(q.a=='del')delete n[q.id];if(q.a=='memo'&&n[q.id])n[q.id].memo=q.memo});
SV=n;keep();SST='ok';SLAST=new Date().toTimeString().slice(0,5)}catch(e){if(SST!='key')SST='off'}
counts();sbBar();if(!document.activeElement||!document.activeElement.classList.contains('memo'))draw()}
const MT={};function memoLater(id,v){clearTimeout(MT[id]);MT[id]=setTimeout(()=>queue({a:'memo',id:id,memo:v}),800)}
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',1800)}
function txt(x){return '['+x.type+'] '+(x.no||'')+' (등록 '+(x.reg||'')+')\n'+x.t+'\n\n요지: '+(x.g||'')+(x.o?'\n\n주문: '+x.o:'')+(x.f?'\n\n사실관계: '+x.f:'')+(x.q?'\n\n질의내용: '+x.q:'')+(x.r?'\n\n회신: '+x.r.slice(0,800)+(x.r.length>800?' …':''):'')+(SV[x.id]&&SV[x.id].memo?'\n\n메모: '+SV[x.id].memo:'')+'\n\n원문: '+(LINK(x)||'https://taxlaw.nts.go.kr')}
function copy(s){if(navigator.clipboard)navigator.clipboard.writeText(s).then(()=>toast('복사했습니다'),()=>toast('복사 실패'));else toast('복사 실패')}
let P=0,K='rul';const $=s=>document.querySelector(s);D.forEach(x=>x.kind=x.kind||'rul');const curList=()=>K=='saved'?Object.values(SV).sort((a,b)=>(b.reg||'').localeCompare(a.reg||'')):D.filter(x=>x.kind==K);
const esc=s=>(s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function fill(id,key,label){const e=$(id);e.innerHTML='<option value="">'+label+'</option>';[...new Set(curList().map(x=>x[key]).filter(Boolean))].sort().forEach(v=>{const o=document.createElement('option');o.value=o.textContent=v;e.appendChild(o)})}
function refill(){fill('#ty','type','유형 전체');fill('#tx','tax','세목 전체')}
function counts(){['rul','prec'].forEach(k=>{$('#n_'+k).textContent=D.filter(x=>x.kind==k).length});$('#n_saved').textContent=Object.keys(SV).length}counts();refill();
function days(a,b){return Math.round((new Date(b)-new Date(a))/864e5)}
function draw(){const q=$('#q').value.trim().toLowerCase(),ty=$('#ty').value,tx=$('#tx').value;
let rows=curList().filter(x=>(!ty||x.type==ty)&&(!tx||x.tax==tx)&&(!q||(x.t+x.g+x.no+(x.f||'')+(x.q||'')).toLowerCase().includes(q))
&&(K=='saved'||P==0||(P==-1?x.new:days(x.reg,TODAY)<P)));$('#exp').style.display=K=='saved'?'':'none';
let h="",cur="";rows.forEach((x,i)=>{if(x.reg!=cur){cur=x.reg;h+='<div class="day">'+(cur||'등록일 미확인')+'</div>'}
h+='<div class="row" data-i="'+i+'"><div class="top"><span class="ty" style="background:'+(COL[x.type]||'#555')+'">'+esc(x.type)+'</span>'
+(SV[x.id]?'<span class="star">★</span>':'')+(x.tax?'<span class="tx">'+esc(x.tax)+'</span>':'')+(x.new?'<span class="nw">새로 뜸</span>':'')
+'<span class="no">'+esc(x.no)+'</span><span class="dt">등록 '+esc(x.reg)+(x.prod&&x.prod!=x.reg?' · 생산 '+esc(x.prod):'')+(x.mod?' · 수정 '+esc(x.mod):'')+(x.late?' · <b style="color:#e8453c">늦게 확인 '+esc(x.first)+'</b>':'')+'</span></div>'
+'<div class="tt">'+esc(x.t)+'</div><div class="g">'+esc(x.g)+'</div>'
+'<div class="more">'+(x.o?'<b>주문</b>\n'+esc(x.o)+'\n\n':'')+(x.f?'<b>사실관계</b>\n'+esc(x.f)+'\n\n':'')+(x.q?'<b>질의내용</b>\n'+esc(x.q)+'\n\n':'')+(x.r?'<b>'+(x.kind=='prec'?'요약':'회신')+'</b>\n'+esc(x.r)+'\n':'')
+(x.x?'<details><summary>'+(x.kind=='prec'?'판결·결정문 전문 보기':'본문 전체 보기')+'</summary>'+esc(x.x)+'</details>':'')+(x.err?'<span class="no">'+esc(x.err)+'</span>\n':'')
+(!x.f&&!x.q&&!x.x&&!x.err&&x.id[0]!='M'?'<span class="no">본문은 다음 확인 때 추가됩니다</span>\n':'')+'<div class="acts"><button data-a="save" class="'+(SV[x.id]?'sv':'')+'">'+(SV[x.id]?'★ 보관됨 (해제)':'☆ 보관')+'</button><button data-a="share">공유</button><button data-a="copy">내용 복사</button><button data-a="open">'+(LINK(x)?'원문 열기':'원문 찾기')+'</button></div>'
+(SV[x.id]?'<textarea class="memo" placeholder="메모 (보관함에 함께 저장)">'+esc(SV[x.id].memo||'')+'</textarea>':'')+'</div></div>'});
$('#list').innerHTML=h||'<div class="none">'+(K=='prec'&&!D.some(x=>x.kind=='prec')?'판례·결정례는 호출 주소 확인 후 표시됩니다':'해당 자료 없음')+'</div>';
const R=rows;document.querySelectorAll('.row').forEach(r=>{const x=R[+r.dataset.i];r.onclick=e=>{if(e.target.closest('.acts,.memo,details'))return;r.classList.toggle('open')};
r.querySelectorAll('.acts button').forEach(b=>b.onclick=e=>{e.stopPropagation();const a=b.dataset.a;
if(a=='save'){if(SV[x.id]){if(!confirm('보관을 해제할까요?\n\n['+(x.no||x.t)+']\n\n해제하면 PC·휴대폰 보관함에서 모두 빠지고, 이 문서에 쓴 메모도 보관함에서 사라집니다.'))return;delete SV[x.id];queue({a:'del',id:x.id});toast('보관 해제')}else{SV[x.id]=Object.assign({},x,{x:'',savedAt:TODAY,memo:''});queue({a:'put',id:x.id,item:SV[x.id]});toast(SKEY?'보관함에 저장 (PC·휴대폰 공유)':'이 기기 보관함에 저장')}keep();counts();const o=r.classList.contains('open');draw();const n=document.querySelector('.row[data-i="'+r.dataset.i+'"]');if(o&&n&&K!='saved')n.classList.add('open')}
if(a=='share'){const s=txt(x);if(navigator.share)navigator.share({title:x.t,text:s}).catch(()=>{});else copy(s)}
if(a=='copy')copy(txt(x));
if(a=='open'){if(LINK(x))window.open(LINK(x),'_blank');else{copy(x.no||x.t);window.open('https://taxlaw.nts.go.kr/qt/USEQTJ001M.do','_blank')}}});
const m=r.querySelector('.memo');if(m){m.onclick=e=>e.stopPropagation();m.oninput=()=>{SV[x.id].memo=m.value;keep();memoLater(x.id,m.value)}}})}
function dl(name,s,type){const b=new Blob([s],{type:type});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=name;document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},500)}
$('#csv').onclick=()=>{const q=v=>'"'+String(v||'').replace(/"/g,'""')+'"';const L=[['구분','유형','세목','문서번호','등록일','생산일','제목','요지','사실관계','질의내용','회신','메모','보관일','원문주소'].map(q).join(',')];
Object.values(SV).forEach(x=>L.push([x.kind=='prec'?'판례':'해석례',x.type,x.tax,x.no,x.reg,x.prod,x.t,x.g,x.f,x.q,x.r,x.memo,x.savedAt,LINK(x)].map(q).join(',')));dl('보관함_'+TODAY+'.csv','\ufeff'+L.join('\r\n'),'text/csv')};
$('#cpall').onclick=()=>copy(Object.values(SV).map(txt).join('\n\n────────\n\n'));
document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{document.querySelectorAll('.chip').forEach(z=>z.classList.remove('on'));c.classList.add('on');P=+c.dataset.p;draw()});
['#q','#ty','#tx'].forEach(s=>$(s).oninput=draw);
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{document.querySelectorAll('.tab').forEach(z=>z.classList.remove('on'));t.classList.add('on');K=t.dataset.k;refill();sbBar();draw()});draw();sbBar();sync();
document.addEventListener('visibilitychange',()=>{if(document.visibilityState=='visible')sync()});window.addEventListener('online',()=>sync());
</script></body></html>"""


def render(items, warn, run):
    items.sort(key=lambda x: (x["reg"], x["id"]), reverse=True)
    w = "".join('<div class="warn">%s</div>' % html.escape(m) for m in warn)
    page = (PAGE.replace("__DATA__", json.dumps(items, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__DAYS__", str(DAYS)).replace("__COUNT__", format(len(items), ","))
            .replace("__RUN__", run).replace("__SAVEBOX__", SAVEBOX).replace("__TODAY__", datetime.now().strftime("%Y-%m-%d")).replace("__WARN__", w))
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    now = datetime.now()
    end_d, start_d = now, now - timedelta(days=DAYS)
    today = now.strftime("%Y-%m-%d")
    probe = [] if "--probe" in sys.argv else None
    seen = load_seen()
    first_run = not seen["first"]
    base = seen.setdefault("base", today)      # 처음 실행한 날: 그날 잡힌 것은 "새로 뜸"으로 표시하지 않음
    warn = []
    try:
        cli = Client()
        items, totals, notes = fetch_all(cli, start_d, end_d, probe)
    except Exception as e:
        items, totals, notes = [], {}, []
        warn.append("국세법령정보시스템 접속 실패: %s (이전 화면 유지)" % e)
    if probe is not None:
        with open("nts_probe.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(probe + notes + warn))
        print("nts_probe.txt 저장")
    if not items and warn and os.path.exists(OUT):
        print(warn[0])
        return
    warn += notes
    if items:
        try:
            warn += fetch_details(cli, items, probe)
        except Exception as e:
            warn.append("본문 받기 실패: %s" % e)
        if probe is not None:
            with open("nts_probe.txt", "a", encoding="utf-8") as f:
                f.write("\n" + "\n".join(p for p in probe if p.startswith("[본문")))
    # 새로 뜬 것 / 늦게 뜬 것 표시
    new_by_type = {}
    for x in items:
        f = seen["first"].get(x["id"])
        if not f:
            f = seen["first"][x["id"]] = today
            if not first_run:
                new_by_type[x["type"]] = new_by_type.get(x["type"], 0) + 1
        x["first"] = f
        x["new"] = (f == today and f != base)
        x["late"] = bool(x["reg"] and f != base and (datetime.strptime(f, "%Y-%m-%d") - datetime.strptime(x["reg"], "%Y-%m-%d")).days > 3)
    # 전체 건수 비교: 사이트 건수가 늘어난 만큼 새로 확인되지 않으면 경고
    for t, n in totals.items():
        prev = seen["totals"].get(t)
        if prev is not None and n - prev > new_by_type.get(t, 0):
            warn.append("%s: 사이트 전체 건수가 %d건 늘었는데 최근 %d일 등록분에서 %d건만 새로 확인됨 → 과거 등록일로 추가된 문서가 있을 수 있음"
                        % (t, n - prev, DAYS, new_by_type.get(t, 0)))
        if prev is not None and n < prev:
            warn.append("%s: 사이트 전체 건수가 %d건 줄었음 (삭제·비공개 전환 가능성)" % (t, prev - n))
    seen["totals"].update(totals)
    # 60일 지난 기록 정리
    cut = (now - timedelta(days=DAYS * 2)).strftime("%Y-%m-%d")
    seen["first"] = {k: v for k, v in seen["first"].items() if v >= cut}
    save_seen(seen)
    render(items, warn, now.strftime("%m.%d %H:%M"))
    np = sum(1 for x in items if x.get("kind") == "prec")
    print("해석례 %d건 / 판례·결정례 %d건 (새로 뜸 %d건) → %s" % (len(items) - np, np, sum(1 for x in items if x["new"]), OUT))
    if "--open" in sys.argv:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(OUT))


if __name__ == "__main__":
    main()
