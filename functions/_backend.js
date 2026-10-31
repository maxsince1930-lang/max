const ANGLES = ['top', 'bottom', 'left', 'right', 'front', 'back'];
const TYPES = {'image/jpeg':['jpg','image'], 'image/png':['png','image'], 'image/webp':['webp','image'], 'video/mp4':['mp4','video'], 'video/webm':['webm','video']};
const encoder = new TextEncoder();
const hex = bytes => Array.from(new Uint8Array(bytes), b=>b.toString(16).padStart(2,'0')).join('');
const random = () => hex(crypto.getRandomValues(new Uint8Array(32)));
const digest = async text => hex(await crypto.subtle.digest('SHA-256', encoder.encode(text)));
const now = () => Math.floor(Date.now()/1000);
function fail(status, message) { throw Object.assign(new Error(message), {status}); }
function json(body, status=200, headers={}) { return new Response(JSON.stringify(body), {status, headers:{'Content-Type':'application/json; charset=utf-8', 'Cache-Control':'no-store', ...headers}}); }
function stmt(env, sql, ...args) { return env.DB.prepare(sql).bind(...args); }
async function first(env, sql, ...args) { return stmt(env,sql,...args).first(); }
async function run(env, sql, ...args) { return stmt(env,sql,...args).run(); }
async function all(env, sql, ...args) { return (await stmt(env,sql,...args).all()).results; }
async function readJSON(request) {
  const reader=request.body?.getReader(); if(!reader) fail(400,'请求格式无效');
  let size=0, chunks=[];
  for(;;) { const {done,value}=await reader.read(); if(done) break; size+=value.length; if(size>65536){await reader.cancel();fail(413,'请求过大');} chunks.push(value); }
  let bytes=new Uint8Array(size), offset=0; for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  try { const data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)); if(!data||typeof data!=='object'||Array.isArray(data)) throw Error(); return data; }
  catch { fail(400,'请求格式无效'); }
}
async function passwordHash(password, salt=random()) {
  const key=await crypto.subtle.importKey('raw',encoder.encode(password),'PBKDF2',false,['deriveBits']);
  const bits=await crypto.subtle.deriveBits({name:'PBKDF2',salt:encoder.encode(salt),iterations:100000,hash:'SHA-256'},key,256);
  return salt+':'+hex(bits);
}
async function equal(a,b) { return typeof a==='string'&&typeof b==='string'&&(await digest(a))===(await digest(b)); }
async function session(request,env) {
  const token=request.headers.get('Cookie')?.match(/(?:^|;\s*)showcase_session=([a-f0-9]{64})(?:;|$)/)?.[1];
  if(!token) return null;
  return first(env,'SELECT * FROM sessions WHERE id=? AND expires>?',await digest(token),now());
}
async function auth(request,env,mutate=false) {
  const s=await session(request,env); if(!s) fail(401,'请先登录后台');
  if(mutate&&!await equal(request.headers.get('X-CSRF-Token'),s.csrf)) fail(403,'登录验证已失效，请刷新页面');
  return s;
}
function product(row) { return {...row, images:JSON.parse(row.images), published:!!row.published}; }
async function validate(data,env) {
  const p={};
  for(const [k,max] of Object.entries({name:120,model:100,country:80,whatsapp:30,description:3000,category:60})) {
    const v=data[k]??''; if(typeof v!=='string'||v.length>max) fail(400,'产品字段格式或长度无效'); p[k]=v.trim();
  }
  if(!p.name||!p.model||!p.country) fail(400,'请填写产品名称、型号和国家');
  p.whatsapp=p.whatsapp.replace(/[\s()+-]/g,'');
  if(p.whatsapp&&!/^[1-9][0-9]{5,14}$/.test(p.whatsapp)) fail(400,'WhatsApp 请填写含国家区号的号码');
  if(data.published!==undefined&&typeof data.published!=='boolean') fail(400,'上架状态无效');
  p.published=data.published?1:0;
  const images=data.images??{};
  if(!images||typeof images!=='object'||Array.isArray(images)||Object.keys(images).some(k=>!ANGLES.includes(k))) fail(400,'图片方位无效');
  for(const mid of Object.values(images)) {
    if(typeof mid!=='string'||!await first(env,"SELECT 1 FROM media WHERE id=? AND kind='image'",mid)) fail(400,'图片不存在，请重新上传');
  }
  p.video=data.video||null;
  if(p.video&&(typeof p.video!=='string'||!await first(env,"SELECT 1 FROM media WHERE id=? AND kind='video'",p.video))) fail(400,'视频不存在，请重新上传');
  if(p.published&&ANGLES.some(k=>!images[k])) fail(400,'上架前请上传完整的六个方位图片；未完成时可先保存草稿');
  p.images=JSON.stringify(images); return p;
}
function signature(bytes,ext) {
  const at=(offset,values)=>values.every((v,i)=>bytes[offset+i]===v);
  return {jpg:()=>at(0,[255,216,255]),png:()=>at(0,[137,80,78,71,13,10,26,10]),webp:()=>at(0,[82,73,70,70])&&at(8,[87,69,66,80]),mp4:()=>at(4,[102,116,121,112]),webm:()=>at(0,[26,69,223,163])}[ext]();
}
async function upload(request,env) {
  await auth(request,env,true);
  if(!env.MEDIA) fail(503,'尚未绑定 Cloudflare R2 媒体存储');
  const mime=(request.headers.get('Content-Type')||'').split(';')[0];
  if(!TYPES[mime]) fail(415,'仅支持 JPG、PNG、WebP 图片和 MP4、WebM 视频');
  const [ext,kind]=TYPES[mime], size=Number(request.headers.get('Content-Length'));
  if(!Number.isSafeInteger(size)||size<=0||size>(kind==='image'?12:95)*1024*1024) fail(413,'图片最大 12 MB，视频最大 95 MB');
  const reader=request.body?.getReader(); if(!reader) fail(400,'文件为空');
  // Only retain enough initial bytes to validate the signature; stream large videos to R2.
  let prefix=[], prefixSize=0;
  while(prefixSize<32) { const chunk=await reader.read(); if(chunk.done) break; prefix.push(chunk.value);prefixSize+=chunk.value.length; }
  const head=new Uint8Array(Math.min(prefixSize,32)); let offset=0;
  for(const chunk of prefix){const part=chunk.subarray(0,head.length-offset);head.set(part,offset);offset+=part.length;}
  if(!signature(head,ext)){await reader.cancel();fail(415,'文件内容与格式不匹配');}
  const id=random().slice(0,32)+'.'+ext;
  const stream=new FixedLengthStream(size), writer=stream.writable.getWriter();
  const pump=(async()=>{try{for(const chunk of prefix) await writer.write(chunk);for(;;){const {done,value}=await reader.read();if(done) break;await writer.write(value);}await writer.close();}catch(err){await writer.abort(err).catch(()=>{});throw err;}})();
  const put=env.MEDIA.put(id,stream.readable,{httpMetadata:{contentType:mime}});
  try { await Promise.all([pump,put]); await run(env,'INSERT INTO media VALUES (?,?,?,?,?)',id,kind,mime,size,now()); }
  catch(err) {await reader.cancel().catch(()=>{}); await env.MEDIA.delete(id);throw err;}
  return json({id,kind});
}
async function media(request,env,path) {
  if(!['GET','HEAD'].includes(request.method)) fail(405,'不支持此操作');
  const id=path.slice('/uploads/'.length);
  if(!/^[a-f0-9]{32}\.(jpg|png|webp|mp4|webm)$/.test(id)) fail(404,'文件不存在');
  const row=await first(env,'SELECT * FROM media WHERE id=?',id);
  const visible=await first(env,'SELECT 1 FROM products WHERE published=1 AND (video=? OR EXISTS (SELECT 1 FROM json_each(products.images) WHERE value=?)) LIMIT 1',id,id);
  if(!row||(!visible&&!await session(request,env))) fail(404,'文件不存在');
  if(!env.MEDIA) fail(503,'尚未绑定 Cloudflare R2 媒体存储');
  let start=0,end=row.size-1,status=200;
  const headers={'Content-Type':row.mime,'Accept-Ranges':'bytes','Cache-Control':visible?'public, max-age=3600':'private, no-store'};
  const range=request.headers.get('Range');
  if(range) {
    const m=/^bytes=(\d*)-(\d*)$/.exec(range);
    if(!m||(!m[1]&&!m[2])) return new Response(null,{status:416,headers:{'Content-Range':'bytes */'+row.size}});
    if(m[1]){start=Number(m[1]);if(m[2]) end=Math.min(Number(m[2]),end);}else start=Math.max(0,row.size-Number(m[2]));
    if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start>=row.size||end<start) return new Response(null,{status:416,headers:{'Content-Range':'bytes */'+row.size}});
    status=206;headers['Content-Range']=`bytes ${start}-${end}/${row.size}`;
  }
  headers['Content-Length']=String(end-start+1);
  const object=request.method==='HEAD'?await env.MEDIA.head(id):await env.MEDIA.get(id,{range:{offset:start,length:end-start+1}});
  if(!object) fail(404,'文件不存在');
  return new Response(request.method==='HEAD'?null:object.body,{status,headers});
}
async function route(request,env) {
  const url=new URL(request.url), path=url.pathname, method=request.method;
  if(['POST','PUT','DELETE','PATCH'].includes(method)) {
    const origin=request.headers.get('Origin');if(origin&&origin!==url.origin) fail(403,'请求来源不匹配');
  }
  if(!env.DB) fail(503,'后台尚未配置：请绑定 Cloudflare D1 数据库并初始化数据表');
  if(path==='/api/health'&&method==='GET') {
    await first(env,'SELECT id FROM products LIMIT 1');
    return json({ok:!!env.MEDIA,database:true,media:!!env.MEDIA});
  }
  if(path.startsWith('/uploads/')) return media(request,env,path);
  if(path==='/api/session'&&method==='GET') {
    const s=await session(request,env), admin=await first(env,'SELECT id FROM admins LIMIT 1');
    return json({authenticated:!!s,csrf:s?.csrf??null,setupRequired:!admin});
  }
  if(['/api/login','/api/setup'].includes(path)&&method==='POST') {
    const data=await readJSON(request), time=now(), ip=request.headers.get('CF-Connecting-IP')||'unknown';
    const attempt=await first(env,'INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(ip) DO UPDATE SET count=CASE WHEN expires<? THEN 1 ELSE count+1 END, expires=CASE WHEN expires<? THEN excluded.expires ELSE expires END RETURNING count',ip,time+900,time,time);
    if(attempt.count>8) fail(429,'尝试过于频繁，请 15 分钟后重试');
    const {username,password}=data;
    if(typeof username!=='string'||username.length>60||typeof password!=='string'||password.length>200) fail(400,'账号格式无效');
    if(path==='/api/setup') {
      if(await first(env,'SELECT id FROM admins LIMIT 1')) fail(409,'管理员已创建，请登录');
      if(!env.SETUP_TOKEN||env.SETUP_TOKEN.length<24) fail(503,'请先在 Cloudflare 设置至少 24 位随机安装码 SETUP_TOKEN');
      if(typeof data.token!=='string'||!await equal(data.token.trim(),env.SETUP_TOKEN.trim())) fail(403,'安装码错误，请核对 Cloudflare 中的 SETUP_TOKEN');
      if(!/^[A-Za-z0-9_.@-]{3,60}$/.test(username)||password.length<12) fail(400,'账号需为 3–60 位英文或数字，密码至少 12 位');
      // A singleton primary key closes the race between concurrent first-install requests.
      const created=await run(env,'INSERT OR IGNORE INTO admins VALUES (1,?,?)',username,await passwordHash(password));
      if(created.meta.changes!==1) fail(409,'管理员已创建，请登录');
    } else {
      const row=await first(env,'SELECT password FROM admins WHERE username=?',username);
      const saved=row?.password||'0'.repeat(64)+':'+'0'.repeat(64);
      if(!await equal(await passwordHash(password,saved.split(':')[0]),saved)) fail(401,'账号或密码错误');
    }
    const token=random(), csrf=random();
    await env.DB.batch([stmt(env,'DELETE FROM sessions WHERE expires<?',time),stmt(env,'DELETE FROM attempts WHERE expires<? OR ip=?',time,ip),stmt(env,'INSERT INTO sessions VALUES (?,?,?)',await digest(token),csrf,time+43200)]);
    return json({csrf},200,{'Set-Cookie':`showcase_session=${token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=43200`});
  }
  if(path==='/api/logout'&&method==='POST') {
    const s=await auth(request,env,true);await run(env,'DELETE FROM sessions WHERE id=?',s.id);
    return json({},200,{'Set-Cookie':'showcase_session=; Max-Age=0; HttpOnly; Secure; SameSite=Strict; Path=/'});
  }
  if(path==='/api/upload'&&method==='POST') return upload(request,env);
  if(path==='/api/products'&&method==='GET') {
    const admin=url.searchParams.get('admin')==='1';if(admin) await auth(request,env);
    return json({products:(await all(env,'SELECT * FROM products '+(admin?'':'WHERE published=1 ')+'ORDER BY created DESC')).map(product)});
  }
  if(path==='/api/products'&&method==='POST') {
    await auth(request,env,true);const p=await validate(await readJSON(request),env),id=random().slice(0,24);
    await run(env,'INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',id,p.name,p.model,p.country,p.whatsapp,p.description,p.category,p.published,p.images,p.video,now(),1);
    return json({id});
  }
  const match=/^\/api\/products\/([a-zA-Z0-9-]+)$/.exec(path);
  if(match) {
    const id=match[1], row=await first(env,'SELECT * FROM products WHERE id=?',id);
    if(method==='GET') {if(!row||(!row.published&&!await session(request,env))) fail(404,'产品不存在');return json(product(row));}
    await auth(request,env,true);if(!['PUT','DELETE'].includes(method)) fail(405,'不支持此操作');
    if(!row) fail(404,'产品不存在');const data=await readJSON(request);
    if(data.version!==row.version) fail(409,'产品已在其他页面修改，请刷新后重试');
    let result;
    if(method==='DELETE') result=await run(env,'DELETE FROM products WHERE id=? AND version=?',id,data.version);
    else {const p=await validate(data,env);result=await run(env,'UPDATE products SET name=?,model=?,country=?,whatsapp=?,description=?,category=?,published=?,images=?,video=?,version=version+1 WHERE id=? AND version=?',p.name,p.model,p.country,p.whatsapp,p.description,p.category,p.published,p.images,p.video,id,data.version);}
    if(result.meta.changes!==1) fail(409,'产品已在其他页面修改，请刷新后重试');return json({ok:true});
  }
  fail(404,'接口不存在');
}
export async function handle({request,env}) {
  let response;
  try { response=await route(request,env); }
  catch(err) {response=json({error:err.status?err.message:'后台暂时不可用，请检查 D1 数据表和存储绑定'},err.status||503);}
  const headers=new Headers(response.headers);headers.set('X-Content-Type-Options','nosniff');headers.set('X-Frame-Options','SAMEORIGIN');headers.set('Referrer-Policy','strict-origin-when-cross-origin');
  return new Response(response.body,{status:response.status,headers});
}
