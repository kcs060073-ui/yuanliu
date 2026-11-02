# -*- coding: utf-8 -*-
"""生产/测试运行边界、唯一任务 ID 与任务目录。"""
import os
import re
import secrets
import socket
import tempfile
import time


SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 任务ID：YL-YYMMDD-HHMMSS-<机器码2~8>-<随机段>
#
# 随机段 2026-09-20（P05）从 4 位十六进制提升到 **8 位**：
# 原来 4 位只有 65536 种取值，同一天同一园区同一风格并发跑时，
# 「尾码撞车 → 草稿目录同名 → 后一条把前一条整个覆盖」是能真实发生的。
# 8 位（2^32 ≈ 43 亿）把概率压到可忽略。
#
# 校验同时接受 4 位：P04 及以前已经落盘的 output/jobs/**、任务回执必须仍可读。
_JOB_RE = re.compile(r'^YL-\d{6}-\d{6}-[A-Z0-9]{2,8}-[A-Z0-9]{4,8}$')


def _machine_code():
    raw = re.sub(r'[^A-Za-z0-9]', '', socket.gethostname().upper())
    return (raw[-4:] if raw else 'PC00').rjust(2, '0')


def new_job_id(now=None, machine=None):
    t = now or time.localtime()
    return 'YL-%s-%s-%s-%s' % (
        time.strftime('%y%m%d', t), time.strftime('%H%M%S', t),
        machine or _machine_code(), secrets.token_hex(4).upper())


class RunContext:
    def __init__(self, mode=None, job_id=None, jobs_root=None):
        self.mode = (mode or os.environ.get('YUANLIU_RUN_MODE') or 'production').lower()
        if self.mode not in ('production', 'test'):
            raise ValueError('运行模式只允许 production/test')
        self.job_id = job_id or new_job_id()
        if not _JOB_RE.match(self.job_id):
            raise ValueError('任务ID格式不合法：%s' % self.job_id)
        if self.mode == 'test':
            base = os.environ.get('YUANLIU_TEST_ROOT') or os.path.join(
                tempfile.gettempdir(), 'yuanliu-tests')
            self.jobs_root = os.path.abspath(jobs_root or os.path.join(base, 'jobs'))
            self.state_root = os.path.abspath(os.path.join(base, 'state'))
        else:
            self.jobs_root = os.path.abspath(jobs_root or os.path.join(SKILL_DIR, 'output', 'jobs'))
            self.state_root = os.path.abspath(os.path.join(SKILL_DIR, 'state'))
        self.job_dir = os.path.join(self.jobs_root, self.job_id)
        self.assert_safe_path(self.job_dir)
        os.makedirs(self.job_dir, exist_ok=True)
        os.makedirs(self.state_root, exist_ok=True)

    def assert_safe_path(self, path):
        p = os.path.normcase(os.path.abspath(path))
        if self.mode == 'test':
            production_roots = [
                os.path.normcase(os.path.abspath(os.path.join(SKILL_DIR, 'output'))),
                os.path.normcase(os.path.abspath(os.path.join(SKILL_DIR, 'state'))),
            ]
            if any(p == r or p.startswith(r + os.sep) for r in production_roots):
                raise RuntimeError('测试模式禁止写入生产目录：%s' % path)
        return p

    def path(self, name):
        p = os.path.join(self.job_dir, name)
        self.assert_safe_path(p)
        return p


def usage_path(mode=None):
    ctx_mode = (mode or os.environ.get('YUANLIU_RUN_MODE') or 'production').lower()
    if ctx_mode == 'test':
        root = os.environ.get('YUANLIU_TEST_ROOT') or os.path.join(tempfile.gettempdir(), 'yuanliu-tests')
        return os.path.join(root, 'state', 'material_usage.json')
    return os.path.join(SKILL_DIR, 'output', 'material_usage.json')


def output_root(mode=None):
    """``output/`` 根目录 —— **凡是往 output/ 写东西的地方都必须走这里**。

    * production → ``<skill>/output``
    * test       → ``<YUANLIU_TEST_ROOT>/state``（与 jobs/state 同一隔离根）

    为什么要有这个函数（2026-09-20 实测教训）
    ----------------------------------------
    ``YUANLIU_RUN_MODE=test`` 一直是本项目宣称的"测试写生产会被硬阻断"的机制，
    ``RunContext`` 也确实实现了拦截。但只要有人**绕开 RunContext 自己拼
    ``os.path.join(SKILL_DIR, 'output', ...)``**，这条隔离就形同虚设。
    做连续出片 E2E 时就是这样发现两处漏网的：

      * ``draft_builder`` 的图片尺寸缓存 ``media_size_cache.json``
      * ``doctor`` 的"output/ 是否可写"写探针

    两者都在测试模式下一次次写/建生产目录，而输出里毫无提示。
    统一从本函数取根目录，隔离才真的是"一套机制"，不是"看谁记得用 RunContext"。
    """
    ctx_mode = (mode or os.environ.get('YUANLIU_RUN_MODE') or 'production').lower()
    if ctx_mode == 'test':
        root = os.environ.get('YUANLIU_TEST_ROOT') or os.path.join(
            tempfile.gettempdir(), 'yuanliu-tests')
        return os.path.join(root, 'state')
    return os.path.join(SKILL_DIR, 'output')
