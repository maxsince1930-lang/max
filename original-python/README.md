# FORM 产品展厅

可独立部署的前后端产品展示网站。前端为 HTML/CSS/JavaScript，后端为 Python WSGI，数据库为 SQLite，上传文件存储在服务器磁盘，无外部图片、字体或 CDN 依赖。

## 已包含

- 前台产品目录、分类筛选、名称/型号/国家搜索和独立产品详情页。
- 每个产品六个独立图片位，固定顺序为 **上、下、左、右、前、后**；页面标签位于每张图片上方。
- 每个产品最多一个视频，固定放在六面图片之后；缩略图亦可切换到视频。
- 产品名称、型号、国家、WhatsApp、分类、介绍。
- WhatsApp 按钮会打开该产品对应号码的咨询窗口；没有填写号码时不显示有效联系链接。
- 后台登录、新增、编辑、草稿、上下架、删除；图片替换及视频替换/移除。
- 上架须有完整六面图，视频可选；上传后的修改须点击保存才生效。
- 手机端布局、上传大小及文件签名校验、CSRF 防护、密码加盐哈希、12 小时登录、登录限速、编辑冲突提示。
- MP4/WebM 分段请求（Range）支持，便于播放器拖动进度。

## 本地运行（Python 3.9+，无需安装依赖）

```sh
python3 app.py
```

打开 `http://127.0.0.1:8088/`，后台地址为 `http://127.0.0.1:8088/admin`。
macOS 也可以双击 `start.command`。系统若未安装 Python，需要先安装 Python。

首次运行会生成 `data/setup-token`。在后台的首次安装页输入该文件中的一次性安装码，设置管理员账号和至少 12 位密码。安装码在创建账号后自动删除；系统没有默认管理员密码。请勿把安装码、数据库或备份放进 public 目录。

需要示例产品时运行：

```sh
DEMO_DATA=1 python3 app.py
```

仅在数据库没有产品时添加 3 个明确标注的示例。示意图不是实际产品照片，六个视角为演示插画。正式部署默认不导入示例。环境变量 `DEMO_DATA=1` 不应保留在正式运行配置中。

## 部署选择

当前安装包面向 **支持 Python 或 Docker 的服务器**。它不是只上传静态网页即可使用的系统。若现有网站后台为 WordPress、纯 PHP 虚拟主机或仅允许静态文件，需要在实际部署时确认 Python/Docker/反向代理支持，或按该平台改接后台。尚未访问、修改或部署你的现有网站。

### A. Docker（建议用于 Linux 服务器）

在项目目录创建 `.env`，例如：

```dotenv
PUBLIC_ORIGIN=https://www.example.com
BASE_PATH=/products
```

`PUBLIC_ORIGIN` 只填协议和域名，端口如有则也填写，**不含子目录**。域名根目录独立部署时 `BASE_PATH` 留空。

```sh
docker compose up -d --build
```

本机 8088 端口经由 Nginx 接到已有网站。参考 `deploy/nginx.conf.example`，把 `location` 配置加入现有 HTTPS 站点；保留已有业务配置。前台位于 `https://www.example.com/products/`，后台为 `/products/admin`。反向代理保留完整路径，不可剥离 `/products`。

查看一次性安装码：

```sh
docker compose exec showroom cat /data/setup-token
```

在后台完成管理员初始化后，不再需要安装码。`showroom_data` 卷持久化数据库及媒体；更新代码请保留该卷，不要执行 `docker compose down -v`。

### B. 直接运行 Python + Gunicorn

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export PUBLIC_ORIGIN=https://www.example.com
export BASE_PATH=/products
export COOKIE_SECURE=1
export DATA_DIR=/absolute/path/to/persistent-data
gunicorn --bind 127.0.0.1:8088 --workers 2 --threads 4 --timeout 300 app:application
```

用 systemd 或服务器面板守护进程，配置与上面相同的反向代理。`python3 app.py` 为本地预览服务器；正式使用 Gunicorn。正式站点使用 HTTPS，并保持 `COOKIE_SECURE=1`。代码目录与数据目录须对运行账号有适当权限，数据库不要放在网络共享盘上。

### 上传设置

- JPG、PNG、WebP：每张最多 12 MB；不接受 SVG。
- MP4、WebM：一个产品最多一个，最大 100 MB；推荐 H.264 MP4。服务器校验格式签名但不转码，浏览器实际可播放性取决于视频编码。
- 网关/主机面板上传限制需大于 100 MB；配置示例为 105 MB、300 秒超时。
- 不限制总产品数；实际容量受服务器磁盘和并发能力约束。此版本适合单机、中小规模产品目录，不含商城、订单、支付或多租户权限。

## 备份、恢复及账号维护

```sh
python3 manage.py backup --output /safe/path/showroom-backup.zip
python3 manage.py reset-admin
```

维护命令与服务使用相同 `DATA_DIR`。Docker 中执行时命令前使用 `docker compose exec showroom`，将备份写到 `/tmp/backup.zip` 后用 `docker compose cp showroom:/tmp/backup.zip ./backup.zip` 取出。备份包含产品、账号哈希和媒体，按私密资料保存。为获得媒体与数据库完全一致的备份，备份期间暂停后台修改/上传。数据库用 SQLite 在线备份接口生成。

恢复时先停止服务，保留现有数据的备份，把 ZIP 中 `data/` 的内容恢复到实际 `DATA_DIR` 或数据卷，再修正文件属主并启动。删除/替换后不立即清除磁盘媒体，避免误删引用；定期检查磁盘占用。公开媒体可能存在最多一小时的浏览器缓存，撤下产品后列表和详情立即隐藏，已有缓存不会被远程擦除。

## 页面品牌

默认 FORM 为暂定品牌。可在 `public/index.html`、`public/app.js` 中修改名称和文案，`public/style.css` 中调整颜色。示例插画在 `public/assets/`。用户上传的实际产品通过后台维护。

## 验证

```sh
python3 tests.py
```

测试在临时数据库中执行，覆盖初始化、认证、CSRF、六图上架校验、图像/视频上传、视频 Range、增改查删、草稿隔离、版本冲突、子路径、登录限速等。测试不会改动实际产品数据。
