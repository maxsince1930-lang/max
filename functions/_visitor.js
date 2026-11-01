const enc = new TextEncoder();
const hex = bytes => Array.from(new Uint8Array(bytes), b=>b.toString(16).padStart(2,'0')).join('');
const random = () => hex(crypto.getRandomValues(new Uint8Array(32)));
const digest = async text => hex(await crypto.subtle.digest('SHA-256',enc.encode(text)));
const time = () => Math.floor(Date.now()/1000);
const statement = (env,sql,...args) => env.DB.prepare(sql).bind(...args);
const first = (env,sql,...args) => statement(env,sql,...args).first();
const run = (env,sql,...args) => statement(env,sql,...args).run();
function fail(status,message,code) { throw Object.assign(new Error(message),{status,code}); }
const json = (body,status=200,headers={}) => new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store',...headers}});
async function hash(password,salt=random()) {
  const key=await crypto.subtle.importKey('raw',enc.encode(password),'PBKDF2',false,['deriveBits']);
  return salt+':'+hex(await crypto.subtle.deriveBits({name:'PBKDF2',salt:enc.encode(salt),iterations:100000,hash:'SHA-256'},key,256));
}
async function config(env) {
  await run(env,'CREATE TABLE IF NOT EXISTS visitor_access (id INTEGER PRIMARY KEY CHECK(id=1), password_hash TEXT NOT NULL, enabled INTEGER NOT NULL, version INTEGER NOT NULL, updated INTEGER NOT NULL)');
  return await first(env,'SELECT * FROM visitor_access WHERE id=1')||{password_hash:'',enabled:0,version:0,updated:null};
}
async function tables(env) {
  await run(env,'CREATE TABLE IF NOT EXISTS visitor_sessions (id TEXT PRIMARY KEY, version INTEGER NOT NULL, expires INTEGER NOT NULL)');
  await run(env,'CREATE TABLE IF NOT EXISTS visitor_attempts (ip TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL)');
  await run(env,'CREATE TABLE IF NOT EXISTS visitor_login_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT NOT NULL, created INTEGER NOT NULL, result TEXT NOT NULL)');
  await run(env,'CREATE INDEX IF NOT EXISTS visitor_logs_created ON visitor_login_logs(created,id)');
}
const cookieToken = request => request.headers.get('Cookie')?.match(/(?:^|;\s*)__Host-runcore_visitor=([a-f0-9]{64})(?:;|$)/)?.[1];
async function visitorSession(request,env,cfg) {
  const token=cookieToken(request);
  return token?await first(env,'SELECT id FROM visitor_sessions WHERE id=? AND version=? AND expires>?',await digest(token),cfg.version,time()):null;
}
export async function requireVisitor(request,env,admin) {
  if(admin)return;
  const cfg=await config(env);
  if(cfg.enabled&&!await visitorSession(request,env,cfg))fail(401,'Please enter the access password to view the collection.','visitor_required');
}
async function log(env,ip,result) {
  await env.DB.batch([
    statement(env,'INSERT INTO visitor_login_logs (ip,created,result) VALUES (?,?,?)',ip,time(),result),
    statement(env,'DELETE FROM visitor_login_logs WHERE created<?',time()-90*86400)
  ]);
}
const cookie = token => `__Host-runcore_visitor=${token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=${token?86400:0}`;
export async function accessRoute(request,env,{auth,session,readJSON}) {
  const url=new URL(request.url),path=url.pathname,method=request.method;
  if(!['/api/visitor/session','/api/visitor/login','/api/visitor/logout','/api/access-settings','/api/access-logs'].includes(path))return null;
  if(path.startsWith('/api/access-'))await auth(request,env,method!=='GET');
  if(path.startsWith('/api/visitor/')&&method==='POST'&&request.headers.get('Origin')!==url.origin)fail(403,'请求来源不匹配');
  const cfg=await config(env);
  if(path==='/api/visitor/session'&&method==='GET') {
    const admin=!!await session(request,env);
    return json({required:!!cfg.enabled,authenticated:admin||!cfg.enabled||!!await visitorSession(request,env,cfg),admin});
  }
  if(path==='/api/access-settings'&&method==='GET')return json({enabled:!!cfg.enabled,passwordSet:!!cfg.password_hash,updated:cfg.updated});
  if(path==='/api/access-settings'&&method==='PUT') {
    const data=await readJSON(request);
    if(typeof data.enabled!=='boolean'||typeof data.password!=='string'||data.password.length>200)fail(400,'访问设置格式无效');
    if(data.password&&data.password.length<8)fail(400,'访问密码至少 8 位');
    if(data.enabled&&!data.password&&!cfg.password_hash)fail(400,'请先设置访问密码');
    await tables(env);
    const password=data.password?await hash(data.password):cfg.password_hash;
    await env.DB.batch([
      statement(env,'INSERT INTO visitor_access (id,password_hash,enabled,version,updated) VALUES (1,?,?,1,?) ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash,enabled=excluded.enabled,version=visitor_access.version+1,updated=excluded.updated',password,data.enabled?1:0,time()),
      statement(env,'DELETE FROM visitor_sessions'),
      statement(env,'DELETE FROM visitor_attempts')
    ]);
    return json({ok:true,enabled:data.enabled,passwordSet:!!password});
  }
  if(path==='/api/access-logs'&&method==='GET') {
    await tables(env);
    await run(env,'DELETE FROM visitor_login_logs WHERE created<?',time()-90*86400);
    const cursor=Number(url.searchParams.get('before')||0);
    if(!Number.isSafeInteger(cursor)||cursor<0)fail(400,'分页参数无效');
    const rows=(await statement(env,'SELECT id,ip,created,result FROM visitor_login_logs '+(cursor?'WHERE id<? ':'')+'ORDER BY id DESC LIMIT 51',...(cursor?[cursor]:[])).all()).results;
    const more=rows.length>50;return json({logs:rows.slice(0,50),next:more?rows[49].id:null,retentionDays:90});
  }
  if(path==='/api/visitor/login'&&method==='POST') {
    if(!cfg.enabled)return json({ok:true});
    await tables(env);
    const ip=(request.headers.get('CF-Connecting-IP')||'unknown').slice(0,64),now=time();
    // Count every attempt atomically before doing expensive password hashing.
    const attempt=await first(env,'INSERT INTO visitor_attempts VALUES (?,1,?) ON CONFLICT(ip) DO UPDATE SET count=CASE WHEN expires<=? THEN 1 ELSE count+1 END, expires=CASE WHEN expires<=? THEN excluded.expires ELSE expires END RETURNING count',ip,now+900,now,now);
    if(attempt.count>10){await log(env,ip,'rate_limited');fail(429,'Too many attempts. Please try again in 15 minutes.','visitor_rate_limited');}
    const {password}=await readJSON(request);
    if(typeof password!=='string'||password.length>200){await log(env,ip,'failure');fail(400,'Invalid password format.','visitor_invalid');}
    if(await digest(await hash(password,cfg.password_hash.split(':')[0]))!==await digest(cfg.password_hash)) {
      await log(env,ip,'failure');fail(401,'Incorrect access password. Please try again.','visitor_invalid');
    }
    // A concurrent password change must not authenticate the old password.
    const latest=await config(env);
    if(!latest.enabled||latest.version!==cfg.version)fail(409,'The access password has changed. Please try again.');
    const token=random();
    await env.DB.batch([
      statement(env,'DELETE FROM visitor_sessions WHERE expires<=?',now),
      statement(env,'DELETE FROM visitor_attempts WHERE expires<=? OR ip=?',now,ip),
      statement(env,'INSERT INTO visitor_sessions VALUES (?,?,?)',await digest(token),cfg.version,now+86400)
    ]);
    await log(env,ip,'success');
    return json({ok:true},200,{'Set-Cookie':cookie(token)});
  }
  if(path==='/api/visitor/logout'&&method==='POST') {
    const token=cookieToken(request);
    if(token){await tables(env);await run(env,'DELETE FROM visitor_sessions WHERE id=?',await digest(token));}
    return json({ok:true},200,{'Set-Cookie':cookie('')});
  }
  fail(405,'不支持此操作');
}
