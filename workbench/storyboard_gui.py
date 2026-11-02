# -*- coding: utf-8 -*-
"""分镜预览与候选替换窗口。候选直接打开本地完整素材预览。"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import storyboard
from atomic_io import atomic_write_json
from runtime_context import new_job_id


class StoryboardWindow(tk.Tk):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.title('源流 · 分镜预览与候选替换')
        self.geometry('1040x680')
        self.minsize(900, 560)
        self.configure(bg='#EEF4FB')
        self.plan = None
        self.scene_no = 1
        self._build()
        self.after(100, self._create_plan)

    def _build(self):
        top = tk.Frame(self, bg='#123158', height=58); top.pack(fill='x')
        tk.Label(top, text='分镜预览与候选替换', bg='#123158', fg='white',
                 font=('Microsoft YaHei UI', 17, 'bold')).pack(side='left', padx=18, pady=13)
        self.status = tk.StringVar(value='正在分析脚本与完整素材库…')
        tk.Label(top, textvariable=self.status, bg='#123158', fg='#B9D8FF',
                 font=('Microsoft YaHei UI', 9)).pack(side='right', padx=18)
        body = tk.PanedWindow(self, orient='horizontal', bg='#D9E5F3', sashwidth=5)
        body.pack(fill='both', expand=True, padx=14, pady=14)
        left = tk.Frame(body, bg='white'); right = tk.Frame(body, bg='white')
        body.add(left, minsize=390); body.add(right, minsize=430)
        tk.Label(left, text='分镜', bg='white', fg='#152238',
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', padx=14, pady=(12, 6))
        self.scenes = ttk.Treeview(left, columns=('type', 'material', 'score'), show='tree headings')
        self.scenes.heading('#0', text='文案'); self.scenes.heading('type', text='类型')
        self.scenes.heading('material', text='当前素材'); self.scenes.heading('score', text='分数')
        self.scenes.column('#0', width=190); self.scenes.column('type', width=72)
        self.scenes.column('material', width=150); self.scenes.column('score', width=48, anchor='center')
        self.scenes.pack(fill='both', expand=True, padx=12, pady=(0, 12))
        self.scenes.bind('<<TreeviewSelect>>', self._scene_changed)
        tk.Label(right, text='候选素材', bg='white', fg='#152238',
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', padx=14, pady=(12, 6))
        self.cands = ttk.Treeview(right, columns=('score', 'duration', 'reason'), show='tree headings')
        self.cands.heading('#0', text='文件'); self.cands.heading('score', text='分数')
        self.cands.heading('duration', text='时长'); self.cands.heading('reason', text='排序理由')
        self.cands.column('#0', width=220); self.cands.column('score', width=48, anchor='center')
        self.cands.column('duration', width=62, anchor='center'); self.cands.column('reason', width=240)
        self.cands.pack(fill='both', expand=True, padx=12, pady=(0, 8))
        self.cands.bind('<Double-1>', lambda _e: self._select_candidate())
        actions = tk.Frame(right, bg='white'); actions.pack(fill='x', padx=12, pady=(0, 12))
        tk.Button(actions, text='预览完整素材', command=self._preview, bg='#E8F2FF', fg='#1768C5',
                  relief='flat', padx=14, pady=6).pack(side='left')
        tk.Button(actions, text='设为当前素材', command=self._select_candidate, bg='#E8F2FF', fg='#1768C5',
                  relief='flat', padx=14, pady=6).pack(side='left', padx=8)
        tk.Button(actions, text='确认分镜并生成草稿', command=self._generate, bg='#1677FF', fg='white',
                  relief='flat', padx=18, pady=7).pack(side='right')

    def _create_plan(self):
        try:
            script = storyboard._read_script(self.args.script)
            self.plan = storyboard.create_plan(script, self.args.materials, self.args.style,
                                                self.args.park, self.args.orientation, 5)
            self._refresh_scenes()
            self.status.set('已生成 %d 个分镜 · 双击候选即可替换' % len(self.plan['scenes']))
        except Exception as e:
            self.status.set('分析失败')
            messagebox.showerror('分镜分析失败', str(e), parent=self)

    def _refresh_scenes(self):
        for x in self.scenes.get_children(): self.scenes.delete(x)
        for scene in self.plan['scenes']:
            chosen = next((c for c in scene['candidates']
                           if c['asset_id'] == scene['selected_asset_id']), None)
            self.scenes.insert('', 'end', iid=str(scene['scene_no']),
                               text='%d. %s' % (scene['scene_no'], scene['text'][:28]),
                               values=(scene['type'], chosen['name'] if chosen else '无候选',
                                       chosen['score'] if chosen else '-'))
        if self.plan['scenes']:
            self.scenes.selection_set('1'); self._show_candidates(1)

    def _scene_changed(self, _event=None):
        sel = self.scenes.selection()
        if sel: self._show_candidates(int(sel[0]))

    def _show_candidates(self, no):
        self.scene_no = no
        for x in self.cands.get_children(): self.cands.delete(x)
        scene = self.plan['scenes'][no - 1]
        for i, c in enumerate(scene['candidates']):
            mark = '✓ ' if c['asset_id'] == scene['selected_asset_id'] else ''
            self.cands.insert('', 'end', iid=str(i), text=mark + c['name'],
                              values=(c['score'], '%.1fs' % (c['duration_us'] / 1e6),
                                      '、'.join(c['reasons'])))

    def _selected_candidate(self):
        sel = self.cands.selection()
        if not sel: return None
        return self.plan['scenes'][self.scene_no - 1]['candidates'][int(sel[0])]

    def _preview(self):
        c = self._selected_candidate()
        if not c: return
        try:
            if os.name == 'nt': os.startfile(c['path'])
            elif sys.platform == 'darwin': subprocess.Popen(['open', c['path']])
            else: subprocess.Popen(['xdg-open', c['path']])
        except Exception as e:
            messagebox.showerror('无法预览', str(e), parent=self)

    def _select_candidate(self):
        c = self._selected_candidate()
        if not c: return
        no = self.scene_no
        storyboard.select_candidate(self.plan, no, c['asset_id'])
        self._refresh_scenes(); self.scenes.selection_set(str(no)); self._show_candidates(no)
        self.status.set('分镜 %d 已替换为 %s' % (no, c['name']))

    def _generate(self):
        try:
            storyboard.validate_plan(self.plan, storyboard._read_script(self.args.script))
            job_id = new_job_id()
            plan_path = os.path.join(tempfile.gettempdir(), 'yuanliu-plan-%s.json' % job_id)
            self.plan['status'] = 'CONFIRMED'; atomic_write_json(plan_path, self.plan)
            cmd = [sys.executable, os.path.join(SCRIPTS, 'make_park_video.py'), self.args.script,
                   '--materials', self.args.materials, '--style', self.args.style,
                   '--orientation', self.args.orientation, '--out', self.args.out,
                   '--park-name', self.args.park or '园区', '--plan', plan_path,
                   '--job-id', job_id]
            if self.args.editor: cmd += ['--editor', self.args.editor]
            subprocess.Popen(cmd, creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
            self.status.set('任务 %s 已启动' % job_id)
            messagebox.showinfo('任务已启动', '任务ID：%s\n生成完成后可在任务回执中查看结果。' % job_id,
                                parent=self)
        except Exception as e:
            messagebox.showerror('无法生成', str(e), parent=self)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--script', required=True); ap.add_argument('--materials', required=True)
    ap.add_argument('--style', default='3'); ap.add_argument('--park', default='')
    ap.add_argument('--orientation', default='竖屏'); ap.add_argument('--editor', default='')
    ap.add_argument('--out', default=r'D:\JianyingPro Drafts')
    ap.add_argument('--allow-empty-materials', dest='allow_empty_materials',
                    action='store_true',
                    help='允许在空素材库上做无画面调试预览（默认关）')
    args = ap.parse_args()

    # P05 §3：分镜预览与正式生成**同一判据**（同一个 validate_materials_dir）。
    # 老行为是空素材库照样打开窗口、出来一片空，用户以为"素材没选好"，
    # 其实是素材库本身就是空的。这里直接拦停并说清是哪种情况。
    import material_matcher as _mm
    _mv = _mm.validate_materials_dir(args.materials)
    if not _mv['ok'] and not args.allow_empty_materials:
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning('分镜预览 · 素材库不可用', _mv['msg'], parent=root)
            root.destroy()
        except Exception:
            print('[素材库] %s' % _mv['msg'])
        raise SystemExit(2)

    StoryboardWindow(args).mainloop()


if __name__ == '__main__':
    main()
