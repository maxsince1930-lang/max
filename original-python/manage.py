"""Offline maintenance; run on the server in the application's environment."""
import argparse, getpass, re, secrets, sqlite3, zipfile
from datetime import datetime
from pathlib import Path
from app import db, DATA, DB, UPLOADS, password_hash

parser=argparse.ArgumentParser(description='产品展厅维护工具')
parser.add_argument('command',choices=['reset-admin','backup'])
parser.add_argument('--output',help='backup ZIP destination (outside public/)')
args=parser.parse_args()
if args.command=='reset-admin':
    username=input('管理员账号（3–60 位英文或数字）: ').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.@-]{3,60}',username): raise SystemExit('账号格式无效')
    password=getpass.getpass('新密码（至少 12 位）: ')
    if len(password)<12 or len(password)>200 or password!=getpass.getpass('确认密码: '): raise SystemExit('密码长度无效或两次输入不一致')
    with db() as c:
        c.execute('INSERT INTO admins VALUES (?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password',(username,password_hash(password)))
        c.execute('DELETE FROM sessions')
    (DATA/'setup-token').unlink(missing_ok=True)
    print('管理员已更新，全部旧会话已退出。')
else:
    output=Path(args.output or ('backup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')).resolve()
    if DATA==output.parent or DATA in output.parents: raise SystemExit('备份请保存到 data 目录外')
    # Use SQLite online backup API, never copy an active WAL database directly.
    snapshot=DATA/('backup-'+secrets.token_hex(6)+'.sqlite3')
    try:
        with db() as source, sqlite3.connect(str(snapshot)) as target: source.backup(target)
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot,'data/catalog.sqlite3')
            for f in UPLOADS.iterdir():
                if f.is_file() and not f.name.endswith('.tmp'): z.write(f,'data/uploads/'+f.name)
        print('备份完成:',output)
    finally: snapshot.unlink(missing_ok=True)
