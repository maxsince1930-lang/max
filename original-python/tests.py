"""Integration checks using WSGI, isolated temporary storage, no network required."""
import os, io, json, tempfile, unittest, base64, importlib.util
from pathlib import Path
scratch=tempfile.TemporaryDirectory(prefix='form-tests-')
os.environ['DATA_DIR']=scratch.name
os.environ.pop('DEMO_DATA',None)
os.environ.pop('PUBLIC_ORIGIN',None)
os.environ.pop('BASE_PATH',None)
spec=importlib.util.spec_from_file_location('showcase_under_test',Path(__file__).with_name('app.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class Flow(unittest.TestCase):
    cookie='';csrf=''
    def request(self,path,method='GET',data=None,raw=None,mime=None,authenticated=True,headers=None,ip='test'):
        body=raw if raw is not None else json.dumps(data).encode() if data is not None else b''
        path,_,qs=path.partition('?')
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':qs,'CONTENT_LENGTH':str(len(body)),'CONTENT_TYPE':mime or 'application/json','wsgi.input':io.BytesIO(body),'wsgi.url_scheme':'http','HTTP_HOST':'localhost','REMOTE_ADDR':ip}
        if authenticated: env.update(HTTP_COOKIE=self.cookie,HTTP_X_CSRF_TOKEN=self.csrf)
        env.update(headers or {})
        captured={}
        def start(status,hs):captured.update(status=int(status.split()[0]),headers=dict(hs))
        chunks=a.application(env,start)
        try: payload=b''.join(chunks)
        finally:
            if hasattr(chunks,'close'):chunks.close()
        captured['bytes']=payload
        try:captured['json']=json.loads(payload)
        except (ValueError,UnicodeDecodeError):pass
        return captured
    def test_complete_lifecycle(self):
        self.assertTrue(self.request('/api/session')['json']['setupRequired'])
        self.assertEqual(self.request('/api/products',method='POST',data={},authenticated=False)['status'],401)
        self.assertEqual(self.request('/api/setup',method='POST',data={'token':'incorrect'},ip='bad-setup')['status'],403)
        password='test-only-password-long'
        r=self.request('/api/setup',method='POST',data={'token':(a.DATA/'setup-token').read_text(),'username':'qa-admin','password':password})
        self.assertEqual(r['status'],200,r)
        self.cookie=r['headers']['Set-Cookie'].split(';')[0];self.csrf=r['json']['csrf']
        self.assertFalse((a.DATA/'setup-token').exists())
        self.assertIn('HttpOnly',r['headers']['Set-Cookie'])
        self.assertEqual(self.request('/api/setup',method='POST',data={})['status'],409)
        self.assertEqual(self.request('/api/products',method='POST',data={},headers={'HTTP_X_CSRF_TOKEN':''})['status'],403)
        self.assertEqual(self.request('/api/products',method='POST',data={},headers={'HTTP_ORIGIN':'https://evil.example'})['status'],403)
        p={'name':'测试 <script>','model':'QA-01','country':'中国','whatsapp':'+86 138 0013 8000','description':'产品测试','category':'测试','published':True,'images':{},'video':None}
        self.assertEqual(self.request('/api/products',method='POST',data=p)['status'],400)
        p['published']=False
        r=self.request('/api/products',method='POST',data=p);self.assertEqual(r['status'],200,r);pid=r['json']['id']
        self.assertEqual(len(self.request('/api/products')['json']['products']),0)
        self.assertEqual(self.request('/api/products/'+pid,authenticated=False)['status'],404)
        self.assertEqual(self.request('/api/products?admin=1',authenticated=False)['status'],401)
        self.assertEqual(len(self.request('/api/products?admin=1')['json']['products']),1)
        self.assertEqual(self.request('/api/upload',method='POST',raw=b'<svg/>',mime='image/svg+xml')['status'],415)
        self.assertEqual(self.request('/api/upload',method='POST',raw=b'not-png',mime='image/png')['status'],415)
        self.assertEqual(self.request('/api/upload',method='POST',raw=b'\0'*(12*1024*1024+1),mime='image/png')['status'],413)
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGmkAAAAASUVORK5CYII=')
        for key in a.LABELS:
            r=self.request('/api/upload',method='POST',raw=png,mime='image/png');self.assertEqual(r['status'],200,r);p['images'][key]=r['json']['id']
        self.assertEqual(self.request('/uploads/'+p['images']['front'],authenticated=False)['status'],404)
        # A container-signature fixture checks transport/storage, not codec playback.
        video=b'\0\0\0\x18ftypmp42'+b'\0'*80
        r=self.request('/api/upload',method='POST',raw=video,mime='video/mp4');self.assertEqual(r['status'],200,r);p['video']=r['json']['id']
        p['version']=1;p['published']=True
        r=self.request('/api/products/'+pid,method='PUT',data=p);self.assertEqual(r['status'],200,r)
        self.assertEqual(self.request('/api/products/'+pid,method='PUT',data=p)['status'],409)
        self.assertEqual(self.request('/api/products/'+pid,authenticated=False)['json']['whatsapp'],'8613800138000')
        r=self.request('/uploads/'+p['video'],headers={'HTTP_RANGE':'bytes=4-11'},authenticated=False)
        self.assertEqual(r['status'],206);self.assertEqual(r['bytes'],video[4:12])
        self.assertEqual(r['headers']['Content-Length'],'8')
        self.assertEqual(self.request('/uploads/'+p['video'],headers={'HTTP_RANGE':'bytes=999999-'})['status'],416)
        self.assertEqual(self.request('/uploads/'+p['video'],headers={'HTTP_RANGE':'bytes=-4'})['bytes'],video[-4:])
        r=self.request('/uploads/'+p['images']['top'],method='HEAD');self.assertEqual(r['bytes'],b'');self.assertEqual(int(r['headers']['Content-Length']),len(png))
        self.assertEqual(self.request('/app.py')['status'],404)
        self.assertEqual(self.request('/../app.py')['status'],404)
        self.assertEqual(self.request('/assets/mark.svg')['status'],200)
        a.BASE='/products'
        try:
            self.assertEqual(self.request('/products')['status'],302)
            self.assertEqual(self.request('/products/api/products')['status'],200)
            self.assertIn(b'/products/style.css',self.request('/products/')['bytes'])
            self.assertEqual(self.request('/api/products')['status'],404)
        finally:a.BASE=''
        p['version']=2;p['published']=False
        self.assertEqual(self.request('/api/products/'+pid,method='PUT',data=p)['status'],200)
        self.assertEqual(self.request('/uploads/'+p['video'],authenticated=False)['status'],404)
        self.assertEqual(self.request('/api/products/'+pid,method='DELETE',data={'version':3})['status'],200)
        self.assertEqual(self.request('/api/products/'+pid)['status'],404)
        self.assertEqual(self.request('/api/logout',method='POST',data={})['status'],200)
        self.assertFalse(self.request('/api/session')['json']['authenticated'])
        for _ in range(8):self.assertEqual(self.request('/api/login',method='POST',data={'username':'no-user','password':'bad'},ip='attacker')['status'],401)
        self.assertEqual(self.request('/api/login',method='POST',data={},ip='attacker')['status'],429)
        r=self.request('/api/login',method='POST',data={'username':'qa-admin','password':password});self.assertEqual(r['status'],200,r)
        with a.db() as c:self.assertNotIn(password,c.execute('SELECT password FROM admins').fetchone()['password'])
        self.assertIn('Content-Security-Policy',r['headers'])

if __name__=='__main__':
    try:unittest.main()
    finally:scratch.cleanup()
