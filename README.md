# fufuView Pro

本地漫画书架：Flask 后端 + 静态前端，浏览本机漫画文件夹，支持标签、收藏、阅读进度、PWA 和 JM 下载。

## 功能

- **本地书架**：扫描一个或多个漫画根目录，自动识别子文件夹为单本漫画
- **阅读器**：翻页 / 双击缩放 / 从右到左阅读 / 阅读进度记忆
- **标签与收藏**：给漫画打标签、按标签筛选、重命名/删除标签、隐藏标签
- **JM 下载**：输入 JM 号搜索并下载，SSE 实时推送下载日志，下载完成后自动回写标签
- **PWA**：可安装为桌面应用，启动时以应用窗口（无地址栏）打开
- **安全护栏**：路径穿越防护、登录密码 + 失败次数限流、会话 TTL
- **图片缓存**：基于 ETag 的 304 协商缓存

## 环境要求

- Python 3.8+
- 依赖：`flask`（必需）、`jmcomic`（只有用 JM 下载功能才需要，不装也能正常浏览本地漫画）

## 快速开始

```bash
pip install -r requirements.txt
python server.py     # Windows 也可以双击 start.bat
```

就这样，不需要先配任何东西。首次启动会：

1. 在程序目录生成 `config.json` 和 `option.yml` 两个配置文件（都是空的默认值）
2. 自动建一个 `library/` 文件夹作为默认书库
3. 自动打开浏览器（Windows 上用 Edge/Chrome 的应用窗口模式，无地址栏）
4. 控制台打印访问地址和书库位置

**想马上用，把漫画文件夹丢进 `library/` 就行** —— 每个子文件夹会被当成一本漫画，刷新页面即可看到。
想用自己已有的漫画目录、想设密码、想用 JM 下载，就按下面改配置。

## 配置

程序读两个配置文件，都放在程序目录下（和 `server.py` 同级），**首次启动自动生成**：

| 文件 | 管什么 | 提交到仓库吗 |
| --- | --- | --- |
| `config.json` | 书库目录、访问密码、端口 | ❌ 不提交 |
| `option.yml` | JM 下载设置（下载目录、账号） | ❌ 不提交 |

**文件名不要改**：`option.yml` 是 jmcomic 库认死的名字，单独调用 jmcomic 读的也是它。
两个文件都在 `.gitignore` 里，你填的本地路径和账号只留在自己机器上，不会跟着代码跑。

> 配置**只在启动时读一次，改完必须重启程序**才生效。

---

### config.json

首次启动自动生成，里面长这样。直接改值，`_` 开头的键是给人和程序看的注释，程序会忽略：

```json
{
  "_说明": "fufuView Pro 配置。改完要重启程序才生效。",
  "_libraryPaths": "漫画书库根目录列表，可以写多个；每个子文件夹会被当成一本漫画。留空则用程序同目录下的 library/。",
  "libraryPaths": [],
  "_password": "访问密码，留空则不启用登录。",
  "password": "",
  "_port": "监听端口。",
  "port": 8004
}
```

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `libraryPaths` | 字符串数组 | `[]` | 漫画根目录列表。留空则使用程序同目录下的 `library/`（自动创建） |
| `password` | 字符串 | `""` | 访问密码。留空则不启用登录，谁访问都直接进 |
| `port` | 数字 | `8004` | 监听端口 |
| `metaFile` | 字符串 | 同目录 `comics_meta.json` | 标签/收藏/进度的存放位置，一般不用写 |

**最常改的就是 `libraryPaths`。** 比如漫画分别放在 `D:\JM` 和 `E:\eh`：

```json
{
  "libraryPaths": [
    "D:\\JM",
    "E:\\eh"
  ],
  "password": "",
  "port": 8004
}
```

写路径的几条规则：

- **Windows 路径的反斜杠要写两个**：JSON 里 `\` 是转义符，`D:\JM` 必须写成 `"D:\\JM"`。懒得转义就写正斜杠 `"D:/JM"`，效果一样。
- **支持 `~`**：`"~/Comics"` 会展开成你的用户目录。
- **相对路径按程序目录解析**：`"../manga"` 指 `server.py` 上一级。
- **可以写多个**，会按顺序全部扫描。
- 每个路径下的**每个子文件夹 = 一本漫画**，所以别把整个盘符直接写进来。

改完重启，控制台会打印实际生效的书库路径，可以对着检查：

```
书库路径:
  - D:\JM
  - E:\eh  ← 目录不存在
```

带 `← 目录不存在` 的就是路径写错了（盘符不对、目录被删、或者反斜杠少写了一个）。

---

### option.yml

这份文件给 jmcomic 用，只有**用 JM 下载功能**才需要动它，纯本地浏览可以直接忽略。
首次启动生成的是不含账号的模板：

```yaml
# jmcomic 下载配置（程序自动生成的模板）
# 文档: https://github.com/hect0x7/JMComic-Crawler-Python

# 下载目录
dir_rule:
  base_dir: ./library

# 客户端: api=APP端(不限ip), html=网页端(限地区但快)
client:
  impl: api
  retry_times: 5

# 下载配置
download:
  cache: true
  image:
    decode: true
  threading:
    image: 4
    photo: 2

# 插件：登录
# 部分内容需要登录才能下载，填入你的 JM 账号后取消注释：
#
# plugins:
#   after_init:
#     - plugin: login
#       kwargs:
#         username: "你的用户名"
#         password: "你的密码"
```

要改的主要是两处：

**1. 下载到哪** —— `dir_rule.base_dir`。注意**书库可以有多个，下载目录只有一个**，这里和
`libraryPaths` 写了几个没关系。默认的 `./library` 就是默认书库，下载完直接出现在书架里。

想让下载落进自己已有的书库，改成那个目录：

```yaml
dir_rule:
  base_dir: D:/Comics
```

不管指向哪，这个目录都要能被书架扫到（在 `config.json` 的 `libraryPaths` 里，或者就是默认的
`library/` 本身），否则下载完书架里看不到。

**2. JM 账号** —— 把末尾的注释去掉，填上自己的账号：

```yaml
plugins:
  after_init:
    - plugin: login
      kwargs:
        username: "你的用户名"
        password: "你的密码"
```

不填账号也能下载，但**部分内容（需要登录/购买才能看的）会失败**。账号只写在这个文件里，
而它不进仓库，不会跟着代码跑。

改完重启程序生效。

## 常见问题

**书架一片空白**

程序会直接告诉你原因，常见两种：

- 提示「书库还是空的」—— 目录对，但里面没有漫画。丢几个文件夹进 `library/` 即可。
- 提示「书库目录读不到」—— 会把读不到的路径逐条列出来。照着检查 `config.json` 里的 `libraryPaths`，
  注意 Windows 反斜杠要写成 `\\`。

**改了配置不生效**

配置只在启动时读一次，改完要重启程序。

**忘了自己配过什么**

打开 `config.json` 和 `option.yml` 直接看，就这两个文件。想恢复出厂设置，把它们删掉再启动，
程序会重新生成一份默认的。

**只想局域网内自己用，不想设密码**

`password` 留空就行。但服务默认监听 `0.0.0.0`，同网段的人都能访问，介意的话自己设一个。

**下载功能报错 / 用不了**

多半是没装 `jmcomic`（`pip install jmcomic`）或没在 `option.yml` 里配账号。本地阅读不受影响。

## 目录结构

```
.
├── server.py                 # Flask 后端（全部 API 与下载逻辑）
├── comic_tagger_gui.py       # 独立工具：Tkinter 漫画自动打标签 GUI
├── start.bat                 # Windows 启动脚本
├── requirements.txt
├── config.json               # 主配置（首次启动生成，不提交）
├── option.yml                # JM 下载配置（首次启动生成，不提交）
├── comics_meta.json          # 标签/收藏/进度（运行时生成，不提交）
├── library/                  # 默认书库（运行时生成，不提交）
├── templates/
│   └── index.html            # 单页应用模板
└── static/
    ├── css/app.css           # 设计系统与组件样式
    ├── js/app.js             # 共享脚本
    ├── js/script.js          # 主逻辑
    ├── icons/                # PWA 图标
    ├── webfonts/             # 字体
    ├── manifest.webmanifest
    └── sw.js                 # Service Worker
```

## API

所有 `/api/*` 接口在设置了 `password` 后都需要登录。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/auth` | 查询是否需要登录 / 当前登录状态 |
| POST | `/api/login` | 登录，成功后写入 `token` Cookie |
| GET | `/api/config` | 获取书库路径列表 |
| GET | `/api/comics?path=` | 列出某个书库下的漫画 |
| GET | `/api/pages?path=&comic=` | 列出某本漫画的图片 |
| GET | `/api/image?path=&comic=&file=` | 读取图片（支持 ETag 304） |
| GET | `/api/search?q=` | 按 JM 号搜索 |
| GET | `/api/name-search?q=&start=&limit=` | 按名称搜索，翻页取 `limit` 条 |
| GET | `/api/detail?id=` | 漫画详情（页数/喜欢/观看/标签） |
| GET | `/cover/<id>?v=` | 封面图代理（3:4 竖版，站点图床要 Referer，浏览器直连会 403） |
| POST | `/api/download` | 提交下载任务 |
| GET | `/api/download/status?id=` | 查询单个任务状态 |
| GET | `/api/download/tasks` | 查询全部任务 |
| GET | `/api/download/history` | 下载历史（进行中/已完成/失败） |
| GET | `/api/download/stream` | SSE 实时下载日志 |
| GET | `/api/stats` | 统计总数、收藏数、标签分布 |
| GET/POST | `/api/tag-config` | 首页标签与标签分类配置 |
| POST | `/api/meta` | 更新收藏 / 标签 / 阅读进度 |
| POST | `/api/reset-progress` | 重置全部阅读进度 |
| POST | `/api/delete-comic` | 删除某本漫画 |
| POST | `/api/clean-del` | 批量清理带 `del` 标签的漫画 |
| POST | `/api/rename-tag` | 重命名标签 |
| POST | `/api/delete-tag` | 删除标签 |
| GET | `/api/info` | 版本信息 |

## 说明

- 服务默认监听 `0.0.0.0`，同一局域网内可访问。若暴露到公网请务必设置 `password`。
- 删除操作（`/api/delete-comic`、`/api/clean-del`）会真实删除磁盘文件，且不可恢复。
- 仅供个人本地整理使用，请遵守所在地区的法律法规。
