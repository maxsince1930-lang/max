"""Portable WSGI product catalogue. Python 3.9+, no runtime dependencies."""
import os, sys, json, sqlite3, secrets, hashlib, hmac, time, re, mimetypes
from pathlib import Path
from http.cookies import SimpleCookie
from urllib.parse import unquote, parse_qs
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', str(ROOT / 'data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
UPLOADS = DATA / 'uploads'
UPLOADS.mkdir(exist_ok=True)
DB = DATA / 'catalog.sqlite3'
SECURE = os.environ.get('COOKIE_SECURE', '0') == '1'
BASE = os.environ.get('BASE_PATH', '').rstrip('/')
if BASE and not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)+', BASE):
    raise RuntimeError('BASE_PATH must be a URL path such as /products')
LABELS = ['top', 'bottom', 'left', 'right', 'front', 'back']
MAX_IMAGE = 12 * 1024 * 1024
MAX_VIDEO = 100 * 1024 * 1024

def db():
    c = sqlite3.connect(str(DB), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def initialize():
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS admins (username TEXT PRIMARY KEY, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (ip TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, kind TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, name TEXT NOT NULL, model TEXT NOT NULL, country TEXT NOT NULL, whatsapp TEXT NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL, published INTEGER NOT NULL, images TEXT NOT NULL, video TEXT, created INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1);
        ''')
        if not c.execute('SELECT 1 FROM admins').fetchone():
            keyfile = DATA / 'setup-token'
            if not keyfile.exists():
                try:
                    fd = os.open(str(keyfile), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    pass  # Another WSGI worker completed initialization.
                else:
                    with os.fdopen(fd, 'w') as f: f.write(secrets.token_urlsafe(28))
        if os.environ.get('DEMO_DATA') == '1' and not c.execute('SELECT 1 FROM products').fetchone():
            for i, (name, model, cat, desc) in enumerate([
                ('ARC 无线耳机', 'ARC-01', '音频设备', '利落的曲线与舒适的佩戴体验，探索产品的每一个细节。'),
                ('FORM 桌面音响', 'FORM-02', '音频设备', '以纯粹的几何语言，为桌面带来一份从容。'),
                ('LUMA 桌面灯', 'LUMA-03', '生活家居', '温暖的光线，简洁的轮廓，为日常空间增添温度。')]):
                imgs = {k: 'demo:%s:%s' % (i, k) for k in LABELS}
                c.execute('INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', ('demo-'+str(i), name, model, '中国', '', desc, cat, 1, json.dumps(imgs), None, int(time.time())-i, 1))

initialize()

class HTTPError(Exception):
    def __init__(self, status, message): self.status, self.message = status, message

def fail(status, message): raise HTTPError(status, message)
def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex()

def product(row):
    p = dict(row)
    p['images'] = json.loads(p['images'])
    p['published'] = bool(p['published'])
    return p

def read_json(env):
    try: length = int(env.get('CONTENT_LENGTH') or 0)
    except ValueError: fail(400, '请求长度无效')
    if length < 0: fail(400, '请求长度无效')
    if length > 65536: fail(413, '请求过大')
    try:
        data = json.loads(env['wsgi.input'].read(length))
        if not isinstance(data, dict): raise ValueError()
        return data
    except (ValueError, UnicodeDecodeError): fail(400, '请求格式无效')

def session(env):
    try:
        cookies = SimpleCookie(env.get('HTTP_COOKIE', ''))
        token = cookies['showcase_session'].value if 'showcase_session' in cookies else ''
    except Exception: return None
    with db() as c:
        return c.execute('SELECT * FROM sessions WHERE id=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()

def auth(env, mutate=False):
    s = session(env)
    if not s: fail(401, '请先登录后台')
    if mutate and not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), s['csrf']): fail(403, '登录验证已失效，请刷新页面')
    return s

def validate_product(data, c):
    result = {}
    for key, maxlen in [('name',120),('model',100),('country',80),('whatsapp',30),('description',3000),('category',60)]:
        val = data.get(key, '')
        if not isinstance(val, str) or len(val) > maxlen: fail(400, '产品字段格式或长度无效')
        result[key] = val.strip()
    if not all(result[k] for k in ['name','model','country']): fail(400, '请填写产品名称、型号和国家')
    number = re.sub(r'[\s()+-]', '', result['whatsapp'])
    if number and not re.fullmatch(r'[1-9][0-9]{5,14}', number): fail(400, 'WhatsApp 请填写含国家区号的号码，例如 +86 138 0013 8000')
    result['whatsapp'] = number
    if type(data.get('published', False)) is not bool: fail(400, '上架状态无效')
    result['published'] = int(data.get('published', False))
    imgs = data.get('images', {})
    if not isinstance(imgs, dict) or set(imgs) - set(LABELS): fail(400, '图片方位无效')
    for k, mid in imgs.items():
        if not isinstance(mid, str): fail(400, '图片无效')
        # Demo media are read-only samples, never a way to inject external URLs.
        if re.fullmatch(r'demo:[0-2]:(top|bottom|left|right|front|back)', mid): continue
        if not c.execute("SELECT 1 FROM media WHERE id=? AND kind='image'", (mid,)).fetchone(): fail(400, '图片不存在，请重新上传')
    video = data.get('video') or None
    if video is not None and (not isinstance(video, str) or not c.execute("SELECT 1 FROM media WHERE id=? AND kind='video'", (video,)).fetchone()): fail(400, '视频不存在，请重新上传')
    if result['published'] and (len(imgs) != 6 or any(not imgs.get(k) for k in LABELS)):
        fail(400, '上架前请上传完整的六个方位图片；未完成时可先保存草稿')
    result['images'], result['video'] = json.dumps(imgs), video
    return result

def route(env):
    method, path = env['REQUEST_METHOD'], unquote(env.get('PATH_INFO', '/'))
    # WSGI server receives the full path; reverse proxy must preserve BASE_PATH.
    if BASE:
        if path == BASE: return 302, [('Location', BASE+'/')], b''
        if not path.startswith(BASE+'/'): fail(404, '页面不存在')
        path = path[len(BASE):]
    if method in ('POST','PUT','DELETE'):
        origin = env.get('HTTP_ORIGIN')
        if origin:
            expected = os.environ.get('PUBLIC_ORIGIN') or (env['wsgi.url_scheme']+'://'+env.get('HTTP_HOST',''))
            if origin != expected.rstrip('/'): fail(403, '请求来源不匹配')
    if path == '/api/session' and method == 'GET':
        s = session(env)
        with db() as c: setup = not bool(c.execute('SELECT 1 FROM admins').fetchone())
        return {'authenticated': bool(s), 'csrf': s['csrf'] if s else None, 'setupRequired': setup}
    if path in ('/api/login','/api/setup') and method == 'POST':
        data = read_json(env)
        ip = env.get('REMOTE_ADDR','unknown')
        now = int(time.time())
        with db() as c:
            attempt = c.execute('SELECT * FROM attempts WHERE ip=? AND expires>?', (ip, now)).fetchone()
            if attempt and attempt['count'] >= 8: fail(429, '尝试过于频繁，请 15 分钟后重试')
            c.execute('INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(ip) DO UPDATE SET count=CASE WHEN expires<? THEN 1 ELSE count+1 END, expires=CASE WHEN expires<? THEN excluded.expires ELSE expires END', (ip,now+900,now,now))
        username, password = data.get('username',''), data.get('password','')
        if not isinstance(username,str) or not isinstance(password,str) or len(password)>200: fail(400,'账号格式无效')
        with db() as c:
            if path == '/api/setup':
                c.execute('BEGIN IMMEDIATE')
                if c.execute('SELECT 1 FROM admins').fetchone(): fail(409, '管理员已创建，请登录')
                keyfile = DATA / 'setup-token'
                if not isinstance(data.get('token'),str) or not keyfile.exists() or not hmac.compare_digest(data['token'].strip(), keyfile.read_text().strip()): fail(403, '安装码错误，请查看服务器 data/setup-token 文件')
                if not re.fullmatch(r'[A-Za-z0-9_.@-]{3,60}', username) or len(password)<12: fail(400,'账号需为 3–60 位英文或数字，密码至少 12 位')
                c.execute('INSERT INTO admins VALUES (?,?)', (username,password_hash(password)))
            else:
                row = c.execute('SELECT password FROM admins WHERE username=?',(username,)).fetchone()
                # Run the same expensive KDF even when the account is absent.
                hashed = row['password'] if row else '0'*32+':'+'0'*64
                if not hmac.compare_digest(password_hash(password, hashed.split(':')[0]), hashed): fail(401,'账号或密码错误')
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            c.execute('DELETE FROM sessions WHERE expires<?',(now,))
            c.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),csrf,now+43200))
            c.execute('DELETE FROM attempts WHERE ip=?',(ip,))
        if path == '/api/setup': (DATA/'setup-token').unlink(missing_ok=True)
        cookie = 'showcase_session='+token+'; HttpOnly; SameSite=Strict; Path='+(BASE or '/')+'; Max-Age=43200'+('; Secure' if SECURE else '')
        return 200, [('Content-Type','application/json; charset=utf-8'),('Set-Cookie',cookie)], json.dumps({'csrf':csrf}).encode()
    if path == '/api/logout' and method == 'POST':
        s=auth(env,True)
        with db() as c: c.execute('DELETE FROM sessions WHERE id=?',(s['id'],))
        return 200,[('Content-Type','application/json'),('Set-Cookie','showcase_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path='+(BASE or '/'))],b'{}'
    if path == '/api/products' and method == 'GET':
        admin = parse_qs(env.get('QUERY_STRING','')).get('admin') == ['1']
        if admin: auth(env)
        with db() as c: rows = c.execute('SELECT * FROM products '+('' if admin else 'WHERE published=1 ')+'ORDER BY created DESC').fetchall()
        return {'products':[product(r) for r in rows]}
    if path == '/api/products' and method == 'POST':
        auth(env,True)
        data=read_json(env)
        with db() as c:
            p=validate_product(data,c)
            pid=secrets.token_hex(12)
            c.execute('INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(pid,p['name'],p['model'],p['country'],p['whatsapp'],p['description'],p['category'],p['published'],p['images'],p['video'],int(time.time()),1))
        return {'id':pid}
    match = re.fullmatch(r'/api/products/([a-zA-Z0-9-]+)',path)
    if match:
        pid=match.group(1)
        if method=='GET':
            with db() as c: row=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
            if not row or (not row['published'] and not session(env)): fail(404,'产品不存在')
            return product(row)
        auth(env,True)
        if method not in ('PUT','DELETE'): fail(405,'不支持此操作')
        data=read_json(env)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
            if not row: fail(404,'产品不存在')
            if data.get('version') != row['version']: fail(409,'产品已在其他页面修改，请刷新后重试')
            if method=='DELETE': c.execute('DELETE FROM products WHERE id=?',(pid,))
            else:
                p=validate_product(data,c)
                c.execute('UPDATE products SET name=?,model=?,country=?,whatsapp=?,description=?,category=?,published=?,images=?,video=?,version=version+1 WHERE id=?',(p['name'],p['model'],p['country'],p['whatsapp'],p['description'],p['category'],p['published'],p['images'],p['video'],pid))
        return {'ok':True}
    if path == '/api/upload' and method == 'POST':
        auth(env,True)
        mime=env.get('CONTENT_TYPE','').split(';')[0]
        types={'image/jpeg':('jpg','image'), 'image/png':('png','image'), 'image/webp':('webp','image'), 'video/mp4':('mp4','video'), 'video/webm':('webm','video')}
        if mime not in types: fail(415,'仅支持 JPG、PNG、WebP 图片和 MP4、WebM 视频')
        ext,kind=types[mime]
        try: size=int(env.get('CONTENT_LENGTH') or 0)
        except ValueError: fail(400,'文件长度无效')
        if size<=0 or size>(MAX_IMAGE if kind=='image' else MAX_VIDEO): fail(413,'图片最大 12 MB，视频最大 100 MB')
        mid=secrets.token_hex(16)+'.'+ext
        temp=UPLOADS/(mid+'.tmp')
        try:
            with temp.open('wb') as f:
                remain=size
                while remain:
                    chunk=env['wsgi.input'].read(min(remain,1024*1024))
                    if not chunk: fail(400,'上传中断，请重试')
                    f.write(chunk); remain-=len(chunk)
            with temp.open('rb') as f: head=f.read(32)
            valid={'jpg':head.startswith(b'\xff\xd8\xff'),'png':head.startswith(b'\x89PNG\r\n\x1a\n'),'webp':head[:4]==b'RIFF' and head[8:12]==b'WEBP','mp4':head[4:8]==b'ftyp','webm':head[:4]==b'\x1aE\xdf\xa3'}[ext]
            if not valid: fail(415,'文件内容与格式不匹配')
            temp.replace(UPLOADS/mid)
            with db() as c: c.execute('INSERT INTO media VALUES (?,?,?,?,?)',(mid,kind,mime,size,int(time.time())))
        finally: temp.unlink(missing_ok=True)
        return {'id':mid,'kind':kind}
    if path == '/api/health': return {'ok':True}
    if path.startswith('/uploads/') and method in ('GET','HEAD'):
        mid=path.split('/')[-1]
        if not re.fullmatch(r'[a-f0-9]{32}\.(jpg|png|webp|mp4|webm)',mid): fail(404,'文件不存在')
        with db() as c:
            media=c.execute('SELECT * FROM media WHERE id=?',(mid,)).fetchone()
            rows=c.execute('SELECT images,video FROM products WHERE published=1').fetchall()
        visible=any(mid in json.loads(r['images']).values() or mid==r['video'] for r in rows)
        if not media or (not visible and not session(env)): fail(404,'文件不存在')
        return file_response(env,UPLOADS/mid,media['mime'],private=not visible)
    if path.startswith('/api/'): fail(404,'接口不存在')
    if method not in ('GET','HEAD'): fail(405,'不支持此操作')
    if path in ('/','/admin','/admin/'): file=ROOT/'public/index.html'
    elif re.fullmatch(r'/product/[a-zA-Z0-9-]+',path): file=ROOT/'public/index.html'
    else:
        file=(ROOT/'public'/path.lstrip('/')).resolve()
        if ROOT/'public' not in file.parents: fail(404,'页面不存在')
    if not file.is_file(): fail(404,'页面不存在')
    if file.name=='index.html':
        content=file.read_text().replace('__BASE__',BASE).encode()
        return 200,[('Content-Type','text/html; charset=utf-8')],content
    return file_response(env,file,mimetypes.guess_type(str(file))[0] or 'application/octet-stream')

class FileBody:
    def __init__(self,path,start,length): self.file=path.open('rb'); self.file.seek(start); self.remaining=length
    def __iter__(self): return self
    def __next__(self):
        if self.remaining<=0: self.close(); raise StopIteration
        chunk=self.file.read(min(self.remaining,65536))
        if not chunk: self.close(); raise StopIteration
        self.remaining-=len(chunk); return chunk
    def close(self): self.file.close()

def file_response(env,path,mime,private=False):
    if not path.is_file(): fail(404,'文件不存在')
    size=path.stat().st_size
    start,end,status=0,size-1,200
    headers=[('Content-Type',mime),('Accept-Ranges','bytes'),('Cache-Control','private, no-store' if private else 'public, max-age=3600')]
    range_header=env.get('HTTP_RANGE')
    if range_header:
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
        if not match or not any(match.groups()): return 416,[('Content-Range','bytes */'+str(size))],b''
        a,b=match.groups()
        if a: start=int(a); end=min(int(b),size-1) if b else size-1
        else: start=max(0,size-int(b))
        if start>=size or end<start: return 416,[('Content-Range','bytes */'+str(size))],b''
        status=206; headers.append(('Content-Range',f'bytes {start}-{end}/{size}'))
    length=max(0,end-start+1)
    headers.append(('Content-Length',str(length)))
    return status,headers,b'' if env['REQUEST_METHOD']=='HEAD' else FileBody(path,start,length)

def application(env,start_response):
    try:
        result=route(env)
        if isinstance(result,dict): status,headers,body=200,[('Content-Type','application/json; charset=utf-8')],json.dumps(result,ensure_ascii=False).encode()
        else: status,headers,body=result
    except HTTPError as e:
        status,headers,body=e.status,[('Content-Type','application/json; charset=utf-8')],json.dumps({'error':e.message},ensure_ascii=False).encode()
    except Exception:
        import traceback; traceback.print_exc()
        status,headers,body=500,[('Content-Type','application/json; charset=utf-8')],b'{"error":"Server error"}'
    if not any(k.lower()=='cache-control' for k,v in headers): headers.append(('Cache-Control','no-store'))
    headers.extend([('X-Content-Type-Options','nosniff'),('X-Frame-Options','SAMEORIGIN'),('Referrer-Policy','strict-origin-when-cross-origin'),('Content-Security-Policy',"default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'")])
    from http import HTTPStatus
    start_response(str(status)+' '+HTTPStatus(status).phrase,headers)
    if env['REQUEST_METHOD']=='HEAD' and isinstance(body,bytes): return [b'']
    return [body] if isinstance(body,bytes) else body

if __name__ == '__main__':
    host=os.environ.get('HOST','127.0.0.1'); port=int(os.environ.get('PORT','8088'))
    print('Preview: http://'+host+':'+str(port)+BASE+'/',flush=True)
    print('First administrator setup: '+str(DATA/'setup-token')+' (installation code; keep private)',flush=True)
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer
    class ThreadedServer(ThreadingMixIn,WSGIServer): daemon_threads=True
    make_server(host,port,application,server_class=ThreadedServer).serve_forever()
