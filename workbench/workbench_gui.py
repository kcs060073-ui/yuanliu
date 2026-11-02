# -*- coding: utf-8 -*-
"""源流园区视频工作台 v3.5（tkinter，零第三方依赖）

v3 美化升级（2026-09-07）：渐变顶部条、浅雾蓝调色、胶囊识别 chip、绿点状态徽章、
按钮分级 + 悬停反馈。

v3.5 精致化一轮（2026-09-07，用户要求"更像工具台"）：
- 顶栏圆形「源」品牌徽标 + 单行标题
- 实时脚本分析统计卡片（字幕条数 / 预估时长·秒 / 总字数 / 素材库就绪），
  随脚本输入实时刷新，数字来自 subtitle_split 真实切分，非估算
- 底部状态栏（就绪 / 生成中 / 批量生成中 + 产品署名）
- 统计卡内素材库状态用颜色徽章（绿=已就绪 / 灰=未设置）

功能与 v2 完全一致（全自动识别风格园区/手动锁定/一键恢复自动/事件队列线程安全/
生成后自检）。界面只拼命令行调 make_park_video.py，不重复实现生成逻辑。
"""
import os
import sys
import json
import re
import queue
import threading
import subprocess
import tempfile
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(SKILL_ROOT, 'scripts')
CONFIG_DIR = os.path.join(SKILL_ROOT, 'config')
MAKE_PY = os.path.join(SCRIPTS_DIR, 'make_park_video.py')
VERIFY_PY = os.path.join(SKILL_ROOT, 'tests', 'verify_draft.py')
STORYBOARD_GUI = os.path.join(SKILL_ROOT, 'workbench', 'storyboard_gui.py')
TASK_CENTER_GUI = os.path.join(SKILL_ROOT, 'workbench', 'task_center.py')
SETTINGS_PATH = os.path.join(CONFIG_DIR, 'gui_settings.json')
STYLE_PATH = os.path.join(CONFIG_DIR, 'default_styles.json')
DEFAULT_OUT = r'D:\JianyingPro Drafts'
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
from runtime_context import new_job_id
import script_split          # P05 §4：脚本切分唯一实现
import draft_naming          # P05 §1：草稿命名/独占预留唯一实现
import material_matcher      # P05 §3：素材库校验唯一实现

# ---------------------------------------------------------------- v3 调色（轻雾蓝 × 藏青）
C_BG = '#EEF3FA'          # 页面底板（浅雾蓝）
C_CARD = '#FFFFFF'        # 卡片纯白
C_LINE = '#E3EAF3'        # 卡片描边
C_LINE2 = '#D5E0EE'
C_TITLE = '#16283E'       # 深藏青标题
C_TEXT = '#24334A'
C_SUB = '#64748B'         # 板岩灰辅助
C_FADED = '#94A3B8'
C_ACCENT = '#2E7CF6'      # 主蓝
C_ACCENT_DK = '#1F62D0'   # 主蓝 hover
C_TINT = '#EAF2FE'        # 浅蓝底
C_TINT_TEXT = '#2A66C9'   # 浅蓝底文字
C_TINT_DK = '#D8E8FF'     # 浅蓝底 hover
C_OK = '#7AD06A'
C_LOGBG = '#101B2D'       # 日志深藏青黑
C_LOGTX = '#CFE8FF'

F = 'Microsoft YaHei UI'


class UiBlocked(Exception):
    """已经在界面上拦停并提示过，调用方只需静默返回（P05 §3）。"""

# ---------------------------------------------------------------- 自动识别知识库
CITY_KWS = ['嘉定', '苏州', '南京', '贵阳', '嘉兴', '德州', '新仓', '淮南', '合肥']
# 7 式命名（实拍园区）：YYMMDD-园区-横/竖-剪辑者；其余走 0 式
SEVEN_STYLE_PARKS = {'新疆园区', '北京园区'}
FILM_PARKS = {'乌鲁木齐': '两河园区', '新疆': '两河园区', '两河园区': '两河园区',
              '两河': '两河园区', '北京': '北京园区'}
PARK_MATERIAL_HINT = {'两河园区': r'E:\园区素材\新疆园区\两河园区'}

BUCKET_CN = {
    'entrance': '门口/大门', 'aerial': '航拍全景', 'railway': '高铁',
    'metro': '地铁', 'highway': '高速', 'traffic': '交通',
    'inside': '厂房内部', 'factory': '厂房外部', 'dormitory': '宿舍',
    'canteen': '食堂', 'office': '办公', 'environment': '配套/环境',
}


# ---------------------------------------------------------------- 工具函数
def _python():
    exe = sys.executable or 'python'
    if os.path.basename(exe).lower().startswith('pythonw'):
        exe = exe[:-4] + '.exe' if exe.lower().endswith('.exe') else exe
        if not os.path.isfile(exe):
            exe = exe[:-4]
    return exe


def load_styles():
    try:
        with open(os.path.join(CONFIG_DIR, 'default_styles.json'),
                  encoding='utf-8') as f:
            styles = json.load(f)['styles']
        return [(k, styles[k].get('name', '风格%s' % k))
                for k in ('1', '2', '3') if k in styles]
    except Exception:
        return [('1', '三连否'), ('2', '招商'), ('3', '没想到')]


def detect_jianying():
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from make_park_video import find_install_dir
        return find_install_dir('')
    except Exception:
        return None


def lib_version(materials):
    try:
        for cand in (materials, os.path.dirname(materials.rstrip('/\\'))):
            for name, key in (('materials_manifest.json', 'library_version'),
                              ('_manifest.json', 'version')):
                mf = os.path.join(cand, name)
                if os.path.isfile(mf):
                    v = json.load(open(mf, encoding='utf-8')).get(key)
                    if v:
                        return str(v)
    except Exception:
        pass
    return '-'


# ---------------------------------------------------------------- 自动识别
def guess_style(text):
    """风格推断：真没想到→3；设问/排比→1；直入→2。"""
    t = re.sub(r'\s+', '', text or '')
    head = t[:80]
    if '真没想到' in head or t.startswith('没想到'):
        return '3', '开场钩子“真没想到”'
    if '？' in head or '?' in head or '有没有' in head or '吗' in head[:60]:
        return '1', '前段设问钩子（？/有没有/…吗）'
    if t[:200].count('不是') >= 2 or '也不' in t[:120]:
        return '1', '排比连击（不是/也不）'
    return '2', '直入式开场（默认招商）'


def guess_park(text):
    """识别脚本属于哪个园区。

    2026-09-14 起**优先走与选片同一套规则**（park_rules.match_script_parks，长词优先），
    保证"界面显示的园区"就是"实际选片的园区"。旧的窗口关键词匹配只作兜底
    （用于素材库里还没有目录的项目，如 淮南/合肥）。
    """
    t = re.sub(r'\s+', '', text or '')
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'scripts'))
        import park_rules as _pr
        hits = _pr.match_script_parks(t, _pr.load().get('parks', {}))
        if hits:
            folder, kw = hits[0][0], hits[0][1]
            disp = (_pr.load()['parks'].get(folder) or {}).get('display') or folder
            mode = '7' if folder in SEVEN_STYLE_PARKS else '0'
            label = '实拍园区' if mode == '7' else '城市园区'
            return disp, mode, '%s“%s”（脚本命中“%s”）' % (label, disp, kw)
    except Exception:
        pass

    # 兜底：素材库没有对应目录的项目
    head = t[:200]
    m = re.search(r'([\u4e00-\u9fa5]{2,14}?)(?:产业园|工业园区|开发区)', head)
    window = m.group(1) if m else t[:60]
    for kw in sorted(CITY_KWS + list(FILM_PARKS), key=len, reverse=True):
        if kw in window:
            if kw in FILM_PARKS:
                return FILM_PARKS[kw], '7', '实拍园区“%s”' % kw
            return kw, '0', '城市园区“%s”' % kw
    return '园区', '0', '未识别到已知园区（可在下方手动改）'


def detect_script(text):
    style_key, style_note = guess_style(text)
    seg, mode, park_note = guess_park(text)
    return {'seg': seg, 'mode': mode, 'style_key': style_key,
            'park_note': park_note, 'style_note': style_note}


def split_multi_scripts(text):
    """把文本框内容切成多条脚本（P05 §4）。

    **P05 起只剩一个分隔符：单独一行的 ``---``（>=3 个短横线）。**
    普通空行不再切分，会原样保留在同一条脚本里当段落 —— 老规则
    「没有 --- 就用空行切」会把一条正常的带空行脚本劈成两条半截脚本。
    唯一实现已移到 ``scripts/script_split.py``，本函数保留为薄封装，
    避免 GUI 内出现第二份切分逻辑（§1 同源要求）。
    """
    return script_split.split_scripts(text)


# ---------------------------------------------------------------- 界面
class Workbench(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('源流')
        self.geometry('900x920')
        self.minsize(820, 740)
        self.configure(bg=C_BG)

        self.styles = load_styles()
        self.cfg = self._load_settings()
        self.generating = False
        self.log_q = queue.Queue()
        self.auto = {'seg': True, 'style': True, 'mode': True}
        self._detect_timer = None
        self._multi_count = 1
        self._batch_rows = []
        self._batch_win = None
        # P05 §2：窗口关闭后，后台线程可能还在回传事件；所有回到主线程的
        # 动作都要先看这个标志，避免操作已销毁的控件（TclError / 崩在 Tcl 层）。
        self._closed = False
        self._last_draft_dir = ''

        self._build_vars()
        self._setup_ttk()
        self._build_ui()
        self._apply_settings()
        self._render_preview()
        self._env_probe()
        self.after(600, self._first_run_check)

        self.after(100, self._drain_log)
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ---------------- 视觉基础 ----------------
    def _setup_ttk(self):
        st = ttk.Style(self)
        try:
            st.theme_use('clam')
        except tk.TclError:
            pass
        base = {'font': (F, 10)}
        st.configure('.', **base, background=C_CARD)
        st.configure('TCombobox', fieldbackground='#FFFFFF', background='#FFFFFF',
                     bordercolor=C_LINE2, lightcolor=C_LINE2, darkcolor=C_LINE2,
                     arrowsize=15, padding=3)
        st.map('TCombobox', fieldbackground=[('readonly', '#FFFFFF')],
               foreground=[('readonly', C_TEXT)],
               bordercolor=[('focus', C_ACCENT)])
        st.configure('TEntry', fieldbackground='#FFFFFF', bordercolor=C_LINE2,
                     lightcolor=C_LINE2, darkcolor=C_LINE2, padding=4)
        st.map('TEntry', bordercolor=[('focus', C_ACCENT)])
        st.configure('Treeview', background='#FFFFFF', fieldbackground='#FFFFFF',
                     foreground=C_TEXT, rowheight=27, bordercolor=C_LINE,
                     font=(F, 9))
        st.configure('Treeview.Heading', font=(F, 9, 'bold'), background='#F1F5FB',
                     foreground=C_TITLE, relief='flat')
        st.map('Treeview', background=[('selected', C_TINT)])

    def _gradient_bar(self, parent, height=3, left='#2E7CF6', right='#BFD9FF'):
        cv = tk.Canvas(parent, height=height, bg=C_CARD, bd=0, highlightthickness=0)
        cv.pack(fill='x')

        def _draw(e):
            cv.delete('all')
            w = e.width
            if w < 2:
                return
            steps = max(24, w // 8)
            for i in range(steps):
                t = i / (steps - 1)
                c = '#%02x%02x%02x' % (
                    int(int(left[1:3], 16) + (int(right[1:3], 16) - int(left[1:3], 16)) * t),
                    int(int(left[3:5], 16) + (int(right[3:5], 16) - int(left[3:5], 16)) * t),
                    int(int(left[5:7], 16) + (int(right[5:7], 16) - int(left[5:7], 16)) * t))
                x0 = w * i / steps
                cv.create_rectangle(int(x0), 0, int(x0 + w / steps) + 1, height,
                                    fill=c, outline=c)
        cv.bind('<Configure>', _draw)
        return cv

    def _btn(self, parent, text, kind='ghost', cmd=None, font=None, padx=14, pady=5):
        spec = {'primary': (C_ACCENT, '#FFFFFF', C_ACCENT_DK),
                'soft': (C_TINT, C_TINT_TEXT, C_TINT_DK),
                'ghost': ('#FFFFFF', C_TEXT, '#EAF1FB'),
                'muted': ('#EEF2F8', C_TEXT, '#E2E9F3')}
        bg, fg, hv = spec[kind]
        b = tk.Button(parent, text=text, relief='flat', bd=0, bg=bg, fg=fg,
                      activebackground=hv, activeforeground=fg, cursor='hand2',
                      font=font or (F, 10), command=cmd, padx=padx, pady=pady,
                      highlightthickness=0)
        b.bind('<Enter>', lambda e, b=b, hv=hv: b.config(bg=hv))
        b.bind('<Leave>', lambda e, b=b, bg=bg: b.config(bg=bg))
        return b

    def _dot(self, parent, color, size=7, side='left'):
        cv = tk.Canvas(parent, width=size + 4, height=size + 4, bg=parent['bg'],
                       bd=0, highlightthickness=0)
        cv.create_oval(2, 2, size + 2, size + 2, fill=color, outline='')
        cv.pack(side=side, padx=(0, 4))
        return cv

    def _card(self, parent):
        return tk.Frame(parent, bg=C_CARD, highlightbackground=C_LINE,
                        highlightthickness=1, bd=0)

    def _sec_head(self, parent, title, sub='', extra=None):
        f = tk.Frame(parent, bg=C_CARD)
        f.pack(fill='x', padx=18, pady=(14, 6))
        bar = tk.Frame(f, bg=C_ACCENT, width=3)
        bar.pack(side='left', fill='y', padx=(0, 8))
        tk.Label(f, text=title, bg=C_CARD, fg=C_TITLE,
                 font=(F, 12, 'bold')).pack(side='left')
        if sub:
            tk.Label(f, text=sub, bg=C_CARD, fg=C_FADED,
                     font=(F, 9)).pack(side='left', padx=(8, 0), pady=(2, 0))
        if extra:
            extra.pack(side='right')
        return f

    def _build_stats(self, parent):
        """实时脚本分析统计卡片：字幕条数 / 预估时长 / 总字数 / 素材库就绪。"""
        wrap = tk.Frame(parent, bg=C_CARD)
        wrap.pack(fill='x', padx=18, pady=(2, 12))
        tk.Label(wrap, text='脚本分析（实时）', bg=C_CARD, fg=C_SUB,
                 font=(F, 9)).pack(anchor='w', pady=(0, 5))
        grid = tk.Frame(wrap, bg=C_CARD)
        grid.pack(fill='x')
        specs = [('count', '字幕条数'), ('dur', '预估时长·秒'),
                 ('chars', '总字数'), ('mat', '素材库')]
        for i, (key, label) in enumerate(specs):
            cell = tk.Frame(grid, bg=C_CARD, highlightbackground=C_LINE,
                            highlightthickness=1)
            cell.grid(row=0, column=i, sticky='nsew', padx=(0 if i == 0 else 6, 0))
            num = tk.Label(cell, text='—', bg=C_CARD, fg=C_ACCENT,
                           font=(F, 19, 'bold'))
            num.pack(padx=8, pady=(9, 0))
            tk.Label(cell, text=label, bg=C_CARD, fg=C_SUB,
                     font=(F, 8)).pack(padx=8, pady=(0, 9))
            self.stat[key] = num
        for c in range(4):
            grid.columnconfigure(c, weight=1)

    def _build_footer(self):
        f = tk.Frame(self, bg=C_TITLE, height=26)
        f.pack(fill='x', side='bottom')
        tk.Label(f, textvariable=self.v_status, bg=C_TITLE, fg='#CFE8FF',
                 font=(F, 9)).pack(side='left', padx=14)
        tk.Label(f, text='源流 · 园区短视频自动成稿', bg=C_TITLE, fg='#7E93AD',
                 font=(F, 9)).pack(side='right', padx=14)

    def _update_stats(self):
        """根据当前脚本 + 所选风格 + 素材库，实时刷新统计卡片。"""
        text = self._script_text().strip()
        if len(text) < 10:
            self.stat['count'].config(text='—', fg=C_FADED)
            self.stat['dur'].config(text='—', fg=C_FADED)
            self.stat['chars'].config(text='—', fg=C_FADED)
            self.stat['mat'].config(text='未设置', fg=C_FADED)
            return
        try:
            sys.path.insert(0, SCRIPTS_DIR)
            import subtitle_split as ss
            with open(STYLE_PATH, encoding='utf-8') as fp:
                styles = json.load(fp)['styles']
            sc = styles.get(self.v_style.get(), styles.get('1', {}))
            scripts = [text] if self._multi_count <= 1 else split_multi_scripts(text)
            n = dur = ch = 0
            for st in scripts:
                subs = ss.split_subtitles(st, sc, no_tts=True)
                n += len(subs)
                dur += ss.estimate_duration_ms(subs)
                ch += sum(s['chars'] for s in subs)
            self.stat['count'].config(text=str(n), fg=C_TITLE)
            self.stat['dur'].config(text='%.0f' % (dur / 1000), fg=C_TITLE)
            self.stat['chars'].config(text=str(ch), fg=C_TITLE)
            m = self.v_materials.get().strip()
            if m and os.path.isdir(m):
                self.stat['mat'].config(text='已就绪', fg='#16A34A')
            else:
                self.stat['mat'].config(text='未设置', fg=C_FADED)
        except Exception:
            pass

    # ---------------- 变量 ----------------
    def _build_vars(self):
        self.v_input_mode = tk.StringVar(value='paste')
        self.v_script_file = tk.StringVar()
        self.v_style = tk.StringVar(value=self.styles[0][0])
        self.v_naming = tk.StringVar(value='0')
        self.v_seg = tk.StringVar(value='')
        self.v_editor = tk.StringVar()
        self.v_materials = tk.StringVar()
        self.v_out = tk.StringVar(value=DEFAULT_OUT)
        self.v_orient = tk.StringVar(value='竖屏')
        self.v_sub_y = tk.StringVar()
        self.v_sub_size = tk.StringVar()
        self.v_bgm = tk.StringVar(value='random')
        self.v_copy = tk.BooleanVar(value=False)
        self.v_tts = tk.BooleanVar(value=False)
        self.v_adv = tk.BooleanVar(value=False)
        # P05 §3：默认关。空素材库时默认拦停，只有显式打开这个开关才放行
        # （给"先调字幕/BGM 节奏"的调试场景留口子）。
        self.v_allow_empty = tk.BooleanVar(value=False)
        self.v_jy = tk.StringVar(value='剪映探测中…')
        self.v_libv = tk.StringVar(value='素材库 -')
        self.v_park_chip = tk.StringVar(value='园区 —')
        self.v_naming_chip = tk.StringVar(value='命名 —')
        self.v_style_chip = tk.StringVar(value='风格 —')
        self.v_detect_note = tk.StringVar(value='')
        self.v_manual = tk.StringVar(value='')
        self.v_status = tk.StringVar(value='就绪')
        self._editor_state = 'disabled'
        self.stat = {}

    def _style_name(self, key=None):
        for k, n in self.styles:
            if k == (key or self.v_style.get()):
                return n
        return self.styles[0][1]

    # ---------------- 布局 ----------------
    def _build_ui(self):
        # 顶部：渐变条 + 白底头栏（产品名一行）
        self._gradient_bar(self)
        top = tk.Frame(self, bg=C_CARD)
        top.pack(fill='x', pady=(12, 4), padx=18)
        logo = tk.Canvas(top, width=34, height=34, bg=C_CARD, bd=0,
                         highlightthickness=0)
        logo.create_oval(2, 2, 32, 32, fill=C_ACCENT, outline='')
        logo.create_text(17, 17, text='源', fill='#FFFFFF', font=(F, 15, 'bold'))
        logo.pack(side='left', padx=(0, 10))
        tk.Label(top, text='信息流视频自动剪辑', bg=C_CARD, fg=C_TITLE,
                 font=(F, 20, 'bold')).pack(side='left', pady=(2, 0), anchor='w')
        self._btn(top, '任务中心', kind='soft', cmd=self._open_task_center,
                  padx=12, pady=4).pack(side='right', padx=6, pady=4)
        # 右上状态徽章
        env = tk.Frame(top, bg=C_TINT)
        env.pack(side='right', padx=6, pady=4)
        env_dot = tk.Frame(env, bg=C_TINT)
        env_dot.pack(side='left', padx=(8, 0), pady=4)
        self._dot(env_dot, C_OK, size=6)
        tk.Label(env, textvariable=self.v_jy, bg=C_TINT, fg=C_TINT_TEXT,
                 font=(F, 9)).pack(side='left', padx=(0, 8))
        lib = tk.Frame(top, bg=C_TINT)
        lib.pack(side='right', padx=6, pady=4)
        lib_dot = tk.Frame(lib, bg=C_TINT)
        lib_dot.pack(side='left', padx=(8, 0), pady=4)
        self._dot(lib_dot, C_ACCENT, size=6)
        tk.Label(lib, textvariable=self.v_libv, bg=C_TINT, fg=C_TINT_TEXT,
                 font=(F, 9)).pack(side='left', padx=(0, 8))

        body = tk.Frame(self, bg=C_BG)
        body.pack(fill='both', expand=True, padx=16, pady=10)

        # ---------- ① 脚本 ----------
        c1 = self._card(body)
        c1.pack(fill='x')
        self._sec_head(c1, '脚本内容', '自动识别风格与园区，无需手选')
        row = tk.Frame(c1, bg=C_CARD)
        row.pack(fill='x', padx=18, pady=(0, 6))
        tk.Radiobutton(row, text='粘贴脚本', value='paste', variable=self.v_input_mode,
                       command=self._switch_input, bg=C_CARD, fg=C_TEXT,
                       activebackground=C_CARD, selectcolor='#FFFFFF',
                       font=(F, 10)).pack(side='left')
        tk.Radiobutton(row, text='从脚本文件读取', value='file', variable=self.v_input_mode,
                       command=self._switch_input, bg=C_CARD, fg=C_TEXT,
                       activebackground=C_CARD, selectcolor='#FFFFFF',
                       font=(F, 10)).pack(side='left', padx=10)
        self.btn_file = self._btn(row, '选择脚本文件…', kind='soft',
                                  cmd=self._pick_script, padx=12)
        self.btn_file.pack(side='right')
        self.btn_file.config(state='disabled')
        self.txt_script = tk.Text(c1, height=5, font=(F, 10), bg='#FBFDFF', fg=C_TEXT,
                                  relief='solid', bd=1, highlightcolor=C_ACCENT,
                                  highlightbackground=C_LINE2, insertbackground=C_ACCENT,
                                  padx=8, pady=6, spacing1=1)
        self.txt_script.pack(fill='x', padx=18, pady=(0, 4))
        self.txt_script.bind('<KeyRelease>', self._on_script_edit)
        self.txt_script.bind('<<Paste>>', lambda e: self.after(120, self._auto_detect))
        self.lab_file = tk.Label(c1, text='', bg=C_CARD, fg=C_FADED, anchor='w',
                                 font=(F, 9))
        self.lab_file.pack(fill='x', padx=18)

        # 识别结果行：三个胶囊 chip
        chiprow = tk.Frame(c1, bg=C_CARD)
        chiprow.pack(fill='x', padx=18, pady=(6, 2))
        for var, label in ((self.v_park_chip, '园区'), (self.v_naming_chip, '命名'),
                           (self.v_style_chip, '风格')):
            chip = tk.Label(chiprow, textvariable=var, bg=C_TINT, fg=C_TINT_TEXT,
                            font=(F, 9, 'bold'), padx=10, pady=3)
            chip.pack(side='left', padx=(0, 8))
        self.lab_note = tk.Label(chiprow, textvariable=self.v_detect_note, bg=C_CARD,
                                 fg=C_FADED, font=(F, 9))
        self.lab_note.pack(side='left', padx=(4, 0))
        self._btn(chiprow, '恢复自动', kind='soft', cmd=self._restore_auto,
                  padx=10).pack(side='right')
        self._btn(chiprow, '手动调整', kind='ghost', cmd=self._toggle_manual,
                  padx=10).pack(side='right', padx=(0, 8))
        self.lab_man = tk.Label(chiprow, textvariable=self.v_manual, bg=C_CARD,
                                fg='#D97706', font=(F, 9))
        self.lab_man.pack(side='right', padx=(0, 10))

        # 草稿名实时预览
        self.lab_prev = tk.Label(c1, text='草稿名预览：—', bg='#FDF4E7', fg='#B45309',
                                 font=(F, 10, 'bold'), anchor='w', padx=14, pady=6)
        self.lab_prev.pack(fill='x', padx=18, pady=(4, 12))

        # 实时脚本分析统计卡片
        self._build_stats(c1)

        # 手动调整（折叠）
        self.manual = tk.Frame(c1, bg=C_CARD)
        mf = tk.Frame(self.manual, bg='#F7FAFF', highlightbackground=C_LINE,
                      highlightthickness=1, bd=0)
        mf.pack(fill='x', padx=18, pady=(0, 12), ipady=6)
        tk.Label(mf, text='风格', bg='#F7FAFF', fg=C_SUB, font=(F, 9)).grid(row=0, column=0, padx=(14, 4), pady=6)
        self.cb_style = ttk.Combobox(mf, values=[n for _, n in self.styles],
                                     state='readonly', width=8)
        self.cb_style.grid(row=0, column=1, padx=(0, 18))
        self.cb_style.bind('<<ComboboxSelected>>', self._on_manual_style)
        tk.Label(mf, text='命名', bg='#F7FAFF', fg=C_SUB, font=(F, 9)).grid(row=0, column=2, padx=(0, 4))
        self.rb_mode0 = tk.Radiobutton(mf, text='0式·城市', value='0', variable=self.v_naming,
                                       command=self._on_manual_mode, bg='#F7FAFF', fg=C_TEXT,
                                       activebackground='#F7FAFF', selectcolor='#FFFFFF', font=(F, 9))
        self.rb_mode0.grid(row=0, column=3)
        self.rb_mode7 = tk.Radiobutton(mf, text='7式·实拍', value='7', variable=self.v_naming,
                                       command=self._on_manual_mode, bg='#F7FAFF', fg=C_TEXT,
                                       activebackground='#F7FAFF', selectcolor='#FFFFFF', font=(F, 9))
        self.rb_mode7.grid(row=0, column=4)
        tk.Label(mf, text='园区段', bg='#F7FAFF', fg=C_SUB, font=(F, 9)).grid(row=0, column=5, padx=(16, 4))
        self.e_seg = tk.Entry(mf, textvariable=self.v_seg, width=12, relief='solid', bd=1,
                              font=(F, 10), bg='#FFFFFF', fg=C_TEXT, highlightcolor=C_ACCENT,
                              highlightbackground=C_LINE2)
        self.e_seg.grid(row=0, column=6)
        self.e_seg.bind('<KeyRelease>', lambda e: (self._mark_manual('seg'),
                                                   self._render_preview()))

        # ---------- ② 成片设置 ----------
        c2 = self._card(body)
        c2.pack(fill='x', pady=(10, 0))
        self._sec_head(c2, '成片设置', '方向决定素材横竖屏过滤；7 式实拍园区需剪辑者')
        f2 = tk.Frame(c2, bg=C_CARD)
        f2.pack(fill='x', padx=18, pady=(4, 14))
        for col, txt in (('成片方向', 0),):
            tk.Label(f2, text=txt, bg=C_CARD, fg=C_SUB, font=(F, 9)).grid(row=0, column=0, sticky='w')
        tk.Radiobutton(f2, text='竖屏', value='竖屏', variable=self.v_orient, bg=C_CARD,
                       fg=C_TEXT, activebackground=C_CARD, selectcolor='#FFFFFF',
                       font=(F, 10)).grid(row=0, column=1, padx=(4, 0))
        tk.Radiobutton(f2, text='横屏', value='横屏', variable=self.v_orient, bg=C_CARD,
                       fg=C_TEXT, activebackground=C_CARD, selectcolor='#FFFFFF',
                       font=(F, 10)).grid(row=0, column=2, padx=(10, 0))
        tk.Label(f2, text='剪辑者（7式必填）', bg=C_CARD, fg=C_SUB,
                 font=(F, 9)).grid(row=0, column=3, padx=(30, 6))
        self.e_editor = tk.Entry(f2, textvariable=self.v_editor, width=14, relief='solid',
                                 bd=1, font=(F, 10), bg='#FFFFFF', fg=C_TEXT,
                                 highlightcolor=C_ACCENT, highlightbackground=C_LINE2)
        self.e_editor.grid(row=0, column=4, ipady=1)
        self.v_editor.trace_add('write', lambda *a: self._render_preview())
        self.v_orient.trace_add('write', lambda *a: self._render_preview())
        tk.Label(f2, text='素材库与输出在下方填写', bg=C_CARD, fg=C_FADED,
                 font=(F, 9)).grid(row=0, column=5, padx=(24, 0))
        f2.columnconfigure(6, weight=1)

        # ---------- ③ 素材库 / 输出 ----------
        c3 = self._card(body)
        c3.pack(fill='x', pady=(10, 0))
        self._sec_head(c3, '素材库与输出', '素材库为空则生成无画面稿；识别实拍园区可自动带出路径')
        f3 = tk.Frame(c3, bg=C_CARD)
        f3.pack(fill='x', padx=18, pady=(4, 6))
        tk.Label(f3, text='素材库目录', bg=C_CARD, fg=C_SUB, font=(F, 9)).grid(row=0, column=0, sticky='w', pady=4)
        tk.Entry(f3, textvariable=self.v_materials, relief='solid', bd=1, font=(F, 10),
                 bg='#FBFDFF', fg=C_TEXT, highlightcolor=C_ACCENT,
                 highlightbackground=C_LINE2).grid(row=0, column=1, sticky='we', padx=(6, 8))
        self.btn_ins = self._btn(f3, '素材体检', kind='soft', cmd=self._inspect_materials, padx=12)
        self.btn_ins.grid(row=0, column=2, padx=(0, 6))
        self._btn(f3, '安装素材包', kind='soft', cmd=self._install_material_package,
                  padx=12).grid(row=0, column=3, padx=(0, 6))
        self._btn(f3, '浏览', kind='muted', cmd=lambda: self._browse('v_materials'),
                  padx=12).grid(row=0, column=4)
        tk.Label(f3, text='草稿输出', bg=C_CARD, fg=C_SUB, font=(F, 9)).grid(row=1, column=0, sticky='w', pady=4)
        tk.Entry(f3, textvariable=self.v_out, relief='solid', bd=1, font=(F, 10),
                 bg='#FBFDFF', fg=C_TEXT, highlightcolor=C_ACCENT,
                 highlightbackground=C_LINE2).grid(row=1, column=1, sticky='we', padx=(6, 8))
        self._btn(f3, '浏览', kind='muted', cmd=lambda: self._browse('v_out'),
                  padx=12).grid(row=1, column=4)
        f3.columnconfigure(1, weight=1)

        # 高级设置
        self.btn_adv = self._btn(body, '高级设置  ▸', kind='ghost', cmd=self._toggle_adv,
                                 padx=8, pady=2)
        self.btn_adv.pack(fill='x', padx=2, pady=(8, 0))
        self.adv = tk.Frame(body, bg=C_CARD, highlightbackground=C_LINE, highlightthickness=1)
        a = tk.Frame(self.adv, bg=C_CARD)
        a.pack(fill='x', padx=18, pady=8)
        tk.Label(a, text='字幕上移（0.06≈半条字幕高，负值下移）', bg=C_CARD, fg=C_SUB,
                 font=(F, 9)).grid(row=0, column=0, sticky='w', pady=3)
        tk.Entry(a, textvariable=self.v_sub_y, width=7, relief='solid', bd=1,
                 font=(F, 10)).grid(row=0, column=1, padx=(4, 16))
        tk.Label(a, text='字幕字号（整数）', bg=C_CARD, fg=C_SUB,
                 font=(F, 9)).grid(row=0, column=2)
        tk.Entry(a, textvariable=self.v_sub_size, width=5, relief='solid', bd=1,
                 font=(F, 10)).grid(row=0, column=3, padx=(4, 16))
        tk.Label(a, text='BGM', bg=C_CARD, fg=C_SUB, font=(F, 9)).grid(row=0, column=4)
        ttk.Combobox(a, textvariable=self.v_bgm, values=('random', 'fixed'),
                     state='readonly', width=7).grid(row=0, column=5, padx=(4, 16))
        tk.Checkbutton(a, text='复制素材到草稿（自包含）', variable=self.v_copy, bg=C_CARD,
                       fg=C_TEXT, activebackground=C_CARD, selectcolor='#FFFFFF',
                       font=(F, 9)).grid(row=0, column=6)
        tk.Checkbutton(a, text='生成占位配音轨', variable=self.v_tts, bg=C_CARD,
                       fg=C_TEXT, activebackground=C_CARD, selectcolor='#FFFFFF',
                       font=(F, 9)).grid(row=0, column=7, padx=(12, 0))
        # P05 §3：允许无画面调试稿（默认关，空素材库默认拦停）
        tk.Checkbutton(a, text='允许无画面调试稿', variable=self.v_allow_empty,
                       bg=C_CARD, fg='#B45309', activebackground=C_CARD,
                       selectcolor='#FFFFFF',
                       font=(F, 9)).grid(row=1, column=0, columnspan=3,
                                         sticky='w', pady=(6, 0))

        # ---------- 生成按钮 ----------
        action_row = tk.Frame(body, bg=C_BG)
        action_row.pack(fill='x', padx=2, pady=(10, 4))
        self.btn_plan = self._btn(action_row, '分镜预览与候选替换', kind='soft',
                                  cmd=self._open_storyboard,
                                  font=(F, 11, 'bold'), padx=18, pady=10)
        self.btn_plan.pack(side='left', fill='x', expand=True, padx=(0, 6))
        self.btn_go = self._btn(action_row, '直接生成草稿', kind='primary', cmd=self._on_generate,
                                font=(F, 14, 'bold'), padx=24, pady=10)
        self.btn_go.pack(side='left', fill='x', expand=True)

        # ---------- 完成提示选项（2026-09-15）----------
        nf = tk.Frame(body, bg=C_BG)
        nf.pack(fill='x', pady=(0, 8))
        self.v_notify = tk.BooleanVar(value=True)
        tk.Checkbutton(nf, text='完成后弹窗提醒', variable=self.v_notify,
                       bg=C_BG, fg=C_SUB, activebackground=C_BG,
                       font=(F, 9), selectcolor='#FFFFFF').pack(side='left')

        # ---------- 日志 ----------
        lh = tk.Frame(body, bg=C_BG)
        lh.pack(fill='x')
        tk.Label(lh, text='运行日志', bg=C_BG, fg=C_TITLE,
                 font=(F, 11, 'bold')).pack(side='left')
        self.txt_log = tk.Text(body, height=10, state='disabled', bg=C_LOGBG, fg=C_LOGTX,
                               font=('Consolas', 9), relief='flat', bd=0,
                               highlightbackground=C_LINE, highlightthickness=1,
                               padx=10, pady=6)
        self.txt_log.pack(fill='both', expand=True, pady=(4, 2))
        self.txt_log.tag_configure('ok', foreground=C_OK)
        self.txt_log.tag_configure('warn', foreground='#F2C14E')
        self.txt_log.tag_configure('err', foreground='#FF8A80')
        self.txt_log.tag_configure('dim', foreground='#7E93AD')
        # 完成横幅：靛蓝底 + 加粗，一眼能看到（2026-09-15）
        self.txt_log.tag_configure('banner', foreground='#0B1F3A', background='#8FD3FF',
                                   font=('Consolas', 11, 'bold'), spacing1=6, spacing3=6)

        self._build_footer()
        self._set_editor_state()

    # ---------------- 交互 ----------------
    def _switch_input(self):
        mode = self.v_input_mode.get()
        self.btn_file.config(state='normal' if mode == 'file' else 'disabled')
        self.lab_file.config(text=self.v_script_file.get() if mode == 'file' else '')
        if mode == 'file':
            p = self.v_script_file.get()
            if p and os.path.isfile(p):
                self._load_file_into_box(p)

    def _pick_script(self):
        p = filedialog.askopenfilename(title='选择脚本文本', filetypes=[('文本', '*.txt'), ('所有', '*.*')])
        if p:
            self.v_script_file.set(p)
            self.lab_file.config(text=p)
            self._load_file_into_box(p)

    def _load_file_into_box(self, path):
        try:
            with open(path, 'rb') as f:
                raw = f.read()
            for enc in ('utf-8-sig', 'utf-8', 'gbk'):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = raw.decode('gbk', errors='replace')
            self.txt_script.delete('1.0', 'end')
            self.txt_script.insert('1.0', text)
            self._auto_detect()
        except OSError as e:
            messagebox.showwarning('读取失败', str(e))

    def _on_script_edit(self, _e=None):
        if self._detect_timer:
            self.after_cancel(self._detect_timer)
        self._detect_timer = self.after(500, self._auto_detect)

    def _on_manual_style(self, _e=None):
        idx = self.cb_style.current()
        if 0 <= idx < len(self.styles):
            self.v_style.set(self.styles[idx][0])
        self._mark_manual('style')
        self._update_stats()
        self._render_preview()

    def _on_manual_mode(self):
        self._mark_manual('mode')
        self._set_editor_state()
        self._render_preview()

    def _mark_manual(self, key):
        self.auto[key] = False
        self._render_preview()

    def _restore_auto(self):
        for k in self.auto:
            self.auto[k] = True
        self._auto_detect()

    def _toggle_manual(self):
        if self.manual.winfo_ismapped():
            self.manual.pack_forget()
        else:
            self.manual.pack(fill='x')
            for i, (k, _n) in enumerate(self.styles):
                if k == self.v_style.get():
                    self.cb_style.current(i)
        if self._detect_timer:
            self.after_cancel(self._detect_timer)

    # ---------------- 自动识别 ----------------
    def _script_text(self):
        if self.v_input_mode.get() == 'file':
            p = self.v_script_file.get()
            if os.path.isfile(p):
                try:
                    return open(p, encoding='utf-8', errors='replace').read()
                except OSError:
                    return ''
            return ''
        return self.txt_script.get('1.0', 'end')

    def _auto_detect(self):
        text = self._script_text().strip()
        if len(text) < 10:
            self.v_park_chip.set('园区 —')
            self.v_naming_chip.set('命名 —')
            self.v_style_chip.set('风格 —')
            self.v_detect_note.set('粘贴脚本后自动识别')
            self._multi_count = 1
            self._update_stats()
            self._update_go_button()
            return
        # 多脚本：>1 条时主按钮进入批量流程（逐条确认方向）
        self._multi_count = len(split_multi_scripts(text))
        if self._multi_count > 1:
            first = detect_script(split_multi_scripts(text)[0])
            self.v_park_chip.set('共 %d 条' % self._multi_count)
            self.v_naming_chip.set('批量模式')
            self.v_style_chip.set('逐条可调方向')
            self.v_detect_note.set('按 --- 分成 %d 条；点「批量生成」逐条确认横竖屏后一起跑'
                                   % self._multi_count)
            self._update_go_button()
            return
        r = detect_script(text)
        if self.auto['style']:
            self.v_style.set(r['style_key'])
        if self.auto['mode']:
            self.v_naming.set(r['mode'])
        if self.auto['seg']:
            self.v_seg.set(r['seg'])
        if self.auto['seg'] and not self.v_materials.get().strip():
            hint = PARK_MATERIAL_HINT.get(self.v_seg.get())
            if hint and os.path.isdir(hint):
                self.v_materials.set(hint)
                self._refresh_lib_version()
        mode_cn = '0式·城市' if self.v_naming.get() == '0' else '7式·实拍'
        self.v_park_chip.set('%s' % r['seg'])
        self.v_naming_chip.set(mode_cn)
        self.v_style_chip.set(self._style_name(r['style_key']))
        self.v_detect_note.set('识别依据：%s；%s' % (r['park_note'], r['style_note']))
        # P05 §4：用户显然在用空行当分隔时，给一句提示（切分结果仍是 1 条）
        if script_split.blank_separator_suspected(text):
            self.v_detect_note.set(self.v_detect_note.get() + ' ｜ ' +
                                   script_split.SPLIT_HINT)
        self._set_editor_state()
        self._render_preview()
        self._update_stats()
        self._update_go_button()

    def _update_go_button(self):
        if self.generating:
            return
        self.btn_go.config(text='批量生成 %d 条' % self._multi_count
                           if self._multi_count > 1 else '生成草稿')

    def _set_editor_state(self):
        need = (self.v_naming.get() == '7')
        state = 'normal' if need else 'disabled'
        if state != self._editor_state:
            self._editor_state = state
            self.e_editor.config(state=state, bg='#FFFFFF' if need else '#EEF2F8')

    def _render_preview(self):
        """草稿名预览 —— 走 ``draft_naming.base_name``（P05 §1.5 同一实现）。"""
        seg = (self.v_seg.get() or '园区').strip()
        naming = self.v_naming.get()
        orient_cn = '竖' if self.v_orient.get() in ('竖屏', '竖版') else '横'
        if naming == '7':
            prev = draft_naming.base_name('7', '', seg, orient_cn, '',
                                          self.v_editor.get().strip() or '剪辑者')
        else:
            prev = draft_naming.base_name('0', '', seg, orient_cn, self._style_name())
        if hasattr(self, 'lab_prev'):
            self.lab_prev.config(text='草稿名预览：%s-XXXXXXXX（尾码每次自动生成）' % prev)
        man = '已手动调整，改动不再被脚本覆盖' if not all(self.auto.values()) else ''
        self.v_manual.set(man)
        self._last_prev = prev

    # ---------------- 其他 ----------------
    def _toggle_adv(self):
        if self.v_adv.get():
            self.adv.pack_forget()
            self.btn_adv.config(text='高级设置  ▸')
            self.v_adv.set(False)
        else:
            self.adv.pack(fill='x', pady=(6, 0))
            self.btn_adv.config(text='高级设置  ▾')
            self.v_adv.set(True)

    def _browse(self, var_name):
        cur = getattr(self, var_name).get()
        p = filedialog.askdirectory(initialdir=cur or '/')
        if p:
            getattr(self, var_name).set(p)
            if var_name == 'v_materials':
                self._refresh_lib_version()
                self._update_stats()

    def _refresh_lib_version(self):
        m = self.v_materials.get().strip()
        self.v_libv.set('素材库 ' + (lib_version(m) if m else '-'))

    def _env_probe(self):
        def work():
            jy = detect_jianying()
            txt = '剪映 %s' % (os.path.basename(os.path.dirname(jy)) if jy else '剪映未探测到')
            self.log_q.put(('env', txt))
        threading.Thread(target=work, daemon=True).start()

    def _first_run_check(self):
        """首启向导 + 环境体检（P1-②，2026-09-09）。

        首启判定：剪辑者姓名从没填过。15 人每人一个姓名，它进草稿名（实拍园区）
        与产出台账 —— 团队排查"谁的哪一条"靠它区分。
        环境体检在后台跑 doctor，FAIL/WARN 推进日志区，不弹阻塞窗。
        """
        if not (self.v_editor.get() or '').strip():
            if messagebox.askyesno(
                    '首次使用',
                    '你还没有填写剪辑者姓名。\n\n'
                    '姓名会写进草稿名（实拍园区）和产出台账，'
                    '15 人批量出片时靠它区分"谁的哪一条"。\n\n'
                    '现在填写？（取消的话，稍后在「剪辑者」框里填也一样）',
                    parent=self):
                self.e_editor.focus_set()
                self.e_editor.select_range(0, 'end')

        # P05 §2：素材库路径**必须在主线程先取出来**再交给后台线程。
        # 老代码在 work() 里直接 self.v_materials.get()，属于跨线程读 Tk 变量。
        materials_snapshot = (self.v_materials.get() or '').strip()

        def work():
            # 工作台自动装到桌面（2026-09-09）：桌面还没有"源流工作台"快捷方式
            # 就调 install.bat 装一次（幂等；失败静默，不影响主界面）。
            try:
                lnk = os.path.join(os.environ.get('USERPROFILE', ''),
                                   'Desktop', '源流工作台.lnk')
                if not os.path.isfile(lnk):
                    inst = os.path.join(SKILL_ROOT, 'workbench', 'install.bat')
                    if os.path.isfile(inst):
                        subprocess.Popen(['cmd', '/c', inst],
                                         creationflags=getattr(subprocess,
                                                              'CREATE_NO_WINDOW', 0))
                        self.log_q.put(('env', '已将「源流工作台」快捷方式放到桌面'))
            except Exception:
                pass
            try:
                sys.path.insert(0, SCRIPTS_DIR)
                import doctor
                results = doctor.run_all(materials_dir=materials_snapshot or None,
                                         verbose=False)
                bad = [r for r in results if r['status'] in ('FAIL', 'WARN')]
                if not bad:
                    self.log_q.put(('env', '环境体检：全部通过'))
                    return
                for r in bad:
                    self.log_q.put(('env', '体检[%s] %s：%s' % (r['status'], r['name'],
                                                               (r['msg'] or '')[:70])))
            except Exception as e:
                self.log_q.put(('env', '体检失败：%s' % e))
        threading.Thread(target=work, daemon=True).start()

    # ---------------- 校验 + 拼命令 ----------------
    def _empty_materials_blocked(self, materials):
        """P05 §3：生成/批量前用**同一判据**检查素材库；空库默认拦停。

        返回 True = 已拦下（调用方直接 return）。
        与 CLI（--allow-empty-materials）和分镜预览共用
        ``material_matcher.validate_materials_dir()``，不另写一套判断。
        """
        mv = material_matcher.validate_materials_dir(materials)
        if mv['ok']:
            return False
        if self.v_allow_empty.get():
            # 高级设置里显式打开了"允许无画面调试稿"：放行，但必须留下痕迹
            self.log_q.put(('out', '[素材库] ⚠ %s' % mv['msg'].replace('\n', '  ')))
            self.log_q.put(('out', '[素材库] 已按「允许无画面调试稿」继续：'
                                   '本次生成的是无画面调试稿，不能用于交付'))
            return False
        messagebox.showwarning('素材库不可用', mv['msg'] +
                               '\n\n（高级设置里可勾选「允许无画面调试稿」强制继续，'
                               '但产出的草稿没有画面，不能用于交付）')
        return True

    def _collect(self):
        mode = self.v_input_mode.get()
        script_path = ''
        if mode == 'file':
            script_path = self.v_script_file.get().strip()
            if not script_path or not os.path.isfile(script_path):
                raise ValueError('请选择有效的脚本文件')
        else:
            text = self.txt_script.get('1.0', 'end').strip()
            if len(text) < 10:
                raise ValueError('脚本内容太短，请粘贴完整招商话术')
            fd, script_path = tempfile.mkstemp(suffix='.txt', prefix='park_script_')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)

        materials = self.v_materials.get().strip()
        out = self.v_out.get().strip() or DEFAULT_OUT
        orient_cn = self.v_orient.get()

        # P05 §3：空素材库默认拦停（与 CLI / 分镜预览同一判据）。
        # 弹窗已经在 _empty_materials_blocked 里弹过了，这里抛"已提示过"的
        # 静默异常，避免调用方再弹一次同样的窗。
        if self._empty_materials_blocked(materials):
            raise UiBlocked('素材库已拦停')

        job_id = new_job_id()
        cmd = [_python(), MAKE_PY, script_path, '--job-id', job_id]
        cmd += ['--style', self.v_style.get()]
        cmd += ['--out', out]
        cmd += ['--orientation', orient_cn]
        if materials:
            cmd += ['--materials', materials]
        if self.v_sub_y.get().strip():
            try:
                cmd += ['--sub-y', str(float(self.v_sub_y.get()))]
            except ValueError:
                raise ValueError('字幕上移需填数字（如 0.06）')
        if self.v_sub_size.get().strip():
            try:
                cmd += ['--sub-size', str(int(float(self.v_sub_size.get())))]
            except ValueError:
                raise ValueError('字幕字号需填整数')
        cmd += ['--bgm', self.v_bgm.get()]
        if self.v_copy.get():
            cmd += ['--copy-materials']
        if self.v_tts.get():
            cmd += ['--with-tts']

        seg = (self.v_seg.get() or '园区').strip()
        if self.v_naming.get() == '7':
            editor = self.v_editor.get().strip()
            if not editor:
                raise ValueError('识别为实拍园区（7式命名），需要填写剪辑者姓名')
            if orient_cn not in ('竖屏', '竖版', '横屏', '横版'):
                raise ValueError('需要选择方向（竖屏/横屏）')
            cmd += ['--naming', '7', '--editor', editor, '--park-name', seg]
        else:
            cmd += ['--park-name', seg]
        if self.v_allow_empty.get():
            cmd += ['--allow-empty-materials']
        # P05 §1.5：这里**不再预测草稿目录**（旧代码自己拼了第二份命名规则，
        # 与生成侧撞车时 GUI 自检的是不存在的目录）。真实目录由生成脚本打印
        # DRAFT_DIR=，见 _run_cmd / _run_generate。
        return cmd

    def _open_storyboard(self):
        """打开真实本地素材候选窗口；预览阶段不写使用次数。"""
        materials = self.v_materials.get().strip()
        # P05 §3：分镜预览与正式生成**同一判据**（同一个 validate_materials_dir）。
        # 老代码只看 os.path.isdir，指向一个空文件夹也放行 → 预览出来全空，
        # 用户以为"素材没选好"，其实是素材库本身是空的。
        if self._empty_materials_blocked(materials):
            return
        text = self._script_text().strip()
        if len(text) < 10:
            messagebox.showwarning('分镜预览', '请先粘贴完整脚本')
            return
        fd, script_path = tempfile.mkstemp(suffix='.txt', prefix='yuanliu_storyboard_')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        cmd = [_python(), STORYBOARD_GUI, '--script', script_path,
               '--materials', materials, '--style', self.v_style.get(),
               '--park', (self.v_seg.get() or '').strip(),
               '--orientation', self.v_orient.get(),
               '--editor', self.v_editor.get().strip(),
               '--out', self.v_out.get().strip() or DEFAULT_OUT]
        try:
            subprocess.Popen(cmd, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.v_status.set('已打开分镜预览')
        except Exception as e:
            messagebox.showerror('无法打开分镜预览', str(e))

    def _open_task_center(self):
        try:
            subprocess.Popen([_python(), TASK_CENTER_GUI],
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception as e:
            messagebox.showerror('无法打开任务中心', str(e))

    def _install_material_package(self):
        target = self.v_materials.get().strip()
        if not target:
            target = filedialog.askdirectory(title='选择本机素材库安装目录')
            if not target:
                return
            self.v_materials.set(target)
        use_zip = messagebox.askyesno('选择素材包', '素材包是 zip 文件吗？\n\n是：选择 zip\n否：选择已下载的文件夹')
        package = (filedialog.askopenfilename(title='选择素材包', filetypes=[('ZIP', '*.zip')])
                   if use_zip else filedialog.askdirectory(title='选择素材包文件夹'))
        if not package:
            return
        self.v_status.set('正在校验并安装素材包…')

        def work():
            try:
                sys.path.insert(0, SCRIPTS_DIR)
                import material_manifest
                result = material_manifest.install_package(package, target)
                self.log_q.put(('mat_install_done', result))
            except Exception as e:
                self.log_q.put(('mat_install_err', str(e)))
        threading.Thread(target=work, daemon=True).start()

    # ---------------- 批量生成 ----------------
    def _on_generate(self):
        if self.generating:
            return
        # 实时重算条数（不必等 500ms 防抖）
        text = self._script_text().strip()
        if len(text) >= 10:
            self._multi_count = len(split_multi_scripts(text))
        self._update_go_button()
        if self._multi_count > 1:
            self._open_batch(text)
            return
        try:
            cmd = self._collect()
        except UiBlocked:
            return                       # 已经弹过提示，直接收手（不再重复弹窗）
        except ValueError as e:
            messagebox.showwarning('参数不完整', str(e))
            return
        self._log('dim', '识别 → 园区:%s  命名:%s式  风格:%s' % (
            self.v_seg.get() or '园区', self.v_naming.get(), self._style_name()))
        self.generating = True
        self.v_status.set('生成中…')
        self.btn_go.config(state='disabled', text='生成中…')
        threading.Thread(target=self._run_generate,
                         args=(cmd, self._freeze_params()), daemon=True).start()

    def _open_batch(self, text):
        """逐条确认弹窗：每条显示识别结果 + 横/竖选择 + (7式)剪辑者。"""
        self._batch_rows = []
        scripts = split_multi_scripts(text)
        win = tk.Toplevel(self)
        self._batch_win = win
        win.title('批量生成确认（%d 条）' % len(scripts))
        win.configure(bg=C_BG)
        win.geometry('920x%d' % min(760, 250 + len(scripts) * 62))
        win.minsize(860, 340)
        head = tk.Frame(win, bg=C_BG)
        head.pack(fill='x', padx=16, pady=(12, 4))
        tk.Label(head, text='逐条确认方向后开始生成', bg=C_BG, fg=C_TITLE,
                 font=(F, 13, 'bold')).pack(side='left')
        tk.Label(head, text='园区 / 命名 / 风格已自动识别，方向与剪辑者请逐条确认',
                 bg=C_BG, fg=C_SUB, font=(F, 9)).pack(side='left', padx=(10, 0), pady=(3, 0))
        body = tk.Frame(win, bg=C_BG)
        body.pack(fill='both', expand=True, padx=12, pady=6)

        default_orient = self.v_orient.get()
        for i, st in enumerate(scripts, 1):
            det = detect_script(st)
            r = tk.Frame(body, bg=C_CARD, highlightbackground=C_LINE, highlightthickness=1)
            r.pack(fill='x', pady=4)
            # 左：序号圆标 + 脚本摘要 + 识别 chips
            lab = tk.Frame(r, bg=C_CARD)
            lab.pack(side='left', fill='x', expand=True, padx=10, pady=6)
            no_cv = tk.Canvas(lab, width=24, height=22, bg=C_CARD, bd=0,
                              highlightthickness=0)
            no_cv.create_oval(1, 1, 23, 21, fill=C_ACCENT, outline='')
            no_cv.create_text(12, 11, text='%d' % i, fill='#FFFFFF', font=(F, 8, 'bold'))
            no_cv.pack(side='left', padx=(0, 8))
            tk.Label(lab, text=st.replace('\n', ' ')[:26] +
                     ('…' if len(st) > 26 else ''), bg=C_CARD, fg=C_TEXT,
                     font=(F, 9), width=24, anchor='w').pack(side='left')
            for txt in ('%s' % det['seg'],
                        '%s式' % ('城市' if det['mode'] == '0' else '实拍'),
                        self._style_name(det['style_key'])):
                tk.Label(lab, text=txt, bg=C_TINT, fg=C_TINT_TEXT, font=(F, 8),
                         padx=7, pady=1).pack(side='left', padx=(5, 0))
            # 右：方向 + 剪辑者 + 状态
            opt = tk.Frame(r, bg=C_CARD)
            opt.pack(side='right', padx=10)
            st_lab = tk.Label(opt, text='待确认', bg=C_CARD, fg=C_FADED, font=(F, 8), width=7)
            st_lab.pack(side='right')
            ov = tk.StringVar(value=default_orient)
            tk.OptionMenu(opt, ov, '竖屏', '横屏').pack(side='right', padx=(4, 8))
            ev = None
            if det['mode'] == '7':
                tk.Label(opt, text='剪辑者', bg=C_CARD, fg=C_SUB,
                         font=(F, 9)).pack(side='right', padx=(8, 2))
                ev = tk.Entry(opt, width=9, relief='solid', bd=1, font=(F, 9))
                ev.insert(0, self.v_editor.get())
                ev.pack(side='right')
            self._batch_rows.append({'no': i, 'text': st, 'det': det,
                                     'ov': ov, 'ev': ev, 'st': st_lab})
        foot = tk.Frame(win, bg=C_BG)
        foot.pack(fill='x', padx=16, pady=10)
        self._btn(foot, '取消', kind='ghost', cmd=self._close_batch, padx=18).pack(side='left')
        self._btn(foot, '全部开始生成', kind='primary', cmd=self._start_batch,
                  padx=22, pady=6).pack(side='right')

    def _close_batch(self):
        if self._batch_win is not None:
            try:
                self._batch_win.destroy()
            except Exception:
                pass
        self._batch_win = None

    def _start_batch(self):
        """主线程完成全部命令组装（读 Tk 变量安全），线程只负责执行。"""
        # 快照主界面通用参数
        materials = self.v_materials.get().strip()
        out = self.v_out.get().strip() or DEFAULT_OUT
        suby = self.v_sub_y.get().strip()
        subsz = self.v_sub_size.get().strip()
        bgm = self.v_bgm.get()
        copy = bool(self.v_copy.get())
        tts = bool(self.v_tts.get())
        try:
            suby_f = str(float(suby)) if suby else ''
        except ValueError:
            suby_f = ''
        try:
            subsz_i = str(int(float(subsz))) if subsz else ''
        except ValueError:
            subsz_i = ''
        if self._empty_materials_blocked(materials):
            return
        jobs = []
        for row in self._batch_rows:
            det = row['det']
            text = row['text']
            orient = row['ov'].get()
            editor = row['ev'].get().strip() if row['ev'] is not None else ''
            no = row['no']
            if det['mode'] == '7' and not editor:
                jobs.append({'no': no, 'skip': '缺剪辑者'})
                continue
            cmd = self._assemble_cmd(text, orient, det['seg'], det['mode'],
                                     det['style_key'], editor, materials, out,
                                     suby_f, subsz_i, bgm, copy, tts,
                                     allow_empty=bool(self.v_allow_empty.get()))
            jobs.append({'no': no, 'cmd': cmd,
                         'label': '%s-%s' % (det['seg'], orient)})
        win = self._batch_win
        if win is not None and win.winfo_exists():
            win.destroy()
        self._batch_win = None
        self.generating = True
        self.v_status.set('批量生成中…')
        self.btn_go.config(state='disabled', text='批量生成中 %d 条…' % len(jobs))
        threading.Thread(target=self._run_batch,
                         args=(jobs, self._freeze_params()), daemon=True).start()

    def _assemble_cmd(self, st, orient_cn, seg, naming, style_key, editor,
                      materials, out, suby, subsz, bgm, copy, tts, allow_empty=False):
        """纯函数组装命令（不含任何 Tk 变量读取，可跨线程安全执行）。

        P05 §1.5：**不再在这里预测草稿目录**。目录名由 ``draft_naming``
        在生产侧唯一决定（还要处理冲突换名），GUI 只认子进程打印出来的
        ``DRAFT_DIR=`` 真实结果，避免"预测的名字"和"真实落盘的名字"不一致。
        """
        fd, script_path = tempfile.mkstemp(suffix='.txt', prefix='park_script_')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(st)
        job_id = new_job_id()
        cmd = [_python(), MAKE_PY, script_path, '--job-id', job_id]
        cmd += ['--style', style_key]
        cmd += ['--out', out]
        cmd += ['--orientation', orient_cn]
        if materials:
            cmd += ['--materials', materials]
        if suby:
            cmd += ['--sub-y', suby]
        if subsz:
            cmd += ['--sub-size', subsz]
        cmd += ['--bgm', bgm]
        if copy:
            cmd += ['--copy-materials']
        if tts:
            cmd += ['--with-tts']
        seg = seg or '园区'
        if naming == '7':
            cmd += ['--naming', '7', '--editor', editor, '--park-name', seg]
        else:
            cmd += ['--park-name', seg]
        if allow_empty:
            cmd += ['--allow-empty-materials']    # §3 调试放行（默认关）
        return cmd

    def _run_batch(self, jobs, ctx=None):
        """后台线程：批量逐条生成 + 自检。

        P05 §2：不再读 self.v_materials / self.v_notify（那是 Tk 变量），
        一律用主线程冻结好的 ``ctx``。异常也必须走 finally 收工。
        """
        ctx = ctx or {}
        self._batch_t0 = time.monotonic()
        n = len(jobs)
        results = []
        try:
            for idx, job in enumerate(jobs, 1):
                if job.get('skip'):
                    self._log('err', '[%d/%d] #%d %s，已跳过' % (idx, n, job['no'], job['skip']))
                    self.log_q.put(('bstat', (job['no'], job['skip'])))
                    results.append((job['no'], '跳过：' + job['skip']))
                    continue
                cmd, label = job['cmd'], job['label']
                self._log('run', '[%d/%d] 生成 #%d %s' % (idx, n, job['no'], label))
                self.log_q.put(('bstat', (job['no'], '生成中…')))
                rc = self._run_cmd(cmd)
                real_dir = self._last_draft_dir
                if rc == 0:
                    if not real_dir or not os.path.isdir(real_dir):
                        self._log('err', '  #%d 拿不到真实草稿目录，跳过自检' % job['no'])
                        self.log_q.put(('bstat', (job['no'], 'FAIL')))
                        results.append((job['no'], 'FAIL（未拿到草稿目录）'))
                        continue
                    self._log('dim', '  #%d 草稿：%s' % (job['no'], os.path.basename(real_dir)))
                    rc2 = self._run_cmd([_python(), VERIFY_PY, real_dir])
                    if rc2 == 0:
                        self._log('ok', '  #%d 自检 PASS' % job['no'])
                        self.log_q.put(('bstat', (job['no'], 'PASS')))
                        results.append((job['no'], 'PASS ' + os.path.basename(real_dir)))
                        continue
                    self._log('err', '  #%d 自检未通过（rc=%s）' % (job['no'], rc2))
                    self.log_q.put(('bstat', (job['no'], 'FAIL')))
                    results.append((job['no'], 'FAIL（自检 rc=%s）' % rc2))
                    continue
                self._log('err', '  #%d 失败（rc=%s）' % (job['no'], rc))
                self.log_q.put(('bstat', (job['no'], 'FAIL')))
                results.append((job['no'], 'FAIL（rc=%s）' % rc))
            _ok = sum(1 for _, s in results if s.startswith('PASS'))
            _sec = time.monotonic() - getattr(self, '_batch_t0', time.monotonic())
            self._banner(_ok == n, '批量出片完成   %d/%d 通过   总耗时 %s'
                         % (_ok, n, self._fmt_elapsed(_sec)), [
                             '素材库：%s' % (ctx.get('materials') or '(未指定)'),
                             '逐条状态见上方日志',
                         ])
            if ctx.get('notify'):
                self._post_dialog('info', '批量出片完成',
                                  '完成 %d/%d 条\n总耗时：%s'
                                  % (_ok, n, self._fmt_elapsed(_sec)))
            self._log('ok', '批量完成：%d 成功 / %d 条' % (_ok, n))
            for no, s in results:
                self._log('dim', '  #%d  %s' % (no, s))
        except Exception as e:
            self._log('err', '批量生成内部错误：%s: %s' % (type(e).__name__, e))
            self._post_dialog('error', '批量生成内部错误',
                              '%s: %s' % (type(e).__name__, e))
        finally:
            self.log_q.put(('__done__', ''))

    # ---------------- 生成（单条） ----------------

    @staticmethod
    def _fmt_elapsed(sec):
        """耗时显示成"X 分 Y 秒"（不足 1 分只显示秒，带 1 位小数）。"""
        if sec >= 60:
            return '%d 分 %02d 秒' % (int(sec // 60), int(round(sec % 60)))
        return '%.1f 秒' % sec

    def _banner(self, ok, title, lines):
        """醒目的完成横幅（日志里靛蓝底加粗）。"""
        bar = '━' * 46
        self._log('banner', bar)
        self._log('banner', '%s %s' % ('✅' if ok else '❌', title))
        for ln in lines:
            self._log('banner', '   ' + ln)
        self._log('banner', bar)

    def _run_generate(self, cmd, ctx=None):
        """后台线程：跑生成 + 自检。

        P05 §2：本函数**不得**触碰任何 Tk 对象；状态/弹窗一律走 log_q。
        §1.6：不再相信调用方预测的 draft_dir，改用生成过程里打印的
        真实最终目录（草稿目录可能因冲突自动换名）。
        """
        ctx = ctx or {}
        t0 = time.monotonic()
        self._log('run', '执行: %s' % os.path.basename(MAKE_PY))
        try:
            rc = self._run_cmd(cmd)
            gen_sec = time.monotonic() - t0
            ok = rc == 0
            real_dir = self._last_draft_dir
            verify_txt = '未执行'
            if rc == 0:
                self._log('ok', '草稿已生成（耗时 %s），开始自检…' % self._fmt_elapsed(gen_sec))
                if not real_dir or not os.path.isdir(real_dir):
                    ok = False
                    verify_txt = '找不到真实草稿目录，已跳过自检'
                    self._log('err', '  %s' % verify_txt)
                else:
                    self._log('dim', '  真实草稿目录：%s' % real_dir)
                    t1 = time.monotonic()
                    rc2 = self._run_cmd([_python(), VERIFY_PY, real_dir])
                    verify_txt = 'PASS（%s）' % self._fmt_elapsed(time.monotonic() - t1) \
                        if rc2 == 0 else '未通过（rc=%s）' % rc2
                    ok = rc2 == 0
                    if rc2 != 0:
                        self._log('err', '自检未通过（rc=%s），请看上方日志' % rc2)
            else:
                self._log('err', '生成失败（rc=%s），请看上方日志' % rc)
            total = time.monotonic() - t0
            title = '草稿生成完成' if ok else ('生成失败' if rc != 0 else '草稿已生成，但自检未通过')
            self._banner(ok, '%s   总耗时 %s' % (title, self._fmt_elapsed(total)), [
                '草稿目录：%s' % (real_dir or '（未拿到）'),
                '生成阶段：%s' % self._fmt_elapsed(gen_sec),
                '自检结果：%s' % verify_txt,
            ])
            self._post_status('%s %s · 耗时 %s' % ('✅' if ok else '❌', title,
                                                   self._fmt_elapsed(total)))
            if ctx.get('notify'):
                self._post_dialog('info' if ok else 'error',
                                  '出片完成' if ok else '出片失败',
                                  '%s\n\n耗时：%s\n草稿：%s\n自检：%s'
                                  % (title, self._fmt_elapsed(total),
                                     real_dir or '（未拿到）', verify_txt))
        except Exception as e:
            # 后台线程绝不能把异常吞掉 —— 按钮会永久卡在"生成中"
            self._log('err', '工作台内部错误：%s: %s' % (type(e).__name__, e))
            self._post_dialog('error', '工作台内部错误',
                              '%s: %s' % (type(e).__name__, e))
        finally:
            # §2：无论成功、失败、异常，都必须告诉主线程「收工了」→ 按钮恢复
            self.log_q.put(('__done__', ''))

    def _run_cmd(self, cmd):
        """跑子进程并逐行回传日志；同时捕获真实草稿目录（P05 §1.6）。"""
        self._last_draft_dir = ''
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding='utf-8', errors='replace',
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception as e:
            self._log('err', '启动失败: %s' % e)
            return -1
        for line in p.stdout:
            line = line.rstrip('\n').rstrip('\r')
            if not line.strip():
                continue
            # 生成脚本会打印 DRAFT_DIR=<绝对路径>：这是**唯一可信**的最终目录，
            # 冲突换名后 GUI 自检/弹窗必须用这个，而不是自己猜的名字。
            if line.startswith('DRAFT_DIR='):
                self._last_draft_dir = line.split('=', 1)[1].strip()
            self._log('out', line)
        p.wait()
        return p.returncode

    def _log(self, kind, msg):
        self.log_q.put((kind, msg))

    def _drain_log(self):
        if self._closed:
            return                       # 窗口已销毁：停掉轮询，不再碰任何控件
        try:
            while True:
                kind, msg = self.log_q.get_nowait()
                if kind == '__done__':
                    self._gen_done()
                    continue
                if kind == 'status':     # P05 §2：后台线程的状态更新走这里
                    self.v_status.set(msg)
                    continue
                if kind == 'dialog':     # P05 §2：后台线程的弹窗走这里
                    level, title, body = msg
                    fn = {'info': messagebox.showinfo,
                          'warning': messagebox.showwarning,
                          'error': messagebox.showerror}.get(level, messagebox.showinfo)
                    try:
                        fn(title, body)
                    except tk.TclError:
                        pass
                    continue
                if kind == 'env':
                    self.v_jy.set(msg)
                    continue
                if kind == 'ins_done':
                    self._fill_inspect(*msg)
                    continue
                if kind == 'ins_err':
                    ins = getattr(self, '_ins', None)
                    if ins:
                        tk.Label(ins['win'], text='扫描失败: %s' % msg,
                                 bg=C_BG, fg='#C62828').pack()
                    continue
                if kind == 'mat_install_done':
                    self._refresh_lib_version()
                    self.v_status.set('素材包 %s 已安装' % msg.get('version', '-'))
                    messagebox.showinfo('素材安装完成',
                                        '版本：%s\n更新：%d/%d 个素材\n目录：%s' %
                                        (msg.get('version','-'), msg.get('installed',0),
                                         msg.get('total',0), msg.get('target','')))
                    continue
                if kind == 'mat_install_err':
                    self.v_status.set('素材包安装失败')
                    messagebox.showerror('素材安装失败', msg)
                    continue
                if kind == 'bstat':
                    no, txt = msg
                    for row in self._batch_rows:
                        if row['no'] == no:
                            try:
                                fg = {'PASS': '#16A34A', 'FAIL': '#DC2626',
                                      '跳过：缺剪辑者': '#D97706'}.get(txt, C_SUB)
                                row['st'].config(text=txt, fg=fg)
                            except tk.TclError:
                                pass
                            break
                    continue
                self.txt_log.config(state='normal')
                self.txt_log.insert('end', msg + '\n', kind)
                self.txt_log.see('end')
                self.txt_log.config(state='disabled')
        except queue.Empty:
            pass
        except tk.TclError:
            # 窗口正在销毁（例如用户关窗瞬间事件还在队列里）：静默收尾
            # P05 §2「关闭后不操作已销毁控件」
            self._closed = True
            return
        try:
            self.after(100, self._drain_log)
        except tk.TclError:
            self._closed = True

    def _gen_done(self):
        if self._closed:
            return
        self.generating = False
        self.v_status.set('就绪 · 上次成稿完成')
        try:
            self.btn_go.config(state='normal')
        except tk.TclError:
            pass
        self._update_go_button()

    # ---------------- 素材体检 ----------------
    def _inspect_materials(self):
        m = self.v_materials.get().strip()
        if not os.path.isdir(m):
            messagebox.showwarning('素材体检', '请先选择有效的素材库目录')
            return
        win = tk.Toplevel(self)
        win.title('素材体检 — %s' % os.path.basename(m.rstrip('/\\')))
        win.configure(bg=C_BG)
        win.geometry('600x450')
        tk.Label(win, text='正在扫描（548 个视频约 5 秒）…', bg=C_BG, fg=C_SUB,
                 font=(F, 10)).pack(anchor='w', padx=14, pady=8)
        tv = ttk.Treeview(win, columns=('n', 'note'), show='tree headings')
        tv.heading('#0', text='语义桶')
        tv.heading('n', text='数量')
        tv.heading('note', text='说明')
        tv.column('#0', width=140)
        tv.column('n', width=70, anchor='center')
        tv.column('note', width=340)
        tv.pack(fill='both', expand=True, padx=14, pady=4)
        self._ins = {'win': win, 'tv': tv, 'path': m}

        def work():
            try:
                sys.path.insert(0, SCRIPTS_DIR)
                import material_matcher as mm
                items = mm.scan_materials(m)
            except Exception as e:
                self.log_q.put(('ins_err', repr(e)))
                return
            from collections import Counter
            c = Counter(it['bucket'] for it in items)
            oc = Counter(it.get('orientation') for it in items)
            short = sum(1 for it in items if it.get('duration') and it['duration'] < 600000)
            data = []
            for b in mm.BUCKET_ORDER:
                n = c.get(b, 0)
                if n:
                    data.append((BUCKET_CN.get(b, b), n, ''))
            unk = c.get(None, 0)
            if unk:
                data.append(('未分类（随机兜底）', unk, ''))
            data.append(('—', None, ''))
            data.append(('横屏标注', oc.get('landscape', 0), '只能用于横版成片'))
            data.append(('竖屏标注', oc.get('portrait', 0), '只能用于竖版成片'))
            data.append(('无标注', len(items) - oc.get('landscape', 0) - oc.get('portrait', 0), '横竖通用'))
            data.append(('过短(<0.6s)', short, '剔除不参与匹配'))
            self.log_q.put(('ins_done', (data, m)))
        threading.Thread(target=work, daemon=True).start()

    def _fill_inspect(self, data, path):
        ins = getattr(self, '_ins', None)
        if not ins or not ins['win'].winfo_exists():
            return
        tv = ins['tv']
        for child in tv.get_children():
            tv.delete(child)
        for name, n, note in data:
            if name == '—':
                tv.insert('', 'end', values=('', '', ''))
                continue
            tv.insert('', 'end', text=name, values=(str(n) if n is not None else '', note))
        tk.Label(ins['win'], text='素材库版本：%s（与团队最新版不一致时请重新拷贝素材）'
                 % lib_version(path), bg=C_BG, fg='#7A5B00').pack(anchor='w', padx=14, pady=6)

    # ---------------- 配置 ----------------
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _apply_settings(self):
        c = self.cfg
        if c.get('materials'):
            self.v_materials.set(c['materials'])
        if c.get('out'):
            self.v_out.set(c['out'])
        if c.get('editor'):
            self.v_editor.set(c['editor'])
        if c.get('orient') in ('竖屏', '横屏'):
            self.v_orient.set(c['orient'])
        if c.get('sub_y'):
            self.v_sub_y.set(str(c['sub_y']))
        if c.get('sub_size'):
            self.v_sub_size.set(str(c['sub_size']))
        if c.get('bgm'):
            self.v_bgm.set(c['bgm'])
        if c.get('copy'):
            self.v_copy.set(True)
        self._refresh_lib_version()

    def _save_settings(self):
        self.cfg.update({
            'materials': self.v_materials.get().strip(),
            'out': self.v_out.get().strip(),
            'editor': self.v_editor.get().strip(),
            'orient': self.v_orient.get(),
            'sub_y': self.v_sub_y.get().strip() or 0,
            'sub_size': self.v_sub_size.get().strip() or '',
            'bgm': self.v_bgm.get(),
            'copy': bool(self.v_copy.get()),
        })
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._closed = True
        self._save_settings()
        self.destroy()

    # ---------------- P05 §2：线程安全的唯一通道 ----------------
    # 规矩（可静态检查，见 tests/test_gui_thread_safety.py）：
    #   后台线程只允许做两件事 —— 跑 Python / 子进程、往 self.log_q 里塞事件。
    #   所有 Tk 操作（读写变量、改控件、弹窗）一律在主线程 _drain_log 里做。
    #   因此下面三个方法是后台线程**唯一**可用的界面出口。
    def _freeze_params(self):
        """在主线程把界面参数**拷成普通 Python 值**再交给后台线程。

        Tkinter 的变量只能在主线程操作；后台线程读 ``StringVar.get()``
        轻则拿到半更新的值（用户正好在改），重则直接崩在 Tcl 层。
        """
        return {
            'materials': (self.v_materials.get() or '').strip(),
            'out': (self.v_out.get() or '').strip() or DEFAULT_OUT,
            'notify': bool(self.v_notify.get()),
            'editor': (self.v_editor.get() or '').strip(),
            'seg': (self.v_seg.get() or '园区').strip(),
        }

    def _post_status(self, text):
        """后台线程 → 状态栏（主线程执行）。"""
        self.log_q.put(('status', text))

    def _post_dialog(self, level, title, message):
        """后台线程 → 弹窗（主线程执行）。level ∈ info/warning/error。"""
        self.log_q.put(('dialog', (level, title, message)))


if __name__ == '__main__':
    import traceback
    try:
        Workbench().mainloop()
    except Exception:
        tb = traceback.format_exc()
        try:
            errf = os.path.join(SKILL_ROOT, 'workbench', 'gui_error.log')
            with open(errf, 'w', encoding='utf-8') as f:
                f.write(tb)
        except Exception:
            errf = '(无法写日志)'
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror('工作台异常',
                                 '发生错误，详情见 %s\n\n%s' % (errf, tb[-300:]))
            r.destroy()
        except Exception:
            pass
        raise
