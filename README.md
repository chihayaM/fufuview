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
- Windows（启动脚本为 `.bat`；`server.py` 本身跨平台）
- 可选：JM 下载功能需要 `jmcomic` 及可用的 JM 账号

## 快速开始

1. 安装依赖

   ```bash
   pip install -r requirements.txt
   ```

2. 生成配置（两个示例文件都需要复制一份）

   ```bash
   cp config.example.json config.json
   cp option.example.yml  option.yml
   ```

   - `config.json`：书库路径、访问密码、端口
   - `option.yml`：JM 下载目录与账号（仅使用下载功能时需要）

3. 编辑 `config.json`，把 `libraryPaths` 改成你自己的漫画根目录，例如：

   ```json
   {
     "libraryPaths": ["D:\\Comics", "E:\\Manga"],
     "password": "",
     "port": 8004
   }
   ```

   `password` 留空即不启用登录。

4. 启动

   ```bash
   start.bat
   ```

   或直接 `python server.py`。启动后会自动打开浏览器（PWA 应用窗口）。

## 配置文件说明

| 文件 | 是否提交 | 说明 |
| --- | --- | --- |
| `config.json` | 否（已 gitignore） | 书库路径、密码、端口 |
| `config.example.json` | 是 | `config.json` 的模板 |
| `option.yml` | 否（已 gitignore） | JM 下载配置，含账号信息 |
| `option.example.yml` | 是 | `option.yml` 的模板 |
| `comics_meta.json` | 否（已 gitignore） | 运行时生成的标签/收藏/阅读进度数据 |

`comics_meta.json` 不需要手动创建，首次修改标签或进度时自动生成。

## 目录结构

```
.
├── server.py                 # Flask 后端（全部 API 与下载逻辑）
├── comic_tagger_gui.py       # 独立工具：Tkinter 漫画自动打标签 GUI
├── start.bat                 # Windows 启动脚本
├── requirements.txt
├── config.example.json
├── option.example.yml
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
