# FORM 产品展厅 · Cloudflare Pages

沿用原展示页面，以 Pages Functions + D1 + R2 实现后台。原 Python 源码保存在 original-python/，不进入 public 发布目录。

## 现有项目配置

GitHub：maxsince1930-lang/max；现有网址：https://max-2v3.pages.dev

| 配置 | 值 |
| --- | --- |
| Production branch | main |
| Framework preset | None |
| Build command | exit 0 |
| Build output directory | public |
| Root directory | 默认（仓库根目录） |

不要把输出目录设为 `.`：只有 public/ 是公开静态文件，functions/ 由 Pages 编译为服务端函数。

## 首次配置（在上线前完成）

1. Cloudflare → Storage & databases → D1 → 创建 `max-products`，在其 Console 执行 migrations/0001_initial.sql 全部内容。
2. Cloudflare → R2 → 创建私有桶 `max-product-media`。无需启用公网桶或 r2.dev；如有开通、协议或付款提示，由账户持有人确认。
3. Workers & Pages → 现有 Pages 项目 → Settings → Bindings，Production 环境绑定 D1（变量名 `DB`，选择 `max-products`）和 R2（变量名 `MEDIA`，选择 `max-product-media`）。如需分支预览，另配隔离的预览资源，勿与正式环境共用。
4. Settings → Variables and Secrets，Production 新增 Secret：`SETUP_TOKEN`，值为自行生成的至少 24 位随机安装码。不要写入 GitHub，也不要使用测试安装码。
5. 设置上表的构建参数，发布 main 的最新提交。配置变更后须重新部署。
6. `/api/health` 应返回 `ok: true, database: true, media: true`；打开 `/admin`，用安装码创建账号（密码至少 12 位）。账号由持有人自行创建。完成后可移除 SETUP_TOKEN 并重新部署，安装接口由数据库中的唯一管理员记录锁定。

没有默认账号、默认密码或公开产品。上线后请添加真实产品。每个产品按上、下、左、右、前、后上传六张图片，视频可选，发布前必须齐全。图片不超过 12 MiB，视频不超过 95 MiB（为平台请求上限预留空间）；显示中的 MB 数值采用 MiB。

## 验证与维护

本地 Node 22.13+ 执行 `node test-cloudflare.mjs`。测试使用内存 SQLite 与 R2/固定长度流替身，覆盖 32 项断言，不能代替真实 Cloudflare 环境验收。原 Python 测试亦已通过。

线上验收：健康检查、首次安装、登录、六图上传、可选视频上传与拖动播放、保存草稿、上架、查看详情、WhatsApp 链接、编辑冲突、下架后媒体隔离。不要把测试产品和默认联系号码作为真实商品发布。

管理员登录记录 12 小时有效，HttpOnly/Secure/SameSite Cookie，写入操作校验来源与 CSRF。数据库只存会话令牌摘要与加盐密码哈希。上传以固定长度流写入私有 R2；草稿媒体需要后台登录。上传成功后须保存产品。产品删除后媒体保留，避免误删引用；需定期检查 R2 占用并按引用清理孤立文件。撤下的文件可能仍有最多一小时浏览器缓存。

更新只发布代码，不重建 D1 或 R2；数据备份需同时备份 D1 与 R2。D1/R2 的用量与计费以账户控制台为准。本项目无付费升级或自动开通逻辑。

## 原站保留

迁移前 main 提交为 `8af4151bc27c9fdb1dadbdfc3f45f112effdbb25`，仅有原始 index.html。准备分支保留该文件；切换 public 输出目录后网站才使用产品展厅。原站与历史提交仍可恢复。
