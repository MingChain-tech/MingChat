#!/usr/bin/env python3
"""
P2P Chat GUI — 对标 bsv-poker Chat tab
= tkinter 桌面界面，asyncio 后台线程桥接
= 身份管理、加密聊天、P2P 节点、SPV 同步、联系人管理
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, simpledialog
import asyncio
import queue
import threading
import json
import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("gui")

# ─── 常量 ─────────────────────────────────────────────────
DATA_DIR = os.path.expanduser("~/.p2pchat")
COLORS = {
    "bg": "#1a1a2e",
    "sidebar_bg": "#16213e",
    "chat_bg": "#0f3460",
    "input_bg": "#1a1a2e",
    "text": "#e0e0e0",
    "text_dim": "#8a8a8a",
    "accent": "#e94560",
    "accent2": "#00d4aa",
    "my_msg": "#533483",
    "their_msg": "#1a3a5c",
    "system_msg": "#2a2a3a",
    "online": "#00d4aa",
    "offline": "#555555",
    "highlight": "#e94560",
    "border": "#2a2a4a",
    "btn_bg": "#533483",
    "btn_fg": "#e0e0e0",
    "input_fg": "#e0e0e0",
    "danger": "#ff4444",
    "warning": "#ffaa00",
    "success": "#00d4aa",
}

# ─── asyncio ↔ tkinter 桥接 ─────────────────────────────
class AsyncBridge:
    """后台 asyncio 线程 + 线程安全队列桥接到 tkinter 主线程"""

    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._running = False
        # GUI ← async 消息队列
        self.gui_queue = queue.Queue()
        # async ← GUI 任务队列
        self.async_queue = queue.Queue()

    def start(self):
        """启动 asyncio 后台线程"""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run_loop(self):
        """后台 asyncio 事件循环"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()

        # 定期检查 GUI 发来的任务
        async def poll_async_queue():
            while self._running:
                try:
                    coro = self.async_queue.get_nowait()
                    await coro
                except queue.Empty:
                    await asyncio.sleep(0.05)

        self.loop.create_task(poll_async_queue())
        self.loop.run_forever()

    def stop(self):
        """停止 asyncio 事件循环"""
        self._running = False
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def run_coro(self, coro):
        """从 GUI 线程调度协程到 asyncio 线程"""
        self.async_queue.put(coro)

    def emit(self, event_type: str, data: dict = None):
        """从 asyncio 线程发送事件到 GUI 线程"""
        self.gui_queue.put({"type": event_type, "data": data or {}, "ts": time.time()})


# 全局桥接实例
bridge = AsyncBridge()


# ─── 身份设置对话框 ─────────────────────────────────────
class IdentityDialog(tk.Toplevel):
    """首次身份创建 / 导入对话框"""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("P2P Chat — 身份设置")
        self.geometry("500x420")
        self.resizable(False, False)
        self.configure(bg=COLORS["bg"])
        self.result = None

        # 居中
        self.transient(parent)
        self.grab_set()

        self._build_ui()

    def _build_ui(self):
        # 标题
        title = tk.Label(
            self, text="🔐 P2P Chat 身份设置",
            font=("Segoe UI", 16, "bold"),
            fg=COLORS["accent2"], bg=COLORS["bg"]
        )
        title.pack(pady=(20, 5))

        subtitle = tk.Label(
            self, text="你的身份密钥对存储在本地，绝不离开你的电脑",
            font=("Segoe UI", 9),
            fg=COLORS["text_dim"], bg=COLORS["bg"]
        )
        subtitle.pack(pady=(0, 20))

        # Tab 切换
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=20)

        # Tab 1: 新建身份
        frame_new = tk.Frame(notebook, bg=COLORS["bg"])
        notebook.add(frame_new, text="  新建身份  ")

        tk.Label(
            frame_new, text="选择你的昵称（handle）",
            font=("Segoe UI", 10),
            fg=COLORS["text"], bg=COLORS["bg"]
        ).pack(pady=(20, 5))

        self.new_handle = tk.Entry(
            frame_new, font=("Segoe UI", 12),
            bg=COLORS["sidebar_bg"], fg=COLORS["input_fg"],
            insertbackground=COLORS["text"],
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            width=30
        )
        self.new_handle.pack(pady=5, ipady=4, padx=20)
        self.new_handle.insert(0, "小马")
        self.new_handle.focus_set()

        tk.Label(
            frame_new,
            text="昵称只用于显示，你的真实身份是 secp256k1 公钥",
            font=("Segoe UI", 8),
            fg=COLORS["text_dim"], bg=COLORS["bg"]
        ).pack(pady=(5, 10))

        self.btn_create = tk.Button(
            frame_new, text="🚀 创建新身份",
            font=("Segoe UI", 11, "bold"),
            bg=COLORS["btn_bg"], fg=COLORS["btn_fg"],
            activebackground=COLORS["accent"],
            activeforeground="#fff",
            relief="flat", bd=0,
            padx=30, pady=8,
            cursor="hand2",
            command=self._create_identity
        )
        self.btn_create.pack(pady=15)

        # Tab 2: 导入现有身份
        frame_import = tk.Frame(notebook, bg=COLORS["bg"])
        notebook.add(frame_import, text="  导入身份  ")

        tk.Label(
            frame_import, text="从 ~/.p2pchat/ 加载已有身份文件",
            font=("Segoe UI", 10),
            fg=COLORS["text"], bg=COLORS["bg"]
        ).pack(pady=(20, 10))

        self.import_listbox = tk.Listbox(
            frame_import,
            font=("Consolas", 10),
            bg=COLORS["sidebar_bg"], fg=COLORS["text"],
            selectbackground=COLORS["accent"],
            selectforeground="#fff",
            relief="flat", bd=0,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            height=6, width=45
        )
        self.import_listbox.pack(pady=5)

        # 扫描已有身份
        self._scan_identities()

        self.btn_import = tk.Button(
            frame_import, text="📂 导入选中身份",
            font=("Segoe UI", 11, "bold"),
            bg=COLORS["btn_bg"], fg=COLORS["btn_fg"],
            activebackground=COLORS["accent"],
            activeforeground="#fff",
            relief="flat", bd=0,
            padx=30, pady=8,
            cursor="hand2",
            command=self._import_identity
        )
        self.btn_import.pack(pady=15)

        # 底部
        bottom = tk.Frame(self, bg=COLORS["bg"])
        bottom.pack(fill="x", padx=20, pady=15)

        self.status_label = tk.Label(
            bottom, text="",
            font=("Segoe UI", 9),
            fg=COLORS["warning"], bg=COLORS["bg"]
        )
        self.status_label.pack(side="left")

        tk.Button(
            bottom, text="❌ 退出",
            font=("Segoe UI", 10),
            bg=COLORS["sidebar_bg"], fg=COLORS["text_dim"],
            activebackground=COLORS["danger"],
            activeforeground="#fff",
            relief="flat", bd=0,
            padx=15, pady=4,
            cursor="hand2",
            command=self.destroy
        ).pack(side="right")

    def _scan_identities(self):
        """扫描 ~/.p2pchat/ 中的身份文件"""
        self.import_listbox.delete(0, tk.END)
        data_dir = Path(DATA_DIR)
        if not data_dir.exists():
            self.import_listbox.insert(tk.END, "  (未找到已有身份)")
            return
        found = False
        for f in sorted(data_dir.glob("identity_*.json")):
            handle = f.stem.replace("identity_", "")
            size_kb = f.stat().st_size / 1024
            self.import_listbox.insert(tk.END, f"  @{handle}  ({size_kb:.1f} KB)")
            found = True
        if not found:
            self.import_listbox.insert(tk.END, "  (未找到已有身份)")

    def _create_identity(self):
        handle = self.new_handle.get().strip().lstrip("@")
        if not handle:
            self.status_label.config(text="请输入昵称")
            return
        if " " in handle or len(handle) > 20:
            self.status_label.config(text="昵称不能含空格，最长20字符")
            return
        self.status_label.config(text="正在生成密钥对...", fg=COLORS["accent2"])
        self.update()
        try:
            from identity import Identity
            identity = Identity.create(handle)
            identity.save(str(Path(DATA_DIR) / f"identity_{handle}.json"))
            self.result = ("new", identity)
            self.status_label.config(text="✅ 身份创建成功！", fg=COLORS["success"])
            self.after(800, self.destroy)
        except Exception as e:
            self.status_label.config(text=f"❌ 创建失败: {e}", fg=COLORS["danger"])

    def _import_identity(self):
        sel = self.import_listbox.curselection()
        if not sel:
            self.status_label.config(text="请选择一个身份")
            return
        item = self.import_listbox.get(sel[0])
        # 提取 handle: "  @小马  (2.1 KB)" → "小马"
        import re
        match = re.search(r'@(\w+)', item)
        if not match:
            return
        handle = match.group(1)
        ident_path = Path(DATA_DIR) / f"identity_{handle}.json"
        if not ident_path.exists():
            self.status_label.config(text="身份文件不存在")
            return
        try:
            from identity import Identity
            identity = Identity.load(str(ident_path))
            self.result = ("import", identity)
            self.status_label.config(text="✅ 身份已加载！", fg=COLORS["success"])
            self.after(800, self.destroy)
        except Exception as e:
            self.status_label.config(text=f"❌ 加载失败: {e}", fg=COLORS["danger"])


# ─── 主窗口 ─────────────────────────────────────────────
class P2PChatGUI:
    """P2P Chat 主 GUI 窗口 — 对标 bsv-poker ChatView"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("P2P Chat")
        self.root.geometry("900x600")
        self.root.minsize(700, 450)
        self.root.configure(bg=COLORS["bg"])

        # 应用图标（Fallback）
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        self.chat_app = None        # P2PChat 实例
        self.identity = None        # Identity 实例
        self._chats = {}            # handle → scrolledtext widget
        self._active_chat = None    # 当前聊天对象
        self._gui_poll_id = None

        # 先隐藏主窗口，弹出身份设置
        self.root.withdraw()

        # 弹出身份设置
        self._setup_identity()

    def _setup_identity(self):
        """身份设置流程"""
        dialog = IdentityDialog(self.root)
        self.root.wait_window(dialog)

        if dialog.result is None:
            # 用户关闭了对话框
            self.root.destroy()
            return

        mode, identity = dialog.result
        self.identity = identity

        # 显示主窗口
        self.root.deiconify()
        self.root.title(f"P2P Chat — @{identity.handle}")

        # 构建 UI
        self._build_ui()

        # 启动 P2P 节点
        self._start_node()

    # ─── UI 构建 ───────────────────────────────────────

    def _build_ui(self):
        """构建完整的 GUI 布局"""
        # ── 菜单栏 ──
        menubar = tk.Menu(self.root, bg=COLORS["sidebar_bg"], fg=COLORS["text"],
                          activebackground=COLORS["accent"], activeforeground="#fff")
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0,
                            bg=COLORS["sidebar_bg"], fg=COLORS["text"],
                            activebackground=COLORS["accent"])
        file_menu.add_command(label="🆕 新建身份", command=self._new_identity)
        file_menu.add_command(label="📂 切换身份", command=self._switch_identity)
        file_menu.add_separator()
        file_menu.add_command(label="📋 导出公钥", command=self._export_pubkey)
        file_menu.add_separator()
        file_menu.add_command(label="❌ 退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        peer_menu = tk.Menu(menubar, tearoff=0,
                            bg=COLORS["sidebar_bg"], fg=COLORS["text"],
                            activebackground=COLORS["accent"])
        peer_menu.add_command(label="➕ 添加联系人", command=self._add_contact_dialog)
        peer_menu.add_command(label="🔗 连接节点", command=self._connect_peer_dialog)
        peer_menu.add_separator()
        peer_menu.add_command(label="📡 刷新离线消息", command=self._fetch_offline)
        menubar.add_cascade(label="节点", menu=peer_menu)

        help_menu = tk.Menu(menubar, tearoff=0,
                            bg=COLORS["sidebar_bg"], fg=COLORS["text"],
                            activebackground=COLORS["accent"])
        help_menu.add_command(label="📖 使用说明", command=self._show_help)
        help_menu.add_command(label="ℹ️ 关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        # ── 主容器 ──
        main_pw = tk.PanedWindow(self.root, orient=tk.HORIZONTAL,
                                 bg=COLORS["border"], sashwidth=2)
        main_pw.pack(fill="both", expand=True)

        # ── 左侧边栏 ──
        self.sidebar = tk.Frame(main_pw, bg=COLORS["sidebar_bg"], width=220)
        main_pw.add(self.sidebar, minsize=180)

        # 身份信息
        ident_frame = tk.Frame(self.sidebar, bg=COLORS["sidebar_bg"])
        ident_frame.pack(fill="x", padx=10, pady=(10, 5))

        tk.Label(
            ident_frame,
            text=f"🆔 @{self.identity.handle}",
            font=("Segoe UI", 13, "bold"),
            fg=COLORS["accent2"], bg=COLORS["sidebar_bg"]
        ).pack(anchor="w")

        self.pubkey_label = tk.Label(
            ident_frame,
            text=self.identity.pubkey_hex[:20] + "...",
            font=("Consolas", 8),
            fg=COLORS["text_dim"], bg=COLORS["sidebar_bg"]
        )
        self.pubkey_label.pack(anchor="w")

        tk.Frame(self.sidebar, bg=COLORS["border"], height=1).pack(fill="x", padx=10, pady=8)

        # 搜索框
        search_frame = tk.Frame(self.sidebar, bg=COLORS["sidebar_bg"])
        search_frame.pack(fill="x", padx=10, pady=(0, 5))

        self.search_var = tk.StringVar()
        self.search_var.trace("w", lambda *a: self._filter_chats())
        self.search_entry = tk.Entry(
            search_frame,
            textvariable=self.search_var,
            font=("Segoe UI", 10),
            bg=COLORS["chat_bg"], fg=COLORS["input_fg"],
            insertbackground=COLORS["text"],
            relief="flat", bd=0,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        self.search_entry.pack(fill="x", ipady=3)
        self.search_entry.insert(0, "🔍 搜索...")
        self.search_entry.bind("<FocusIn>", lambda e: self.search_entry.delete(0, tk.END))
        self.search_entry.bind("<FocusOut>", lambda e:
            self.search_entry.insert(0, "🔍 搜索...") if not self.search_var.get() else None)

        # 聊天列表标签
        self.chat_list_label = tk.Label(
            self.sidebar,
            text="💬 对话",
            font=("Segoe UI", 9, "bold"),
            fg=COLORS["text_dim"], bg=COLORS["sidebar_bg"]
        )
        self.chat_list_label.pack(anchor="w", padx=12, pady=(5, 2))

        # 聊天列表（可滚动）
        self.chat_list_frame = tk.Frame(self.sidebar, bg=COLORS["sidebar_bg"])
        self.chat_list_frame.pack(fill="both", expand=True, padx=5)

        tk.Frame(self.sidebar, bg=COLORS["border"], height=1).pack(fill="x", padx=10, pady=5)

        # 在线节点标签
        self.peers_label = tk.Label(
            self.sidebar,
            text="🌐 在线节点 (0)",
            font=("Segoe UI", 9, "bold"),
            fg=COLORS["text_dim"], bg=COLORS["sidebar_bg"]
        )
        self.peers_label.pack(anchor="w", padx=12, pady=(5, 2))

        # 节点列表
        self.peers_frame = tk.Frame(self.sidebar, bg=COLORS["sidebar_bg"])
        self.peers_frame.pack(fill="x", padx=5, pady=(0, 10))

        # ── 右侧聊天区 ──
        right_frame = tk.Frame(main_pw, bg=COLORS["bg"])
        main_pw.add(right_frame, minsize=400)

        # 聊天标题
        self.chat_title = tk.Label(
            right_frame,
            text="📋 选择一个对话开始聊天",
            font=("Segoe UI", 12, "bold"),
            fg=COLORS["text"], bg=COLORS["bg"],
            anchor="w"
        )
        self.chat_title.pack(fill="x", padx=15, pady=(12, 5))

        tk.Frame(right_frame, bg=COLORS["border"], height=1).pack(fill="x", padx=15)

        # 消息显示区
        self.chat_display = scrolledtext.ScrolledText(
            right_frame,
            font=("Segoe UI", 10),
            bg=COLORS["chat_bg"], fg=COLORS["text"],
            wrap=tk.WORD,
            state="disabled",
            relief="flat", bd=0,
            highlightthickness=0,
        )
        self.chat_display.pack(fill="both", expand=True, padx=15, pady=10)

        # 配置文本标签颜色
        self.chat_display.tag_config("system", foreground=COLORS["text_dim"], font=("Segoe UI", 9))
        self.chat_display.tag_config("me", foreground="#c8a8ff", font=("Segoe UI", 10))
        self.chat_display.tag_config("them", foreground=COLORS["accent2"], font=("Segoe UI", 10, "bold"))
        self.chat_display.tag_config("timestamp", foreground=COLORS["text_dim"], font=("Segoe UI", 7))
        self.chat_display.tag_config("content", foreground=COLORS["text"], font=("Segoe UI", 10))
        self.chat_display.tag_config("error", foreground=COLORS["danger"])
        self.chat_display.tag_config("success", foreground=COLORS["success"])

        # 输入区
        input_frame = tk.Frame(right_frame, bg=COLORS["bg"])
        input_frame.pack(fill="x", padx=15, pady=(0, 10))

        self.msg_input = tk.Text(
            input_frame,
            font=("Segoe UI", 11),
            bg=COLORS["input_bg"], fg=COLORS["input_fg"],
            insertbackground=COLORS["text"],
            relief="flat", bd=0,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            height=3,
            wrap=tk.WORD,
        )
        self.msg_input.pack(side="left", fill="x", expand=True)
        self.msg_input.bind("<Return>", self._on_send_key)
        self.msg_input.bind("<Shift-Return>", lambda e: None)  # Shift+Enter 换行

        send_btn = tk.Button(
            input_frame, text="发送 ➤",
            font=("Segoe UI", 10, "bold"),
            bg=COLORS["accent"], fg="#fff",
            activebackground=COLORS["accent2"],
            activeforeground="#fff",
            relief="flat", bd=0,
            padx=15,
            cursor="hand2",
            command=self._send_message
        )
        send_btn.pack(side="right", padx=(8, 0), fill="y")

        # ── 状态栏 ──
        status_frame = tk.Frame(self.root, bg=COLORS["sidebar_bg"], height=28)
        status_frame.pack(fill="x", side="bottom")
        status_frame.pack_propagate(False)

        self.status_text = tk.Label(
            status_frame,
            text="⏳ 正在启动节点...",
            font=("Segoe UI", 9),
            fg=COLORS["text_dim"], bg=COLORS["sidebar_bg"],
            anchor="w"
        )
        self.status_text.pack(side="left", padx=12, pady=3)

        self.spv_status = tk.Label(
            status_frame,
            text="",
            font=("Segoe UI", 9),
            fg=COLORS["text_dim"], bg=COLORS["sidebar_bg"],
            anchor="e"
        )
        self.spv_status.pack(side="right", padx=12, pady=3)

        # ── 事件绑定 ──
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── 启动 GUI 消息轮询 ──
        self._start_gui_poll()

    # ─── 节点启动 ──────────────────────────────────────

    def _start_node(self):
        """在后台异步启动 P2P 节点"""
        bridge.start()

        async def start_chat():
            from app import P2PChat
            chat = P2PChat(self.identity, host="127.0.0.1", port=0, data_dir=DATA_DIR)
            await chat.start("127.0.0.1", 0, sync_spv=True)
            return chat

        async def wrapper():
            try:
                self.chat_app = await start_chat()
                status = self.chat_app.status
                bridge.emit("node_started", {
                    "host": status["listening"],
                    "handle": status["handle"],
                    "pubkey": status["pubkey"],
                    "spv": status["spv"],
                })
            except Exception as e:
                bridge.emit("error", {"msg": f"启动失败: {e}"})
                log.exception("Node start failed")

        bridge.run_coro(wrapper())

    # ─── GUI 消息轮询 ─────────────────────────────────

    def _start_gui_poll(self):
        """启动主循环轮询 async→GUI 消息"""
        self._process_gui_queue()
        # 定期刷新状态
        self._refresh_status()

    def _process_gui_queue(self):
        """处理从 asyncio 线程发来的消息"""
        try:
            while True:
                msg = bridge.gui_queue.get_nowait()
                self._handle_gui_event(msg["type"], msg["data"])
        except queue.Empty:
            pass
        self._gui_poll_id = self.root.after(100, self._process_gui_queue)

    def _handle_gui_event(self, event_type: str, data: dict):
        """处理 GUI 事件"""
        if event_type == "node_started":
            self.status_text.config(text=f"🟢 在线 — {data['host']}")
            self.spv_status.config(text=f"🔗 SPV: 同步中...")
            log.info(f"Node started on {data['host']}")

        elif event_type == "spv_synced":
            headers = data.get("headers", 0)
            self.spv_status.config(text=f"🔗 SPV: {headers} headers ✓")
            log.info(f"SPV synced: {headers} headers")

        elif event_type == "message_received":
            self._display_received_message(data)

        elif event_type == "peer_joined":
            self._refresh_peers()
            self._add_chat_message(
                "📋 系统消息", f"🔗 {data.get('handle', '?')} 上线了",
                tag="system"
            )

        elif event_type == "peer_left":
            self._refresh_peers()

        elif event_type == "offline_message":
            self._display_received_message(data, offline=True)

        elif event_type == "error":
            self._add_chat_message(
                "⚠️ 系统", data.get("msg", "未知错误"), tag="error"
            )

        elif event_type == "contact_added":
            self._refresh_chat_list()

    def _display_received_message(self, data: dict, offline: bool = False):
        """在聊天区显示收到的消息"""
        from_handle = data.get("from", "?")
        content = data.get("content", "")
        prefix = "📩 [离线]" if offline else "💬"
        self._add_chat_message(
            f"{prefix} {from_handle}", content, tag="them"
        )
        # 更新聊天列表
        self._refresh_chat_list()

    # ─── 消息显示 ─────────────────────────────────────

    def _add_chat_message(self, sender: str, content: str, tag: str = "content"):
        """向聊天显示区添加一条消息"""
        self.chat_display.config(state="normal")

        # 分隔线
        if self.chat_display.get("1.0", "end-1c").strip():
            self.chat_display.insert(tk.END, "\n")

        # 时间戳
        ts = datetime.now().strftime("%H:%M")
        self.chat_display.insert(tk.END, f"{ts}  ", "timestamp")

        # 发送者
        self.chat_display.insert(tk.END, f"{sender}\n", tag)

        # 内容
        self.chat_display.insert(tk.END, f"{content}\n", "content")

        self.chat_display.config(state="disabled")
        self.chat_display.see(tk.END)

    def _append_system_msg(self, text: str):
        """添加系统消息"""
        self._add_chat_message("📋 系统消息", text, tag="system")

    # ─── 发送消息 ─────────────────────────────────────

    def _on_send_key(self, event):
        """Enter 键发送（Shift+Enter 换行）"""
        if not (event.state & 0x1):  # 无 Shift
            self._send_message()
            return "break"  # 阻止默认换行
        return None

    def _send_message(self):
        """发送当前输入的消息"""
        text = self.msg_input.get("1.0", "end-1c").strip()
        if not text:
            return

        target = self._active_chat
        if not target:
            self._append_system_msg("⚠️ 请先从左侧选择聊天对象")
            return

        if not self.chat_app:
            self._append_system_msg("⚠️ 节点尚未就绪")
            return

        # 清空输入
        self.msg_input.delete("1.0", tk.END)

        # 显示自己的消息
        self._add_chat_message(f"📤 @{self.identity.handle} → @{target}", text, tag="me")

        # 异步发送
        async def do_send():
            try:
                msg = await self.chat_app.send_message(target, text)
                if msg:
                    bridge.emit("message_sent", {
                        "to": target,
                        "content": text,
                    })
                else:
                    bridge.emit("error", {"msg": f"发送失败：未找到联系人 @{target}"})
            except Exception as e:
                bridge.emit("error", {"msg": f"发送错误: {e}"})

        bridge.run_coro(do_send())

    # ─── 命令处理 ─────────────────────────────────────

    def _handle_command(self, text: str):
        """处理 /开头的命令"""
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()

        if cmd == "/add" and len(parts) >= 3:
            handle = parts[1].lstrip("@")
            pubkey = parts[2]
            if self.chat_app and self.chat_app.identity:
                self.chat_app.identity.add_contact(handle, pubkey)
                self._append_system_msg(f"✅ 已添加联系人 @{handle}")
                self._refresh_chat_list()

        elif cmd == "/connect" and len(parts) >= 2:
            addr = parts[1]
            if ":" in addr:
                host, port_str = addr.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    self._append_system_msg("⚠️ 端口号格式错误")
                    return
            else:
                host = addr
                port = 9876

            async def do_connect():
                success = await self.chat_app.connect_peer(host, port)
                if success:
                    bridge.emit("peer_joined", {"handle": f"{host}:{port}"})
                else:
                    bridge.emit("error", {"msg": f"连接 {host}:{port} 失败"})

            bridge.run_coro(do_connect())
            self._append_system_msg(f"🔗 正在连接 {host}:{port}...")

        elif cmd == "/peers":
            if self.chat_app:
                peers = list(self.chat_app.mesh._peers.values())
                if peers:
                    lines = ["🌐 在线节点:"]
                    for p in peers:
                        lines.append(f"  · @{p.handle or '?'} ({p.addr})")
                    self._append_system_msg("\n".join(lines))
                else:
                    self._append_system_msg("🌐 暂无连接节点")
            else:
                self._append_system_msg("⚠️ 节点未运行")

        elif cmd == "/spv":
            if self.chat_app:
                s = self.chat_app.status["spv"]
                self._append_system_msg(
                    f"🔗 SPV 状态:\n"
                    f"  网络: {s['network']}\n"
                    f"  同步: {'✅ 完成' if s['synced'] else '⏳ 进行中'}\n"
                    f"  区块头: {s['headers']}\n"
                    f"  链尖: {s['tip_hash']}"
                )
            else:
                self._append_system_msg("⚠️ SPV 未运行")

        elif cmd == "/offline":
            self._append_system_msg("📡 正在扫描链上离线消息...")
            async def do_fetch():
                try:
                    msgs = await self.chat_app.fetch_offline_now()
                    if msgs:
                        count = len(msgs)
                        bridge.emit("error", {"msg": f"发现 {count} 条链上消息，正在验证..."})
                    else:
                        bridge.emit("error", {"msg": "没有新的链上消息"})
                except Exception as e:
                    bridge.emit("error", {"msg": f"扫描失败: {e}"})
            bridge.run_coro(do_fetch())

        elif cmd == "/help":
            self._append_system_msg(
                "📖 可用命令:\n"
                "  /add @昵称 <公钥hex>  — 添加联系人\n"
                "  /connect <ip:port>     — 连接节点\n"
                "  /peers                 — 查看在线节点\n"
                "  /spv                   — SPV 同步状态\n"
                "  /offline               — 扫描离线消息\n"
                "  /broadcast <消息>      — 广播消息\n"
                "  /help                  — 显示帮助"
            )

        elif cmd == "/broadcast" and len(parts) >= 2:
            content = parts[1]
            async def do_broadcast():
                await self.chat_app.send_broadcast(content)
            bridge.run_coro(do_broadcast())
            self._append_system_msg(f"📢 广播已发送: {content[:50]}")

        else:
            self._append_system_msg(f"⚠️ 未知命令: {cmd}。输入 /help 查看可用命令。")

    # ─── 联系人 / 节点 ────────────────────────────────

    def _add_contact_dialog(self):
        """添加联系人对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("添加联系人")
        dialog.geometry("420x200")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog, text="添加联系人",
            font=("Segoe UI", 12, "bold"),
            fg=COLORS["accent2"], bg=COLORS["bg"]
        ).pack(pady=(15, 10))

        row1 = tk.Frame(dialog, bg=COLORS["bg"])
        row1.pack(fill="x", padx=20)
        tk.Label(row1, text="昵称 @", font=("Segoe UI", 10),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(side="left")
        handle_entry = tk.Entry(row1, font=("Segoe UI", 11),
                                bg=COLORS["sidebar_bg"], fg=COLORS["input_fg"],
                                insertbackground=COLORS["text"],
                                relief="flat", highlightthickness=1,
                                highlightbackground=COLORS["border"])
        handle_entry.pack(side="left", fill="x", expand=True, padx=(5, 0), ipady=2)

        row2 = tk.Frame(dialog, bg=COLORS["bg"])
        row2.pack(fill="x", padx=20, pady=(10, 0))
        tk.Label(row2, text="公钥  ", font=("Segoe UI", 10),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(side="left")
        pk_entry = tk.Entry(row2, font=("Consolas", 10),
                            bg=COLORS["sidebar_bg"], fg=COLORS["input_fg"],
                            insertbackground=COLORS["text"],
                            relief="flat", highlightthickness=1,
                            highlightbackground=COLORS["border"])
        pk_entry.pack(side="left", fill="x", expand=True, padx=(5, 0), ipady=2)

        def do_add():
            handle = handle_entry.get().strip().lstrip("@")
            pk = pk_entry.get().strip()
            if not handle or not pk:
                return
            if self.chat_app and self.chat_app.identity:
                self.chat_app.identity.add_contact(handle, pk)
                self._append_system_msg(f"✅ 联系人 @{handle} 已添加")
                self._refresh_chat_list()
            dialog.destroy()

        tk.Button(
            dialog, text="✅ 添加",
            font=("Segoe UI", 10, "bold"),
            bg=COLORS["btn_bg"], fg=COLORS["btn_fg"],
            activebackground=COLORS["accent2"],
            relief="flat", bd=0, padx=20, pady=5,
            cursor="hand2", command=do_add
        ).pack(pady=15)

        handle_entry.focus_set()

    def _connect_peer_dialog(self):
        """连接节点对话框"""
        addr = simpledialog.askstring(
            "连接节点", "输入节点地址 (ip:port):",
            parent=self.root
        )
        if addr:
            self.msg_input.delete("1.0", tk.END)
            self.msg_input.insert("1.0", f"/connect {addr}")
            self._send_message()

    def _fetch_offline(self):
        """手动获取离线消息"""
        self._handle_command("/offline")

    def _new_identity(self):
        """新建身份（重启）"""
        if messagebox.askyesno("新建身份", "这将关闭当前节点并创建新身份，确定吗？"):
            self._on_close()
            # 重新启动
            os.execl(sys.executable, sys.executable, *sys.argv)

    def _switch_identity(self):
        """切换身份（重启）"""
        self._new_identity()

    def _export_pubkey(self):
        """导出公钥"""
        if self.identity:
            pk = self.identity.pubkey_hex
            self.root.clipboard_clear()
            self.root.clipboard_append(pk)
            self._append_system_msg(f"📋 公钥已复制到剪贴板:\n{pk[:40]}...")

    def _show_help(self):
        self._handle_command("/help")

    def _show_about(self):
        messagebox.showinfo(
            "关于 P2P Chat",
            "P2P Chat v1.0\n\n"
            "去中心化点对点加密即时通讯\n\n"
            "🔐 ECDH + AES-256-GCM 端到端加密\n"
            "🌐 P2P gossip 网格传输\n"
            "🔗 SPV 轻节点链上验证\n"
            "📜 BSV 链上消息存证\n\n"
            "对标 bsv-poker 架构\n"
            "技术栈: Python + tkinter + asyncio"
        )

    # ─── 聊天列表管理 ────────────────────────────────

    def _refresh_chat_list(self):
        """刷新左侧聊天列表"""
        for widget in self.chat_list_frame.winfo_children():
            widget.destroy()

        if not self.chat_app:
            return

        # 获取联系人
        contacts = list(self.chat_app.identity.contacts.keys())

        # 也添加有消息历史的联系人
        from collections import Counter
        handles = Counter()
        for msg in self.chat_app.store.messages:
            if msg.from_handle != self.identity.handle:
                handles[msg.from_handle] += 1
            if msg.to_handle and msg.to_handle != self.identity.handle:
                handles[msg.to_handle] += 1

        all_handles = set(contacts) | set(handles.keys())
        if self.identity.handle in all_handles:
            all_handles.remove(self.identity.handle)

        for handle in sorted(all_handles):
            unread = handles.get(handle, 0)
            label_text = f"@{handle}"
            if unread > 0:
                label_text += f" ({unread})"

            lbl = tk.Label(
                self.chat_list_frame,
                text=label_text,
                font=("Segoe UI", 10),
                fg=COLORS["text"], bg=COLORS["sidebar_bg"],
                anchor="w", padx=10, pady=4,
                cursor="hand2",
            )
            lbl.pack(fill="x")
            lbl.bind("<Button-1>", lambda e, h=handle: self._select_chat(h))
            lbl.bind("<Enter>", lambda e, w=lbl: w.configure(bg=COLORS["chat_bg"]))
            lbl.bind("<Leave>", lambda e, w=lbl: w.configure(bg=COLORS["sidebar_bg"]))

        if not all_handles:
            empty = tk.Label(
                self.chat_list_frame,
                text="(暂无联系人)\n用 /add 添加",
                font=("Segoe UI", 9),
                fg=COLORS["text_dim"], bg=COLORS["sidebar_bg"],
                justify="center"
            )
            empty.pack(pady=20)

    def _select_chat(self, handle: str):
        """选中一个聊天"""
        self._active_chat = handle
        self.chat_title.config(text=f"💬 @{handle}")
        self.chat_display.config(state="normal")
        self.chat_display.delete("1.0", tk.END)

        # 加载历史消息
        if self.chat_app:
            msgs = self.chat_app.store.get_for(handle, limit=50)
            for msg in msgs:
                ts = datetime.fromtimestamp(msg.timestamp).strftime("%H:%M")
                sender = f"📤 @{msg.from_handle}" if msg.from_handle == self.identity.handle else f"📥 @{msg.from_handle}"
                tag = "me" if msg.from_handle == self.identity.handle else "them"
                self.chat_display.insert(tk.END, f"{ts}  ", "timestamp")
                self.chat_display.insert(tk.END, f"{sender}\n", tag)
                self.chat_display.insert(tk.END, f"{msg.content}\n\n", "content")

        self.chat_display.config(state="disabled")
        self.chat_display.see(tk.END)

        # 更新高亮
        for widget in self.chat_list_frame.winfo_children():
            widget.configure(bg=COLORS["sidebar_bg"])

    def _filter_chats(self):
        """搜索过滤聊天列表"""
        query = self.search_var.get().strip().lower()
        if query in ("", "🔍 搜索..."):
            self._refresh_chat_list()
            return

        for widget in self.chat_list_frame.winfo_children():
            if hasattr(widget, "cget"):
                text = widget.cget("text").lower()
                if query in text:
                    widget.pack(fill="x")
                else:
                    widget.pack_forget()

    def _refresh_peers(self):
        """刷新在线节点列表"""
        for widget in self.peers_frame.winfo_children():
            widget.destroy()

        if not self.chat_app:
            return

        peers = list(self.chat_app.mesh._peers.values())
        self.peers_label.config(text=f"🌐 在线节点 ({len(peers)})")

        for peer in peers:
            handle = peer.handle or "?"
            addr = getattr(peer, 'addr', '?')
            frame = tk.Frame(self.peers_frame, bg=COLORS["sidebar_bg"])
            frame.pack(fill="x", padx=5, pady=1)

            # 在线指示灯
            indicator = tk.Canvas(frame, width=8, height=8,
                                  bg=COLORS["sidebar_bg"], highlightthickness=0)
            indicator.create_oval(1, 1, 7, 7, fill=COLORS["online"], outline="")
            indicator.pack(side="left", padx=(5, 5))

            tk.Label(
                frame,
                text=f"@{handle}",
                font=("Segoe UI", 9),
                fg=COLORS["accent2"], bg=COLORS["sidebar_bg"]
            ).pack(side="left")

            if addr and addr != "?":
                tk.Label(
                    frame,
                    text=f"  {addr}",
                    font=("Segoe UI", 7),
                    fg=COLORS["text_dim"], bg=COLORS["sidebar_bg"]
                ).pack(side="right", padx=5)

    def _refresh_status(self):
        """定期刷新状态栏"""
        if self.chat_app:
            s = self.chat_app.status
            if s["spv"]["synced"]:
                self.spv_status.config(
                    text=f"🔗 SPV: {s['spv']['headers']} headers ✓"
                )
            else:
                self.spv_status.config(
                    text=f"🔗 SPV: 同步中 ({s['spv']['headers']})"
                )
        self.root.after(5000, self._refresh_status)

    # ─── 生命周期 ─────────────────────────────────────

    def _on_close(self):
        """关闭应用"""
        async def shutdown():
            if self.chat_app:
                await self.chat_app.stop()
            bridge.stop()

        if self.chat_app:
            # 异步关闭
            bridge.run_coro(shutdown())
            self.root.after(500, self._force_close)
        else:
            self._force_close()

    def _force_close(self):
        """强制关闭"""
        if self._gui_poll_id:
            self.root.after_cancel(self._gui_poll_id)
        try:
            bridge.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        """启动 GUI 主循环"""
        self.root.mainloop()


# ─── 入口 ─────────────────────────────────────────────────
def main():
    """启动 P2P Chat GUI"""
    # 确保数据目录存在
    os.makedirs(DATA_DIR, exist_ok=True)

    app = P2PChatGUI()
    app.run()


if __name__ == "__main__":
    main()
