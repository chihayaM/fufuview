"""fufuView Pro · Flask 后端
移植自 http.server 版本，新增：安全护栏 / SSE 下载日志
"""
import os, sys, json, re, shutil, threading, hashlib, secrets, time, queue, subprocess, webbrowser

# Windows 中文环境强制 UTF-8
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
        sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)
    except Exception:
        pass

from flask import Flask, request, jsonify, send_file, render_template, Response, make_response

# ========== 基础路径 ==========
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 统一工作目录到程序所在目录。
# option.yml 里的相对路径（默认的 ./library）是按 CWD 解析的，如果不切换，
# 从别的目录启动时漫画会下载到 CWD/library，而书架扫的是程序目录/library，对不上。
try:
    os.chdir(BASE_DIR)
except OSError as e:
    print(f"⚠️ 无法切换工作目录到 {BASE_DIR}: {e}")

# ========== 本地配置 ==========
# config.json 是唯一的配置文件，不纳入版本管理（里面有书库路径和密码）。
# 缺失时按下面的模板自动生成；每个字段的含义见 README。
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')

DEFAULT_CONFIG = {
    "_说明": "fufuView Pro 配置。改完要重启程序才生效。",
    "_libraryPaths": "漫画书库根目录列表，可以写多个；每个子文件夹会被当成一本漫画。留空则用程序同目录下的 library/。",
    "libraryPaths": [],
    "_password": "访问密码，留空则不启用登录。",
    "password": "",
    "_port": "监听端口。",
    "port": 8004,
}

def ensure_config_file():
    if os.path.exists(CONFIG_FILE):
        return True
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
            f.write('\n')
        print("已生成默认的 config.json（使用默认书库 library/）")
        return True
    except OSError as e:
        print(f"⚠️ 无法生成 config.json: {e}")
        return False

def load_config():
    ensure_config_file()
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ config.json 解析失败，改用默认配置: {e}")
        return {}

_CONFIG = load_config()

PORT = int(_CONFIG.get('port', 8004))
META_FILE = _CONFIG.get('metaFile') or os.path.join(BASE_DIR, 'comics_meta.json')

# ========== 运行配置 ==========
# 漫画书库根目录列表，在 config.json 的 libraryPaths 里配置
LIBRARY_PATHS = [os.path.abspath(os.path.expanduser(p))
                 for p in (_CONFIG.get('libraryPaths') or []) if p]

# 一个书库都没配时，退回到程序同目录下的 library/，首次运行自动创建
if not LIBRARY_PATHS:
    DEFAULT_LIBRARY = os.path.join(BASE_DIR, 'library')
    try:
        os.makedirs(DEFAULT_LIBRARY, exist_ok=True)
    except OSError as e:
        print(f"⚠️ 无法创建默认书库目录: {e}")
    LIBRARY_PATHS = [DEFAULT_LIBRARY]
    USING_DEFAULT_LIBRARY = True
else:
    USING_DEFAULT_LIBRARY = False

# 访问密码，留空则不启用登录
PASSWORD = _CONFIG.get('password', '')

# ========== JM 下载配置 ==========
# 固定用 option.yml 这个名字 —— jmcomic 约定的就是它，单独跑 jmcomic 时读的也是它。
# 里面会写 JM 账号，所以不纳入版本管理；缺失时按下面模板自动生成。
OPTION_FILE = os.path.join(BASE_DIR, 'option.yml')

DEFAULT_OPTION_YML = """# jmcomic 下载配置（程序自动生成的模板）
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
"""

def ensure_option_file():
    if os.path.exists(OPTION_FILE):
        return True
    try:
        with open(OPTION_FILE, 'w', encoding='utf-8') as f:
            f.write(DEFAULT_OPTION_YML)
        print("已生成默认的 option.yml（未配置账号，部分内容可能无法下载）")
        return True
    except OSError as e:
        print(f"⚠️ 无法生成 option.yml: {e}")
        return False

# ========== Flask 应用 ==========
app = Flask(__name__,
            static_folder=os.path.join(BASE_DIR, 'static'),
            template_folder=os.path.join(BASE_DIR, 'templates'))

# ========== 安全护栏 ==========
_WIN_FORBID_CHARS = set('\\/:*?"<>|\n\t\r')

def fix_windir_name(name, attr_char='_'):
    result = ''.join(attr_char if c in _WIN_FORBID_CHARS else c for c in name)
    return result.rstrip('.')

def _valid_name(name):
    """校验文件/文件夹名合法性"""
    if not name or '..' in name or '/' in name or '\\' in name:
        return False
    if any(c in _WIN_FORBID_CHARS for c in name):
        return False
    return True

def _within_root(root, target):
    """确保 target 在 root 内（防路径穿越）"""
    root_n = os.path.normpath(root)
    target_n = os.path.normpath(target)
    return target_n == root_n or target_n.startswith(root_n + os.sep)

def _guard_dangerous_path(path):
    """拒绝非书库路径"""
    return any(os.path.normpath(path) == os.path.normpath(lp) for lp in LIBRARY_PATHS)

def safe_path(lib_path, *parts):
    target = os.path.normpath(os.path.join(lib_path, *parts))
    if not _within_root(lib_path, target):
        return None
    return target

# ========== 认证系统 ==========
_sessions = {}
_sessions_lock = threading.Lock()
SESSION_TTL = 86400

_login_attempts = {}
_login_lock = threading.Lock()
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300
LOGIN_FAIL_DELAY = 2

def _hash_password(pw):
    return hashlib.sha256(pw.encode('utf-8')).hexdigest()

_PASSWORD_HASH = _hash_password(PASSWORD) if PASSWORD else ''

def check_login_rate(ip):
    with _login_lock:
        entry = _login_attempts.get(ip)
        if not entry:
            return True, 0
        now = time.time()
        if entry['count'] >= MAX_LOGIN_ATTEMPTS:
            elapsed = now - entry['last_attempt']
            if elapsed < LOGIN_LOCKOUT_SECONDS:
                return False, int(LOGIN_LOCKOUT_SECONDS - elapsed)
            del _login_attempts[ip]
            return True, 0
        return True, 0

def record_login_failure(ip):
    with _login_lock:
        entry = _login_attempts.get(ip, {"count": 0, "last_attempt": 0})
        entry['count'] += 1
        entry['last_attempt'] = time.time()
        _login_attempts[ip] = entry

def reset_login_attempts(ip):
    with _login_lock:
        _login_attempts.pop(ip, None)

def create_session():
    token = secrets.token_hex(32)
    with _sessions_lock:
        _sessions[token] = time.time() + SESSION_TTL
    return token

def check_session(token):
    if not token:
        return False
    with _sessions_lock:
        exp = _sessions.get(token)
        if exp is None:
            return False
        if time.time() > exp:
            del _sessions[token]
            return False
        return True

def cleanup_sessions():
    now = time.time()
    with _sessions_lock:
        expired = [t for t, exp in _sessions.items() if now > exp]
        for t in expired:
            del _sessions[t]

def cleanup_login_attempts():
    now = time.time()
    with _login_lock:
        expired = [ip for ip, e in _login_attempts.items() if now - e['last_attempt'] > LOGIN_LOCKOUT_SECONDS]
        for ip in expired:
            del _login_attempts[ip]

def _periodic_cleanup():
    while True:
        time.sleep(600)
        try:
            cleanup_sessions()
            cleanup_login_attempts()
        except Exception as e:
            print(f"⚠️ 定期清理异常: {e}")

threading.Thread(target=_periodic_cleanup, daemon=True).start()

# ========== 认证辅助 ==========
def _get_token():
    token = request.cookies.get('token', '')
    if not token:
        token = request.args.get('token', '')
    return token

def _is_authed():
    if not _PASSWORD_HASH:
        return True
    return check_session(_get_token())

def _require_auth():
    """返回 None 表示通过，否则返回 401 Response"""
    if _is_authed():
        return None
    return jsonify({"error": "unauthorized"}), 401

# ========== 漫画元数据 ==========
_meta_lock = threading.RLock()
_meta_mtime = 0
_comics_cache = {}
_comics_lock = threading.Lock()

def load_meta():
    global _meta_mtime
    with _meta_lock:
        if os.path.exists(META_FILE):
            try:
                _meta_mtime = os.path.getmtime(META_FILE)
                with open(META_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
    return {}

def save_meta(meta):
    global _meta_mtime
    tmp = META_FILE + '.tmp.' + str(os.getpid()) + '.' + str(threading.get_ident())
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    with _meta_lock:
        for retry in range(3):
            try:
                os.replace(tmp, META_FILE)
                break
            except PermissionError:
                if retry == 2:
                    raise
                time.sleep(0.1 * (retry + 1))
        _meta_mtime = os.path.getmtime(META_FILE)

def comic_key(path, name):
    return f"{path}||{name}"

def natural_sort_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split('([0-9]+)', s)]

# ========== SSE 下载日志 ==========
_sse_subscribers = []
_sse_lock = threading.Lock()

def _broadcast_sse(event_type, data):
    msg = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(msg)
            except:
                dead.append(q)
        for q in dead:
            _sse_subscribers.remove(q)

# ========== jmcomic 下载 ==========
_jm_client = None
_jm_option = None
_jm_lock = threading.Lock()

def get_jmcomic():
    global _jm_client, _jm_option
    if _jm_client is not None:
        return _jm_client, _jm_option
    with _jm_lock:
        if _jm_client is not None:
            return _jm_client, _jm_option
        import jmcomic
        from jmcomic import JmOption, disable_jm_log
        disable_jm_log()
        # 固定用 option.yml（jmcomic 约定），缺失时自动生成一份模板
        if not ensure_option_file():
            _jm_option = JmOption.default()
        else:
            try:
                _jm_option = jmcomic.create_option_by_file(OPTION_FILE)
            except Exception as e:
                print(f"⚠️ option.yml 加载失败: {e}，使用默认配置")
                _jm_option = JmOption.default()
        _jm_client = _jm_option.build_jm_client()
        return _jm_client, _jm_option


# 自动打标签时，「作者」归到这个分类里（分类名要跟 tagCategories 里的一致）
AUTHOR_CATEGORY = '艺术家'
# 页数超过这个值就自动补一个「长篇」标签
LONG_ALBUM_PAGES = 50


def fetch_album_detail(client, jm_id):
    """按 JM 号取本子详情，顺便把 jmcomic 丢掉的 total_photos（总页数）捞回来。

    jmcomic 的适配器会把 page_count / pub_date 硬编码成 '0'，所以对象上的
    page_count 恒为 0，判断不了长篇；原始响应里的 total_photos 才是真实页数。
    直接读原始响应，若 jmcomic 内部结构变了就退回公开接口（此时拿不到页数）。
    """
    import jmcomic
    try:
        resp = client.req_api(client.append_params_to_url(client.API_ALBUM, {'id': jm_id}))
        if not resp.encoded_data or resp.res_data.get('name') is None:
            jmcomic.ExceptionTool.raise_missing(resp, jm_id)
        album = jmcomic.JmApiAdaptTool.parse_entity(
            resp.res_data, jmcomic.JmModuleConfig.album_class())
        album.total_photos = int(resp.res_data.get('total_photos') or 0)
        return album
    except AttributeError:
        return client.get_album_detail(jm_id)

download_tasks = {}
dl_lock = threading.Lock()

def _sync_jm_tags(jm_id, album, download_dir=None, total_photos=0):
    """下载完成后同步 JM 标签到 comics_meta.json"""
    if not album:
        return
    jm_tags = []
    for t in (album.tags or []):
        jm_tags.append(t.name if hasattr(t, 'name') else str(t))
    album_name = album.name or ""
    _SKIP_TAGS = {
        '中文', '中国', '中国語', '日文', '日語', '韩文', '英文',
        'DL版', 'dl版', 'Digital', 'digital',
        '単行本', '单行本', '短篇', '短編', '単話', '同人',
        '一般向', '成年向', '長篇', '长篇', '分卷',
        '修正', '無修正', '无修正', '高清', 'CG集',
        'AI', 'AI Generated', 'ゲーム',
        '雑誌', '週刊', '月刊', 'シリーズ',
        '番外編', 'おまけ', '特典', '附録', '完結', '未完', '続編',
        '番外', '附录', '完结', '外传', '连载', '系列',
        '翻訳', '汉化', '翻译',
    }
    sync_tags = [t for t in jm_tags if t not in _SKIP_TAGS]

    # 作者：既是标签，也要归到「艺术家」分类，不能掉进未分类
    author_tags = []
    for a in (getattr(album, 'authors', None) or []):
        a = str(a).strip()
        if a and len(a) <= 60 and a not in author_tags and a not in sync_tags:
            author_tags.append(a)
    if not author_tags:
        a = str(getattr(album, 'author', '') or '').strip()
        if a and len(a) <= 60 and a not in sync_tags:
            author_tags.append(a)
    sync_tags.extend(author_tags)

    # 长篇判断用 total_photos（JM 的真实页数）。
    # 对象上的 page_count 恒为 0，所以以前这段判断从来没生效过。
    if total_photos > LONG_ALBUM_PAGES and '长篇' not in sync_tags:
        sync_tags.append('长篇')
    if not sync_tags:
        return

    search_dirs = []
    if download_dir and os.path.isdir(download_dir):
        search_dirs.append(download_dir)
    for lib_path in LIBRARY_PATHS:
        if lib_path not in search_dirs and os.path.isdir(lib_path):
            search_dirs.append(lib_path)

    found_folder = None
    found_lib = None
    jm_id_pattern = re.compile(r'(?i)(^jm)?' + re.escape(jm_id) + r'(\D|$)')
    album_name_san = fix_windir_name(album_name) if album_name else ""

    for lib_path in search_dirs:
        try:
            folder_names = [d for d in os.listdir(lib_path)
                           if os.path.isdir(os.path.join(lib_path, d)) and d != '_trash']
            for folder_name in folder_names:
                if jm_id_pattern.search(folder_name):
                    found_folder, found_lib = folder_name, lib_path
                    break
            if not found_folder:
                for folder_name in folder_names:
                    if fix_windir_name(folder_name) == album_name_san:
                        found_folder, found_lib = folder_name, lib_path
                        break
            if not found_folder and album_name_san:
                for folder_name in folder_names:
                    folder_san = fix_windir_name(folder_name)
                    if album_name_san in folder_san or folder_san in album_name_san:
                        found_folder, found_lib = folder_name, lib_path
                        break
            if found_folder:
                break
        except Exception:
            pass

    if not found_folder:
        return

    key = comic_key(found_lib, found_folder)
    with _meta_lock:
        meta = load_meta()
        entry = meta.get(key, {})
        existing = entry.get("tags", [])
        truly_new = [t for t in sync_tags if t not in existing]
        changed = False
        if truly_new:
            entry["tags"] = existing + truly_new
            changed = True
        if not entry.get("jm_synced"):
            entry["jm_synced"] = True
            changed = True
        if entry.get("jm_id") != jm_id:
            entry["jm_id"] = jm_id
            changed = True

        # 作者标签归到「艺术家」分类，不然会出现在「未分类」里。
        # 已经手动归过类的标签一律不动（用户可能特意放进了题材等分类）。
        if author_tags:
            cfg = meta.setdefault('__tag_config__', {})
            cats = cfg.setdefault('tagCategories', {})
            placed = {t for k, v in cats.items() if k != AUTHOR_CATEGORY for t in v}
            to_add = [t for t in author_tags
                      if t not in placed and t not in cats.get(AUTHOR_CATEGORY, [])]
            if to_add:
                cats.setdefault(AUTHOR_CATEGORY, []).extend(to_add)
                changed = True

        if changed:
            meta[key] = entry
            save_meta(meta)
        if truly_new:
            print(f"🏷️ 同步标签 JM{jm_id}: [{found_folder[:30]}] +{truly_new}")
    with _comics_lock:
        _comics_cache.pop(found_lib, None)

def do_download(jm_ids):
    client, option = get_jmcomic()

    def _download_one(jm_id, idx):
        with dl_lock:
            download_tasks[jm_id]["status"] = "downloading"
            download_tasks[jm_id]["progress"] = f"正在搜索... ({idx+1}/{len(jm_ids)})"
        _broadcast_sse('log', {"id": jm_id, "msg": f"开始搜索 JM{jm_id}"})
        print(f"📥 [{jm_id}] 开始搜索...")
        try:
            # 直接取详情：一次请求就拿到标题、标签、作者和总页数，比 search_site 少一趟网络
            try:
                album = fetch_album_detail(client, jm_id)
            except Exception as e:
                if '不存在' in str(e) or 'MissingAlbum' in type(e).__name__:
                    with dl_lock:
                        download_tasks[jm_id] = {"status": "error", "progress": "未找到该漫画", "name": ""}
                    _broadcast_sse('log', {"id": jm_id, "msg": "未找到该漫画", "level": "error"})
                    print(f"⚠️ [{jm_id}] 未找到该漫画")
                    return
                raise
            with dl_lock:
                download_tasks[jm_id]["name"] = album.name
                download_tasks[jm_id]["progress"] = f"正在下载... ({idx+1}/{len(jm_ids)})"
            _broadcast_sse('log', {"id": jm_id, "msg": f"开始下载: {album.name[:40]}"})
            print(f"📥 [{jm_id}] 开始下载: {album.name[:50]}")

            dl_result = option.download_album(jm_id)
            dl_album = dl_result[0] if isinstance(dl_result, tuple) else album
            # album 是下载前取的完整详情（带 tags / authors），同步标签以它为准，
            # 不用再为标签单独查一次
            if not getattr(dl_album, 'tags', None) or not getattr(dl_album, 'authors', None):
                dl_album = album

            download_dir = option.dir_rule.base_dir
            _sync_jm_tags(jm_id, dl_album, download_dir=download_dir,
                          total_photos=getattr(album, 'total_photos', 0))

            with dl_lock:
                download_tasks[jm_id]["status"] = "done"
                download_tasks[jm_id]["progress"] = "下载完成！"
                download_tasks[jm_id]["finish_time"] = time.time()
                download_tasks[jm_id]["download_time"] = time.time()
            _broadcast_sse('log', {"id": jm_id, "msg": "下载完成 ✓", "level": "success"})
            print(f"✅ [{jm_id}] 下载完成")
        except Exception as e:
            import traceback
            traceback.print_exc()
            with dl_lock:
                download_tasks[jm_id] = {"status": "error", "progress": f"失败: {str(e)[:200]}", "finish_time": time.time()}
            _broadcast_sse('log', {"id": jm_id, "msg": f"下载失败: {str(e)[:100]}", "level": "error"})
            print(f"❌ [{jm_id}] 下载失败: {str(e)[:100]}")

    max_concurrent = min(3, len(jm_ids))
    threads = []
    for idx, jm_id in enumerate(jm_ids):
        t = threading.Thread(target=_download_one, args=(jm_id, idx), daemon=True)
        threads.append(t)
        t.start()
        if len(threads) >= max_concurrent:
            threads[0].join()
            threads = [t for t in threads if t.is_alive()]
    for t in threads:
        t.join()

# ========== IP 探测 ==========
def get_local_ip():
    """UDP connect 探测本机出口 IP（不发包，无需外网连通）"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith('127.'):
            return ip
    except:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith('127.'):
            return ip
    except:
        pass
    return "127.0.0.1"

# ========== 路由 ==========

@app.route('/')
def index():
    return render_template('index.html')

# ---------- 认证 ----------
@app.route('/api/auth')
def api_auth():
    return jsonify({"need_auth": bool(_PASSWORD_HASH), "authed": _is_authed()})

@app.route('/api/login', methods=['GET', 'POST'])
def api_login():
    if request.method == 'GET':
        if not _PASSWORD_HASH:
            return jsonify({"ok": True, "token": "", "msg": "无密码保护"})
        return jsonify({"ok": False, "error": "请使用 POST 登录"}), 405
    # POST
    if not _PASSWORD_HASH:
        return jsonify({"ok": True, "token": ""})
    data = request.get_json(silent=True) or {}
    client_ip = request.remote_addr or '0.0.0.0'
    allowed, lockout = check_login_rate(client_ip)
    if not allowed:
        time.sleep(LOGIN_FAIL_DELAY)
        return jsonify({"ok": False, "error": f"登录尝试过多，请 {lockout} 秒后再试"}), 429
    if _hash_password(data.get('password', '')) == _PASSWORD_HASH:
        reset_login_attempts(client_ip)
        token = create_session()
        resp = make_response(jsonify({"ok": True, "token": token}))
        resp.set_cookie('token', token, max_age=SESSION_TTL, httponly=True, samesite='Strict', path='/')
        return resp
    record_login_failure(client_ip)
    time.sleep(LOGIN_FAIL_DELAY)
    return jsonify({"ok": False, "error": "密码错误"}), 401

# ---------- 配置 ----------
@app.route('/api/config')
def api_config():
    err = _require_auth()
    if err: return err
    return jsonify({"libraryPaths": LIBRARY_PATHS})

# ---------- 漫画列表 ----------
@app.route('/api/comics')
def api_comics():
    err = _require_auth()
    if err: return err
    lib_path = request.args.get('path', '')
    if not _guard_dangerous_path(lib_path):
        return jsonify({"error": "非法路径"}), 403
    if not os.path.exists(lib_path):
        return jsonify({"error": "Library path not found"}), 404
    try:
        dir_mtime = os.path.getmtime(lib_path)
    except:
        dir_mtime = 0
    cur_meta_mtime = os.path.getmtime(META_FILE) if os.path.exists(META_FILE) else 0
    with _comics_lock:
        cached = _comics_cache.get(lib_path)
        if cached and cached['mtime'] == dir_mtime and cached.get('meta_mtime') == cur_meta_mtime:
            return jsonify({"comics": cached['comics']})
    items = []
    meta = load_meta()
    valid_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    try:
        for name in os.listdir(lib_path):
            full_path = os.path.join(lib_path, name)
            if os.path.isdir(full_path) and name != '_trash':
                key = comic_key(lib_path, name)
                m = meta.get(key, {})
                try:
                    page_files = sorted([f for f in os.listdir(full_path) if f.lower().endswith(valid_exts)], key=natural_sort_key)
                    pages = len(page_files)
                except:
                    page_files = []
                    pages = 0
                items.append({
                    "name": name, "mtime": os.path.getmtime(full_path), "pages": pages,
                    "fav": m.get("fav", False), "tags": m.get("tags", []),
                    "cover": page_files[0] if page_files else "",
                    "lastRead": m.get("lastRead", 0), "readProgress": m.get("readProgress", 0),
                    "jmId": m.get("jm_id", ""),
                })
        items.sort(key=lambda x: x['mtime'], reverse=True)
        with _comics_lock:
            _comics_cache[lib_path] = {"mtime": dir_mtime, "meta_mtime": cur_meta_mtime, "comics": items}
        return jsonify({"comics": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------- 页面列表 ----------
@app.route('/api/pages')
def api_pages():
    err = _require_auth()
    if err: return err
    lib_path = request.args.get('path', '')
    if not _guard_dangerous_path(lib_path):
        return jsonify({"error": "Invalid library path"}), 403
    comic_name = request.args.get('comic', '')
    if not _valid_name(comic_name):
        return jsonify({"error": "Invalid name"}), 403
    comic_path = safe_path(lib_path, comic_name)
    if comic_path and os.path.isdir(comic_path):
        valid_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
        files = sorted([f for f in os.listdir(comic_path) if f.lower().endswith(valid_exts)], key=natural_sort_key)
        return jsonify({"pages": files})
    return jsonify({"error": "not found"}), 404

# ---------- 图片（ETag） ----------
@app.route('/api/image')
def api_image():
    err = _require_auth()
    if err: return err
    lib_path = request.args.get('path', '')
    if not _guard_dangerous_path(lib_path):
        return jsonify({"error": "Invalid library path"}), 403
    comic_name = request.args.get('comic', '')
    file_name = request.args.get('file', '')
    if not _valid_name(comic_name) or not _valid_name(file_name):
        return jsonify({"error": "Invalid name"}), 403
    img_path = safe_path(lib_path, comic_name, file_name)
    if img_path and os.path.isfile(img_path):
        stat = os.stat(img_path)
        etag = hashlib.md5(f"{img_path}:{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()
        if request.headers.get('If-None-Match') == etag:
            return Response(status=304, headers={'ETag': etag, 'Cache-Control': 'no-cache'})
        ext = img_path.lower()
        mime = 'image/jpeg'
        if ext.endswith('.png'): mime = 'image/png'
        elif ext.endswith('.webp'): mime = 'image/webp'
        elif ext.endswith('.gif'): mime = 'image/gif'
        resp = send_file(img_path, mimetype=mime)
        resp.headers['ETag'] = etag
        resp.headers['Cache-Control'] = 'no-cache'
        return resp
    return jsonify({"error": "not found"}), 404

# ---------- JM 搜索 ----------
# 按 JM 号查到的详情缓存。禁漫站单次请求就要 4~5 秒，同一个号没必要重复跑网络。
_lookup_cache = {}
LOOKUP_TTL = 600  # 秒
_lookup_lock = threading.Lock()


def _pick_cover(album):
    for attr in ('cover', 'image', 'thumb', 'cover_url'):
        val = getattr(album, attr, '')
        if val and isinstance(val, str) and val.startswith('http'):
            return val
    return ''


def _is_downloaded(jm_id):
    """书库目录名里含这个 JM 号、且目录非空，就算已下载。"""
    for lib_path in LIBRARY_PATHS:
        if not os.path.exists(lib_path):
            continue
        try:
            for d in os.listdir(lib_path):
                if not os.path.isdir(os.path.join(lib_path, d)) or d == '_trash':
                    continue
                if not re.search(r'(^|\D)' + re.escape(jm_id) + r'(\D|$)', d):
                    continue
                if os.listdir(os.path.join(lib_path, d)):
                    return True
        except OSError:
            pass
    return False


@app.route('/api/search')
def api_search():
    err = _require_auth()
    if err: return err
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({"error": "请输入 JM 号"}), 400
    pure_id = re.sub(r'(?i)^jm', '', q).strip()
    if not pure_id.isdigit():
        return jsonify({"error": f"'{q}' 不是有效的 JM 号"}), 400

    with _lookup_lock:
        hit = _lookup_cache.get(pure_id)
        info = dict(hit[1]) if hit and time.time() - hit[0] < LOOKUP_TTL else None

    if info is None:
        client = get_jmcomic()[0]
        # 直接取详情，一次请求就够。
        # 走 search_site 会先搜一次、再被 302 重定向到详情页，白白多花一趟网络。
        try:
            album = client.get_album_detail(pure_id)
        except Exception as e:
            if '不存在' in str(e) or 'MissingAlbum' in type(e).__name__:
                return jsonify({"error": f"未找到 JM{pure_id}"}), 404
            return jsonify({"error": f"搜索失败: {str(e)[:100]}"}), 500
        info = {
            "id": pure_id,
            "name": album.name,
            "author": getattr(album, 'author', ''),
            "tags": getattr(album, 'tags', []),
            "episode_count": len(getattr(album, 'episode_list', None)
                                 or getattr(album, 'episodes', {}) or {}),
            "cover": _pick_cover(album),
        }
        with _lookup_lock:
            if len(_lookup_cache) > 200:  # 顺手清掉过期的，别无限涨
                now = time.time()
                for k, v in list(_lookup_cache.items()):
                    if now - v[0] >= LOOKUP_TTL:
                        _lookup_cache.pop(k, None)
            _lookup_cache[pure_id] = (time.time(), info)

    # 是否已下载每次现算：本地目录随时会变，不能跟着缓存走
    result = dict(info)
    result['downloaded'] = _is_downloaded(pure_id)
    return jsonify(result)

# ---------- 下载 ----------
@app.route('/api/download', methods=['POST'])
def api_download():
    err = _require_auth()
    if err: return err
    data = request.get_json(silent=True) or {}
    raw_ids = data.get('ids') or data.get('id', '')
    if isinstance(raw_ids, str):
        raw_ids = re.split(r'[,，\s\n]+', raw_ids)
    raw_ids = [x.strip() for x in raw_ids if x.strip()]
    if not raw_ids:
        return jsonify({"error": "请输入 JM 号"}), 400
    jm_ids = []
    for raw in raw_ids:
        pure = re.sub(r'(?i)^jm', '', raw).strip()
        if not pure.isdigit():
            return jsonify({"error": f"'{raw}' 不是有效的 JM 号"}), 400
        jm_ids.append(pure)
    new_ids = []
    for jm_id in jm_ids:
        with dl_lock:
            existing = download_tasks.get(jm_id)
            if existing and existing['status'] == 'downloading':
                continue
            if existing and existing['status'] == 'done':
                still_exists = False
                for lib_path in LIBRARY_PATHS:
                    if not os.path.exists(lib_path):
                        continue
                    try:
                        for d in os.listdir(lib_path):
                            if os.path.isdir(os.path.join(lib_path, d)) and d != '_trash' and re.search(r'(^|\D)' + re.escape(jm_id) + r'(\D|$)', d):
                                if os.listdir(os.path.join(lib_path, d)):
                                    still_exists = True
                                    break
                    except:
                        pass
                    if still_exists:
                        break
                if still_exists:
                    continue
                del download_tasks[jm_id]
        new_ids.append(jm_id)
    if not new_ids:
        return jsonify({"message": "所有任务已存在", "ids": jm_ids})
    with dl_lock:
        for jm_id in new_ids:
            download_tasks[jm_id] = {"status": "pending", "progress": "排队中...", "name": "", "queue": len(new_ids)}
    threading.Thread(target=do_download, args=(new_ids,), daemon=True).start()
    return jsonify({"message": f"已启动 {len(new_ids)} 个下载任务", "ids": new_ids, "status": "pending"})

@app.route('/api/download/status')
def api_download_status():
    err = _require_auth()
    if err: return err
    jm_id = request.args.get('id', '').strip()
    with dl_lock:
        task = download_tasks.get(jm_id)
    return jsonify(task if task else {"status": "none"})

@app.route('/api/download/tasks')
def api_download_tasks():
    err = _require_auth()
    if err: return err
    with dl_lock:
        tasks = {jid: {"status": t.get("status"), "name": t.get("name", ""), "progress": t.get("progress", "")} for jid, t in download_tasks.items()}
    return jsonify({"tasks": tasks})

@app.route('/api/download/history')
def api_download_history():
    err = _require_auth()
    if err: return err
    with dl_lock:
        active, completed, errors = [], [], []
        for jid, t in download_tasks.items():
            item = {"id": jid, "name": t.get("name", ""), "progress": t.get("progress", ""), "download_time": t.get("download_time", 0)}
            status = t.get("status", "unknown")
            if status in ('downloading', 'pending'):
                active.append(item)
            elif status == 'done':
                completed.append(item)
            elif status == 'error':
                item["error"] = t.get("progress", "未知错误")
                errors.append(item)
        active.sort(key=lambda x: x.get("download_time", 0), reverse=True)
        completed.sort(key=lambda x: x.get("download_time", 0), reverse=True)
        errors.sort(key=lambda x: x.get("download_time", 0), reverse=True)
    return jsonify({"active": active, "completed": completed, "errors": errors, "total": len(active) + len(completed) + len(errors)})

# ---------- SSE 下载日志流 ----------
@app.route('/api/download/stream')
def api_download_stream():
    err = _require_auth()
    if err: return err
    q = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_subscribers.append(q)
    def generate():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if q in _sse_subscribers:
                    _sse_subscribers.remove(q)
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

# ---------- 统计 ----------
@app.route('/api/stats')
def api_stats():
    err = _require_auth()
    if err: return err
    meta = load_meta()
    total = fav_count = 0
    all_tags = {}
    for path in LIBRARY_PATHS:
        if not os.path.exists(path):
            continue
        with _comics_lock:
            cached = _comics_cache.get(path)
        if cached and cached.get('meta_mtime') == (os.path.getmtime(META_FILE) if os.path.exists(META_FILE) else 0):
            for c in cached['comics']:
                total += 1
                if c.get('fav'): fav_count += 1
                for t in c.get('tags', []):
                    all_tags[t] = all_tags.get(t, 0) + 1
        else:
            for name in os.listdir(path):
                if os.path.isdir(os.path.join(path, name)) and name != '_trash':
                    total += 1
                    m = meta.get(comic_key(path, name), {})
                    if m.get("fav"): fav_count += 1
                    for t in m.get("tags", []):
                        all_tags[t] = all_tags.get(t, 0) + 1
    return jsonify({"total": total, "fav": fav_count, "tags": all_tags})

# ---------- 标签配置 ----------
@app.route('/api/tag-config', methods=['GET', 'POST'])
def api_tag_config():
    err = _require_auth()
    if err: return err
    if request.method == 'GET':
        with _meta_lock:
            meta = load_meta()
            cfg = meta.get('__tag_config__', {})
            if 'homepageTags' not in cfg:
                all_tags_set = set()
                for k, v in meta.items():
                    if k == '__tag_config__' or not isinstance(v, dict): continue
                    for t in v.get('tags', []):
                        if t != 'del': all_tags_set.add(t)
                old_hidden = set(cfg.get('hiddenTags', []))
                cfg['homepageTags'] = sorted([t for t in all_tags_set if t not in old_hidden])
                meta['__tag_config__'] = cfg
                save_meta(meta)
        return jsonify({"homepageTags": cfg.get('homepageTags', []), "tagCategories": cfg.get('tagCategories', {})})
    # POST
    data = request.get_json(silent=True) or {}
    with _meta_lock:
        meta = load_meta()
        cfg = meta.get('__tag_config__', {})
        if 'homepageTags' not in cfg:
            all_tags_set = set()
            for k, v in meta.items():
                if k == '__tag_config__' or not isinstance(v, dict): continue
                for t in v.get('tags', []):
                    if t != 'del': all_tags_set.add(t)
            old_hidden = set(cfg.get('hiddenTags', []))
            cfg['homepageTags'] = sorted([t for t in all_tags_set if t not in old_hidden])
        if 'homepageTags' in data:
            cfg['homepageTags'] = list(data['homepageTags'])
        if 'tagCategories' in data:
            cfg['tagCategories'] = data['tagCategories']
        meta['__tag_config__'] = cfg
        save_meta(meta)
    return jsonify({"ok": True, "homepageTags": cfg.get('homepageTags', []), "tagCategories": cfg.get('tagCategories', {})})

# ---------- 元数据更新 ----------
@app.route('/api/meta', methods=['POST'])
def api_meta():
    err = _require_auth()
    if err: return err
    data = request.get_json(silent=True) or {}
    path = data.get('path', '')
    name = data.get('name', '')
    if not path or not name:
        return jsonify({"error": "缺少 path 或 name"}), 400
    key = comic_key(path, name)
    with _meta_lock:
        meta = load_meta()
        if key not in meta:
            meta[key] = {}
        if 'fav' in data:
            meta[key]['fav'] = bool(data['fav'])
        if 'tags' in data:
            meta[key]['tags'] = list(data['tags'])
        if 'addTag' in data:
            tags = meta[key].get('tags', [])
            if data['addTag'] not in tags:
                tags.append(data['addTag'])
            meta[key]['tags'] = tags
        if 'removeTag' in data:
            tags = meta[key].get('tags', [])
            if data['removeTag'] in tags:
                tags.remove(data['removeTag'])
            meta[key]['tags'] = tags
        if 'lastRead' in data:
            meta[key]['lastRead'] = int(data['lastRead'])
            read_entries = [(k, v.get('lastRead', 0)) for k, v in meta.items()
                            if k != '__tag_config__' and isinstance(v, dict) and v.get('lastRead', 0) > 0]
            if len(read_entries) > 20:
                read_entries.sort(key=lambda x: x[1], reverse=True)
                for old_key, _ in read_entries[20:]:
                    meta[old_key].pop('lastRead', None)
        if 'readProgress' in data:
            meta[key]['readProgress'] = int(data['readProgress'])
        save_meta(meta)
    return jsonify({"ok": True, **meta[key]})

# ---------- 重置阅读进度 ----------
@app.route('/api/reset-progress', methods=['POST'])
def api_reset_progress():
    err = _require_auth()
    if err: return err
    with _meta_lock:
        meta = load_meta()
        count = 0
        for key, val in meta.items():
            if key == '__tag_config__': continue
            if not isinstance(val, dict): continue
            if 'lastRead' in val or 'readProgress' in val:
                val.pop('lastRead', None)
                val.pop('readProgress', None)
                count += 1
        save_meta(meta)
    with _comics_lock:
        _comics_cache.clear()
    return jsonify({"ok": True, "reset": count})

# ---------- 删除漫画 ----------
@app.route('/api/delete-comic', methods=['POST'])
def api_delete_comic():
    err = _require_auth()
    if err: return err
    data = request.get_json(silent=True) or {}
    path = data.get('path', '')
    name = data.get('name', '')
    if not path or not name:
        return jsonify({"error": "缺少 path 或 name"}), 400
    if not _guard_dangerous_path(path):
        return jsonify({"error": "非法路径"}), 403
    if not _valid_name(name):
        return jsonify({"error": "非法名称"}), 403
    key = comic_key(path, name)
    comic_path = os.path.join(path, name)
    try:
        if os.path.exists(comic_path):
            shutil.rmtree(comic_path)
        with _meta_lock:
            meta = load_meta()
            if key in meta:
                jm_match = re.search(r'(\d{5,})', name)
                if jm_match:
                    with dl_lock:
                        download_tasks.pop(jm_match.group(1), None)
                del meta[key]
                save_meta(meta)
        with _comics_lock:
            _comics_cache.clear()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

# ---------- 批量清理 del 标签漫画 ----------
@app.route('/api/clean-del', methods=['POST'])
def api_clean_del():
    err = _require_auth()
    if err: return err
    meta = load_meta()
    deleted = 0
    errors = []
    to_remove_keys = []
    for key, val in meta.items():
        if not isinstance(val, dict): continue
        if 'del' not in val.get('tags', []): continue
        if '||' not in key: continue
        path, name = key.split('||', 1)
        comic_path = os.path.join(path, name)
        try:
            if os.path.exists(comic_path):
                shutil.rmtree(comic_path)
            to_remove_keys.append(key)
            deleted += 1
            jm_match = re.search(r'(\d{5,})', name)
            if jm_match:
                with dl_lock:
                    download_tasks.pop(jm_match.group(1), None)
        except Exception as e:
            errors.append(f"{name}: {str(e)[:100]}")
    if to_remove_keys:
        with _meta_lock:
            meta = load_meta()
            for key in to_remove_keys:
                if key in meta:
                    del meta[key]
            save_meta(meta)
    with _comics_lock:
        _comics_cache.clear()
    return jsonify({"ok": True, "deleted": deleted, "errors": errors})

# ---------- 重命名标签 ----------
@app.route('/api/rename-tag', methods=['POST'])
def api_rename_tag():
    err = _require_auth()
    if err: return err
    data = request.get_json(silent=True) or {}
    old_name = (data.get('oldName') or '').strip()
    new_name = (data.get('newName') or '').strip()
    if not old_name or not new_name:
        return jsonify({"error": "缺少标签名"}), 400
    if old_name == new_name:
        return jsonify({"error": "新旧标签名相同"}), 400
    with _meta_lock:
        meta = load_meta()
        count = 0
        for key, val in meta.items():
            if key == '__tag_config__' or not isinstance(val, dict): continue
            tags = val.get('tags', [])
            if old_name in tags:
                if new_name in tags:
                    tags.remove(old_name)
                else:
                    tags[tags.index(old_name)] = new_name
                count += 1
        cfg = meta.get('__tag_config__', {})
        for cat, tags in cfg.get('tagCategories', {}).items():
            if old_name in tags:
                tags[tags.index(old_name)] = new_name
        homepage = cfg.get('homepageTags', [])
        if old_name in homepage:
            homepage[homepage.index(old_name)] = new_name
        meta['__tag_config__'] = cfg
        save_meta(meta)
    with _comics_lock:
        _comics_cache.clear()
    return jsonify({"ok": True, "renamed": count, "newName": new_name})

# ---------- 删除标签 ----------
@app.route('/api/delete-tag', methods=['POST'])
def api_delete_tag():
    err = _require_auth()
    if err: return err
    data = request.get_json(silent=True) or {}
    tag_name = (data.get('name') or '').strip()
    if not tag_name:
        return jsonify({"error": "缺少标签名"}), 400
    with _meta_lock:
        meta = load_meta()
        count = 0
        for key, val in meta.items():
            if key == '__tag_config__' or not isinstance(val, dict): continue
            tags = val.get('tags', [])
            if tag_name in tags:
                tags.remove(tag_name)
                count += 1
        cfg = meta.get('__tag_config__', {})
        for cat, tags in list(cfg.get('tagCategories', {}).items()):
            if tag_name in tags:
                tags.remove(tag_name)
            if not tags:
                del cfg['tagCategories'][cat]
        homepage = cfg.get('homepageTags', [])
        if tag_name in homepage:
            homepage.remove(tag_name)
        meta['__tag_config__'] = cfg
        save_meta(meta)
    with _comics_lock:
        _comics_cache.clear()
    return jsonify({"ok": True, "removed": count})

# ---------- 服务器信息 ----------
@app.route('/api/info')
def api_info():
    return jsonify({"version": "2.0.0", "name": "fufuView Pro"})

# ========== 启动时以 PWA 应用窗口打开 ==========
def _find_browser_exe():
    """查找 Edge / Chrome 可执行文件（用于 --app 应用窗口模式）"""
    local_app = os.environ.get('LOCALAPPDATA', '')
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.join(local_app, r"Microsoft\Edge\Application\msedge.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(local_app, r"Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None

def _open_app_window(url):
    """以独立应用窗口（PWA --app 模式，无地址栏/标签栏）打开；失败则退回默认浏览器"""
    exe = _find_browser_exe()
    if exe:
        try:
            subprocess.Popen([exe, f'--app={url}'])
            print(f"   已以应用窗口(PWA)模式打开")
            return
        except Exception as e:
            print(f"⚠️ 应用窗口打开失败，退回默认浏览器: {e}")
    webbrowser.open(url)

# ========== 启动 ==========
if __name__ == '__main__':
    import socket as _socket
    local_ip = get_local_ip()

    # --- 并发启动: jmcomic 加载 + Flask 服务 ---
    jm_result = {}

    def _init_jmcomic():
        try:
            jm_dir = get_jmcomic()[1].dir_rule.base_dir
            jm_result['ok'] = True
            jm_result['dir'] = jm_dir
        except Exception as e:
            jm_result['ok'] = False
            jm_result['error'] = e

    jm_thread = threading.Thread(target=_init_jmcomic, daemon=True)
    jm_thread.start()

    flask_thread = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=PORT, threaded=True, debug=False),
        daemon=True
    )
    flask_thread.start()

    # --- 等待 Flask 端口就绪 ---
    for _ in range(50):  # 最多等 5 秒
        try:
            with _socket.create_connection(('127.0.0.1', PORT), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

    # --- Flask 就绪后立即拉起浏览器（jmcomic 后台继续加载） ---
    print(f"✅ fufuView Pro 服务器已启动！")
    print(f"   访问地址: http://{local_ip}:{PORT}")
    for p in LIBRARY_PATHS:
        mark = "（默认）" if USING_DEFAULT_LIBRARY else ""
        exists = "" if os.path.isdir(p) else "  ← 目录不存在"
        print(f"   书库: {p} {mark}{exists}")
    if USING_DEFAULT_LIBRARY:
        print(f"   还没有配置漫画目录，已自动使用 {LIBRARY_PATHS[0]}")
        print(f"   把漫画文件夹放进去即可；或编辑 config.json 的 libraryPaths 添加其他目录")
    _open_app_window(f'http://{local_ip}:{PORT}')

    # --- jmcomic 加载结果异步输出 ---
    def _report_jm():
        jm_thread.join(timeout=30)
        if jm_result.get('ok'):
            print(f"   jmcomic 已加载 | 下载目录: {jm_result['dir']}")
        else:
            print(f"⚠️ jmcomic 加载失败（本地阅读不受影响）: {jm_result.get('error', '超时')}")
    threading.Thread(target=_report_jm, daemon=True).start()

    # 主线程保持存活
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n👋 服务器已停止")
