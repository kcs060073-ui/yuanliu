# -*- coding: utf-8 -*-
"""独立任务回执查看器。"""
import json
import os
import tkinter as tk
from tkinter import ttk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS = os.path.join(ROOT, 'output', 'jobs')


class TaskCenter(tk.Tk):
    def __init__(self):
        super().__init__(); self.title('源流 · 任务中心'); self.geometry('980x580')
        self.configure(bg='#EEF4FB'); self._build(); self.refresh()

    def _build(self):
        head = tk.Frame(self, bg='#123158'); head.pack(fill='x')
        tk.Label(head, text='任务中心', bg='#123158', fg='white',
                 font=('Microsoft YaHei UI', 17, 'bold')).pack(side='left', padx=18, pady=13)
        tk.Button(head, text='刷新', command=self.refresh, relief='flat',
                  bg='#E8F2FF', fg='#1768C5', padx=14).pack(side='right', padx=18)
        body = tk.PanedWindow(self, orient='horizontal', bg='#D9E5F3', sashwidth=5)
        body.pack(fill='both', expand=True, padx=14, pady=14)
        left = tk.Frame(body, bg='white'); right = tk.Frame(body, bg='white')
        body.add(left, minsize=550); body.add(right, minsize=310)
        self.tv = ttk.Treeview(left, columns=('status','editor','park','created'), show='tree headings')
        for col, title, width in (('#0','任务ID',240),('status','状态',85),('editor','剪辑者',80),
                                  ('park','园区',90),('created','创建时间',140)):
            self.tv.heading(col, text=title); self.tv.column(col, width=width)
        self.tv.pack(fill='both', expand=True, padx=10, pady=10)
        self.tv.bind('<<TreeviewSelect>>', self.show)
        self.detail = tk.Text(right, bg='#FBFDFF', fg='#24334A', relief='flat',
                              font=('Microsoft YaHei UI', 10), padx=12, pady=12)
        self.detail.pack(fill='both', expand=True, padx=10, pady=10)

    def refresh(self):
        for x in self.tv.get_children(): self.tv.delete(x)
        if not os.path.isdir(JOBS): return
        rows = []
        for job_id in os.listdir(JOBS):
            p = os.path.join(JOBS, job_id, 'receipt.json')
            if not os.path.isfile(p): p = os.path.join(JOBS, job_id, 'status.json')
            try:
                data = json.load(open(p, encoding='utf-8')); rows.append((job_id, data, p))
            except Exception: pass
        rows.sort(key=lambda x: x[1].get('created_at',''), reverse=True)
        self._rows = {}
        for job_id, data, p in rows:
            self._rows[job_id] = (data, p)
            self.tv.insert('', 'end', iid=job_id, text=job_id,
                           values=(data.get('status',''), data.get('editor',''), data.get('park',''),
                                   data.get('created_at','')))

    def show(self, _event=None):
        sel = self.tv.selection()
        if not sel: return
        data, path = self._rows[sel[0]]
        lines = [
            '任务ID：' + data.get('job_id',''), '状态：' + data.get('status',''),
            '剪辑者：' + data.get('editor',''), '园区：' + data.get('park',''),
            '草稿：' + data.get('draft_dir',''), '自检：' + data.get('verify',''),
            '耗时：%s 秒' % data.get('elapsed_s','-'), '错误：' + data.get('error',''), '',
            '使用素材：'
        ]
        lines += ['  - ' + (m.get('name') or m.get('path','')) for m in data.get('materials',[])]
        lines += ['', '回执文件：' + path]
        self.detail.delete('1.0','end'); self.detail.insert('1.0','\n'.join(lines))


if __name__ == '__main__':
    TaskCenter().mainloop()
