"""作者名解析（GUI 标签工具与 Flask 下载同步共用）。

单独放一个模块是为了让 server.py 也能用同一套规则：GUI 那边的
comic_tagger_gui.py 会 import tkinter / cv2，Flask 端不该为了一个正则去背这些依赖。

这里只管「名字怎么认、怎么归一化」——不碰文件、不碰 meta、不依赖 GUI。
"""
import re
import unicodedata

# 平假名 / 片假名 / 汉字
JP_PATTERN = re.compile(r'[぀-ゟ゠-ヿ一-鿿]')
# 子串匹配的非作者块（制作工序 / 状态 / 语言 / 翻译者），繁简都覆盖。
# 这些词出现在目录名的 [ ] 块里，几乎必然是标注而不是作者名。
IGNORE_KEYWORDS = (
    '汉化', '漢化', '翻译', '翻譯', '中国', '中國', '修正', '訳', '译', '譯',
    '整合', '重嵌', '嵌字', '校对', '校對', '修图', '修圖', '掃圖', '扫图', '圖源', '图源',
    '連載', '连载', '進行', '进行', '完結', '完结',
)
# 精确匹配的非作者块（语言 / 画质 / 平台 / 合集 / 作品名）。这里用精确匹配而不是子串，
# 是为了别误伤真名——Chinese 是语言，Chihiro 是人名；Digital 是画质标记，
# Digital Lover 却是个真社团。子串匹配会把后者一起干掉。
NOISE_BLOCKS = {
    'chinese', 'japanese', 'english', 'korean', 'decensored', 'uncensored', 'censored',
    'digital', 'dl', 'dl版', 'raw', 'sample', 'anthology', 'color', 'full color', 'colorized',
    'patreon', 'pixiv', 'fanbox', 'fantia', 'twitter', 'booth', 'dlsite', 'melonbooks',
    # 作品名（同人本目录里常见的作品标注，作者另有其人；按需补充）
    '原神',
}
_NOISE_RE = re.compile(r'^(?:[Cc]\d{2,3}|COMIC\s.*|コミック.*)$')
_ID_LIKE_RE = re.compile(r'^[A-Z0-9][A-Z0-9\-_]{7,}$')   # MJK-22-Z2820 这类编号
# 话数 / 章节标记：第1话、第二話、第10章 这类，不是作者
_EPISODE_RE = re.compile(r'^第[一二三四五六七八九十百千万\d０-９]+[话話章回]$')


def _is_noise_block(block):
    s = block.strip()
    if not s:
        return True
    if any(kw in s for kw in IGNORE_KEYWORDS):
        return True
    if s.lower() in NOISE_BLOCKS:
        return True
    return bool(_NOISE_RE.match(s) or _ID_LIKE_RE.match(s) or _EPISODE_RE.match(s))


def extract_author(name):
    """从目录名的 [ ] 块里取作者。

    含日文的块优先（最稳，也跟老逻辑一致，不动已打好的标签）；
    一个都没有的话，退回第一个纯英文/罗马字的块——[MANA]、[flanvia]、[kataokasan] 这类
    以前会被漏掉，现在也能认出来。
    """
    blocks = [b.strip() for b in re.findall(r'\[([^\]]+)\]', name) if not _is_noise_block(b)]
    for b in blocks:
        if JP_PATTERN.search(b):
            return b
    return blocks[0] if blocks else None

# 「社团 (作者)」形式的标签，取括号里的作者名
INNER_AUTHOR_RE = re.compile(r'\(\s*([^()]+?)\s*\)\s*$')
# 比较用的归一化：忽略空格（半角/全角）和大小写。
# 同一批本子里 [GlowMercury(すこやかグミ)] 和 [GlowMercury (すこやかグミ)] 都出现过。
_SPACE_RE = re.compile(r'[\s　]+')

# 简繁 / 和制汉字差异：同一个社团被写成 [満天宝岛] / [満天宝島] / [满天宝岛]，
# 库里就裂成三个标签。折叠成同一个代表字再比较，就能认出是一个。
# 只收确定不会误伤的对（旧字体/繁体 → 简体），拿不准的一律不加——
# 加错会把两个不同社团并成一个，比漏并难查得多。
_VARIANT_PAIRS = {
    '満': '满', '島': '岛', '気': '气', '輪': '轮', '飯': '饭', '場': '场',
    '裏': '里', '納': '纳', '庫': '库', '巣': '巢', '楽': '乐', '學': '学',
    '國': '国', '團': '团', '圖': '图', '點': '点', '實': '实', '圓': '圆',
    '樓': '楼', '愛': '爱', '戀': '恋', '慶': '庆', '緣': '缘', '縁': '缘',
    '風': '风', '總': '总', '総': '总', '燈': '灯', '裝': '装', '寶': '宝',
    '潔': '洁', '頭': '头', '額': '额', '館': '馆', '雙': '双', '對': '对',
    '際': '际', '與': '与', '臺': '台', '灣': '湾', '無': '无', '種': '种',
    '樣': '样', '醫': '医', '藥': '药', '續': '续', '變': '变', '邊': '边',
    '戶': '户', '緑': '绿', '訳': '译', '譯': '译', '龍': '龙', '聲': '声',
    '職': '职', '銀': '银', '鐵': '铁', '號': '号', '夢': '梦', '話': '话',
    '語': '语', '體': '体', '髪': '发', '髮': '发', '顏': '颜', '顔': '颜',
    '廣': '广', '廠': '厂', '讓': '让', '認': '认', '識': '识', '護': '护',
    '歡': '欢', '觀': '观', '權': '权', '驗': '验', '願': '愿', '嚴': '严',
    '產': '产', '畫': '画', '會': '会', '戰': '战', '獨': '独', '紺': '绀',
}
# 折叠表本身，以及「旧写法」的那一侧（挑正式名时优先保留原样写法用得上）
VARIANT_CHARS = str.maketrans(_VARIANT_PAIRS)
VARIANT_ORIGINAL = set(_VARIANT_PAIRS)


def author_key(s):
    """归一化后的比较键。

    折掉的差异：全角/半角（NFKC）、空格（含全角）、大小写、简繁/和制汉字。
    都是同一个社团的写法抖动，不是不同的人。
    """
    if not s:
        return ''
    s = unicodedata.normalize('NFKC', s)
    return _SPACE_RE.sub('', s).lower().translate(VARIANT_CHARS)


def circle_part(tag):
    """「社团 (作者)」→ 社团名；没有括号（裸作者名）就返回标签本身。"""
    m = INNER_AUTHOR_RE.search(tag or '')
    return (tag[:m.start()] if m else (tag or '')).strip()


def inner_author(tag):
    """「社团 (作者)」→ 作者名；不是这种形式就返回 None。"""
    m = INNER_AUTHOR_RE.search(tag or '')
    return m.group(1) if m else None


def resolve_author(author, artists):
    """把新认到的作者名并到库里已有的标签上。

    [flanvia] 和 [自家発電処 (flanvia)] 是同一个人，库里已经有后者时就不再新造一个 flanvia。
    [MANA] 同理并到 けんじゃたいむ (MANA)。
    [とっとこSたろう(MだSたろう)] 和 [とっとこSたろう (MだSたろう)] 只差个空格，也该当同一个。

    比较一律用 author_key 归一化（全半角、空格、大小写、简繁）。

    顺序不能反：先比整名，再退到「括号里的作者名」。
    整名比较要排除自身——扫描时候选集里本来就带着这个裸名自己，不排除的话
    [flanvia] 会立刻自匹配返回，永远并不进 自家発電処 (flanvia) 去。
    「括号里的作者名」这一步则在候选唯一时才并：同一个笔名被十几个社团共用
    （よろず）时到底指哪个社团说不清，保持原样不瞎猜。

    注意这里只负责「新名字 → 已有标签」。两个社团形式之间的写法差异
    （[蜂巢 (Apoidea)] / [蜂巣 (Apoidea)]）得靠 circle_part + author_key 先把
    艺术家列表归一化掉。
    """
    if not author:
        return author
    k = author_key(author)
    same = sorted(a for a in artists if a != author and author_key(a) == k)
    if same:
        return same[0]           # 同一个名字的不同写法，并到先有的那个
    hits = sorted(a for a in artists if author_key(inner_author(a)) == k)
    if hits:
        return hits[0] if len(hits) == 1 else author   # 多个说明有歧义，不猜
    return author

