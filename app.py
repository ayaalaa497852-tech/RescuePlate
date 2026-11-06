"""RescuePlate — full-stack campus surplus food reservation platform."""
import os, re, io, json, time, hmac, uuid, hashlib, secrets, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Optional
import qrcode
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from starlette.responses import JSONResponse
from collections import defaultdict, deque
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).parent
DB_FILE = Path(os.getenv('RESCUEPLATE_DB', str(BASE/'rescueplate.sqlite3')))
COOKIE_SECURE = os.getenv('COOKIE_SECURE', '0') == '1'
SESSION_DAYS = 14
MAX_PICKUP_WINDOW_MINUTES = 120
BOOKING_WINDOW_MINUTES = 60
MIN_BOOKING_LEAD_MINUTES = 10
DEMO_MODE = os.getenv('DEMO_MODE', '1') == '1'
app = FastAPI(title='RescuePlate', version='1.0.0', docs_url='/api/docs', redoc_url=None)
app.mount('/static', StaticFiles(directory=BASE/'static'), name='static')
_LOGIN_ATTEMPTS = defaultdict(deque)

@app.middleware('http')
async def security_middleware(request:Request, call_next):
    # Mutating JSON endpoints require a same-origin JS request, plus a per-session CSRF token.
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if request.url.path.startswith('/api/') and request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
            return JSONResponse({'detail':'JSON content type required'},status_code=415)
        if request.headers.get('x-requested-with')!='RescuePlate':
            return JSONResponse({'detail':'Missing application request header'},status_code=403)
        fetch_site=request.headers.get('sec-fetch-site')
        if fetch_site and fetch_site not in ('same-origin','none'):
            return JSONResponse({'detail':'Cross-site request rejected'},status_code=403)
        if origin:
            from urllib.parse import urlsplit
            requested=urlsplit(str(request.base_url))
            sent=urlsplit(origin)
            if (requested.scheme,requested.netloc)!=(sent.scheme,sent.netloc):
                return JSONResponse({'detail':'Cross-origin request rejected'},status_code=403)
        # CSRF token stored alongside the session, never supplied through a URL.
        if request.cookies.get('rp_session') and request.url.path not in ('/api/login','/api/register'):
            token=request.cookies.get('rp_session','')
            with db() as conn:
                record=conn.execute('SELECT csrf_token FROM sessions WHERE token_hash=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),iso())).fetchone()
            if not record or not hmac.compare_digest(record['csrf_token'] or '',request.headers.get('x-csrf-token','')):
                return JSONResponse({'detail':'CSRF validation failed'},status_code=403)
    if request.url.path=='/api/login' and request.method=='POST':
        key=request.client.host if request.client else 'unknown'
        window=_LOGIN_ATTEMPTS[key]
        stamp=time.monotonic()
        while window and stamp-window[0]>300:window.popleft()
        if len(window)>=12:
            return JSONResponse({'detail':'Too many login attempts. Try again later.'},status_code=429,headers={'Retry-After':'300'})
        window.append(stamp)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    if response.status_code in (401,403,429) and request.url.path.startswith('/api/'):
        # Persist security events in a fresh transaction so an API error rollback cannot erase them.
        try:
            with db() as c:log(c,None,'security_rejected',f'{request.url.path} : HTTP {response.status_code}')
        except Exception:pass
    return response


def now(): return datetime.now(timezone.utc)
def iso(d=None): return (d or now()).isoformat(timespec='seconds')
def connect():
    c=sqlite3.connect(str(DB_FILE),timeout=15)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    return c
@contextmanager
def db():
    conn=connect()
    try:
        yield conn
        conn.commit()
    except:
        conn.rollback();raise
    finally:conn.close()

def init():
    DB_FILE.parent.mkdir(parents=True,exist_ok=True)
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('student','cafeteria','admin')), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires TEXT NOT NULL, csrf_token TEXT);
        CREATE TABLE IF NOT EXISTS cafeterias (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, location TEXT NOT NULL, description TEXT DEFAULT '', approved INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS meals (id INTEGER PRIMARY KEY, cafeteria_id INTEGER NOT NULL REFERENCES cafeterias(id) ON DELETE CASCADE, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>=0), price REAL NOT NULL CHECK(price>=0), original_price REAL NOT NULL CHECK(original_price>=0), pickup_start TEXT NOT NULL, pickup_end TEXT NOT NULL, image_emoji TEXT NOT NULL DEFAULT '🥗', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reservations (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), meal_id INTEGER NOT NULL REFERENCES meals(id), pickup_code TEXT NOT NULL UNIQUE, status TEXT NOT NULL CHECK(status IN ('reserved','collected','cancelled','expired')), created_at TEXT NOT NULL, collected_at TEXT);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id), event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_meals_cafe ON meals(cafeteria_id);
        CREATE INDEX IF NOT EXISTS idx_res_user ON reservations(user_id);
        CREATE INDEX IF NOT EXISTS idx_res_meal ON reservations(meal_id);
        ''')
        # Add localized fields without destroying an existing demo database.
        for table, columns in {'meals':['title_ar','description_ar','prepared_at','safe_until','ingredients','allergens','storage_instructions','image_url'], 'cafeterias':['name_ar','location_ar'], 'sessions':['csrf_token']}.items():
            existing={row[1] for row in c.execute(f'PRAGMA table_info({table})')}
            for column in columns:
                if column not in existing:c.execute(f'ALTER TABLE {table} ADD COLUMN {column} TEXT')
        c.execute('UPDATE sessions SET csrf_token=lower(hex(randomblob(32))) WHERE csrf_token IS NULL')
        c.execute('DELETE FROM sessions WHERE expires < ?', (iso(),))
        if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            admin_email=os.getenv('ADMIN_EMAIL','admin@rescueplate.local').lower()
            admin_pass=os.getenv('ADMIN_PASSWORD','DemoAdmin@123!') if DEMO_MODE else os.getenv('ADMIN_PASSWORD')
            if not admin_pass: raise RuntimeError('ADMIN_PASSWORD required for live mode')
            c.execute('INSERT INTO users(name,email,password,role,created_at) VALUES(?,?,?,?,?)',('Platform Admin',admin_email,pwd_hash(admin_pass),'admin',iso()))
init_pending=False

def pwd_hash(password):
    salt=secrets.token_bytes(16)
    key=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,220000)
    return f'{salt.hex()}:{key.hex()}'
def pwd_verify(password,stored):
    try:
        salt,k=stored.split(':')
        return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),220000),bytes.fromhex(k))
    except (ValueError,TypeError):return False

def fail(code,msg):raise HTTPException(status_code=code,detail=msg)
def user_of(request:Request,c):
    token=request.cookies.get('rp_session')
    if not token:return None
    row=c.execute('SELECT u.id,u.name,u.email,u.role FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token_hash=? AND s.expires>?', (hashlib.sha256(token.encode()).hexdigest(),iso())).fetchone()
    return dict(row) if row else None

def require(request,c,roles=()):
    u=user_of(request,c)
    if not u: fail(401,'برجاء تسجيل الدخول أولًا')
    if roles and u['role'] not in roles:
        log(c,u['id'],'unauthorized_access','required:'+','.join(roles));fail(403,'غير مسموح بهذا الإجراء')
    return u

def log(c,u,event,detail):c.execute('INSERT INTO audit(actor_id,event,detail,created_at) VALUES(?,?,?,?)',(u,event,detail,iso()))
def expire(c):
    ids=c.execute("SELECT r.id,r.meal_id FROM reservations r JOIN meals m ON m.id=r.meal_id WHERE r.status='reserved' AND m.pickup_end<?",(iso(),)).fetchall()
    for r in ids:
        c.execute("UPDATE reservations SET status='expired' WHERE id=?",(r['id'],))
        # expired meals shouldn't return to publicly bookable quantity

class Auth(BaseModel):
    email:str
    password:str
class Register(Auth):
    name:str
    role:str='student'
    cafeteria_name:Optional[str]=None
    location:Optional[str]=None
    cafeteria_name_ar:Optional[str]=None
    location_ar:Optional[str]=None
class MealIn(BaseModel):
    title:str=Field(min_length=2,max_length=100)
    description:str=Field(default='',max_length=500)
    title_ar:Optional[str]=Field(default=None,max_length=100)
    description_ar:Optional[str]=Field(default=None,max_length=500)
    category:str='other'
    quantity:int=Field(ge=1,le=5000)
    price:float=Field(ge=0,le=100000)
    original_price:float=Field(ge=0,le=100000)
    pickup_start:str
    pickup_end:str
    image_emoji:str='🥗'
    prepared_at:str
    safe_until:str
    ingredients:str=Field(min_length=2,max_length=500)
    allergens:str=Field(min_length=2,max_length=300)
    storage_instructions:str=Field(min_length=2,max_length=300)
class QuantityIn(BaseModel):
    quantity:int=Field(ge=0,le=5000)
class ClaimIn(BaseModel):code:str
class Approval(BaseModel):approved:bool
class CafeEdit(BaseModel):name:str;location:str;description:str=''

def booking_deadline(meal):
    # Bookings close after one hour, with a 10-minute pickup buffer for shorter offers.
    begin=datetime.fromisoformat(meal['pickup_start'].replace('Z','+00:00'))
    end=datetime.fromisoformat(meal['pickup_end'].replace('Z','+00:00'))
    return min(begin+timedelta(minutes=BOOKING_WINDOW_MINUTES),end-timedelta(minutes=MIN_BOOKING_LEAD_MINUTES))

def public_meal(meal):
    d=dict(meal)
    d['booking_end']=iso(booking_deadline(d))
    return d

def validate_slot(a,b):
    try:s=datetime.fromisoformat(a.replace('Z','+00:00'));e=datetime.fromisoformat(b.replace('Z','+00:00'))
    except ValueError:fail(400,'وقت الاستلام غير صحيح')
    if s.tzinfo is None or e.tzinfo is None:fail(400,'يجب إرسال الوقت بالمنطقة الزمنية')
    if e<=s or e<=now():fail(400,'وقت الاستلام يجب أن يكون في المستقبل')
    if (e-s).total_seconds()>MAX_PICKUP_WINDOW_MINUTES*60:fail(400,'نافذة العرض والاستلام لا تتجاوز ساعتين (120 دقيقة)')
    if s<now()-timedelta(minutes=5):fail(400,'بداية الاستلام لا يمكن أن تكون في الماضي البعيد')
    if e>now()+timedelta(hours=3):fail(400,'آخر موعد للاستلام يجب أن يكون خلال 3 ساعات')

def valid_safety(prepared_at,safe_until, pickup_end):
    try:
        prepared=datetime.fromisoformat(prepared_at.replace('Z','+00:00'))
        safe=datetime.fromisoformat(safe_until.replace('Z','+00:00'))
        last=datetime.fromisoformat(pickup_end.replace('Z','+00:00'))
    except (ValueError,AttributeError):fail(400,'تواريخ سلامة الطعام غير صحيحة')
    if any(x.tzinfo is None for x in (prepared,safe,last)):fail(400,'يجب تحديد المنطقة الزمنية')
    if prepared>now()+timedelta(minutes=5) or prepared>last or safe<=prepared or safe<last:
        fail(400,'يجب أن يكون وقت التحضير قبل الاستلام وآخر موعد آمن بعد أو عند نهاية الاستلام')

@app.get('/')
def homepage():return FileResponse(BASE/'static'/'index.html')
@app.get('/health')
def health():return {'status':'ok','service':'RescuePlate'}
@app.post('/api/register')
def register(body:Register, request:Request, response:Response):
    name=body.name.strip();email=body.email.strip().lower()
    if len(name)<2 or len(name)>80:fail(400,'الاسم يجب أن يكون من 2 إلى 80 حرفًا')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):fail(400,'البريد الإلكتروني غير صحيح')
    if len(body.password)<10 or len(body.password)>128:fail(400,'كلمة المرور يجب أن تكون من 10 إلى 128 حرفًا')
    if body.role not in ('student','cafeteria'):fail(400,'نوع الحساب غير صالح')
    if body.role=='cafeteria' and (not (body.cafeteria_name or '').strip() or not (body.location or '').strip()):fail(400,'أدخل اسم الكافيتريا ومكانها')
    with db() as c:
        try:
            cur=c.execute('INSERT INTO users(name,email,password,role,created_at) VALUES(?,?,?,?,?)',(name,email,pwd_hash(body.password),body.role,iso()))
        except sqlite3.IntegrityError:fail(409,'البريد الإلكتروني مستخدم بالفعل')
        u={'id':cur.lastrowid,'name':name,'email':email,'role':body.role}
        if body.role=='cafeteria':c.execute('INSERT INTO cafeterias(user_id,name,location,name_ar,location_ar,approved) VALUES(?,?,?,?,?,0)',(u['id'],body.cafeteria_name.strip()[:100],body.location.strip()[:150],(body.cafeteria_name_ar or '').strip()[:100],(body.location_ar or '').strip()[:150]))
        session(c,u,response)
        log(c,u['id'],'register',body.role)
        return {'user':u}

def session(c,u,response):
    tok=secrets.token_urlsafe(32)
    c.execute('INSERT INTO sessions(token_hash,user_id,expires,csrf_token) VALUES(?,?,?,?)',(hashlib.sha256(tok.encode()).hexdigest(),u['id'],iso(now()+timedelta(days=SESSION_DAYS)),secrets.token_hex(32)))
    response.set_cookie('rp_session',tok,httponly=True,secure=COOKIE_SECURE,samesite='lax',max_age=SESSION_DAYS*86400,path='/')

@app.post('/api/login')
def login(body:Auth,response:Response):
    with db() as c:
        r=c.execute('SELECT * FROM users WHERE email=?',(body.email.strip().lower(),)).fetchone()
        if not r or not pwd_verify(body.password,r['password']):
            log(c,r['id'] if r else None,'failed_login','authentication_failed');fail(401,'البريد أو كلمة المرور غير صحيحة')
        u={k:r[k] for k in ('id','name','email','role')}
        session(c,u,response)
        log(c,u['id'],'login','successful')
        return {'user':u}
@app.get('/api/csrf')
def csrf(request:Request):
    with db() as c:
        token=request.cookies.get('rp_session','')
        r=c.execute('SELECT csrf_token FROM sessions WHERE token_hash=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),iso())).fetchone() if token else None
        return {'csrf_token':r['csrf_token'] if r else None}

@app.post('/api/logout')
def logout(request:Request,response:Response):
    tok=request.cookies.get('rp_session')
    if tok:
        with db() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(tok.encode()).hexdigest(),))
    response.delete_cookie('rp_session',path='/')
    return {'ok':True}
@app.get('/api/me')
def me(request:Request):
    with db() as c:
        u=user_of(request,c)
        if not u:return {'user':None}
        if u['role']=='cafeteria':
            caf=c.execute('SELECT * FROM cafeterias WHERE user_id=?',(u['id'],)).fetchone()
            u['cafeteria']=dict(caf) if caf else None
        return {'user':u}

@app.get('/api/meals')
def meals(category:str='',q:str=''):
    with db() as c:
        expire(c)
        sql='''SELECT m.*, f.name cafeteria_name,f.location,f.name_ar cafeteria_name_ar,f.location_ar FROM meals m JOIN cafeterias f ON f.id=m.cafeteria_id WHERE m.active=1 AND f.approved=1 AND m.quantity>0 AND m.pickup_end>?'''
        p=[iso(now()+timedelta(minutes=MIN_BOOKING_LEAD_MINUTES))]
        if category and category!='all':sql+=' AND m.category=?';p.append(category)
        if q.strip():sql+=' AND (m.title LIKE ? OR m.description LIKE ? OR f.name LIKE ?)';p.extend(['%'+q.strip()+'%']*3)
        sql+=' ORDER BY m.pickup_end ASC LIMIT 150'
        return {'meals':[public_meal(x) for x in c.execute(sql,p).fetchall() if booking_deadline(x)>now()]}
@app.get('/api/cafeteria/meals')
def cafe_meals(request:Request):
    with db() as c:
        u=require(request,c,('cafeteria',));f=c.execute('SELECT * FROM cafeterias WHERE user_id=?',(u['id'],)).fetchone()
        return {'cafeteria':dict(f),'meals':[dict(x) for x in c.execute('SELECT * FROM meals WHERE cafeteria_id=? ORDER BY id DESC',(f['id'],))]}
@app.put('/api/cafeteria')
def update_cafe(body:CafeEdit,request:Request):
    with db() as c:
        u=require(request,c,('cafeteria',))
        if not body.name.strip() or not body.location.strip():fail(400,'الاسم والموقع مطلوبان')
        c.execute('UPDATE cafeterias SET name=?,location=?,description=? WHERE user_id=?',(body.name.strip()[:100],body.location.strip()[:150],body.description.strip()[:500],u['id']))
        return {'ok':True}
@app.post('/api/meals')
def create_meal(body:MealIn,request:Request):
    validate_slot(body.pickup_start,body.pickup_end)
    valid_safety(body.prepared_at,body.safe_until,body.pickup_end)
    if any(not getattr(body,x).strip() for x in ('ingredients','allergens','storage_instructions')):fail(400,'بيانات سلامة الطعام مطلوبة')
    if body.price>body.original_price:fail(400,'السعر المخفض لا يتجاوز السعر الأصلي')
    with db() as c:
        u=require(request,c,('cafeteria',));caf=c.execute('SELECT * FROM cafeterias WHERE user_id=?',(u['id'],)).fetchone()
        if not caf['approved']:fail(403,'حساب الكافيتريا ينتظر موافقة المشرف')
        cur=c.execute('''INSERT INTO meals(cafeteria_id,title,description,title_ar,description_ar,category,quantity,price,original_price,pickup_start,pickup_end,image_emoji,created_at,prepared_at,safe_until,ingredients,allergens,storage_instructions) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(caf['id'],body.title.strip(),body.description.strip(),(body.title_ar or '').strip(),(body.description_ar or '').strip(),body.category,body.quantity,body.price,body.original_price,body.pickup_start,body.pickup_end,body.image_emoji[:8],iso(),body.prepared_at,body.safe_until,body.ingredients.strip(),body.allergens.strip(),body.storage_instructions.strip()))
        log(c,u['id'],'meal_added',str(cur.lastrowid))
        return {'id':cur.lastrowid,'ok':True}
@app.patch('/api/meals/{meal_id}/quantity')
def quantity(meal_id:int,body:QuantityIn,request:Request):
    with db() as c:
        u=require(request,c,('cafeteria',))
        c.execute('BEGIN IMMEDIATE')
        meal=c.execute('SELECT m.id,m.active,m.pickup_end FROM meals m JOIN cafeterias f ON f.id=m.cafeteria_id WHERE m.id=? AND f.user_id=?',(meal_id,u['id'])).fetchone()
        if not meal:fail(404,'الوجبة غير موجودة')
        # quantity is UNSOLD, available stock. Reserved meals remain separately allocated.
        if meal['pickup_end']<=iso() or not meal['active']:fail(409,'لا يمكن تغيير مخزون عرض منتهي أو متوقف')
        cur=c.execute('UPDATE meals SET quantity=? WHERE id=?',(body.quantity,meal_id))
        log(c,u['id'],'stock_updated',f'{meal_id}:{body.quantity}')
        if not cur.rowcount:fail(404,'الوجبة غير موجودة')
        return {'ok':True}
@app.patch('/api/meals/{meal_id}/toggle')
def toggle(meal_id:int,request:Request):
    with db() as c:
        u=require(request,c,('cafeteria',))
        cur=c.execute('UPDATE meals SET active=1-active WHERE id=? AND cafeteria_id=(SELECT id FROM cafeterias WHERE user_id=?)',(meal_id,u['id']))
        if not cur.rowcount:fail(404,'الوجبة غير موجودة')
        return {'ok':True}
@app.post('/api/meals/{meal_id}/reserve')
def reserve(meal_id:int,request:Request):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        u=require(request,c,('student',))
        expire(c)
        meal=c.execute('SELECT m.*,f.approved FROM meals m JOIN cafeterias f ON f.id=m.cafeteria_id WHERE m.id=?',(meal_id,)).fetchone()
        if not meal or not meal['active'] or not meal['approved'] or meal['quantity']<1 or datetime.fromisoformat(meal['pickup_start'].replace('Z','+00:00'))>now() or booking_deadline(meal)<=now():fail(409,'انتهت مدة الحجز لهذه الوجبة؛ لا يزال الاستلام متاحًا للحجوزات المؤكدة حتى الموعد النهائي')
        if c.execute("SELECT 1 FROM reservations WHERE user_id=? AND meal_id=? AND status='reserved'",(u['id'],meal_id)).fetchone():fail(409,'لديك حجز نشط لهذه الوجبة')
        changed=c.execute('UPDATE meals SET quantity=quantity-1 WHERE id=? AND quantity>0',(meal_id,))
        if changed.rowcount!=1:fail(409,'الكمية نفدت')
        code=secrets.token_hex(5).upper()
        cur=c.execute("INSERT INTO reservations(user_id,meal_id,pickup_code,status,created_at) VALUES(?,?,?,'reserved',?)",(u['id'],meal_id,code,iso()))
        log(c,u['id'],'reserve',str(cur.lastrowid))
        return {'id':cur.lastrowid,'code':code,'ok':True}
@app.get('/api/my/reservations')
def my_reservations(request:Request):
    with db() as c:
        u=require(request,c,('student',));expire(c)
        records=c.execute('''SELECT r.*,m.title,m.title_ar,m.price,m.image_emoji,m.pickup_start,m.pickup_end,f.name cafeteria_name,f.name_ar cafeteria_name_ar,f.location FROM reservations r JOIN meals m ON m.id=r.meal_id JOIN cafeterias f ON f.id=m.cafeteria_id WHERE r.user_id=? ORDER BY r.id DESC''',(u['id'],)).fetchall()
        return {'reservations':[dict(x) for x in records]}
@app.post('/api/reservations/{res_id}/cancel')
def cancel(res_id:int,request:Request):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        u=require(request,c,('student',));expire(c)
        r=c.execute("SELECT r.*,m.pickup_end FROM reservations r JOIN meals m ON m.id=r.meal_id WHERE r.id=? AND r.user_id=?",(res_id,u['id'])).fetchone()
        if not r or r['status']!='reserved':fail(409,'لا يمكن إلغاء هذا الحجز')
        c.execute("UPDATE reservations SET status='cancelled' WHERE id=?",(res_id,))
        if r['pickup_end']>iso():c.execute('UPDATE meals SET quantity=quantity+1 WHERE id=?',(r['meal_id'],))
        log(c,u['id'],'cancel',str(res_id))
        return {'ok':True}
@app.get('/api/reservations/{res_id}/qr')
def qr(res_id:int,request:Request):
    with db() as c:
        u=require(request,c,('student',))
        r=c.execute("SELECT pickup_code FROM reservations WHERE id=? AND user_id=? AND status='reserved'",(res_id,u['id'])).fetchone()
        if not r:fail(404,'الحجز غير موجود')
        pic=qrcode.make('RESCUEPLATE:'+r['pickup_code'])
        buff=io.BytesIO();pic.save(buff,format='PNG');buff.seek(0)
        return StreamingResponse(buff,media_type='image/png',headers={'Cache-Control':'no-store'})
@app.get('/api/cafeteria/reservations')
def cafe_reservations(request:Request):
    with db() as c:
        u=require(request,c,('cafeteria',));expire(c)
        data=c.execute('''SELECT r.id,r.pickup_code,r.status,r.created_at,r.collected_at,m.title,m.title_ar,m.pickup_end,s.name student_name FROM reservations r JOIN meals m ON m.id=r.meal_id JOIN cafeterias f ON f.id=m.cafeteria_id JOIN users s ON s.id=r.user_id WHERE f.user_id=? ORDER BY r.id DESC LIMIT 250''',(u['id'],)).fetchall()
        return {'reservations':[dict(x) for x in data]}
@app.post('/api/cafeteria/collect')
def collect(body:ClaimIn,request:Request):
    code=body.code.strip().upper().replace('RESCUEPLATE:','')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        u=require(request,c,('cafeteria',));expire(c)
        r=c.execute('''SELECT r.id,r.status,m.pickup_start,m.pickup_end FROM reservations r JOIN meals m ON m.id=r.meal_id JOIN cafeterias f ON f.id=m.cafeteria_id WHERE r.pickup_code=? AND f.user_id=?''',(code,u['id'])).fetchone()
        if not r:fail(404,'كود غير صحيح أو لا يخص هذه الكافيتريا')
        if r['status']!='reserved':fail(409,'هذا الحجز غير نشط أو تم استلامه بالفعل')
        if r['pickup_start']>iso() or r['pickup_end']<iso():fail(409,'الاستلام متاح فقط خلال الموعد المحدد')
        c.execute("UPDATE reservations SET status='collected',collected_at=? WHERE id=?",(iso(),r['id']))
        log(c,u['id'],'collected',str(r['id']))
        return {'ok':True,'reservation_id':r['id']}
@app.get('/api/cafeteria/stats')
def cafe_stats(request:Request):
    with db() as c:
        u=require(request,c,('cafeteria',))
        f=c.execute('SELECT id FROM cafeterias WHERE user_id=?',(u['id'],)).fetchone()
        counts=c.execute('''SELECT COUNT(DISTINCT m.id) meals,COALESCE(SUM(CASE WHEN r.status='collected' THEN 1 ELSE 0 END),0) collected,COALESCE(SUM(CASE WHEN r.status='reserved' THEN 1 ELSE 0 END),0) reserved FROM meals m LEFT JOIN reservations r ON r.meal_id=m.id WHERE m.cafeteria_id=?''',(f['id'],)).fetchone()
        return {'stats':dict(counts)}
@app.get('/api/admin/overview')
def overview(request:Request):
    with db() as c:
        u=require(request,c,('admin',));expire(c)
        stats={}
        for key,sql in {'students':"SELECT COUNT(*) FROM users WHERE role='student'",'cafeterias':'SELECT COUNT(*) FROM cafeterias','pending':'SELECT COUNT(*) FROM cafeterias WHERE approved=0','meals':'SELECT COUNT(*) FROM meals','collected':"SELECT COUNT(*) FROM reservations WHERE status='collected'",'reserved':"SELECT COUNT(*) FROM reservations WHERE status='reserved'",'expired':"SELECT COUNT(*) FROM reservations WHERE status='expired'"}.items():stats[key]=c.execute(sql).fetchone()[0]
        cafes=[dict(x) for x in c.execute('''SELECT f.*,u.name owner,u.email FROM cafeterias f JOIN users u ON u.id=f.user_id ORDER BY f.approved,f.id DESC''')]
        audit=[dict(x) for x in c.execute('SELECT a.*,u.name actor FROM audit a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 25')]
        return {'stats':stats,'cafeterias':cafes,'audit':audit}
@app.patch('/api/admin/cafeterias/{cafe_id}/approval')
def approval(cafe_id:int,body:Approval,request:Request):
    with db() as c:
        u=require(request,c,('admin',))
        cur=c.execute('UPDATE cafeterias SET approved=? WHERE id=?',(int(body.approved),cafe_id))
        if not cur.rowcount:fail(404,'الكافيتريا غير موجودة')
        log(c,u['id'],'approval',f'{cafe_id}:{body.approved}')
        return {'ok':True}

init()

@app.get('/api/public/stats')
def public_stats():
    with db() as c:
        return {'collected':c.execute("SELECT COUNT(*) FROM reservations WHERE status='collected'").fetchone()[0],
                'cafeterias':c.execute('SELECT COUNT(*) FROM cafeterias WHERE approved=1').fetchone()[0],
                'meals':c.execute('SELECT COUNT(*) FROM meals WHERE active=1').fetchone()[0]}

# Demo uses referenced original menu prices; rescue discounts and availability are simulated.
def seed_demo():
    if not DEMO_MODE: return
    with db() as c:
        if c.execute('SELECT 1 FROM cafeterias').fetchone(): return
        demo_users=[('Demo Student','student@rescueplate.local','student'),('Campus Cafe Manager','cafe@rescueplate.local','cafeteria'),('Green Kitchen Manager','kitchen@rescueplate.local','cafeteria'),('Fresh Corner Manager','fresh@rescueplate.local','cafeteria')]
        ids={}
        for n,email,role in demo_users:
            cur=c.execute('INSERT INTO users(name,email,password,role,created_at) VALUES(?,?,?,?,?)',(n,email,pwd_hash('Demo@12345'),role,iso()))
            ids[email]=cur.lastrowid
        cafs=[]
        for email,name,loc in [('cafe@rescueplate.local','Campus Café • Demo','University Main Gate'),('kitchen@rescueplate.local','Green Kitchen • Demo','Student Center'),('fresh@rescueplate.local','Fresh Corner • Demo','Faculty Food Court')]:
            cafs.append(c.execute('INSERT INTO cafeterias(user_id,name,location,name_ar,location_ar,description,approved) VALUES(?,?,?,?,?,?,1)',(ids[email],name,loc,{'Campus Café • Demo':'كافيه الحرم • تجريبي','Green Kitchen • Demo':'المطبخ الأخضر • تجريبي','Fresh Corner • Demo':'ركن الطازج • تجريبي'}[name],{'University Main Gate':'البوابة الرئيسية للجامعة','Student Center':'مركز الطلاب','Faculty Food Court':'منطقة مطاعم الكلية'}[loc],'Educational demo cafeteria')).lastrowid)
        # Published original-menu prices, El Haty online menu (checked 2026-11-06).
        # Names, availability, quantities and rescue discounts below are simulated.
        catalog=[
            (0,'Chicken Kofta Meal','Chicken kofta with basmati rice and fries.','وجبة كفتة فراخ','كفتة فراخ مع أرز بسمتي وبطاطس محمرة.','chicken',204,173,'🍢'),
            (1,'Chicken Panne Meal','Chicken panne with pasta and fries.','وجبة فراخ بانيه','فراخ بانيه مع مكرونة وبطاطس محمرة.','chicken',267,219,'🍝'),
            (2,'Shish Tawook Meal','Shish tawook with basmati rice and fries.','وجبة شيش طاووق','شيش طاووق مع أرز بسمتي وبطاطس محمرة.','chicken',238,190,'🍱'),
            (0,'Half Grilled Chicken Meal','Half grilled chicken with basmati rice and fries.','وجبة نصف فرخة مشوية','نصف فرخة مشوية مع أرز بسمتي وبطاطس محمرة.','chicken',296,251,'🍗'),
            (1,'Chicken Hawawshi Sandwich','Chicken hawawshi sandwich.','ساندوتش حواوشي فراخ','ساندوتش حواوشي بالفراخ.','sandwich',99,84,'🥪'),
            (2,'Chicken Panne Sandwich','Chicken panne sandwich.','ساندوتش فراخ بانيه','ساندوتش بانيه فراخ.','sandwich',127,108,'🥙'),
            (0,'Grilled Kofta Sandwich','Grilled kofta sandwich.','ساندوتش كفتة مشوية','ساندوتش كفتة مشوية.','sandwich',139,111,'🥖'),
            (1,'Béchamel Pasta Tajine','Baked béchamel pasta tajine.','طاجن مكرونة بالبشاميل','طاجن مكرونة بالبشاميل.','other',162,138,'🍲')]
        start=iso(now()-timedelta(minutes=2));end=iso(now()+timedelta(minutes=118));prepared=iso(now()-timedelta(minutes=10));safe=end
        for i,(idx,title,descr,title_ar,desc_ar,category,orig,price,emoji) in enumerate(catalog):
            c.execute('INSERT INTO meals(cafeteria_id,title,description,title_ar,description_ar,category,quantity,price,original_price,pickup_start,pickup_end,image_emoji,created_at,prepared_at,safe_until,ingredients,allergens,storage_instructions) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(cafs[idx],title,descr,title_ar,desc_ar,category,5+i,price,orig,start,end,emoji,iso(),prepared,safe,'Refer to restaurant ingredient information / راجع معلومات المكونات لدى المطعم','Unknown: verify with restaurant / غير محدد: يجب التأكد من المطعم','Demo only: safe storage not verified / بيانات تخزين تجريبية غير معتمدة'))
        log(c,None,'demo_seed','Demo accounts, referenced menu prices, simulated discounts')

seed_demo()
