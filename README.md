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

1. 在程序同目录下自动建一个 `library/` 文件夹，作为默认书库
2. 自动打开浏览器（Windows 上用 Edge/Chrome 的应用窗口模式，无地址栏）
3. 控制台打印访问地址和书库位置

**接下来只要把漫画文件夹丢进 `library/`** —— 每个子文件夹会被当成一本漫画，刷新页面即可看到。

### 换成你自己的漫画目录

默认的 `library/` 只是个落脚点。想指向已有的漫画目录，新建 `config.local.json`：

```json
{
  "libraryPaths": ["D:\\Comics", "E:\\Manga"]
}
```

保存后重启程序。路径支持多个、支持 `~`，相对路径按程序所在目录解析。

## 配置文件说明

程序读两个配置文件，后者覆盖前者：

| 文件 | 是否提交 | 放什么 |
| --- | --- | --- |
| `config.json` | ✅ 仓库自带 | 默认值。**不要在这里写密码** |
| `config.local.json` | ❌ 已 gitignore | 你的个人设置，密码写这里 |
| `option.yml` | ✅ 仓库自带 | JM 下载的默认值，没有账号信息 |
| `option.local.yml` | ❌ 已 gitignore | 你的 JM 账号等个人设置 |

这么分是因为 `config.json` 是**仓库跟踪**的文件 —— 一旦你在里面填了密码，`git add .` 就会把它提交上去。所以个人设置一律写 `.local` 那一份。

> 程序启动时如果发现 `config.json` 里填了密码，会在控制台警告你。

### config.json / config.local.json 字段

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `libraryPaths` | 字符串数组 | `[]` | 漫画根目录列表。留空则用程序同目录下的 `library/`（自动创建） |
| `password` | 字符串 | `""` | 访问密码。留空则不启用登录，任何人访问都直接进 |
| `port` | 数字 | `8004` | 监听端口 |
| `metaFile` | 字符串 | 同目录 `comics_meta.json` | 标签/收藏/进度的存放位置，一般不用改 |

### 运行时会生成的文件（都已 gitignore）

| 文件 | 说明 |
| --- | --- |
| `comics_meta.json` | 标签、收藏、阅读进度。首次打标签时自动创建，不用手动建 |
| `library/` | 默认书库目录 |

### JM 下载的账号

`option.yml` 里只有下载目录等默认值，**登录插件是注释掉的**。要用下载功能，在 `option.local.yml` 里写：

```yaml
dir_rule:
  base_dir: D:/Comics

plugins:
  after_init:
    - plugin: login
      kwargs:
        username: "你的用户名"
        password: "你的密码"
```

`option.local.yml` 只要存在，`option.yml` 就会被整体忽略，所以这份要写全。

## 常见问题

**书架一片空白**

现在的版本会直接告诉你原因。常见两种：

- 提示「书库还是空的」—— 目录对，但里面没有漫画。丢几个文件夹进 `library/` 即可。
- 提示「书库目录读不到」—— 会把读不到的路径列出来。检查 `config.local.json` 里的 `libraryPaths` 写对没有，注意 Windows 路径的反斜杠要写成 `\\`。

**改了配置不生效**

配置只在启动时读一次，改完要重启程序。

**只想局域网内自己用，不想设密码**

`password` 留空就行。但服务默认监听 `0.0.0.0`，同网段的人都能访问，介意的话自己设一个。

**下载功能报错 / 用不了**

多半是没装 `jmcomic`（`pip install jmcomic`）或没配 `option.local.yml` 的账号。本地阅读不受影响。

## 目录结构

```
.
├── server.py                 # Flask 后端（全部 API 与下载逻辑）
├── comic_tagger_gui.py       # 独立工具：Tkinter 漫画自动打标签 GUI
├── start.bat                 # Windows 启动脚本
├── requirements.txt
├── config.json               # 默认配置（可提交）
├── option.yml                # JM 下载默认配置（可提交）
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
