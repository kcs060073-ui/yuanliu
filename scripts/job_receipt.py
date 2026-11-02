# -*- coding: utf-8 -*-
"""独立任务状态与回执。每个任务只写自己的目录。"""
import hashlib
import json
import os
import socket
import time

from atomic_io import atomic_write_json
from runtime_context import RunContext


def _sha256_text(text):
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


class JobReceipt:
    def __init__(self, context, script='', editor='', park='', versions=None):
        if not isinstance(context, RunContext):
            raise TypeError('context 必须是 RunContext')
        self.ctx = context
        self.started_at = time.time()
        self.data = {
            'schema_version': 1,
            'job_id': context.job_id,
            'mode': context.mode,
            'status': 'CREATED',
            'host': socket.gethostname(),
            'editor': editor or '',
            'park': park or '',
            'script_sha256': _sha256_text(script),
            'versions': versions or {},
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'draft_dir': '',
            'plan_path': '',
            'materials': [],
            'verify': 'PENDING',
            # P05 新增（**纯追加**，P04 的全部字段与语义不变）：
            # 生成过程中出现的"不致命但必须让人看见"的情况，例如
            # 「本机未找到可用BGM，已生成无BGM草稿」。GUI/日志/完成弹窗同源引用它。
            'warnings': [],
            'error': '',
        }
        atomic_write_json(self.ctx.path('input.json'), {
            'job_id': context.job_id, 'script': script, 'script_sha256': self.data['script_sha256'],
            'editor': editor or '', 'park': park or '', 'versions': versions or {},
        })
        self._save_status()

    def _save_status(self):
        self.data['updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        atomic_write_json(self.ctx.path('status.json'), self.data)

    def update(self, status, **fields):
        self.data.update(fields)
        self.data['status'] = status
        self._save_status()

    def finish(self, ok, **fields):
        self.data.update(fields)
        self.data['status'] = 'SUCCESS' if ok else 'FAILED'
        self.data['elapsed_s'] = round(time.time() - self.started_at, 3)
        self.data['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        self._save_status()
        atomic_write_json(self.ctx.path('receipt.json'), self.data)
        return self.ctx.path('receipt.json')


def selected_materials_from_info(info):
    seen, out = set(), []
    for item in (info or {}).get('video_materials', []):
        p = item.get('path') or ''
        if not p or p in seen:
            continue
        seen.add(p)
        out.append({'path': p, 'name': item.get('name') or os.path.basename(p)})
    return out
