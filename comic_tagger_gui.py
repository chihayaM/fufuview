#!/usr/bin/env python3
"""
漫画自动标签工具 v4.0 — UI 优化版
"""
import os, sys, json, re, shutil, threading, time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try: ctypes.windll.user32.SetProcessDPIAware()
        except Exception: pass

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    HAS_BOOTSTRAP = True
except ImportError:
    from tkinter import ttk
    HAS_BOOTSTRAP = False

try:
    import cv2, numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# ===================== 配置 =====================
LIBRARY_PATHS = [r'D:\JM', r'D:\eh', r'E:\ehviewer']
DEFAULT_META   = r'D:\Code_field\comics-app\comics_meta.json'
FONT = 'Microsoft YaHei UI'
MONO = 'Consolas'

# ===================== 核心逻辑（不变） =====================
JP_PATTERN = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]')
IGNORE_KEYWORDS = ('Digital', 'digital', 'DL', '汉化', '翻译', '中国', '修正', '訳', '译')
VALID_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif')

_global_pool, _pool_lock = None, threading.Lock()

def _get_global_pool(max_workers=4):
    global _global_pool
    with _pool_lock:
        if _global_pool is None or _global_pool._shutdown:
            _global_pool = ThreadPoolExecutor(max_workers=max_workers)
        return _global_pool

def natural_sort_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'([0-9]+)', s)]

def load_meta(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                c = f.read().strip()
                if c: return json.loads(c)
        except (json.JSONDecodeError, UnicodeDecodeError): pass
    return {}

def save_meta(meta, path):
    if os.path.exists(path): shutil.copy2(path, path + '.bak')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def _list_images(comic_path):
    try:
        files = sorted([f for f in os.listdir(comic_path)
                        if f.lower().endswith(VALID_EXTS)
                        and os.path.isfile(os.path.join(comic_path, f))],
                       key=natural_sort_key)
        return files, len(files)
    except Exception: return [], 0

def get_saturation(path):
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None: return -1
        return float(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 1].mean())
    except Exception: return -1

def check_colorful(comic_path, sample=5, min_count=4, threshold=20):
    files, count = _list_images(comic_path)
    if not files: return False, 0, 0
    picks = files if count <= sample else [files[int(i * count / sample)] for i in range(sample)]
    paths = [os.path.join(comic_path, f) for f in picks]
    colorful = failed = 0
    pool = _get_global_pool()
    for sat in [f.result() for f in [pool.submit(get_saturation, p) for p in paths]]:
        if sat < 0: failed += 1
        elif sat > threshold: colorful += 1
    return colorful >= min_count, colorful, failed

def extract_author(name):
    for block in re.findall(r'\[([^\]]+)\]', name):
        if any(kw in block for kw in IGNORE_KEYWORDS): continue
        if JP_PATTERN.search(block): return block.strip()
    return None

def ensure_tag_config(meta):
    meta.setdefault('__tag_config__', {})
    cfg = meta['__tag_config__']
    cfg.setdefault('hiddenTags', [])
    cfg.setdefault('tagCategories', {})
    cfg['tagCategories'].setdefault('艺术家', [])
    return cfg

# ===================== 扫描引擎（不变） =====================
class ScanEngine:
    def __init__(self, dirs, meta_path, color_sample, color_min,
                 color_threshold, author_min, long_pages, dry_run=False):
        self.dirs, self.meta_path = dirs, meta_path
        self.color_sample, self.color_min = color_sample, color_min
        self.color_threshold, self.author_min = color_threshold, author_min
        self.long_pages, self.dry_run = long_pages, dry_run
        self.meta = load_meta(meta_path)
        self.comic_info, self.author_map, self.valid_authors = [], {}, {}
        self.stats = {'total': 0, 'updated': 0, 'skipped': {}, 'new_artists': [], 'errors': []}
        self._stop = False

    def scan_dirs(self):
        self.comic_info, self.author_map = [], {}
        scanned = 0
        for lib_path in self.dirs:
            if not os.path.exists(lib_path): continue
            for name in os.listdir(lib_path):
                if self._stop: return scanned
                comic_path = os.path.join(lib_path, name)
                if not os.path.isdir(comic_path) or name == '_trash': continue
                scanned += 1
                key = f"{lib_path}||{name}"
                author = extract_author(name)
                self.comic_info.append((key, name, comic_path, author))
                if author: self.author_map.setdefault(author, []).append(key)
        self.valid_authors = {a: k for a, k in self.author_map.items() if len(k) >= self.author_min}
        return scanned

    def run_rules(self, rules, progress_cb=None, hit_cb=None):
        meta, cfg = self.meta, ensure_tag_config(self.meta)
        artists = set(cfg['tagCategories']['艺术家'])
        new_artists, updated, errors = [], 0, []
        skipped = {r: 0 for r in rules}

        if '作者' in rules:
            for author in self.valid_authors:
                if author not in artists:
                    artists.add(author); new_artists.append(author)
            cfg['tagCategories']['艺术家'] = sorted(artists)

        for idx, (key, name, comic_path, author) in enumerate(self.comic_info):
            if self._stop: break
            meta.setdefault(key, {})
            tags = set(meta[key].get('tags', []))
            changed = False

            if '无修正' in rules:
                if '无修正' not in tags:
                    if '無修正' in name or '无修正' in name:
                        tags.add('无修正'); changed = True
                        if hit_cb: hit_cb('无修正', name, '')
                else: skipped['无修正'] += 1

            if '长篇' in rules:
                if '长篇' not in tags:
                    _, pc = _list_images(comic_path)
                    if pc > self.long_pages:
                        tags.add('长篇'); changed = True
                        if hit_cb: hit_cb('长篇', name, f'{pc}页')
                else: skipped['长篇'] += 1

            if '全彩' in rules:
                if '全彩' not in tags:
                    try:
                        ok, c, _ = check_colorful(comic_path, self.color_sample,
                                                   self.color_min, self.color_threshold)
                        if ok:
                            tags.add('全彩'); changed = True
                            if hit_cb: hit_cb('全彩', name, f'{c}/{self.color_sample}')
                    except Exception as e: errors.append(f'{name}: {e}')
                else: skipped['全彩'] += 1

            if '作者' in rules:
                if author and author in self.valid_authors:
                    if author not in tags:
                        tags.add(author); changed = True
                        if hit_cb: hit_cb('作者', name, author)
                    else: skipped['作者'] += 1

            if changed:
                meta[key]['tags'] = sorted(tags); updated += 1
            elif not tags:
                meta.pop(key, None)

            if progress_cb: progress_cb(idx + 1)

        if not self.dry_run and (updated or new_artists):
            save_meta(meta, self.meta_path)

        self.stats.update({'total': len(self.comic_info), 'updated': updated,
                           'skipped': skipped, 'new_artists': new_artists, 'errors': errors})
        return updated, skipped, new_artists

    def stop(self): self._stop = True

# ===================== 暗色主题 =====================
C_BG     = '#0a0f14'
C_CARD   = '#111820'
C_INPUT  = '#182030'
C_BORDER = '#253040'
C_FG     = '#e8ecf0'
C_DIM    = '#99aabb'
C_GREEN  = '#4ade9a'
C_RED    = '#f06e8a'
C_YELLOW = '#f0c76e'
C_BLUE   = '#38bdf8'

def apply_fallback_theme(root):
    from tkinter import ttk as _ttk
    s = _ttk.Style(root); s.theme_use('clam')
    s.configure('.', background=C_BG, foreground=C_FG, fieldbackground=C_INPUT,
                bordercolor=C_BORDER, troughcolor=C_INPUT, selectbackground=C_BLUE,
                selectforeground='#000', font=(FONT, 11), relief='flat', borderwidth=0)
    s.configure('TFrame', background=C_BG)
    s.configure('TLabel', background=C_BG, foreground=C_FG, font=(FONT, 11))
    s.configure('TLabelframe', background=C_CARD, foreground=C_FG,
                bordercolor=C_BORDER, relief='solid', borderwidth=1)
    s.configure('TLabelframe.Label', background=C_CARD, foreground=C_BLUE, font=(FONT, 11, 'bold'))
    s.configure('TButton', background=C_INPUT, foreground=C_FG,
                bordercolor=C_BORDER, padding=(12, 6), font=(FONT, 11))
    s.map('TButton', background=[('active', '#203040'), ('pressed', C_BLUE)],
           foreground=[('active', C_FG), ('pressed', '#000')])
    s.configure('Accent.TButton', background=C_BLUE, foreground='#000',
                font=(FONT, 12, 'bold'), padding=(20, 8))
    s.map('Accent.TButton', background=[('active', '#5cc8ff'), ('pressed', '#20a0e0')])
    s.configure('Danger.TButton', background='#2a1525', foreground=C_RED, bordercolor='#3a2035')
    s.map('Danger.TButton', background=[('active', '#3a2035')])
    s.configure('TCheckbutton', background=C_CARD, foreground=C_FG,
                indicatorbackground=C_INPUT, font=(FONT, 11))
    s.map('TCheckbutton', indicatorbackground=[('selected', C_BLUE)])
    s.configure('TSpinbox', background=C_INPUT, foreground=C_FG, arrowcolor=C_DIM,
                bordercolor=C_BORDER, fieldbackground=C_INPUT, font=(FONT, 11), padding=4)
    s.map('TSpinbox', bordercolor=[('focus', C_BLUE)])
    s.configure('TEntry', fieldbackground=C_INPUT, foreground=C_FG,
                bordercolor=C_BORDER, font=(FONT, 11), padding=4)
    s.map('TEntry', bordercolor=[('focus', C_BLUE)])
    s.configure('Horizontal.TProgressbar', background=C_BLUE, troughcolor=C_INPUT, bordercolor=C_BORDER)
    root.configure(bg=C_BG)

# ===================== Tooltip =====================
class ToolTip:
    """鼠标悬停提示"""
    def __init__(self, widget, text, delay=400):
        self.widget, self.text, self.delay = widget, text, delay
        self.tip_win, self.after_id = None, None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _schedule(self, e=None):
        self.after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        if self.tip_win: return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f'+{x}+{y}')
        try: tw.wm_attributes('-topmost', True)
        except Exception: pass
        bg, fg = '#1e2a3a', '#e0e8f0'
        frame = tk.Frame(tw, bg=bg, padx=10, pady=6)
        frame.pack()
        tk.Label(frame, text=self.text, bg=bg, fg=fg,
                 font=(FONT, 10), wraplength=320, justify='left').pack()
        self.tip_win = tw

    def _hide(self, e=None):
        if self.after_id:
            self.widget.after_cancel(self.after_id); self.after_id = None
        if self.tip_win:
            self.tip_win.destroy(); self.tip_win = None

# ===================== GUI =====================
class App:
    def __init__(self):
        if HAS_BOOTSTRAP:
            self.root = ttk.Window(title='漫画自动标签工具', themename='darkly',
                                   size=(960, 780), minsize=(840, 640),
                                   resizable=(True, True))
            s = self.root.style
            s.colors.primary = '#38bdf8'
            for w in ['TButton', 'TLabel', 'TLabelframe.Label',
                       'TCheckbutton', 'TSpinbox', 'TEntry']:
                s.configure(w, foreground='#e8ecf0')
            s.configure('TLabelframe.Label', foreground='#e8ecf0', font=(FONT, 11, 'bold'))
            s.configure('TLabelframe', foreground='#e8ecf0')
            s.configure('Horizontal.TProgressbar', background='#38bdf8')
            s.map('TButton', foreground=[('active', '#38bdf8')])
            s.map('TCheckbutton', indicatorbackground=[('selected', '#38bdf8')])
            s.map('TSpinbox', bordercolor=[('focus', '#38bdf8')])
            s.map('TEntry', bordercolor=[('focus', '#38bdf8')])
        else:
            self.root = tk.Tk()
            self.root.title('漫画自动标签工具')
            self.root.geometry('960x780')
            self.root.minsize(840, 640)
            self.root.resizable(True, True)
            apply_fallback_theme(self.root)

        self.engine = None
        self.running = False
        self.start_time = 0
        self._build_ui()
        self._bind_shortcuts()

        if not HAS_BOOTSTRAP:
            self.root.after(500, lambda: self.log(
                '💡 安装 ttkbootstrap 可获得更好界面: pip install ttkbootstrap', 'warn'))

    def _w(self, parent, cls_name, **kw):
        """统一创建控件，兼容 bootstrap 和原生 ttk

        ttkbootstrap 的 LabelFrame 包装器有 bug：
        会把 bootstyle/padding 等参数透传给底层 Tk labelframe 控件，
        所以 LabelFrame 必须走特殊路径，只传 text。
        """
        if cls_name == 'LabelFrame':
            text = kw.pop('text', '')
            kw.pop('bootstyle', None)
            kw.pop('padding', None)
            if HAS_BOOTSTRAP:
                return ttk.LabelFrame(parent, text=text)
            from tkinter import ttk as _ttk
            return _ttk.LabelFrame(parent, text=text, padding=8)

        if not HAS_BOOTSTRAP:
            kw.pop('bootstyle', None)
            from tkinter import ttk as _ttk
            return getattr(_ttk, cls_name)(parent, **kw)

        return getattr(ttk, cls_name)(parent, **kw)

    def _bind_shortcuts(self):
        """键盘快捷键"""
        self.root.bind('<Control-Return>', lambda e: self._run())
        self.root.bind('<Control-r>', lambda e: self._run())
        self.root.bind('<Escape>', lambda e: self._stop_cmd() if self.running else None)

    # ==================== UI 构建 ====================
    def _build_ui(self):
        r = self.root
        main = self._w(r, 'Frame', padding=16)
        main.pack(fill='both', expand=True)

        # ===== 上半：配置 + 统计 =====
        top = self._w(main, 'Frame')
        top.pack(fill='both', expand=True, pady=(0, 10))

        # --- 左栏 ---
        left = self._w(top, 'Frame')
        left.pack(side='left', fill='both', expand=True, padx=(0, 8))

        # 📂 目录
        lf = self._w(left, 'LabelFrame', text=' 📂 漫画目录 ',
                      **({'bootstyle': 'secondary'} if HAS_BOOTSTRAP else {}))
        lf.pack(fill='x', pady=(0, 8))
        self._build_dir_section(lf)

        # 📄 Meta
        lf2 = self._w(left, 'LabelFrame', text=' 📄 Meta 文件 ',
                       **({'bootstyle': 'secondary'} if HAS_BOOTSTRAP else {}))
        lf2.pack(fill='x', pady=(0, 8))
        self._build_meta_section(lf2)

        # ⚙️ 参数
        lf3 = self._w(left, 'LabelFrame', text=' ⚙️ 参数 ',
                       **({'bootstyle': 'secondary'} if HAS_BOOTSTRAP else {}))
        lf3.pack(fill='x', pady=(0, 8))
        self._build_params_section(lf3)

        # 🏷️ 规则
        lf4 = self._w(left, 'LabelFrame', text=' 🏷️ 执行规则 ',
                       **({'bootstyle': 'secondary'} if HAS_BOOTSTRAP else {}))
        lf4.pack(fill='x', pady=(0, 8))
        self._build_rules_section(lf4)

        # 🔍 试运行
        self.dry_run_var = tk.BooleanVar(value=False)
        cb = self._w(left, 'Checkbutton', text=' 🔍 试运行（只预览，不写入）',
                      variable=self.dry_run_var,
                      **({'bootstyle': 'secondary'} if HAS_BOOTSTRAP else {}))
        cb.pack(anchor='w', pady=(4, 0))
        ToolTip(cb, '勾选后只扫描预览，不会修改 Meta 文件\n适合先检查规则命中情况')

        # --- 右栏：统计 ---
        right = self._w(top, 'Frame')
        right.pack(side='left', fill='both', expand=True, padx=(8, 0))

        lf5 = self._w(right, 'LabelFrame', text=' 📊 统计 ',
                       **({'bootstyle': 'secondary'} if HAS_BOOTSTRAP else {}))
        lf5.pack(fill='both', expand=True)
        self._build_stats_section(lf5)

        # ===== 底部操作栏 =====
        self._build_action_bar(main)

        # ===== 日志 =====
        lf6 = self._w(main, 'LabelFrame', text=' 📋 日志 ',
                       **({'bootstyle': 'secondary'} if HAS_BOOTSTRAP else {}))
        lf6.pack(fill='both', expand=True)
        self._build_log_section(lf6)

    # ---------- 目录区 ----------
    def _build_dir_section(self, parent):
        row = self._w(parent, 'Frame')
        row.pack(fill='both', expand=True, padx=8, pady=8)

        # Listbox + 滚动条
        list_frame = tk.Frame(row, bg=C_INPUT if not HAS_BOOTSTRAP else '#182030')
        list_frame.pack(side='left', fill='both', expand=True)

        sb = tk.Scrollbar(list_frame, orient='vertical')
        self.dir_listbox = tk.Listbox(
            list_frame, height=4, font=(MONO, 10),
            bg='#182030', fg='#d0e0f0',
            selectbackground=C_BLUE, selectforeground='#000',
            borderwidth=0, highlightthickness=1,
            highlightbackground='#253040', highlightcolor=C_BLUE,
            relief='flat', activestyle='none',
            yscrollcommand=sb.set)
        sb.config(command=self.dir_listbox.yview)
        self.dir_listbox.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        for d in LIBRARY_PATHS:
            self.dir_listbox.insert('end', d)

        # 双击编辑
        self.dir_listbox.bind('<Double-Button-1>', self._edit_dir)
        # Delete 键删除
        self.dir_listbox.bind('<Delete>', lambda e: self._del_dir())

        # 按钮
        bf = self._w(row, 'Frame')
        bf.pack(side='left', padx=(6, 0))

        btn_add = self._w(bf, 'Button', text='＋', command=self._add_dir,
                           **({'bootstyle': 'info-outline'} if HAS_BOOTSTRAP else {}),
                           width=3)
        btn_add.pack(fill='x', pady=(0, 2))
        ToolTip(btn_add, '添加目录 (Ctrl+O)')

        btn_del = self._w(bf, 'Button', text='－', command=self._del_dir,
                           **({'bootstyle': 'danger-outline'} if HAS_BOOTSTRAP else {}),
                           width=3)
        btn_del.pack(fill='x')
        ToolTip(btn_del, '删除选中目录 (Delete)')

        self.root.bind('<Control-o>', lambda e: self._add_dir())

    # ---------- Meta 区 ----------
    def _build_meta_section(self, parent):
        row = self._w(parent, 'Frame')
        row.pack(fill='x', padx=8, pady=8)

        self.meta_var = tk.StringVar(value=DEFAULT_META)
        entry = self._w(row, 'Entry', textvariable=self.meta_var, font=(MONO, 10))
        entry.pack(side='left', fill='x', expand=True)

        btn = self._w(row, 'Button', text='浏览', command=self._browse_meta,
                       **({'bootstyle': 'secondary'} if HAS_BOOTSTRAP else {}))
        btn.pack(side='left', padx=(6, 0))
        ToolTip(btn, '选择或新建 Meta JSON 文件')

    # ---------- 参数区 ----------
    def _build_params_section(self, parent):
        grid = self._w(parent, 'Frame')
        grid.pack(fill='x', padx=8, pady=8)

        params = [
            ('抽样数',     'color_sample',    5,   1, 50,
             '全彩检测时随机抽取的图片数量\n越大越准确，但速度越慢'),
            ('达标数',     'color_min',       4,   1, 50,
             '抽样中饱和度超标的最低数量\n达到此数即判定为「全彩」'),
            ('饱和度阈值', 'color_threshold', 20,  1, 255,
             'HSV 饱和度均值阈值\n低于此值视为灰度/低彩'),
            ('作者最低本数','author_min',      3,   2, 100,
             '作者至少要有多少本才自动打标签\n避免偶然同名误判'),
            ('长篇页数阈值','long_pages',     50,  10, 9999,
             '超过此页数自动标记为「长篇」'),
        ]
        self.param_vars = {}
        for i, (label, key, default, lo, hi, tip) in enumerate(params):
            rr, cc = divmod(i, 3)
            lbl = self._w(grid, 'Label', text=f'{label}:', font=(FONT, 10))
            lbl.grid(row=rr, column=cc * 2, sticky='e', padx=(4, 2), pady=4)
            ToolTip(lbl, tip)

            var = tk.IntVar(value=default)
            self.param_vars[key] = var
            sp = self._w(grid, 'Spinbox', from_=lo, to=hi, textvariable=var,
                          width=6, font=(FONT, 10),
                          **({'bootstyle': 'info'} if HAS_BOOTSTRAP else {}))
            sp.grid(row=rr, column=cc * 2 + 1, sticky='w', padx=(0, 10), pady=4)
            ToolTip(sp, tip)

    # ---------- 规则区 ----------
    def _build_rules_section(self, parent):
        rules_frame = self._w(parent, 'Frame')
        rules_frame.pack(fill='x', padx=8, pady=(8, 4))

        rule_tips = {
            '无修正': '扫描目录名中包含「無修正」或「无修正」的漫画',
            '长篇':   '页数超过阈值的漫画自动标记为「长篇」',
            '全彩':   '通过饱和度分析检测彩色漫画（需要 opencv）',
            '作者':   '从目录名 [作者名] 提取作者并自动打标签',
        }
        self.rule_vars = {}
        for i, (name, default) in enumerate(
                [('无修正', True), ('长篇', True), ('全彩', True), ('作者', True)]):
            var = tk.BooleanVar(value=default)
            self.rule_vars[name] = var
            cb = self._w(rules_frame, 'Checkbutton', text=name, variable=var,
                          **({'bootstyle': 'info'} if HAS_BOOTSTRAP else {}))
            cb.grid(row=i // 2, column=i % 2, sticky='w', padx=(0, 16), pady=2)
            ToolTip(cb, rule_tips.get(name, ''))

        # 全选 / 全不选
        btn_frame = self._w(parent, 'Frame')
        btn_frame.pack(fill='x', padx=8, pady=(0, 4))

        btn_all = self._w(btn_frame, 'Button', text='全选',
                           command=lambda: self._toggle_all(True),
                           **({'bootstyle': 'secondary-link'} if HAS_BOOTSTRAP else {}))
        btn_all.pack(side='left', padx=(0, 8))

        btn_none = self._w(btn_frame, 'Button', text='全不选',
                            command=lambda: self._toggle_all(False),
                            **({'bootstyle': 'secondary-link'} if HAS_BOOTSTRAP else {}))
        btn_none.pack(side='left')

    # ---------- 统计区 ----------
    def _build_stats_section(self, parent):
        bg_input = '#182030'
        self.stats_text = tk.Text(
            parent, bg=bg_input, fg='#e0e0e0', font=(FONT, 11),
            wrap='word', state='disabled', cursor='arrow',
            borderwidth=0, highlightthickness=1,
            highlightbackground='#253040', relief='flat', padx=10, pady=8)
        self.stats_text.pack(fill='both', expand=True, padx=8, pady=8)
        self.stats_text.tag_configure('h', foreground=C_BLUE, font=(FONT, 12, 'bold'))
        self.stats_text.tag_configure('v', foreground=C_GREEN)
        self.stats_text.tag_configure('d', foreground=C_DIM)
        self.stats_text.tag_configure('w', foreground=C_YELLOW)
        self.stats_text.tag_configure('e', foreground=C_RED)
        self._show_welcome()

    # ---------- 操作栏 ----------
    def _build_action_bar(self, parent):
        bot = self._w(parent, 'Frame')
        bot.pack(fill='x', pady=(0, 8))

        run_kw = {'bootstyle': 'info'} if HAS_BOOTSTRAP else {'style': 'Accent.TButton'}
        self.btn_run = self._w(bot, 'Button', text='▶  开始执行',
                                command=self._run, **run_kw)
        self.btn_run.pack(side='left', padx=(0, 8))
        ToolTip(self.btn_run, '开始扫描并执行规则 (Ctrl+Enter)')

        self.btn_stop = self._w(bot, 'Button', text='■ 停止',
                                 command=self._stop_cmd,
                                 **({'bootstyle': 'danger-outline'} if HAS_BOOTSTRAP else {}),
                                 state='disabled')
        self.btn_stop.pack(side='left', padx=(0, 12))
        ToolTip(self.btn_stop, '停止当前任务 (Esc)')

        self.progress = self._w(bot, 'Progressbar',
                                 **({'bootstyle': 'info-striped'} if HAS_BOOTSTRAP else {}))
        self.progress.pack(side='left', fill='x', expand=True, padx=(0, 12))

        self.status_var = tk.StringVar(value='就绪')
        self._w(bot, 'Label', textvariable=self.status_var,
                 foreground=C_DIM, font=(FONT, 11)).pack(side='left')

    # ---------- 日志区 ----------
    def _build_log_section(self, parent):
        tb = self._w(parent, 'Frame')
        tb.pack(fill='x', padx=8, pady=(8, 4))

        btn_clear = self._w(tb, 'Button', text='清空',
                             command=self._clear_log,
                             **({'bootstyle': 'secondary-link'} if HAS_BOOTSTRAP else {}))
        btn_clear.pack(side='left', padx=(0, 4))

        btn_export = self._w(tb, 'Button', text='导出',
                              command=self._export_log,
                              **({'bootstyle': 'secondary-link'} if HAS_BOOTSTRAP else {}))
        btn_export.pack(side='left', padx=(0, 12))

        # 快捷键提示
        self._w(tb, 'Label', text='Ctrl+Enter 运行 · Esc 停止 · Ctrl+O 添加目录',
                 foreground=C_DIM, font=(FONT, 9)).pack(side='right')

        self.log_text = scrolledtext.ScrolledText(
            parent, wrap='word', font=(MONO, 10),
            bg='#182030', fg='#e0e0e0',
            borderwidth=0, highlightthickness=1,
            highlightbackground='#253040',
            insertbackground=C_FG, state='disabled',
            cursor='arrow', padx=8, pady=6)
        self.log_text.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        self.log_text.tag_configure('hit', foreground=C_GREEN)
        self.log_text.tag_configure('dim', foreground=C_DIM)
        self.log_text.tag_configure('error', foreground=C_RED)
        self.log_text.tag_configure('warn', foreground=C_YELLOW)

    # ==================== 交互方法 ====================
    def _show_welcome(self):
        t = self.stats_text
        t.configure(state='normal')
        t.delete('1.0', 'end')
        t.insert('end', '欢迎使用\n\n', 'h')
        t.insert('end', '配置好目录和参数后\n', 'd')
        t.insert('end', '点击「开始执行」或按 Ctrl+Enter\n\n', 'd')
        t.insert('end', '快捷键\n', 'h')
        t.insert('end', '  Ctrl+Enter  开始执行\n', 'd')
        t.insert('end', '  Ctrl+O      添加目录\n', 'd')
        t.insert('end', '  Delete      删除目录\n', 'd')
        t.insert('end', '  Esc         停止任务\n', 'd')
        t.insert('end', '  双击目录    编辑路径\n', 'd')
        t.insert('end', '\n提示\n', 'h')
        t.insert('end', '  • 试运行只预览不写入\n', 'd')
        t.insert('end', '  • 参数标签悬停可看说明\n', 'd')
        t.configure(state='disabled')

    def _add_dir(self):
        d = filedialog.askdirectory(title='选择漫画目录')
        if d and d not in self.dir_listbox.get(0, 'end'):
            self.dir_listbox.insert('end', d)
            self.dir_listbox.see('end')
            self.log(f'📂 已添加: {d}')

    def _del_dir(self):
        sel = self.dir_listbox.curselection()
        if sel:
            removed = self.dir_listbox.get(sel[0])
            self.dir_listbox.delete(sel[0])
            self.log(f'📂 已移除: {removed}')

    def _edit_dir(self, event=None):
        """双击编辑目录路径"""
        sel = self.dir_listbox.curselection()
        if not sel: return
        idx = sel[0]
        old_path = self.dir_listbox.get(idx)

        # 弹出编辑对话框
        dialog = tk.Toplevel(self.root)
        dialog.title('编辑目录')
        dialog.geometry('500x80')
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        if HAS_BOOTSTRAP:
            dialog.configure(bg=C_BG)

        frame = self._w(dialog, 'Frame', padding=12)
        frame.pack(fill='both', expand=True)

        var = tk.StringVar(value=old_path)
        entry = self._w(frame, 'Entry', textvariable=var, font=(MONO, 10))
        entry.pack(side='left', fill='x', expand=True)
        entry.select_range(0, 'end')
        entry.focus_set()

        def confirm():
            new_path = var.get().strip()
            if new_path and new_path != old_path:
                self.dir_listbox.delete(idx)
                self.dir_listbox.insert(idx, new_path)
                self.log(f'📂 已修改: {old_path} → {new_path}')
            dialog.destroy()

        confirm_kw = {'bootstyle': 'info'} if HAS_BOOTSTRAP else {'style': 'Accent.TButton'}
        btn = self._w(frame, 'Button', text='确定', command=confirm, **confirm_kw)
        btn.pack(side='left', padx=(6, 0))

        entry.bind('<Return>', lambda e: confirm())
        entry.bind('<Escape>', lambda e: dialog.destroy())

        # 居中
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f'+{x}+{y}')

    def _browse_meta(self):
        f = filedialog.asksaveasfilename(title='选择 Meta 文件',
                                          defaultextension='.json',
                                          filetypes=[('JSON', '*.json')],
                                          initialfile='comics_meta.json')
        if f: self.meta_var.set(f)

    def _toggle_all(self, state):
        for var in self.rule_vars.values():
            var.set(state)

    # ========== 日志 ==========
    def log(self, msg, tag=None):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', msg + '\n', tag or ())
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def _on_hit(self, tag, name, detail):
        s = f' ({detail})' if detail else ''
        self.log(f'  ✅ [{tag}] {name}{s}', 'hit')

    def _clear_log(self):
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')

    def _export_log(self):
        f = filedialog.asksaveasfilename(title='导出日志', defaultextension='.txt',
                                          filetypes=[('Text', '*.txt')],
                                          initialfile='tag_log.txt')
        if f:
            with open(f, 'w', encoding='utf-8') as fh:
                fh.write(self.log_text.get('1.0', 'end'))
            self.log(f'📁 已导出: {f}')

    # ========== 统计 ==========
    def _update_stats(self, scanned, updated, skipped, new_artists, errors, elapsed):
        t = self.stats_text
        t.configure(state='normal')
        t.delete('1.0', 'end')
        t.insert('end', '扫描结果\n', 'h')
        t.insert('end', f'  扫描总数  ', 'd'); t.insert('end', f'{scanned}\n', 'v')
        t.insert('end', f'  更新数量  ', 'd'); t.insert('end', f'{updated}\n', 'v')
        t.insert('end', f'  耗时      ', 'd'); t.insert('end', f'{elapsed:.1f}s\n', 'v')
        if self.dry_run_var.get():
            t.insert('end', '\n  ⚠ 试运行，未写入\n', 'w')
        t.insert('end', '\n跳过（已有 tag）\n', 'h')
        for rule, count in skipped.items():
            t.insert('end', f'  {rule}  ', 'd'); t.insert('end', f'{count}\n', 'v')
        if new_artists:
            t.insert('end', '\n新增艺术家\n', 'h')
            for a in new_artists:
                t.insert('end', f'  🎨 {a}\n', 'v')
        if errors:
            t.insert('end', f'\n错误 ({len(errors)})\n', 'h')
            for e in errors[:10]:
                t.insert('end', f'  ❌ {e}\n', 'e')
            if len(errors) > 10:
                t.insert('end', f'  ... 还有 {len(errors)-10} 个\n', 'd')
        t.configure(state='disabled')

    # ========== 执行 ==========
    def _run(self):
        if self.running: return
        if not HAS_CV2:
            messagebox.showerror('缺少依赖',
                                  '需要安装 opencv-python 和 numpy:\n'
                                  'pip install opencv-python numpy')
            return
        dirs = list(self.dir_listbox.get(0, 'end'))
        if not dirs:
            messagebox.showwarning('提示', '请先添加漫画目录'); return
        rules = [n for n, v in self.rule_vars.items() if v.get()]
        if not rules:
            messagebox.showwarning('提示', '请至少选择一个规则'); return
        if '全彩' in rules:
            cs, cm = self.param_vars['color_sample'].get(), self.param_vars['color_min'].get()
            if cm > cs:
                messagebox.showwarning('参数错误', f'达标数({cm})不能大于抽样数({cs})'); return

        dry = self.dry_run_var.get()
        self.running = True
        self.start_time = time.time()
        self.btn_run.configure(state='disabled')
        self.btn_stop.configure(state='normal')
        self._clear_log()
        self.log(f'{"🔍 试运行" if dry else "🚀 正式执行"}: {", ".join(rules)}')
        self.log(f'📂 目录: {len(dirs)} 个')

        def worker():
            try:
                self.engine = ScanEngine(
                    dirs=dirs, meta_path=self.meta_var.get(),
                    color_sample=self.param_vars['color_sample'].get(),
                    color_min=self.param_vars['color_min'].get(),
                    color_threshold=self.param_vars['color_threshold'].get(),
                    author_min=self.param_vars['author_min'].get(),
                    long_pages=self.param_vars['long_pages'].get(),
                    dry_run=dry)

                self.root.after(0, lambda: self.status_var.set('扫描目录...'))
                scanned = self.engine.scan_dirs()
                self.root.after(0, lambda: self.log(f'📚 扫描到 {scanned} 本漫画'))
                va = self.engine.valid_authors
                self.root.after(0, lambda: self.log(
                    f'👤 {len(self.engine.author_map)} 个作者, '
                    f'{len(va)} 个 ≥{self.param_vars["author_min"].get()} 本'))
                for a, keys in sorted(va.items(), key=lambda x: -len(x[1])):
                    self.root.after(0, lambda _a=a, _k=keys:
                                    self.log(f'  👤 {_a} ({len(_k)}本)', 'dim'))

                self.root.after(0, lambda: self.status_var.set('执行规则...'))
                self.root.after(0, lambda: self.progress.configure(maximum=scanned, value=0))

                last_ui = [0.0]
                def on_progress(done):
                    now = time.time()
                    if now - last_ui[0] < 0.2 and done != scanned: return
                    last_ui[0] = now
                    pct = int(done / scanned * 100) if scanned else 0
                    elapsed = now - self.start_time
                    eta = (elapsed / done * (scanned - done)) if done > 0 else 0
                    self.root.after(0, lambda d=done, p=pct, e=eta: (
                        self.progress.configure(value=d),
                        self.status_var.set(f'{d}/{scanned} ({p}%) · 剩余 {e:.0f}s'),
                        self.root.title(f'漫画标签 — {p}%')))

                def on_hit(tag, name, detail):
                    self.root.after(0, lambda: self._on_hit(tag, name, detail))

                updated, skipped, new_artists = self.engine.run_rules(
                    rules, progress_cb=on_progress, hit_cb=on_hit)

                elapsed = time.time() - self.start_time
                self.root.after(0, lambda: self._finish(
                    scanned, updated, skipped, new_artists,
                    self.engine.stats['errors'], elapsed))

            except Exception as e:
                self.root.after(0, lambda: self.log(f'❌ 错误: {e}', 'error'))
                self.root.after(0, lambda: self.status_var.set('出错'))
            finally:
                self.root.after(0, self._unlock)

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, scanned, updated, skipped, new_artists, errors, elapsed):
        self.log(f'\n{"═" * 48}')
        if self.dry_run_var.get():
            self.log(f'🔍 试运行完成: {updated} 本会被更新（未写入）', 'warn')
        else:
            self.log(f'📊 完成: 更新 {updated} 本', 'hit')
        self.log(f'⏱  {elapsed:.1f}s')
        self.status_var.set(f'完成 · 更新 {updated} 本 · {elapsed:.1f}s')
        self.root.title('漫画标签工具 — 完成')
        self._update_stats(scanned, updated, skipped, new_artists, errors, elapsed)

    def _stop_cmd(self):
        if self.engine: self.engine.stop()
        self.log('⏹ 已停止', 'warn')
        self.status_var.set('已停止')

    def _unlock(self):
        self.running = False
        self.btn_run.configure(state='normal')
        self.btn_stop.configure(state='disabled')


if __name__ == '__main__':
    app = App()
    app.root.mainloop()
