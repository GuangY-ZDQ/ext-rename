# -*- coding: utf-8 -*-
"""
批量改后缀 · ExtRename
------------------------------------------------------------------
把一批文件 / 文件夹拖进窗口 → 下拉框选目标后缀（或自定义）→ 预览 → 执行。
只修改文件名的扩展名，不动文件内容。
"""

import os
import sys
import json
import time
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ---------- 现代化界面（缺依赖时自动退回原生 tkinter）----------
try:
    import customtkinter as ctk
    CTK_OK = True
except Exception:
    ctk = None
    CTK_OK = False

# ---------- 拖拽支持 ----------
DND_AVAILABLE = False
DND_FILES = None
TkinterDnD = None
try:
    from tkinterdnd2 import DND_FILES as _DND_FILES, TkinterDnD as _TkinterDnD
    DND_FILES = _DND_FILES
    TkinterDnD = _TkinterDnD
    DND_AVAILABLE = True
except Exception:
    pass

# ---------- 常量 ----------
APP_TITLE = "批量改后缀"
APP_VER = "1.0"
PRESETS = [".zip", ".7z", ".rar", ".mp4", ".mp3", ".jpg", ".png", ".pdf", ".txt"]
CUSTOM_LABEL = "自定义…"
ILLEGAL_CHARS = set('\\/:*?"<>|')

if getattr(sys, "frozen", False):          # 打包后，撤销记录放在 exe 同目录，不往系统盘写东西
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UNDO_FILE = os.path.join(BASE_DIR, "撤销记录.json")
MAX_ROWS = 3000

# 配色：只有浅色一套，克制、干净
L = dict(
    bg="#F4F6FA",          # 窗口底色
    card="#FFFFFF",        # 卡片
    card2="#EEF3FB",       # 输入框 / 次级按钮底色
    hover="#E1E9F8",       # 次级按钮悬停
    line="#DFE6F2",        # 描边
    text="#1B2130",        # 主文字
    text2="#3E4A60",       # 次级按钮文字
    sub="#6E7787",         # 说明文字
    switch_off="#D6DDEA",  # 开关关闭时的轨道
    accent="#2F6BFF",      # 主色
    accent_h="#1F56E8",
    tree_bg="#FFFFFF",
    tree_head="#F6F8FC",
    tree_sel="#2F6BFF",
)


# ======================= 纯逻辑 =======================
def normalize_ext(raw):
    """把用户输入整理成 '.xxx'。返回 (ext, 错误信息)。"""
    if raw is None:
        return None, "后缀为空"
    s = raw.strip()
    if not s:
        return None, "后缀为空"
    s = s.lstrip(".").strip()
    if not s:
        return None, "后缀为空"
    if any(c in ILLEGAL_CHARS for c in s):
        return None, "后缀里含有非法字符"
    if any(c.isspace() for c in s):
        return None, "后缀里不能有空格"
    if len(s) > 32:
        return None, "后缀太长了"
    return "." + s, None


def collect_files(paths, recursive=True):
    """把文件 / 文件夹混合列表展开成文件列表（去重）。"""
    result, seen = [], set()

    def add(p):
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen and os.path.isfile(p):
            seen.add(key)
            result.append(os.path.abspath(p))

    for p in paths:
        if not p or not os.path.exists(p):
            continue
        if os.path.isdir(p):
            if recursive:
                for root, dirs, files in os.walk(p):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for f in files:
                        add(os.path.join(root, f))
            else:
                for f in os.listdir(p):
                    add(os.path.join(p, f))
        else:
            add(p)
    return result


def build_plan(files, new_ext, conflict="suffix"):
    """生成改名计划。conflict: 'suffix' 自动编号 / 'skip' 跳过。"""
    plan, reserved = [], set()

    def norm(d, p):
        return os.path.normcase(os.path.join(d, p))

    for src in files:
        d, name = os.path.dirname(src), os.path.basename(src)
        stem, _old = os.path.splitext(name)
        target = stem + new_ext

        if norm(d, name) == norm(d, target):
            plan.append(dict(src=src, dir=d, name=name, new_name=name,
                             status="无需修改", do=False))
            continue

        busy = os.path.exists(os.path.join(d, target)) or norm(d, target) in reserved
        if not busy:
            reserved.add(norm(d, target))
            plan.append(dict(src=src, dir=d, name=name, new_name=target,
                             status="待修改", do=True))
            continue

        if conflict == "skip":
            plan.append(dict(src=src, dir=d, name=name, new_name=target,
                             status="跳过 · 同名已存在", do=False))
            continue

        i = 1
        while i <= 9999:
            cand = "%s_%d%s" % (stem, i, new_ext)
            if not os.path.exists(os.path.join(d, cand)) and norm(d, cand) not in reserved:
                reserved.add(norm(d, cand))
                plan.append(dict(src=src, dir=d, name=name, new_name=cand,
                                 status="自动编号", do=True))
                break
            i += 1
        else:
            plan.append(dict(src=src, dir=d, name=name, new_name=target,
                             status="跳过 · 编号用尽", do=False))
    return plan


def execute_plan(plan):
    """执行改名，返回 (成功数, 失败明细, 撤销记录)。"""
    done, failed, undo = 0, [], []
    for item in plan:
        if not item["do"]:
            continue
        src = item["src"]
        dst = os.path.join(item["dir"], item["new_name"])
        try:
            if os.path.exists(dst) and os.path.normcase(src) != os.path.normcase(dst):
                failed.append({"src": src, "reason": "目标文件已存在"})
                continue
            os.rename(src, dst)
            done += 1
            undo.append([src, dst])
        except Exception as e:
            failed.append({"src": src, "reason": str(e)})
    return done, failed, undo


def save_undo(undo):
    try:
        with open(UNDO_FILE, "w", encoding="utf-8") as f:
            json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "items": undo},
                      f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def load_undo():
    try:
        with open(UNDO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f TB" % n


# ============================== 界面 ==============================
if CTK_OK:
    if DND_AVAILABLE:
        class Root(ctk.CTk, TkinterDnD.DnDWrapper):
            def __init__(self):
                super().__init__()
                self.dnd_ready = False
                try:
                    self.TkdndVersion = TkinterDnD._require(self)
                    self.dnd_ready = True
                except Exception:
                    self.dnd_ready = False
    else:
        class Root(ctk.CTk):
            def __init__(self):
                super().__init__()
                self.dnd_ready = False
else:
    if DND_AVAILABLE:
        class Root(TkinterDnD.Tk):
            def __init__(self):
                super().__init__()
                self.dnd_ready = True
    else:
        class Root(tk.Tk):
            def __init__(self):
                super().__init__()
                self.dnd_ready = False


class Toggle(tk.Canvas):
    """自绘 iOS 风格开关：蓝/灰圆角轨道 + 白色圆点，比 CTkSwitch 更可控。"""

    def __init__(self, master, variable, command=None, width=46, height=26,
                 track_on="#2F6BFF", track_off="#DDE3EE", bg="#FFFFFF"):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.cmd = command
        self.w, self.h = width, height
        self.on_color, self.off_color = track_on, track_off
        self.bind("<Button-1>", self._on_click)
        try:
            self.var.trace_add("write", lambda *a: self.redraw())
        except Exception:
            pass
        self.redraw()

    def _capsule(self, x1, y1, x2, y2, color):
        """两头半圆的长条，保证胶囊形完全圆润。"""
        r = (y2 - y1) / 2.0
        self.create_oval(x1, y1, x1 + 2 * r, y2, fill=color, outline=color)
        self.create_oval(x2 - 2 * r, y1, x2, y2, fill=color, outline=color)
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=color, outline=color)

    def redraw(self):
        self.delete("all")
        on = bool(self.var.get())
        pad = 1
        self._capsule(pad, pad, self.w - pad, self.h - pad,
                      self.on_color if on else self.off_color)
        kd = self.h - 2 * pad - 6           # 圆点直径
        cy = self.h / 2.0
        cx = (self.w - pad - 3 - kd / 2.0) if on else (pad + 3 + kd / 2.0)
        self.create_oval(cx - kd / 2.0, cy - kd / 2.0, cx + kd / 2.0, cy + kd / 2.0,
                         fill="#FFFFFF", outline="")

    def _on_click(self, _event=None):
        self.var.set(not bool(self.var.get()))
        self.redraw()
        if self.cmd:
            self.cmd()


class App:
    def __init__(self, root):
        self.root = root
        self.plan = []
        self.sources = []
        self.excluded = set()
        self.all_files = []

        self.ext_var = tk.StringVar(value=PRESETS[0])
        self.custom_var = tk.StringVar(value=PRESETS[0])
        self.preset_var = tk.StringVar(value=PRESETS[0])
        self.recursive_var = tk.BooleanVar(value=True)
        self.conflict_var = tk.StringVar(value="自动编号")
        self.status_var = tk.StringVar(value="就绪")

        root.title(APP_TITLE + "  v" + APP_VER)
        root.geometry(self._center(1000, 720))
        root.minsize(860, 600)
        self.pick_font()
        self.set_window_icon()
        self.build()

        if getattr(root, "dnd_ready", False):
            try:
                root.drop_target_register(DND_FILES)
                root.dnd_bind("<<Drop>>", self.on_drop)
            except Exception:
                pass

    # ---------- 小工具 ----------
    def _center(self, w, h):
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            return "%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3))
        except Exception:
            return "%dx%d" % (w, h)

    def pick_font(self):
        self.font_family = "TkDefaultFont"
        try:
            import tkinter.font as tkfont
            fams = set(tkfont.families(self.root))
            for f in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                      "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei"):
                if f in fams:
                    self.font_family = f
                    break
        except Exception:
            pass

    def F(self, size=12, weight="normal"):
        return (self.font_family, size, weight)

    def set_window_icon(self):
        """标题栏 / 任务栏图标（打包后从临时目录取）。"""
        try:
            base = getattr(sys, "_MEIPASS", BASE_DIR)
            ico = os.path.join(base, "app.ico")
            if os.path.exists(ico):
                self.root.iconbitmap(ico)
                self.root.iconbitmap(default=ico)
        except Exception:
            pass

    def C(self, key):
        return L[key]

    # ---------- 构建界面 ----------
    def build(self):
        if CTK_OK:
            ctk.set_appearance_mode("light")
            try:
                ctk.set_default_color_theme("blue")
            except Exception:
                pass
            self.root.configure(fg_color=self.C("bg"))
            self.build_ctk()
        else:
            self.root.configure(bg=L["bg"])
            self.build_plain()

        self.apply_colors()
        self.apply_tree_style()

    def build_ctk(self):
        P = 18

        # 顶部标题栏
        head = ctk.CTkFrame(self.root, fg_color="transparent")
        head.pack(fill="x", padx=P, pady=(P, 8))
        left = ctk.CTkFrame(head, fg_color="transparent")
        left.pack(side="left")
        self.title_lbl = ctk.CTkLabel(left, text=APP_TITLE, font=self.F(26, "bold"),
                                      text_color=self.C("text"))
        self.title_lbl.pack(anchor="w")
        self.sub_lbl = ctk.CTkLabel(left, text="拖入文件，批量改扩展名 · 只改文件名，不动内容",
                                    font=self.F(12), text_color=self.C("sub"))
        self.sub_lbl.pack(anchor="w", pady=(2, 0))

        # 拖放区
        self.drop = ctk.CTkFrame(self.root, height=132, corner_radius=18,
                                 fg_color=self.C("card"), border_width=2,
                                 border_color=self.C("line"))
        self.drop.pack(fill="x", padx=P, pady=(6, 12))
        self.drop.pack_propagate(False)

        self.drop_icon = ctk.CTkLabel(self.drop, text="＋", font=self.F(34, "bold"),
                                      text_color=self.C("accent"))
        self.drop_icon.pack(pady=(18, 0))
        self.drop_lbl = ctk.CTkLabel(
            self.drop,
            text="把文件或文件夹拖到这里" if getattr(self.root, "dnd_ready", False)
            else "点击这里选择文件（拖拽组件未加载）",
            font=self.F(15, "bold"), text_color=self.C("text"))
        self.drop_lbl.pack(pady=(2, 0))
        self.drop_hint = ctk.CTkLabel(self.drop, text="支持一次拖入多个文件 / 整个文件夹",
                                      font=self.F(11), text_color=self.C("sub"))
        self.drop_hint.pack(pady=(2, 16))
        for w in (self.drop, self.drop_icon, self.drop_lbl, self.drop_hint):
            w.bind("<Button-1>", lambda e: self.add_files())

        # 设置卡片
        self.card_frames = []
        card = ctk.CTkFrame(self.root, corner_radius=16, fg_color=self.C("card"),
                            border_width=1, border_color=self.C("line"))
        self.card_frames.append(card)
        card.pack(fill="x", padx=P, pady=(0, 12))

        r1 = ctk.CTkFrame(card, fg_color="transparent")
        r1.pack(fill="x", padx=16, pady=(14, 4))
        self.lbl_ext = ctk.CTkLabel(r1, text="目标后缀", font=self.F(13, "bold"),
                                    text_color=self.C("text"), width=80, anchor="w")
        self.lbl_ext.pack(side="left")
        self.opt = ctk.CTkOptionMenu(r1, values=PRESETS + [CUSTOM_LABEL], width=130, height=34,
                                     corner_radius=9, font=self.F(13), dropdown_font=self.F(13),
                                     variable=self.preset_var, command=self.on_preset,
                                     fg_color=self.C("card2"), button_color=self.C("card2"),
                                     button_hover_color=self.C("line"),
                                     text_color=self.C("text"), dropdown_text_color=self.C("text"),
                                     dropdown_fg_color=self.C("card"))
        self.opt.pack(side="left", padx=(0, 10))
        self.ent_custom = ctk.CTkEntry(r1, textvariable=self.custom_var, width=170, height=34,
                                       corner_radius=9, font=self.F(13),
                                       placeholder_text="例如 7z / tar.gz",
                                       fg_color=self.C("card2"), border_color=self.C("line"),
                                       text_color=self.C("text"))
        self.ent_custom.bind("<KeyRelease>", self.on_ext_key)
        self.hint_lbl = ctk.CTkLabel(r1, text="选「自定义…」可以填别的后缀",
                                     font=self.F(11), text_color=self.C("sub"))
        self.hint_lbl.pack(side="left", padx=(10, 0))

        self.lbl_rec = ctk.CTkLabel(r1, text="包含子文件夹", font=self.F(12),
                                    text_color=self.C("sub"))
        self.lbl_rec.pack(side="right")
        self.lbl_rec.bind("<Button-1>", lambda e: self.sw_rec._on_click())
        self.sw_rec = Toggle(r1, self.recursive_var, command=self.rebuild,
                             track_on=self.C("accent"), track_off=self.C("switch_off"),
                             bg=self.C("card"))
        self.sw_rec.pack(side="right", padx=(0, 8), pady=(2, 0))

        r2 = ctk.CTkFrame(card, fg_color="transparent")
        r2.pack(fill="x", padx=16, pady=(6, 14))
        self.lbl_conf = ctk.CTkLabel(r2, text="同名冲突", font=self.F(13, "bold"),
                                     text_color=self.C("text"), width=80, anchor="w")
        self.lbl_conf.pack(side="left")
        self.seg = ctk.CTkOptionMenu(r2, values=["自动编号", "跳过"], width=120, height=32,
                                     corner_radius=9, font=self.F(12), dropdown_font=self.F(12),
                                     variable=self.conflict_var,
                                     command=lambda v: self.refresh_plan(),
                                     fg_color=self.C("card2"), button_color=self.C("card2"),
                                     button_hover_color=self.C("line"),
                                     text_color=self.C("text"),
                                     dropdown_fg_color=self.C("card"),
                                     dropdown_text_color=self.C("text"))
        self.seg.pack(side="left")
        self.tip_lbl = ctk.CTkLabel(r2, text="自动编号会把 a.zip 变成 a_1.zip，不会覆盖原文件",
                                   font=self.F(11), text_color=self.C("sub"))
        self.tip_lbl.pack(side="left", padx=(12, 0))

        # 列表头
        bar = ctk.CTkFrame(self.root, fg_color="transparent")
        bar.pack(fill="x", padx=P, pady=(0, 6))
        self.list_lbl = ctk.CTkLabel(bar, text="预览", font=self.F(13, "bold"),
                                     text_color=self.C("text"))
        self.list_lbl.pack(side="left")
        self.count_lbl = ctk.CTkLabel(bar, text="", font=self.F(11), text_color=self.C("sub"))
        self.count_lbl.pack(side="left", padx=(10, 0))
        ctk.CTkButton(bar, text="清空", width=64, height=28, corner_radius=8, font=self.F(11),
                      fg_color=self.C("card2"), hover_color=self.C("hover"),
                      text_color=self.C("text2"), border_width=0, command=self.clear_all).pack(side="right")
        ctk.CTkButton(bar, text="移除选中", width=88, height=28, corner_radius=8, font=self.F(11),
                      fg_color=self.C("card2"), hover_color=self.C("hover"),
                      text_color=self.C("text2"), border_width=0, command=self.remove_selected).pack(side="right", padx=(0, 8))
        ctk.CTkButton(bar, text="添加文件夹", width=88, height=28, corner_radius=8, font=self.F(11),
                      fg_color=self.C("card2"), hover_color=self.C("hover"),
                      text_color=self.C("text2"), border_width=0, command=self.add_folder).pack(side="right", padx=(0, 8))
        ctk.CTkButton(bar, text="添加文件", width=80, height=28, corner_radius=8, font=self.F(11),
                      fg_color=self.C("card2"), hover_color=self.C("hover"),
                      text_color=self.C("text2"), border_width=0, command=self.add_files).pack(side="right", padx=(0, 8))

        # 底部操作栏（先占住底部，避免被表格挤出去）
        foot = ctk.CTkFrame(self.root, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=P, pady=(0, P))
        self.status_lbl = ctk.CTkLabel(foot, textvariable=self.status_var, font=self.F(11),
                                       text_color=self.C("sub"), anchor="w")
        self.status_lbl.pack(side="left")
        self.btn_run = ctk.CTkButton(foot, text="执行改名", width=140, height=40, corner_radius=10,
                                     font=self.F(14, "bold"), fg_color=self.C("accent"),
                                     hover_color=self.C("accent_h"), command=self.run)
        self.btn_run.pack(side="right")
        self.btn_undo = ctk.CTkButton(foot, text="撤销上一次", width=110, height=40, corner_radius=10,
                                      font=self.F(12), fg_color=self.C("card2"),
                                      hover_color=self.C("hover"), text_color=self.C("text2"),
                                      border_width=0, command=self.undo_last)
        self.btn_undo.pack(side="right", padx=(0, 10))
        self.btn_refresh = ctk.CTkButton(foot, text="刷新", width=80, height=40, corner_radius=10,
                                         font=self.F(12), fg_color=self.C("card2"),
                                         hover_color=self.C("hover"), text_color=self.C("text2"),
                                         border_width=0, command=self.refresh_plan)
        self.btn_refresh.pack(side="right", padx=(0, 10))

        # 表格
        wrap = ctk.CTkFrame(self.root, corner_radius=14, fg_color=self.C("card"),
                            border_width=1, border_color=self.C("line"))
        self.card_frames.append(wrap)
        wrap.pack(fill="both", expand=True, padx=P, pady=(0, 12))
        cols = ("name", "new", "status")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended",
                                 style="App.Treeview")
        for c, t, w in (("name", "原文件名", 360), ("new", "新文件名", 360), ("status", "状态", 160)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w", stretch=(c != "status"))
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview, style="App.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        vsb.pack(side="right", fill="y", pady=10, padx=(0, 6))

    def build_plain(self):
        """没装 customtkinter 时的兜底界面。"""
        f = tk.Frame(self.root, bg="#0F1115")
        f.pack(fill="both", expand=True)
        tk.Label(f, text=APP_TITLE, bg="#0F1115", fg="#E8EBF0",
                 font=(self.font_family, 22, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        tk.Button(f, text="添加文件", command=self.add_files).pack(anchor="w", padx=18)
        cols = ("name", "new", "status")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", style="App.Treeview")
        for c, t, w in (("name", "原文件名", 360), ("new", "新文件名", 360), ("status", "状态", 160)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=18, pady=10)
        tk.Label(f, textvariable=self.status_var, bg="#0F1115", fg="#9099A8").pack(anchor="w", padx=18)

    # ---------- 配色 ----------
    def apply_colors(self):
        c = self.C
        try:
            self.root.configure(fg_color=c("bg"))
        except Exception:
            self.root.configure(bg=c("bg"))
        for attr in ("title_lbl", "sub_lbl", "drop", "drop_icon", "drop_lbl", "drop_hint",
                     "lbl_ext", "lbl_conf", "tip_lbl", "hint_lbl", "list_lbl", "count_lbl",
                     "status_lbl", "lbl_rec", "btn_undo", "btn_refresh"):
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                if attr in ("title_lbl", "drop_lbl", "lbl_ext", "lbl_conf", "list_lbl"):
                    w.configure(text_color=c("text"))
                elif attr in ("sub_lbl", "drop_hint", "tip_lbl", "hint_lbl", "count_lbl",
                              "status_lbl", "lbl_rec"):
                    w.configure(text_color=c("sub"))
                elif attr in ("btn_undo", "btn_refresh"):
                    w.configure(text_color=c("text2"), fg_color=c("card2"),
                                hover_color=c("hover"))
                elif attr == "drop_icon":
                    w.configure(text_color=c("accent"))
                elif attr == "drop":
                    w.configure(fg_color=c("card"), border_color=c("accent"))
            except Exception:
                pass
        try:
            self.opt.configure(fg_color=c("card2"), button_color=c("card2"),
                               button_hover_color=c("line"), text_color=c("text"),
                               dropdown_fg_color=c("card"), dropdown_text_color=c("text"))
            self.ent_custom.configure(fg_color=c("card2"), border_color=c("line"), text_color=c("text"))
            self.seg.configure(fg_color=c("card2"), button_color=c("card2"),
                               button_hover_color=c("line"), text_color=c("text"),
                               dropdown_fg_color=c("card"), dropdown_text_color=c("text"))
            self.btn_run.configure(fg_color=c("accent"), hover_color=c("accent_h"))
            for frame in getattr(self, "card_frames", []):
                try:
                    frame.configure(fg_color=c("card"), border_color=c("line"))
                except Exception:
                    pass
        except Exception:
            pass

    def apply_tree_style(self):
        pal = L
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.layout("App.Treeview", [("App.Treeview.treearea", {"sticky": "nswe"})])
        style.configure("App.Treeview", background=pal["tree_bg"], fieldbackground=pal["tree_bg"],
                        foreground=pal["text"], rowheight=30, borderwidth=0,
                        font=self.F(11))
        style.configure("App.Treeview.Heading", background=pal["tree_head"],
                        foreground=pal["sub"], relief="flat", borderwidth=0,
                        font=self.F(11, "bold"))
        style.map("App.Treeview",
                  background=[("selected", pal["tree_sel"])],
                  foreground=[("selected", "#FFFFFF")])
        style.map("App.Treeview.Heading", background=[("active", pal["tree_head"])])
        style.configure("App.Vertical.TScrollbar",
                        troughcolor=pal["card"], background=pal["card2"],
                        bordercolor=pal["card"], darkcolor=pal["card2"],
                        lightcolor=pal["card2"], arrowcolor=pal["sub"],
                        borderwidth=0, width=10, relief="flat")
        style.map("App.Vertical.TScrollbar",
                  background=[("active", pal["accent"]), ("pressed", pal["accent"])])
        try:
            self.tree.tag_configure("skip", foreground="#A2AAB8")
            self.tree.tag_configure("warn", foreground="#E0A33E")
        except Exception:
            pass

    # ---------- 拖拽 ----------
    def on_drop(self, event):
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            paths = [p.strip("{}") for p in str(event.data).split()]
        self.add_paths(paths)

    # ---------- 数据 ----------
    def add_files(self):
        paths = filedialog.askopenfilenames(title="选择要改后缀的文件")
        if paths:
            self.add_paths(list(paths))

    def add_folder(self):
        path = filedialog.askdirectory(title="选择文件夹")
        if path:
            self.add_paths([path])

    def add_paths(self, paths):
        valid = [p for p in paths if os.path.exists(p)]
        if not valid:
            self.status_var.set("拖进来的路径不存在")
            return
        self.sources.extend(valid)
        self.rebuild()
        self.status_var.set("已添加 %d 项 · 共 %d 个文件" % (len(valid), len(self.all_files)))

    def rebuild(self):
        self.all_files = [f for f in collect_files(self.sources, recursive=self.recursive_var.get())
                          if os.path.normcase(f) not in self.excluded]
        self.refresh_plan()

    def remove_selected(self):
        idx = []
        for i in self.tree.selection():
            try:
                idx.append(int(i))
            except ValueError:
                pass
        for i in sorted(idx, reverse=True):
            if 0 <= i < len(self.plan):
                src = self.plan[i]["src"]
                self.excluded.add(os.path.normcase(src))
                self.all_files = [f for f in self.all_files
                                  if os.path.normcase(f) != os.path.normcase(src)]
        self.refresh_plan()

    def clear_all(self):
        self.sources, self.excluded, self.all_files, self.plan = [], set(), [], []
        self.render()
        self.status_var.set("已清空")

    # ---------- 后缀 ----------
    def on_preset(self, value):
        """选预设时收起输入框；选「自定义…」才出现输入框。"""
        if value == CUSTOM_LABEL:
            try:
                self.ent_custom.pack(side="left", padx=(0, 8), before=self.hint_lbl)
            except Exception:
                self.ent_custom.pack(side="left", padx=(0, 8))
            self.ent_custom.focus_set()
            self.hint_lbl.configure(text="在这里输入，带不带点都行")
        else:
            self.ent_custom.pack_forget()
            self.custom_var.set(value)
            self.hint_lbl.configure(text="选「自定义…」可以填别的后缀")
        self.refresh_plan()

    def on_ext_key(self, event=None):
        """手动输入时把下拉框切到“自定义…”。"""
        if self.preset_var.get() != CUSTOM_LABEL:
            self.preset_var.set(CUSTOM_LABEL)
        self.refresh_plan()

    def current_ext(self):
        return normalize_ext(self.custom_var.get())

    # ---------- 预览 ----------
    def refresh_plan(self):
        ext, err = self.current_ext()
        if err:
            self.plan = []
            self.render()
            self.status_var.set("后缀无效：" + err)
            return
        conflict = "skip" if self.conflict_var.get() == "跳过" else "suffix"
        self.plan = build_plan(self.all_files, ext, conflict=conflict)
        self.render()
        doable = sum(1 for p in self.plan if p["do"])
        self.status_var.set("共 %d 个文件 · 待改名 %d 个 · 跳过 %d 个"
                            % (len(self.plan), doable, len(self.plan) - doable))

    def render(self):
        self.tree.delete(*self.tree.get_children())
        for i, item in enumerate(self.plan[:MAX_ROWS]):
            tag = "skip" if not item["do"] else ("warn" if item["status"] == "自动编号" else "")
            self.tree.insert("", "end", iid=str(i),
                             values=(item["name"], item["new_name"], item["status"]),
                             tags=(tag,) if tag else ())
        if len(self.plan) > MAX_ROWS:
            self.tree.insert("", "end", values=("…", "只显示前 %d 行，其余照样会处理" % MAX_ROWS, ""))
        self.count_lbl.configure(text="%d 个文件" % len(self.all_files))

    # ---------- 执行 ----------
    def run(self):
        if not self.all_files:
            messagebox.showinfo(APP_TITLE, "请先添加文件。")
            return
        ext, err = self.current_ext()
        if err:
            messagebox.showwarning(APP_TITLE, "后缀无效：" + err)
            return
        doable = [p for p in self.plan if p["do"]]
        if not doable:
            messagebox.showinfo(APP_TITLE, "没有需要修改的文件。")
            return
        if not messagebox.askyesno(APP_TITLE,
                                   "把 %d 个文件的扩展名改成 %s？\n\n只改文件名后缀，不会转换文件内容。"
                                   % (len(doable), ext)):
            return

        done, failed, undo = execute_plan(self.plan)
        if undo:
            save_undo(undo)
        if failed:
            detail = "\n".join("· %s：%s" % (os.path.basename(f["src"]), f["reason"])
                               for f in failed[:10])
            messagebox.showwarning(APP_TITLE, "成功 %d 个，失败 %d 个：\n\n%s"
                                   % (done, len(failed), detail))
        else:
            messagebox.showinfo(APP_TITLE, "完成，成功改名 %d 个文件。" % done)

        mapping = {os.path.normcase(old): new for old, new in undo}
        self.sources = [mapping.get(os.path.normcase(s), s) for s in self.sources]
        self.excluded = {os.path.normcase(mapping.get(e, e)) for e in self.excluded}
        self.rebuild()
        self.status_var.set("上次操作：成功 %d 个，失败 %d 个 · 可点“撤销上一次”还原"
                            % (done, len(failed)))

    def undo_last(self):
        data = load_undo()
        if not data or not data.get("items"):
            messagebox.showinfo(APP_TITLE, "没有可撤销的记录。")
            return
        items = data["items"]
        if not messagebox.askyesno(APP_TITLE,
                                   "撤销 %s 的这次操作，把 %d 个文件改回原名？"
                                   % (data.get("time", "上一次"), len(items))):
            return
        ok, fail = 0, 0
        for old, new in reversed(items):
            try:
                if os.path.exists(new) and not os.path.exists(old):
                    os.rename(new, old)
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
        if fail:
            messagebox.showwarning(APP_TITLE,
                                   "已还原 %d 个，%d 个无法还原（原文件已存在或被占用）。" % (ok, fail))
        else:
            messagebox.showinfo(APP_TITLE, "已还原 %d 个文件。" % ok)

        back = {os.path.normcase(new): old for old, new in items}
        self.sources = [back.get(os.path.normcase(s), s) for s in self.sources]
        self.excluded = {os.path.normcase(back.get(e, e)) for e in self.excluded}
        try:
            os.remove(UNDO_FILE)
        except Exception:
            pass
        self.rebuild()
        self.status_var.set("已撤销 %d 个文件" % ok)


def main():
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
            try:
                windll.user32.SetProcessDpiAwarenessContext(-4)   # per-monitor v2
            except Exception:
                pass
        except Exception:
            pass

    root = Root()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        msg = traceback.format_exc()
        try:
            messagebox.showerror(APP_TITLE, msg)
        except Exception:
            print(msg)
