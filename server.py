#!/usr/bin/env python3
"""EcoPoints backend. Python 3.8+ standard library only. Run: python3 server.py"""
import os,re,json,sqlite3,hashlib,secrets,time,base64,hmac
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from http.cookies import SimpleCookie
ROOT=os.path.dirname(os.path.abspath(__file__))
DATA=os.environ.get("ECO_DATA","/tmp/ecopoints" if os.environ.get("VERCEL") else os.path.join(ROOT,"data"));UP=os.path.join(DATA,"uploads")
PORT=int(os.environ.get("PORT",8000));HOST=os.environ.get("HOST","127.0.0.1")
ALLOW_DEMO=os.environ.get("ALLOW_DEMO","1")=="1";SECURE=os.environ.get("COOKIE_SECURE")=="1" or bool(os.environ.get("VERCEL"))
CATS=["plastic","paper","metal","dry","ewaste","battery","other"]
VS={"ok":"Points Awarded","rev":"Needs Review","rej":"Rejected"}
CS=["Awaiting collection","Collected","Handed over for recycling"]
NAME=re.compile(r"^[\w .\-]{1,16}$");LABEL=re.compile(r"^[\w .\-&/()]{1,40}$");USER=re.compile(r"^[A-Za-z0-9_.\-]{3,24}$")
SCHEMA="""
create table if not exists users(id integer primary key,username text unique collate nocase,name text,dept text default '',role text default 'student',pw text,created int);
create table if not exists sessions(id text primary key,uid int,exp int);
create table if not exists points(id integer primary key,name text,tok text unique,active int default 1);
create table if not exists cfg(k text primary key,v text);
create table if not exists subs(id text primary key,uid int,type text,cat text,cp int,conf real,hash text,vs text,cs text,reason text,pts int,demo int,t int);
create table if not exists ledger(id integer primary key,uid int,sid text unique,pts int,reason text,t int);
create table if not exists rewards(id integer primary key,name text,req int);
create table if not exists reds(id integer primary key,uid int,name text,status text,t int);
create table if not exists audit(id integer primary key,uid int,a text,d text,t int);
create table if not exists proofs(id text primary key,uid int,cp int,exp int,used int default 0);
create index if not exists ix_sub_uid on subs(uid,t);create index if not exists ix_led_uid on ledger(uid);
"""
ms=lambda:int(time.time()*1000)
def conn():
    c=sqlite3.connect(os.path.join(DATA,"eco.db"),timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row;c.execute("pragma journal_mode=wal");return c
def hpw(p,salt=None):
    salt=salt or secrets.token_hex(16);return salt+"$"+hashlib.pbkdf2_hmac("sha256",p.encode(),bytes.fromhex(salt),200000).hex()
def vpw(p,s):return "$" in s and hmac.compare_digest(hpw(p,s.split("$")[0]),s)
def cfg(db):return {r["k"]:json.loads(r["v"]) for r in db.execute("select * from cfg")}
def audit(db,uid,a,d):db.execute("insert into audit(uid,a,d,t) values(?,?,?,?)",(uid,a,str(d),ms()))
def init():
    os.makedirs(UP,exist_ok=True);db=conn();db.executescript(SCHEMA)
    if db.execute("select 1 from users").fetchone():return
    pw=os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9);au=os.environ.get("ADMIN_USER","admin")
    db.execute("insert into users(username,name,role,pw,created) values(?,?,?,?,?)",(au,"Admin","admin",hpw(pw),ms()))
    print(f"\n*** First run. Admin login -> username: {au}  password: {pw}  (shown once) ***\n")
    for n,t,a in[("Library Block Bin","ECO-LIB-01",1),("Canteen E-Waste Box","ECO-CAN-02",1),("Hostel Gate Bin","ECO-HOS-03",1),("Old Gym Bin","ECO-GYM-04",0)]:
        db.execute("insert into points(name,tok,active) values(?,?,?)",(n,t,a))
    for k,v in{"v":{"plastic":10,"paper":10,"metal":10,"dry":5,"ewaste":20,"battery":20,"other":5},"report":5,"daily":5,"thr":0.6}.items():
        db.execute("insert into cfg values(?,?)",(k,json.dumps(v)))
    for n,r in[("Eco-achiever badge",50),("Digital certificate",150),("Participation recognition",300)]:db.execute("insert into rewards(name,req) values(?,?)",(n,r))
    if os.environ.get("SEED_DEMO","1")=="1":   # fake leaderboard rows; they cannot log in
        for n,d,p,m in[("GreenFox","Civil",420,90),("RiverMint","CSE",365,70),("LeafyLee","Mech",240,60),("SunnyPine","ECE",180,30)]:
            uid=db.execute("insert into users(username,name,dept,pw,created) values(?,?,?,?,?)",("demo_"+n,n,d,"!",ms())).lastrowid
            db.execute("insert into ledger(uid,sid,pts,reason,t) values(?,?,?,?,?)",(uid,f"seed:{uid}:a",p-m,"Seed",ms()-60*86400000))
            db.execute("insert into ledger(uid,sid,pts,reason,t) values(?,?,?,?,?)",(uid,f"seed:{uid}:b",m,"Seed",ms()))
class Err(Exception):
    def __init__(s,code,msg):s.code,s.msg=code,msg
ROUTES=[]
def route(m,rx,role="user"):
    def d(f):ROUTES.append((m,re.compile(rx+"$"),f,role));return f
    return d
def sub(r):return dict(id=r["id"],type=r["type"],cat=r["cat"],cp=str(r["cp"]),img=f"/api/submissions/{r['id']}/image",ai={"cat":r["cat"],"conf":r["conf"]},vs=VS[r["vs"]],cs=r["cs"],reason=r["reason"],t=r["t"],pts=r["pts"],demo=r["demo"])
def award(db,uid,sid,pts,reason):db.execute("insert or ignore into ledger(uid,sid,pts,reason,t) values(?,?,?,?,?)",(uid,sid,pts,reason,ms()))
def balance(db,uid):return db.execute("select coalesce(sum(pts),0) from ledger where uid=?",(uid,)).fetchone()[0]
def midnight():lt=time.localtime();return int(time.mktime((lt.tm_year,lt.tm_mon,lt.tm_mday,0,0,0,0,0,-1))*1000)
def need(b,k,rx):
    v=b.get(k)
    if not isinstance(v,str) or not rx.match(v.strip()):raise Err(400,f"Invalid {k}")
    return v.strip()
FAILS={}
def setcookie(tok,age):return ("Set-Cookie",f"eco_sid={tok}; HttpOnly; SameSite=Lax; Path=/; Max-Age={age}"+("; Secure" if SECURE else ""))
def newsession(db,uid):
    tok=secrets.token_urlsafe(32);db.execute("insert into sessions values(?,?,?)",(hashlib.sha256(tok.encode()).hexdigest(),uid,ms()+7*86400000));return tok
@route("POST","/api/auth/register",None)
def register(h,db,u):
    b=h.body;un=need(b,"username",USER);nm=need(b,"name",NAME);dp=(b.get("dept") or "").strip()
    if dp and not NAME.match(dp):raise Err(400,"Invalid dept")
    pw=b.get("password")
    if not isinstance(pw,str) or not 8<=len(pw)<=100:raise Err(400,"Password must be 8+ characters")
    try:uid=db.execute("insert into users(username,name,dept,pw,created) values(?,?,?,?,?)",(un,nm,dp,hpw(pw),ms())).lastrowid
    except sqlite3.IntegrityError:raise Err(409,"Username already taken")
    return {"ok":True},200,[setcookie(newsession(db,uid),604800)]
@route("POST","/api/auth/login",None)
def login(h,db,u):
    un=str(h.body.get("username","")).strip();k=(h.client_address[0],un.lower());now=time.time()
    FAILS[k]=[t for t in FAILS.get(k,[]) if now-t<300]
    if len(FAILS[k])>=5:raise Err(429,"Too many attempts. Try again in a few minutes.")
    r=db.execute("select * from users where username=?",(un,)).fetchone()
    if not r or not vpw(str(h.body.get("password","")),r["pw"]):FAILS[k].append(now);raise Err(401,"Wrong username or password")
    return {"ok":True},200,[setcookie(newsession(db,r["id"]),604800)]
@route("POST","/api/auth/logout","user")
def logout(h,db,u):
    db.execute("delete from sessions where uid=?",(u["id"],)) if h.body.get("all") else db.execute("delete from sessions where id=?",(h.sid,));return {"ok":True},200,[setcookie("",0)]
@route("PATCH","/api/me")
def rename(h,db,u):db.execute("update users set name=? where id=?",(need(h.body,"name",NAME),u["id"]));return {"ok":True}
@route("GET","/api/state")
def state(h,db,u):
    adm=u["role"]=="admin";q=lambda s,*a:db.execute(s,a).fetchall()
    cps=[dict(id=str(r["id"]),name=r["name"],on=r["active"],**({"tok":r["tok"]} if adm else {})) for r in q("select * from points")]
    if adm:
        subs=q("select * from subs order by t desc limit 300");led=q("select pts,reason,t from ledger order by t desc limit 5000")
        reds=q("select r.name||' - '||u.name as name,r.status from reds r join users u on u.id=r.uid order by r.t desc limit 50")
        au=[dict(a=r["a"],d=r["d"],t=r["t"]) for r in q("select * from audit order by id desc limit 50")];others=[]
    else:
        subs=q("select * from subs where uid=? order by t desc limit 300",u["id"]);led=q("select pts,reason,t from ledger where uid=? order by t",u["id"])
        reds=q("select name,status from reds where uid=? order by t desc",u["id"]);au=[]
        mo=time.mktime(time.localtime()[:2]+(1,0,0,0,0,0,-1))*1000
        others=[dict(n=r["name"],d=r["dept"] or "-",p=r["p"],m=r["m"]) for r in q("select u.name,u.dept,coalesce(sum(case when l.pts>0 then l.pts end),0) p,coalesce(sum(case when l.pts>0 and l.t>=? then l.pts end),0) m from users u left join ledger l on l.uid=u.id where u.role='student' and u.id!=? group by u.id",mo,u["id"])]
    return dict(me={"name":u["name"]},role=u["role"],cps=cps,cfg=cfg(db),subs=[sub(r) for r in subs],led=[dict(r) for r in led],rewards=[dict(r) for r in q("select * from rewards order by req")],reds=[dict(r) for r in reds],audit=au,others=others)
@route("POST","/api/qr/verify")
def qr(h,db,u):
    try:cp=int(h.body.get("cp"))
    except(TypeError,ValueError):raise Err(400,"Choose a collection point first.")
    p=db.execute("select * from points where id=? and active=1",(cp,)).fetchone();t=str(h.body.get("token","")).strip()
    if not p or not hmac.compare_digest(p["tok"],t):raise Err(400,"Invalid or deactivated QR code for this point.")
    pid=secrets.token_urlsafe(16);db.execute("insert into proofs values(?,?,?,?,0)",(pid,u["id"],cp,ms()+600000));return {"ok":True,"proof":pid}
def ham(a,b):return sum(x!=y for x,y in zip(a,b))
def classify(frames,thumb,declared):
    """PLUG-IN POINT: replace with a real image classifier. Returns (category,confidence). Currently a placeholder."""
    L=frames[5];m=sum(L)/1024;vr=sum((x-m)**2 for x in L)/1024
    return declared,min(.97,max(.2,.4+min(vr,2500)/2500*.55)),vr
@route("POST","/api/submissions")
def submit(h,db,u):
    b=h.body;typ,cat,fr,demo=b.get("type"),b.get("cat"),b.get("frames"),bool(b.get("demo"))
    if typ not in("dispose","report") or cat not in CATS:raise Err(400,"Invalid type or category")
    if demo and not ALLOW_DEMO:raise Err(400,"Demo camera is disabled on this server")
    try:cp=int(b.get("cp"))
    except(TypeError,ValueError):raise Err(400,"Choose a collection point")
    if not db.execute("select 1 from points where id=? and active=1",(cp,)).fetchone():raise Err(400,"Choose an active collection point")
    if not(isinstance(fr,list) and len(fr)==6 and all(isinstance(f,list) and len(f)==1024 and all(isinstance(x,(int,float)) for x in f) for f in fr)):raise Err(400,"Invalid camera data")
    m=re.fullmatch(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)",str(b.get("th","")))
    img=base64.b64decode(m.group(1)) if m else b""
    if not img.startswith(b"\xff\xd8") or len(img)>400000:raise Err(400,"A live photo is required")
    L=fr[5];mean=sum(L)/1024;mo=sum(sum(abs(a-c) for a,c in zip(fr[i],fr[i-1]))/1024 for i in range(1,6))/5
    hs="".join("1" if sum(L[(y0*4+y)*32+x0*4+x] for y in range(4) for x in range(4))/16>mean else "0" for y0 in range(8) for x0 in range(8))
    ai,conf,vr=classify(fr,img,cat);now=ms();sid="s"+secrets.token_hex(6)
    db.execute("begin immediate")
    try:
        c=cfg(db);qr_ok=False
        pr=b.get("proof")
        if pr:
            qr_ok=db.execute("update proofs set used=1 where id=? and uid=? and cp=? and used=0 and exp>?",(pr,u["id"],cp,now)).rowcount==1
        day=db.execute("select count(*) from subs where uid=? and vs='ok' and t>=?",(u["id"],midnight())).fetchone()[0]
        dupe=any(ham(hs,r[0])<=5 for r in db.execute("select hash from subs where hash!='' order by t desc limit 2000"))
        same=db.execute("select 1 from subs where uid=? and cp=? and cat=? and vs='ok' and t>?",(u["id"],cp,cat,now-600000)).fetchone()
        vs,why="ok",""
        if vr<60:vs,why="rej","The image looks blank or unclear. Try again with better light."
        elif typ=="dispose" and not qr_ok:vs,why="rej","Disposal needs a valid QR scan."
        elif mo<.8 and not demo:vs,why="rev","No live movement detected. Hold the phone and move slightly."
        elif dupe:vs,why="rev","Looks similar to an earlier photo. Sent for review."
        elif same:vs,why="rev","This point and category already earned points recently."
        elif conf<c["thr"]:vs,why="rev","Low image confidence. Sent for review."
        elif day>=c["daily"]:vs,why="rev","Daily reward limit reached."
        pts=(c["v"][cat] if typ=="dispose" else c["report"]) if vs=="ok" else 0
        if demo:why=(why+" " if why else "")+"Demo camera used."
        db.execute("insert into subs values(?,?,?,?,?,?,?,?,?,?,?,?,?)",(sid,u["id"],typ,cat,cp,conf,hs,vs,CS[0],why,pts,int(demo),now))
        if pts:award(db,u["id"],sid,pts,("Verified disposal: " if typ=="dispose" else "Waste report: ")+cat.capitalize())
        audit(db,u["id"],"verify:"+VS[vs],f"{sid} {why}");db.execute("commit")
    except BaseException:db.execute("rollback");raise
    open(os.path.join(UP,sid+".jpg"),"wb").write(img)
    return {"submission":sub(db.execute("select * from subs where id=?",(sid,)).fetchone())}
@route("GET",r"/api/submissions/(s[0-9a-f]+)/image")
def image(h,db,u,sid):
    r=db.execute("select uid from subs where id=?",(sid,)).fetchone()
    if not r or(r["uid"]!=u["id"] and u["role"]!="admin"):raise Err(404,"Not found")
    return open(os.path.join(UP,sid+".jpg"),"rb").read(),200,[("Content-Type","image/jpeg")]
@route("POST","/api/redeem")
def redeem(h,db,u):
    db.execute("begin immediate")
    try:
        r=db.execute("select * from rewards where id=?",(h.body.get("id"),)).fetchone()
        if not r:raise Err(404,"Unknown reward")
        if balance(db,u["id"])<r["req"]:raise Err(400,"Not enough points")
        award(db,u["id"],"red:"+secrets.token_hex(6),-r["req"],"Redeemed: "+r["name"])
        db.execute("insert into reds(uid,name,status,t) values(?,?,?,?)",(u["id"],r["name"],"Requested",ms()));audit(db,u["id"],"redeem",r["name"]);db.execute("commit")
    except BaseException:db.execute("rollback");raise
    return {"ok":True}
@route("POST","/api/admin/points","admin")
def addpt(h,db,u):
    n=need(h.body,"name",LABEL);tok="ECO-"+secrets.token_hex(2).upper()+"-"+str(db.execute("select count(*)+1 from points").fetchone()[0])
    db.execute("insert into points(name,tok) values(?,?)",(n,tok));audit(db,u["id"],"point added",n);return {"ok":True}
@route("PATCH",r"/api/admin/points/(\d+)","admin")
def togpt(h,db,u,i):
    db.execute("update points set active=? where id=?",(int(bool(h.body.get("active"))),i));audit(db,u["id"],"point",f"{i} active={h.body.get('active')}");return {"ok":True}
@route("PUT","/api/admin/config","admin")
def setcfg(h,db,u):
    b=h.body;v=b.get("v")
    ok=lambda x:isinstance(x,int) and not isinstance(x,bool) and 0<=x<=1000
    if not(isinstance(v,dict) and set(v)==set(CATS) and all(map(ok,v.values())) and ok(b.get("report")) and ok(b.get("daily"))):raise Err(400,"Invalid config")
    for k in("v","report","daily"):db.execute("update cfg set v=? where k=?",(json.dumps(b[k]),k))
    audit(db,u["id"],"config",json.dumps(b));return {"ok":True}
@route("POST",r"/api/admin/submissions/(s[0-9a-f]+)/decision","admin")
def decide(h,db,u,sid):
    db.execute("begin immediate")
    try:
        r=db.execute("select * from subs where id=? and vs='rev'",(sid,)).fetchone()
        if not r:raise Err(404,"Not awaiting review")
        if h.body.get("approve"):
            c=cfg(db);pts=c["v"][r["cat"]] if r["type"]=="dispose" else c["report"]
            db.execute("update subs set vs='ok',pts=? where id=?",(pts,sid));award(db,r["uid"],sid,pts,"Approved: "+r["cat"].capitalize())
        else:db.execute("update subs set vs='rej',reason='Rejected by admin.' where id=?",(sid,))
        audit(db,u["id"],"approve" if h.body.get("approve") else "reject",sid);db.execute("commit")
    except BaseException:db.execute("rollback");raise
    return {"ok":True}
@route("PATCH",r"/api/admin/submissions/(s[0-9a-f]+)/collection","admin")
def coll(h,db,u,sid):
    if h.body.get("cs") not in CS:raise Err(400,"Invalid status")
    db.execute("update subs set cs=? where id=? and vs='ok'",(h.body["cs"],sid));audit(db,u["id"],"collection",h.body["cs"]);return {"ok":True}
@route("POST","/api/admin/redemptions/(\\d+)","admin")
def fulfil(h,db,u,i):
    db.execute("update reds set status='Fulfilled' where id=?",(i,));audit(db,u["id"],"fulfil",i);return {"ok":True}
class H(BaseHTTPRequestHandler):
    server_version="EcoPoints";sid=None
    def log_message(s,*a):pass
    def out(s,code,body,ct="application/json",extra=()):
        s.send_response(code);hd=dict(extra);hd.setdefault("Content-Type",ct)
        s.send_header("Content-Length",str(len(body)))
        for k,v in{"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"same-origin","Cache-Control":"no-store",**hd}.items():s.send_header(k,v)
        s.end_headers();s.wfile.write(body)
    def go(s):
        path=s.path.split("?")[0];m=s.command;db=None
        try:
            if m=="GET" and path=="/":return s.out(200,open(os.path.join(ROOT,"public","index.html"),"rb").read(),"text/html; charset=utf-8")
            if not path.startswith("/api/"):raise Err(404,"Not found")
            s.body={}
            if m!="GET":
                o=s.headers.get("Origin")
                if o and o.split("://",1)[-1]!=s.headers.get("Host"):raise Err(403,"Bad origin")
                n=int(s.headers.get("Content-Length") or 0)
                if n>2_000_000:raise Err(413,"Request too large")
                try:s.body=json.loads(s.rfile.read(n) or b"{}")
                except ValueError:raise Err(400,"Bad JSON")
                if not isinstance(s.body,dict):raise Err(400,"Bad JSON")
            for rm,rx,fn,role in ROUTES:
                g=rx.match(path)
                if rm!=m or not g:continue
                db=conn();u=None
                ck=SimpleCookie(s.headers.get("Cookie",""));
                if "eco_sid" in ck:
                    s.sid=hashlib.sha256(ck["eco_sid"].value.encode()).hexdigest()
                    u=db.execute("select u.* from sessions s join users u on u.id=s.uid where s.id=? and s.exp>?",(s.sid,ms())).fetchone()
                if role and not u:raise Err(401,"Please log in")
                if role=="admin" and u["role"]!="admin":raise Err(403,"Admins only")
                r=fn(s,db,u,*g.groups())
                if isinstance(r,dict):return s.out(200,json.dumps(r).encode())
                body,code,ex=r
                return s.out(code,body if isinstance(body,bytes) else json.dumps(body).encode(),extra=ex)
            raise Err(404,"Not found")
        except Err as e:s.out(e.code,json.dumps({"error":e.msg}).encode())
        except Exception as e:
            print("ERROR",repr(e));s.out(500,b'{"error":"Server error"}')
        finally:
            if db:db.close()
    do_GET=do_POST=do_PATCH=do_PUT=go
# ---- WSGI entry point (used by Vercel; python3 server.py still works locally) ----
from http import HTTPStatus
class _W(H):
    def __init__(s,env):
        s.command=env["REQUEST_METHOD"];s.path=env.get("PATH_INFO","/")+("?"+env["QUERY_STRING"] if env.get("QUERY_STRING") else "")
        s.headers={k[5:].replace("_","-").title():v for k,v in env.items() if k.startswith("HTTP_")}
        s.headers["Content-Length"]=env.get("CONTENT_LENGTH") or "0";s.rfile=env["wsgi.input"];s.sid=None;s.res=None
        s.client_address=((env.get("HTTP_X_FORWARDED_FOR") or env.get("REMOTE_ADDR") or "?").split(",")[0].strip(),0)
    def out(s,code,body,ct="application/json",extra=()):
        hd={"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"same-origin","Cache-Control":"no-store","Content-Type":ct};hd.update(dict(extra));s.res=(code,hd,body)
def app(env,start):
    h=_W(env);h.go();code,hd,body=h.res
    start(f"{code} {HTTPStatus(code).phrase}",[*hd.items(),("Content-Length",str(len(body)))]);return [body]
if os.environ.get("VERCEL"):init()
if __name__=="__main__":
    init();print(f"EcoPoints running at http://{HOST}:{PORT}");ThreadingHTTPServer((HOST,PORT),H).serve_forever()
