"""Small Tk GUI, modelled after waterRPA's start/stop/log workflow."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from douyin_automation_dual_store_v49 import AutomationRunner, ROOT, VERSION, append_urls, load_config
from recording_store import RecordingStore


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"多平台商品草稿自动上款 - Python {VERSION}")
        self.geometry("980x720")
        self.minsize(700, 480)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.record_commands: queue.Queue[str] = queue.Queue()
        self.recording_active = False
        self.pending_recording: tuple[str, str] | None = None
        self.store_mode_var = tk.StringVar(value=str(load_config(ROOT).get("storeMode", "dual")))
        self.destination_var = tk.StringVar(value="douyin")
        self._build()
        self.after(100, self._drain_events)

    def _build(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        self.start_button = ttk.Button(top, text="开始处理", command=lambda: self.start(False))
        self.start_button.pack(side="left")
        self.login_button = ttk.Button(top, text="登录环境", command=lambda: self.start(True))
        self.login_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(top, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        ttk.Label(top, text="上款模式：").pack(side="left", padx=(18, 4))
        ttk.Radiobutton(top, text="单抖店", variable=self.store_mode_var, value="single").pack(side="left")
        ttk.Radiobutton(top, text="双抖店", variable=self.store_mode_var, value="dual").pack(side="left")
        ttk.Button(top, text="打开工作目录", command=self.open_work).pack(side="left", padx=(24, 0))
        self.status = ttk.Label(top, text=f"就绪 · {VERSION}")
        self.status.pack(side="right")
        info = "粘贴淘宝链接并选择目的站点；支持抖店、搜款网、17网，所有流程只保存草稿。"
        ttk.Label(self, text=info, padding=(10, 0)).pack(anchor="w")
        destination = ttk.Frame(self, padding=(10, 6, 10, 0)); destination.pack(fill="x")
        ttk.Label(destination, text="目的上架网站：").pack(side="left")
        for text, value in (("抖店", "douyin"), ("搜款网", "vvic"), ("17网", "zwd17")):
            ttk.Radiobutton(destination, text=text, variable=self.destination_var, value=value).pack(side="left", padx=4)
        self._build_recording_panel()
        wash_frame = ttk.Frame(self, padding=(10, 8, 10, 0))
        wash_frame.pack(fill="x")
        ttk.Label(wash_frame, text="洗水标路径：").pack(side="left")
        configured = load_config(ROOT).get("fixedWashLabelImage", r"E:\Try-onApp\wash-lable.jpg")
        self.wash_label_var = tk.StringVar(value=str(configured))
        self.wash_label_entry = ttk.Entry(wash_frame, textvariable=self.wash_label_var)
        self.wash_label_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(wash_frame, text="选择本地文件", command=self.choose_wash_label).pack(side="left")
        extension_frame = ttk.Frame(self, padding=(10, 8, 10, 0))
        extension_frame.pack(fill="x")
        ttk.Label(extension_frame, text="淘宝扩展目录：").pack(side="left")
        extension_configured = load_config(ROOT).get("browserExtensionDir", "")
        self.extension_dir_var = tk.StringVar(value=str(extension_configured))
        self.extension_dir_entry = ttk.Entry(extension_frame, textvariable=self.extension_dir_var)
        self.extension_dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(extension_frame, text="选择扩展目录", command=self.choose_extension_dir).pack(side="left")
        self.log = tk.Text(self, wrap="word", state="disabled", font=("Microsoft YaHei UI", 10))
        self.log.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        append_frame = ttk.LabelFrame(self, text="追加任务（运行中提交，当前任务完成后处理）", padding=6)
        append_frame.pack(fill="x", padx=10, pady=4)
        self.append_text = tk.Text(append_frame, height=3, wrap="none")
        self.append_text.pack(side="left", fill="x", expand=True)
        ttk.Button(append_frame, text="追加 T 恤网址", command=lambda: self.append_tasks("tshirt")).pack(side="left", padx=(8, 0))
        ttk.Button(append_frame, text="追加针织衫网址", command=lambda: self.append_tasks("knitwear")).pack(side="left", padx=(6, 0))
        self.queue_status = ttk.Label(append_frame, text="追加队列：未读取")
        self.queue_status.pack(side="left", padx=8)
        result_frame = ttk.LabelFrame(self, text="本轮失败任务", padding=4)
        result_frame.pack(fill="both", expand=False, padx=10, pady=(4, 10))
        self.failures = ttk.Treeview(result_frame, columns=("url", "category", "destination", "reason"), show="headings", height=4)
        for key, title, width in (("url", "网址", 300), ("category", "类别", 80), ("destination", "目的站点", 90), ("reason", "失败原因", 360)):
            self.failures.heading(key, text=title); self.failures.column(key, width=width, anchor="w")
        self.failures.pack(fill="x")

    def _build_recording_panel(self) -> None:
        panel = ttk.LabelFrame(self, text="搜款网 / 17网操作录制", padding=6)
        panel.pack(fill="x", padx=10, pady=(6, 0))
        ttk.Label(panel, text="录制类别：").pack(side="left")
        self.record_category_var = tk.StringVar(value="tshirt")
        self.record_category = ttk.Combobox(panel, textvariable=self.record_category_var, state="readonly", width=10,
                                            values=("tshirt", "knitwear"))
        self.record_category.pack(side="left", padx=(0, 8))
        self.record_start = ttk.Button(panel, text="开始录制", command=self.start_recording)
        self.record_start.pack(side="left")
        self.record_pause = ttk.Button(panel, text="暂停", command=lambda: self.record_command("pause"), state="disabled")
        self.record_pause.pack(side="left", padx=3)
        self.record_resume = ttk.Button(panel, text="继续", command=lambda: self.record_command("resume"), state="disabled")
        self.record_resume.pack(side="left")
        self.record_finish = ttk.Button(panel, text="完成录制", command=lambda: self.record_command("finish"), state="disabled")
        self.record_finish.pack(side="left", padx=3)
        self.record_cancel = ttk.Button(panel, text="取消", command=lambda: self.record_command("cancel"), state="disabled")
        self.record_cancel.pack(side="left")
        self.record_status = ttk.Label(panel, text="")
        self.record_status.pack(side="left", padx=10)
        self.refresh_recording_status()

    def refresh_recording_status(self) -> None:
        store = AutomationRunner(ROOT).recording_store()
        labels = {("vvic", "tshirt"): "搜T", ("vvic", "knitwear"): "搜针",
                  ("zwd17", "tshirt"): "17T", ("zwd17", "knitwear"): "17针"}
        parts = []
        for status in store.list_statuses():
            mark = "可用" if status.compatible else "损坏" if status.exists else "未录"
            parts.append(f"{labels[(status.site, status.category)]}:{mark}")
        self.record_status.config(text="  ".join(parts))

    def start_recording(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("当前忙碌", "请先等待当前任务或录制结束。")
            return
        site = self.destination_var.get()
        if site == "douyin":
            messagebox.showinfo("无需录制", "抖店继续使用原有自动化流程，请选择搜款网或17网。")
            return
        category = self.record_category_var.get()
        self.stop_event.clear(); self.recording_active = True
        while not self.record_commands.empty():
            self.record_commands.get_nowait()
        self._set_recording_controls("recording")
        self._set_running(True, "录制中")

        def work() -> None:
            try:
                runner = AutomationRunner(ROOT, lambda text: self.events.put(("log", text)), self.stop_event)
                asyncio.run(runner.record_operations(site, category, self.record_commands))
                self.events.put(("record_done", "录制结束"))
            except Exception as error:
                self.events.put(("record_error", str(error)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def record_command(self, command: str) -> None:
        if not self.recording_active:
            return
        self.record_commands.put(command)
        if command == "pause": self._set_recording_controls("paused")
        elif command == "resume": self._set_recording_controls("recording")
        elif command in {"finish", "cancel"}: self._set_recording_controls("finishing")

    def _set_recording_controls(self, state: str) -> None:
        self.record_start.config(state="disabled" if state != "idle" else "normal")
        self.record_pause.config(state="normal" if state == "recording" else "disabled")
        self.record_resume.config(state="normal" if state == "paused" else "disabled")
        terminal = "normal" if state in {"recording", "paused"} else "disabled"
        self.record_finish.config(state=terminal); self.record_cancel.config(state=terminal)

    def start(self, login_only: bool) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.save_local_paths(validate_wash=not login_only):
            return
        self.stop_event.clear()
        self._set_running(True, "等待登录" if login_only else "运行中")

        def work() -> None:
            try:
                runner = AutomationRunner(ROOT, lambda text: self.events.put(("log", text)), self.stop_event)
                asyncio.run(runner.run(login_only=login_only))
                self.events.put(("done", "已停止" if self.stop_event.is_set() else "完成"))
            except Exception as error:
                self.events.put(("error", str(error)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.status.config(text="正在停止…")
        self._append("已请求停止；当前浏览器操作结束后安全退出。")

    def append_tasks(self, category: str) -> None:
        lines = [line.strip() for line in self.append_text.get("1.0", "end").splitlines() if line.strip()]
        if not lines:
            messagebox.showwarning("没有网址", "请先粘贴一个或多个商品网址。")
            return
        path = Path(load_config(ROOT)["queuePath"])
        added, invalid = append_urls(path, lines, category, self.destination_var.get())
        self.append_text.delete("1.0", "end")
        self.queue_status.config(text=f"追加队列：新增 {added} 条")
        label = "T 恤" if category == "tshirt" else "针织衫"
        self._append(f"已加入{label}任务 {added} 条，目的站点：{self.destination_var.get()}。")
        if invalid:
            messagebox.showwarning("部分网址无效", "以下内容不是有效 HTTP/HTTPS 网址：\n" + "\n".join(invalid))

    def _set_running(self, running: bool, status: str) -> None:
        self.start_button.config(state="disabled" if running else "normal")
        self.login_button.config(state="disabled" if running else "normal")
        self.stop_button.config(state="normal" if running else "disabled")
        for child in self.winfo_children():
            if isinstance(child, ttk.Frame):
                for widget in child.winfo_children():
                    if isinstance(widget, ttk.Radiobutton):
                        widget.config(state="disabled" if running else "normal")
        self.status.config(text=status)
        self.wash_label_entry.config(state="disabled" if running else "normal")
        self.extension_dir_entry.config(state="disabled" if running else "normal")

    def _append(self, text: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "log":
                    self._append(text)
                    if text.startswith("RECORDING_RERECORD_REQUIRED "):
                        try:
                            notice = json.loads(text.split(" ", 1)[1])
                            site_name = {"vvic": "搜款网", "zwd17": "17网"}.get(notice["site"], notice["site"])
                            category_name = "T 恤" if notice["category"] == "tshirt" else "针织衫"
                            prompt = (f"{site_name} / {category_name} 的步骤 {notice['step_id']} 已连续失败"
                                      f" {notice['count']} 条任务。\n最近原因：{notice.get('reason','')}\n\n是否在本轮结束后立即重新录制？")
                            if messagebox.askyesno("建议重新录制", prompt):
                                self.pending_recording = (notice["site"], notice["category"])
                        except Exception as error:
                            self._append(f"重新录制提醒解析失败：{error}")
                    match = __import__("re").search(r"任务失败：\[([^\]]+)\]\[([^\]]+)\]\s+(https?://\S+)：(.+)$", text)
                    if match:
                        category = "T 恤" if match.group(1) == "tshirt" else "针织衫"
                        destination = {"douyin": "抖店", "vvic": "搜款网", "zwd17": "17网"}.get(match.group(2), match.group(2))
                        self.failures.insert("", "end", values=(match.group(3), category, destination, match.group(4)))
                elif kind == "done":
                    self._set_running(False, text)
                    self._append(text)
                    if self.pending_recording:
                        site, category = self.pending_recording; self.pending_recording = None
                        self.destination_var.set(site); self.record_category_var.set(category)
                        self.after(300, self.start_recording)
                elif kind == "record_done":
                    self.recording_active = False
                    self._set_running(False, text); self._set_recording_controls("idle")
                    self.refresh_recording_status(); self._append(text)
                elif kind == "record_error":
                    self.recording_active = False
                    self._set_running(False, "录制失败"); self._set_recording_controls("idle")
                    self.refresh_recording_status(); self._append("录制失败：" + text)
                    messagebox.showerror("录制失败", text)
                else:
                    self._set_running(False, "失败")
                    self._append("运行失败：" + text)
                    messagebox.showerror("运行失败", text)
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _config_path(self, key: str) -> Path:
        return Path(load_config(ROOT)[key])

    def choose_wash_label(self) -> None:
        filename = filedialog.askopenfilename(
            title="选择洗水标图片",
            initialdir=str(Path(self.wash_label_var.get()).parent),
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.webp *.bmp"), ("所有文件", "*.*")],
        )
        if filename:
            self.wash_label_var.set(filename)

    def save_wash_label_path(self) -> bool:
        image = Path(self.wash_label_var.get().strip())
        if not image.is_file():
            messagebox.showerror("洗水标图片不存在", f"找不到本地图片：\n{image}")
            return False
        config_path = ROOT / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        config["storeMode"] = self.store_mode_var.get()
        config["fixedWashLabelImage"] = str(image.resolve())
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True

    def choose_extension_dir(self) -> None:
        initial = self.extension_dir_var.get().strip() or str(ROOT)
        directory = filedialog.askdirectory(title="选择包含 manifest.json 的扩展目录", initialdir=initial)
        if directory:
            self.extension_dir_var.set(directory)

    def save_local_paths(self, validate_wash: bool) -> bool:
        from douyin_automation_dual_store_v49 import extension_manifest_dir

        if validate_wash:
            image = Path(self.wash_label_var.get().strip())
            if not image.is_file():
                messagebox.showerror("洗水标图片不存在", f"找不到本地图片：\n{image}")
                return False
        extension_value = self.extension_dir_var.get().strip()
        if extension_value:
            try:
                extension_value = str(extension_manifest_dir(extension_value))
            except RuntimeError as error:
                messagebox.showerror("扩展目录无效", str(error))
                return False
        config_path = ROOT / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        config["storeMode"] = self.store_mode_var.get()
        if self.wash_label_var.get().strip():
            config["fixedWashLabelImage"] = str(Path(self.wash_label_var.get().strip()).resolve())
        config["browserExtensionDir"] = extension_value
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True

    def open_excel(self) -> None:
        path = self._config_path("excelPath")
        if not path.exists():
            from douyin_automation_dual_store_v49 import ExcelStore
            try:
                ExcelStore(path).load()
            except RuntimeError:
                pass
        import os
        os.startfile(path)

    def open_work(self) -> None:
        path = self._config_path("workDir")
        path.mkdir(parents=True, exist_ok=True)
        import os
        os.startfile(path)


if __name__ == "__main__":
    App().mainloop()
