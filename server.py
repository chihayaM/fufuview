import http.server
import socketserver
import os
import json
import urllib.parse
import re
import socket
import shutil
import threading
import hashlib
import secrets
import time

PORT = 8004
META_FILE = 'comics_meta.json'

# ========== 硬编码配置 ==========
LIBRARY_PATHS = [
    "D:\\JM",
    "D:\\eh",
    "E:\\ehviewer",
]
PASSWORD = "#1221#"

# ========== 认证系统 ==========
_sessions = {}  # token -> expire_time
_sessions_lock = threading.Lock()
SESSION_TTL = 86400  # 24小时

# 登录安全：暴力破解防护
_login_attempts = {}  # ip -> {"count": int, "last_attempt": float}
_login_lock = threading.Lock()
MAX_LOGIN_ATTEMPTS = 5        # 最大尝试次数
LOGIN_LOCKOUT_SECONDS = 300   # 锁定时间（5分钟）
LOGIN_FAIL_DELAY = 2          # 失败后固定延迟（秒）

def _hash_password(pw):
    """SHA-256 哈希密码"""
    return hashlib.sha256(pw.encode('utf-8')).hexdigest()

# 预计算哈希（启动时一次性）
_PASSWORD_HASH = _hash_password(PASSWORD) if PASSWORD else ''

def get_password():
    """返回哈希后的密码（无密码时返回空字符串）"""
    return _PASSWORD_HASH

def check_login_rate(ip):
    """检查 IP 是否被锁定，返回 (allowed, remaining_lockout)"""
    with _login_lock:
        entry = _login_attempts.get(ip)
        if not entry:
            return True, 0
        now = time.time()
        if entry['count'] >= MAX_LOGIN_ATTEMPTS:
            elapsed = now - entry['last_attempt']
            if elapsed < LOGIN_LOCKOUT_SECONDS:
                return False, int(LOGIN_LOCKOUT_SECONDS - elapsed)
            # 锁定时间过了，重置
            del _login_attempts[ip]
            return True, 0
        return True, 0

def record_login_failure(ip):
    """记录一次登录失败"""
    with _login_lock:
        entry = _login_attempts.get(ip, {"count": 0, "last_attempt": 0})
        entry['count'] += 1
        entry['last_attempt'] = time.time()
        _login_attempts[ip] = entry

def reset_login_attempts(ip):
    """登录成功后清除失败记录"""
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
    """清理超过锁定时间的登录失败记录"""
    now = time.time()
    with _login_lock:
        expired = [ip for ip, entry in _login_attempts.items()
                   if now - entry['last_attempt'] > LOGIN_LOCKOUT_SECONDS]
        for ip in expired:
            del _login_attempts[ip]

def _periodic_cleanup():
    """后台定时清理过期数据"""
    while True:
        time.sleep(600)  # 每 10 分钟
        try:
            cleanup_sessions()
            cleanup_login_attempts()
            _cleanup_dl_tasks()
        except Exception as e:
            print(f"⚠️ 定期清理异常: {e}")

_cleanup_thread = threading.Thread(target=_periodic_cleanup, daemon=True)
_cleanup_thread.start()

# ========== 漫画元数据（comics_meta.json） ==========
_meta_lock = threading.Lock()
_meta_mtime = 0
_comics_cache = {}  # path -> { "mtime": dir_mtime, "meta_mtime": meta_mtime, "comics": [...] }
_comics_lock = threading.Lock()

def load_meta():
    global _meta_mtime
    if os.path.exists(META_FILE):
        try:
            _meta_mtime = os.path.getmtime(META_FILE)
            with open(META_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

def save_meta(meta):
    global _meta_mtime
    with _meta_lock:
        tmp = META_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, META_FILE)
        _meta_mtime = os.path.getmtime(META_FILE)

def comic_key(path, name):
    return f"{path}||{name}"

# ========== jmcomic 下载 ==========
_jm_client = None
_jm_option = None
_jm_lock = threading.Lock()

def get_jmcomic() -> tuple:
    global _jm_client, _jm_option
    if _jm_client is not None:
        return _jm_client, _jm_option
    with _jm_lock:
        if _jm_client is not None:
            return _jm_client, _jm_option
        import jmcomic
        from jmcomic import JmOption, disable_jm_log
        disable_jm_log()
        try:
            _jm_option = jmcomic.create_option_by_file('option.yml')
        except Exception as e:
            print(f"⚠️  option.yml 加载失败: {e}，使用默认配置")
            _jm_option = JmOption.default()
        _jm_client = _jm_option.new_jm_client()
        return _jm_client, _jm_option

download_tasks = {}
dl_lock = threading.Lock()
def _cleanup_dl_tasks():
    """不再自动清理下载任务，服务重启即清空"""
    pass

def do_download(jm_ids):
    """jm_ids: list of JM ID strings"""
    for jm_id in jm_ids:
        with dl_lock:
            download_tasks[jm_id] = {"status": "downloading", "progress": "等待中...", "name": "", "queue": len(jm_ids)}
    client, option = get_jmcomic()
    assert client is not None and option is not None

    def _download_one(jm_id, idx):
        with dl_lock:
            download_tasks[jm_id]["progress"] = f"正在搜索... ({idx+1}/{len(jm_ids)})"
        try:
            page = client.search_site(search_query=jm_id)
            album = page.single_album
            if not album:
                with dl_lock:
                    download_tasks[jm_id] = {"status": "error", "progress": "未找到该漫画", "name": ""}
                return
            with dl_lock:
                download_tasks[jm_id]["name"] = album.name
                download_tasks[jm_id]["progress"] = f"正在下载... ({idx+1}/{len(jm_ids)})"
            option.download_album(jm_id)
            with dl_lock:
                download_tasks[jm_id]["status"] = "done"
                download_tasks[jm_id]["progress"] = "下载完成！"
                download_tasks[jm_id]["finish_time"] = time.time()
                download_tasks[jm_id]["download_time"] = time.time()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"❌ 下载 JM{jm_id} 失败:\n{tb}")
            with dl_lock:
                download_tasks[jm_id] = {"status": "error", "progress": f"失败: {str(e)[:200]}", "finish_time": time.time()}

    # 并行下载，最多同时 3 个线程
    max_concurrent = min(3, len(jm_ids))
    threads = []
    for idx, jm_id in enumerate(jm_ids):
        t = threading.Thread(target=_download_one, args=(jm_id, idx), daemon=True)
        threads.append(t)
        t.start()
        # 控制并发数：每启动一个线程，检查是否需要等待
        if len(threads) >= max_concurrent:
            threads[0].join()
            threads = [t for t in threads if t.is_alive()]
    # 等待剩余线程完成
    for t in threads:
        t.join()


def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def send_json(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(body)

def safe_path(lib_path, *parts):
    """拼接并验证路径不越界，返回 normalized 路径或 None"""
    target = os.path.normpath(os.path.join(lib_path, *parts))
    # 确保目标路径在 lib_path 下（防止 ../ 穿越）
    if not (os.path.normpath(target).startswith(os.path.normpath(lib_path) + os.sep) or os.path.normpath(target) == os.path.normpath(lib_path)):
        return None
    return target


class ComicReaderHandler(http.server.SimpleHTTPRequestHandler):
    def address_string(self):
        return self.client_address[0]

    def get_token(self):
        """从 cookie 或 query 参数获取 token"""
        # 检查 cookie
        cookie = self.headers.get('Cookie', '')
        match = re.search(r'token=([a-f0-9]+)', cookie)
        if match:
            return match.group(1)
        # 检查 query 参数
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        return params.get('token', [''])[0]

    def is_authed(self):
        """检查是否已认证（无密码时放行）"""
        pw = get_password()
        if not pw:
            return True
        return check_session(self.get_token())

    def require_auth(self):
        """需要认证，未认证返回 401 并返回 False"""
        if self.is_authed():
            return True
        send_json(self, {"error": "unauthorized"}, 401)
        return False

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        raw_params = urllib.parse.parse_qs(parsed_path.query)
        params = {k: urllib.parse.unquote(v[0]) for k, v in raw_params.items() if v}

        # 禁止直接访问敏感文件和源码
        blocked_exts = ('.py', '.yml', '.yaml', '.env', '.sh')
        if parsed_path.path in ('/comics_meta.json', '/option.yml') or any(parsed_path.path.endswith(ext) for ext in blocked_exts):
            self.send_error(403)
            return

        # 登录接口 - 不需要认证（GET 用于检查状态）
        if parsed_path.path == '/api/login':
            pw = get_password()
            if not pw:
                send_json(self, {"ok": True, "token": "", "msg": "无密码保护"})
                return
            send_json(self, {"ok": False, "error": "请使用 POST 登录"}, 405)
            return

        # 配置接口
        if parsed_path.path == '/api/config':
            if not self.require_auth(): return
            send_json(self, {"libraryPaths": LIBRARY_PATHS})
            return

        # 认证状态检查（前端用）
        if parsed_path.path == '/api/auth':
            pw = get_password()
            send_json(self, {"need_auth": bool(pw), "authed": self.is_authed()})
            return

        # 所有其他 API 需要认证
        if parsed_path.path.startswith('/api/'):
            if not self.require_auth(): return

        if parsed_path.path == '/api/comics':
            lib_path = params.get('path', '')
            if os.path.exists(lib_path):
                # 检查缓存（同时检查目录 mtime 和 meta 文件 mtime）
                try:
                    dir_mtime = os.path.getmtime(lib_path)
                except:
                    dir_mtime = 0
                cur_meta_mtime = os.path.getmtime(META_FILE) if os.path.exists(META_FILE) else 0
                with _comics_lock:
                    cached = _comics_cache.get(lib_path)
                    if cached and cached['mtime'] == dir_mtime and cached.get('meta_mtime') == cur_meta_mtime:
                        send_json(self, {"comics": cached['comics']})
                        return

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
                                "name": name,
                                "mtime": os.path.getmtime(full_path),
                                "pages": pages,
                                "fav": m.get("fav", False),
                                "tags": m.get("tags", []),
                                "cover": page_files[0] if page_files else "",
                                "lastRead": m.get("lastRead", 0),
                                "readProgress": m.get("readProgress", 0),
                            })
                    items.sort(key=lambda x: x['mtime'], reverse=True)
                    with _comics_lock:
                        _comics_cache[lib_path] = {"mtime": dir_mtime, "meta_mtime": cur_meta_mtime, "comics": items}
                    send_json(self, {"comics": items})
                except Exception as e:
                    self.send_error(500, str(e))
            else:
                self.send_error(404, "Library path not found")

        elif parsed_path.path == '/api/pages':
            lib_path = params.get('path', '')
            comic_name = params.get('comic', '')
            comic_path = safe_path(lib_path, comic_name)
            if comic_path and os.path.exists(comic_path):
                valid_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
                try:
                    files = [f for f in os.listdir(comic_path) if f.lower().endswith(valid_exts)]
                    files.sort(key=natural_sort_key)
                    send_json(self, {"pages": files})
                except Exception as e:
                    self.send_error(500, str(e))
            else:
                self.send_error(404)

        elif parsed_path.path == '/api/image':
            lib_path = params.get('path', '')
            comic_name = params.get('comic', '')
            file_name = params.get('file', '')
            img_path = safe_path(lib_path, comic_name, file_name)
            if img_path and os.path.exists(img_path) and os.path.isfile(img_path):
                ext = img_path.lower()
                content_type = 'image/jpeg'
                if ext.endswith('.png'): content_type = 'image/png'
                elif ext.endswith('.webp'): content_type = 'image/webp'
                elif ext.endswith('.gif'): content_type = 'image/gif'
                # ETag 支持：基于文件路径+mtime+size 生成
                stat = os.stat(img_path)
                etag = hashlib.md5(f"{img_path}:{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()
                client_etag = self.headers.get('If-None-Match', '')
                if client_etag == etag:
                    self.send_response(304)
                    self.send_header('Cache-Control', 'public, max-age=604800')
                    self.send_header('ETag', etag)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-type', content_type)
                self.send_header('Content-Length', str(stat.st_size))
                self.send_header('Cache-Control', 'public, max-age=604800')
                self.send_header('ETag', etag)
                self.end_headers()
                with open(img_path, 'rb') as f:
                    shutil.copyfileobj(f, self.wfile)
            else:
                self.send_error(404)

        elif parsed_path.path == '/api/search':
            q = params.get('q', '').strip()
            if not q:
                send_json(self, {"error": "请输入 JM 号"}, 400)
                return
            pure_id = re.sub(r'(?i)^jm', '', q.strip()).strip()
            if not pure_id.isdigit():
                send_json(self, {"error": f"'{q}' 不是有效的 JM 号"}, 400)
                return
            client, option = get_jmcomic()
            assert client is not None
            try:
                page = client.search_site(search_query=pure_id)
                album = page.single_album
                if album:
                    # 尝试获取封面 URL
                    cover_url = ''
                    for attr in ('cover', 'image', 'thumb', 'cover_url'):
                        val = getattr(album, attr, '')
                        if val and isinstance(val, str) and val.startswith('http'):
                            cover_url = val
                            break
                    if not cover_url:
                        # 尝试从第一话第一页获取
                        try:
                            eps = getattr(album, 'episodes', {})
                            if eps:
                                first_ep = list(eps.values())[0]
                                photos = getattr(first_ep, 'photos', []) or getattr(first_ep, 'photo_list', [])
                                if photos:
                                    p0 = photos[0]
                                    for attr in ('img_url', 'src', 'url', 'image'):
                                        val = getattr(p0, attr, '')
                                        if val and isinstance(val, str) and val.startswith('http'):
                                            cover_url = val
                                            break
                        except: pass
                    # 检查本地目录是否实际存在
                    already_downloaded = False
                    for lib_path in LIBRARY_PATHS:
                        if not os.path.exists(lib_path):
                            continue
                        try:
                            for d in os.listdir(lib_path):
                                if os.path.isdir(os.path.join(lib_path, d)) and d != '_trash' and re.search(r'(^|\D)' + re.escape(pure_id) + r'(\D|$)', d):
                                    full = os.path.join(lib_path, d)
                                    if os.listdir(full):
                                        already_downloaded = True
                                        break
                        except:
                            pass
                        if already_downloaded:
                            break

                    send_json(self, {
                        "id": pure_id,
                        "name": album.name,
                        "author": getattr(album, 'author', ''),
                        "tags": getattr(album, 'tags', []),
                        "episode_count": len(getattr(album, 'episodes', {})),
                        "cover": cover_url,
                        "downloaded": already_downloaded,
                    })
                else:
                    send_json(self, {"error": f"未找到 JM{pure_id}"}, 404)
            except Exception as e:
                send_json(self, {"error": f"搜索失败: {str(e)[:100]}"}, 500)

        elif parsed_path.path == '/api/download/status':
            jm_id = params.get('id', '').strip()
            with dl_lock:
                task = download_tasks.get(jm_id)
            send_json(self, task if task else {"status": "none"})

        elif parsed_path.path == '/api/download/tasks':
            with dl_lock:
                tasks = {}
                for jid, t in download_tasks.items():
                    tasks[jid] = {
                        "status": t.get("status", "unknown"),
                        "name": t.get("name", ""),
                        "progress": t.get("progress", ""),
                    }
            send_json(self, {"tasks": tasks})

        elif parsed_path.path == '/api/download/history':
            with dl_lock:
                active = []
                completed = []
                errors = []
                for jid, t in download_tasks.items():
                    item = {
                        "id": jid,
                        "name": t.get("name", ""),
                        "progress": t.get("progress", ""),
                        "download_time": t.get("download_time", 0),
                    }
                    status = t.get("status", "unknown")
                    if status in ('downloading', 'pending'):
                        active.append(item)
                    elif status == 'done':
                        completed.append(item)
                    elif status == 'error':
                        item["error"] = t.get("progress", "未知错误")
                        errors.append(item)
                # 按下载时间倒序
                active.sort(key=lambda x: x.get("download_time", 0), reverse=True)
                completed.sort(key=lambda x: x.get("download_time", 0), reverse=True)
                errors.sort(key=lambda x: x.get("download_time", 0), reverse=True)
            send_json(self, {
                "active": active,
                "completed": completed,
                "errors": errors,
                "total": len(active) + len(completed) + len(errors),
            })

        elif parsed_path.path == '/api/stats':
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
            send_json(self, {"total": total, "fav": fav_count, "tags": all_tags})

        elif parsed_path.path == '/api/tag-config':
            if not self.require_auth(): return
            meta = load_meta()
            cfg = meta.get('__tag_config__', {'hiddenTags': [], 'tagCategories': {}})
            send_json(self, cfg)

        else:
            return super().do_GET()



    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else '{}'
        try:
            data = json.loads(body)
        except:
            data = {}

        # 登录接口 - 不需要认证
        if parsed_path.path == '/api/login':
            pw = get_password()
            if not pw:
                send_json(self, {"ok": True, "token": ""})
                return
            client_ip = self.client_address[0]
            allowed, lockout = check_login_rate(client_ip)
            if not allowed:
                time.sleep(LOGIN_FAIL_DELAY)
                send_json(self, {"ok": False, "error": f"登录尝试过多，请 {lockout} 秒后再试"}, 429)
                return
            if _hash_password(data.get('password', '')) == pw:
                reset_login_attempts(client_ip)
                token = create_session()
                self.send_response(200)
                self.send_header('Content-type', 'application/json; charset=utf-8')
                self.send_header('Set-Cookie', f'token={token}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict')
                body_bytes = json.dumps({"ok": True, "token": token}).encode('utf-8')
                self.send_header('Content-Length', str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
                return
            record_login_failure(client_ip)
            time.sleep(LOGIN_FAIL_DELAY)
            send_json(self, {"ok": False, "error": "密码错误"}, 401)
            return

        # 所有其他 POST 需要认证
        if not self.require_auth(): return

        if parsed_path.path == '/api/download':
            raw_ids = data.get('ids') or data.get('id', '')
            if isinstance(raw_ids, str):
                raw_ids = re.split(r'[,，\s\n]+', raw_ids)
            raw_ids = [x.strip() for x in raw_ids if x.strip()]
            if not raw_ids:
                send_json(self, {"error": "请输入 JM 号"}, 400)
                return
            jm_ids = []
            for raw in raw_ids:
                pure = re.sub(r'(?i)^jm', '', raw).strip()
                if not pure.isdigit():
                    send_json(self, {"error": f"'{raw}' 不是有效的 JM 号"}, 400)
                    return
                jm_ids.append(pure)
            # 检查重复任务
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
                send_json(self, {"message": "所有任务已存在", "ids": jm_ids})
                return
            t = threading.Thread(target=do_download, args=(new_ids,), daemon=True)
            t.start()
            send_json(self, {"message": f"已启动 {len(new_ids)} 个下载任务", "ids": new_ids, "status": "pending"})

        elif parsed_path.path == '/api/meta':
            path = data.get('path', '')
            name = data.get('name', '')
            if not path or not name:
                send_json(self, {"error": "缺少 path 或 name"}, 400)
                return
            key = comic_key(path, name)
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
                                if k != '__tag_config__' and v.get('lastRead', 0) > 0]
                if len(read_entries) > 20:
                    read_entries.sort(key=lambda x: x[1], reverse=True)
                    for old_key, _ in read_entries[20:]:
                        meta[old_key].pop('lastRead', None)
            if 'readProgress' in data:
                meta[key]['readProgress'] = int(data['readProgress'])
            save_meta(meta)
            send_json(self, {"ok": True, **meta[key]})

        elif parsed_path.path == '/api/reset-progress':
            meta = load_meta()
            count = 0
            for key, val in meta.items():
                if key == '__tag_config__':
                    continue
                if 'lastRead' in val or 'readProgress' in val:
                    val.pop('lastRead', None)
                    val.pop('readProgress', None)
                    count += 1
            save_meta(meta)
            with _comics_lock:
                _comics_cache.clear()
            send_json(self, {"ok": True, "reset": count})

        elif parsed_path.path == '/api/delete-comic':
            path = data.get('path', '')
            name = data.get('name', '')
            if not path or not name:
                send_json(self, {"error": "缺少 path 或 name"}, 400)
                return
            allowed = any(os.path.normpath(path) == os.path.normpath(lp) for lp in LIBRARY_PATHS)
            if not allowed:
                send_json(self, {"error": "非法路径"}, 403)
                return
            if os.sep in name or '/' in name or '..' in name:
                send_json(self, {"error": "非法名称"}, 403)
                return
            key = comic_key(path, name)
            comic_path = os.path.join(path, name)
            meta = load_meta()
            try:
                if os.path.exists(comic_path):
                    shutil.rmtree(comic_path)
                if key in meta:
                    jm_match = re.search(r'(\d{5,})', name)
                    if jm_match:
                        with dl_lock:
                            download_tasks.pop(jm_match.group(1), None)
                    del meta[key]
                    save_meta(meta)
                with _comics_lock:
                    _comics_cache.clear()
                send_json(self, {"ok": True})
            except Exception as e:
                send_json(self, {"error": str(e)[:200]}, 500)

        elif parsed_path.path == '/api/clean-del':
            meta = load_meta()
            deleted = 0
            errors = []
            to_remove_keys = []
            for key, val in meta.items():
                if 'del' not in val.get('tags', []):
                    continue
                if '||' not in key:
                    continue
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
            for key in to_remove_keys:
                del meta[key]
            save_meta(meta)
            with _comics_lock:
                _comics_cache.clear()
            send_json(self, {"ok": True, "deleted": deleted, "errors": errors})

        elif parsed_path.path == '/api/tag-config':
            meta = load_meta()
            cfg = meta.get('__tag_config__', {'hiddenTags': [], 'tagCategories': {}})
            if 'hiddenTags' in data:
                cfg['hiddenTags'] = list(data['hiddenTags'])
            if 'tagCategories' in data:
                cfg['tagCategories'] = data['tagCategories']
            meta['__tag_config__'] = cfg
            save_meta(meta)
            send_json(self, {"ok": True, **cfg})

        elif parsed_path.path == '/api/rename-tag':
            old_name = (data.get('oldName') or '').strip()
            new_name = (data.get('newName') or '').strip()
            if not old_name or not new_name:
                send_json(self, {"error": "缺少标签名"}, 400)
                return
            if old_name == new_name:
                send_json(self, {"error": "新旧标签名相同"}, 400)
                return
            meta = load_meta()
            count = 0
            for key, val in meta.items():
                if key == '__tag_config__' or not isinstance(val, dict):
                    continue
                tags = val.get('tags', [])
                if old_name in tags:
                    idx = tags.index(old_name)
                    tags[idx] = new_name
                    count += 1
            # 同步更新分类配置
            cfg = meta.get('__tag_config__', {})
            for cat, tags in cfg.get('tagCategories', {}).items():
                if old_name in tags:
                    tags[tags.index(old_name)] = new_name
            # 同步更新隐藏标签
            hidden = cfg.get('hiddenTags', [])
            if old_name in hidden:
                hidden[hidden.index(old_name)] = new_name
            meta['__tag_config__'] = cfg
            save_meta(meta)
            with _comics_lock:
                _comics_cache.clear()
            send_json(self, {"ok": True, "renamed": count, "newName": new_name})

        elif parsed_path.path == '/api/delete-tag':
            tag_name = (data.get('name') or '').strip()
            if not tag_name:
                send_json(self, {"error": "缺少标签名"}, 400)
                return
            meta = load_meta()
            count = 0
            for key, val in meta.items():
                if key == '__tag_config__' or not isinstance(val, dict):
                    continue
                tags = val.get('tags', [])
                if tag_name in tags:
                    tags.remove(tag_name)
                    count += 1
            # 从分类配置中移除
            cfg = meta.get('__tag_config__', {})
            for cat, tags in list(cfg.get('tagCategories', {}).items()):
                if tag_name in tags:
                    tags.remove(tag_name)
                if not tags:
                    del cfg['tagCategories'][cat]
            # 从隐藏标签中移除
            hidden = cfg.get('hiddenTags', [])
            if tag_name in hidden:
                hidden.remove(tag_name)
            meta['__tag_config__'] = cfg
            save_meta(meta)
            with _comics_lock:
                _comics_cache.clear()
            send_json(self, {"ok": True, "removed": count})

        else:
            self.send_error(404)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

if __name__ == '__main__':
    local_ip = get_local_ip()
    print(f" fufuView Pro 服务器已启动！")
    print(f" 访问地址: http://{local_ip}:{PORT}")
    import webbrowser
    webbrowser.open(f'http://{local_ip}:{PORT}')
    import jmcomic
    from jmcomic import JmOption
    try:
        opt = jmcomic.create_option_by_file('option.yml')
    except Exception as e:
        print(f"⚠️  option.yml 加载失败: {e}")
        opt = JmOption.default()
    print(f"✅ jmcomic 已加载 | 下载目录: {opt.dir_rule.base_dir}")
    httpd = ThreadingHTTPServer(('', PORT), ComicReaderHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
