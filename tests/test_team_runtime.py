# -*- coding: utf-8 -*-
"""P03 团队可靠性：隔离、原子写、并发计数、任务回执。

P05 补充（2026-09-20）：``test_cli_env_isolation`` —— 实测过 CLI 的
``--run-mode`` 默认值写死成 ``production``，把 ``YUANLIU_RUN_MODE=test``
整个架空：环境变量说是测试模式，CLI 却照样往生产 ``output/`` 写任务回执、
产出台账和素材使用次数。SKILL.md 一直宣称"测试写生产会被硬阻断"，
这条阻断必须真的生效，所以这里用**子进程**跑一次 CLI 来锁住它。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

from atomic_io import atomic_write_json, locked_json_update
from runtime_context import RunContext, new_job_id
from job_receipt import JobReceipt
import material_governance as mg


def _snapshot(path):
    """目录快照：{相对路径: (大小, mtime)}。"""
    out = {}
    for cur, _d, fs in os.walk(path):
        for fn in fs:
            fp = os.path.join(cur, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            out[os.path.relpath(fp, path)] = (st.st_size, int(st.st_mtime))
    return out


def test_cli_env_isolation():
    """YUANLIU_RUN_MODE=test 时，CLI 不得往生产 output/ 写任何东西。"""
    out_dir = os.path.join(ROOT, 'output')
    before = _snapshot(out_dir) if os.path.isdir(out_dir) else {}
    with tempfile.TemporaryDirectory() as td:
        script = os.path.join(td, 's.txt')
        open(script, 'w', encoding='utf-8').write('嘉兴园区厂房招商，配套齐全。\n')
        empty_mats = os.path.join(td, '空素材库')
        os.makedirs(empty_mats)          # 空库 → 生成前就会拦停，不需要剪映

        env = os.environ.copy()
        env['YUANLIU_RUN_MODE'] = 'test'
        env['YUANLIU_TEST_ROOT'] = td
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, 'scripts', 'make_park_video.py'),
             script, '--style', '3', '--materials', empty_mats,
             '--out', os.path.join(td, 'out'), '--orientation', '横版',
             '--park-name', '嘉兴'],
            capture_output=True, timeout=300, env=env)
        assert r.returncode != 0, '空素材库应当被拦停（rc=%s）' % r.returncode

        # 1) 任务目录必须落在隔离根里
        jobs = os.path.join(td, 'jobs')
        assert os.path.isdir(jobs), '测试模式的任务目录没落在 YUANLIU_TEST_ROOT 下：%s' % jobs
        job_ids = os.listdir(jobs)
        assert job_ids, '没有创建任何任务目录'
        jd = os.path.join(jobs, job_ids[0])
        assert os.path.isfile(os.path.join(jd, 'input.json')), '缺少 input.json'
        assert os.path.isfile(os.path.join(jd, 'status.json')), '缺少 status.json'
        st = json.load(open(os.path.join(jd, 'status.json'), encoding='utf-8'))
        assert st['job_id'] == job_ids[0]

        # 2) 生产 output/ 必须一字未动
        after = _snapshot(out_dir) if os.path.isdir(out_dir) else {}
        changed = sorted(set(before) ^ set(after)) + \
            sorted(k for k in set(before) & set(after) if before[k] != after[k])
        assert not changed, '测试模式仍然写到了生产 output/：%s' % changed[:10]

        # 3) 任务ID 必须是 8 位随机段（P05 §1）
        tail = job_ids[0].rsplit('-', 1)[-1]
        assert len(tail) == 8, '任务ID随机段应 8 位，实为 %r（%s）' % (tail, job_ids[0])


def test_no_hardcoded_output_writes():
    """静态守卫：往 output/ 写东西的地方，只允许出现在 runtime_context 里。

    背景（2026-09-20）：``YUANLIU_RUN_MODE=test`` 的隔离被两处"自己拼路径"
    绕过了（draft_builder 的 media_size_cache、doctor 的写探针）。
    只要有人写 ``os.path.join(SKILL_DIR, 'output', ...)``，隔离就有漏洞。
    现在统一从 ``runtime_context.output_root()`` 取根目录，这条测试锁住它。
    """
    import io
    import re
    allow = {'runtime_context.py', 'production_log.py', 'park_audit.py',
             'material_governance.py', 'upgrade.py', 'pack_release.py',
             'release_manifest.py'}
    pat = re.compile(r"os\.path\.join\([^)]*['\"]output['\"]")
    bad = []
    sdir = os.path.join(ROOT, 'scripts')
    for fn in sorted(os.listdir(sdir)):
        if not fn.endswith('.py') or fn in allow:
            continue
        src = io.open(os.path.join(sdir, fn), encoding='utf-8').read()
        for m in pat.finditer(src):
            bad.append('%s: %s' % (fn, m.group(0)[:70]))
    assert not bad, ('这些地方直接拼了 output/ 路径，会绕过测试模式隔离，'
                     '请改用 runtime_context.output_root()：%s' % bad)


def main():
    with tempfile.TemporaryDirectory() as td:
        os.environ['YUANLIU_RUN_MODE'] = 'test'
        os.environ['YUANLIU_TEST_ROOT'] = td
        ctx = RunContext('test', new_job_id(machine='T01'))
        assert ctx.job_dir.startswith(td)
        try:
            ctx.assert_safe_path(os.path.join(ROOT, 'output', 'should-not-write.json'))
            raise AssertionError('测试路径保护未生效')
        except RuntimeError:
            pass

        target = os.path.join(td, 'atomic.json')
        atomic_write_json(target, {'n': 1})
        assert json.load(open(target, encoding='utf-8')) == {'n': 1}

        def inc():
            for _ in range(25):
                locked_json_update(target, lambda d: {'n': int(d.get('n', 0)) + 1})
        threads = [threading.Thread(target=inc) for _ in range(4)]
        [t.start() for t in threads]; [t.join() for t in threads]
        assert json.load(open(target, encoding='utf-8'))['n'] == 101

        usage_file = mg.runtime_usage_path()
        threads = [threading.Thread(target=lambda: mg.record_usage_atomic(['X:/a.mp4']))
                   for _ in range(8)]
        [t.start() for t in threads]; [t.join() for t in threads]
        usage = mg.load_usage(usage_file)
        assert list(usage.values())[0]['n'] == 8
        assert usage_file.startswith(td)

        rec = JobReceipt(ctx, script='测试脚本', editor='测试员', park='测试园区')
        rec.update('WRITING_DRAFT', draft_dir='X:/draft')
        rp = rec.finish(True, verify='PASS')
        data = json.load(open(rp, encoding='utf-8'))
        assert data['job_id'] == ctx.job_id and data['status'] == 'SUCCESS'
        assert data.get('warnings') == []       # P05 新增字段，默认空
    print('PASS team runtime')
    test_no_hardcoded_output_writes()
    print('OK: 没有绕开 runtime_context.output_root() 的 output/ 写入')
    test_cli_env_isolation()
    print('OK: CLI 环境变量隔离生效（YUANLIU_RUN_MODE=test 不写生产 output/）')


if __name__ == '__main__':
    main()
