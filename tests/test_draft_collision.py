# -*- coding: utf-8 -*-
"""P05 §1：草稿目录「独占预留 / 防覆盖」回归测试。

覆盖 10 类场景（含 §1 要求的 7 类）：
  1. 同名任务连续两条 → 目录名不同，第一条内容哈希不变（**核心防覆盖**）
  2. 目标目录已含有效草稿 → 默认绝不覆盖，自动 -2 换名
  3. --overwrite + 显式目标名 → 沿用原目录（唯一的覆盖入口）
  4. 空目录残留（无草稿标志）→ 换名走，不删不改
  5. 同任务中断后重跑（预留标记 job_id 相同、目录内无草稿）→ 复用
  6. 尾码：新8位任务ID取8位；旧4位任务ID兼容；full_stem 幂等
  7. 后缀耗尽（>MAX_SUFFIX 个候选全被占）→ 换新尾码再试
  8. GUI 与 CLI 用同一算法（base_name/full_stem 是唯一实现）
  9. sanitize：Windows 非法字符、保留设备名、空串
 10. 多线程并发抢同一名字 → 只有一个拿到 stem，其余自动错开，绝不共用目录

单独运行：
    python tests/test_draft_collision.py
"""
import hashlib
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import draft_naming as dn

PASSED = []
FAILED = []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        print('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        print('  [FAIL] %s %s' % (name, extra))


def sha_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()


def make_real_draft(d):
    """在目录里放一份「看起来是有效草稿」的最小内容。"""
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, 'draft_content.json')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('{"sentinel": "第一条草稿，绝不能被覆盖"}')
    with open(os.path.join(d, 'draft_meta_info.json'), 'w', encoding='utf-8') as f:
        f.write('{"meta": 1}')
    return p


# --------------------------------------------------------------------------
def t1_continuous_two_drafts(td):
    """1. 连续两条同名任务：目录不同、第一条完好。"""
    out = os.path.join(td, 't1')
    job1 = 'YL-260920-093000-DESK-A1B2C3D4'
    job2 = 'YL-260920-093005-DESK-5E6F7A8B'
    base = dn.base_name('0', '260920', '嘉兴', '横', '招商')

    r1 = dn.plan_target(out, base, job_id=job1)
    f1 = make_real_draft(r1['path'])
    h1 = sha_of(f1)

    r2 = dn.plan_target(out, base, job_id=job2)
    check('1a 两条草稿目录不同', r1['path'] != r2['path'],
          '%s vs %s' % (r1['name'], r2['name']))
    check('1b 第二条未落进第一条目录', os.path.isfile(f1))
    check('1c 第一条哈希不变', sha_of(f1) == h1)
    check('1d 两条目录名都带8位尾码',
          r1['name'].endswith('A1B2C3D4') and r2['name'].endswith('5E6F7A8B'),
          '%s / %s' % (r1['name'], r2['name']))
    check('1e 根目录下确有 2 个目录',
          len([d for d in os.listdir(out)]) == 2, str(os.listdir(out)))


def t2_existing_draft_no_overwrite(td):
    """2. 目标目录已有草稿 → 自动换名。"""
    out = os.path.join(td, 't2')
    base = '260920-嘉兴-横-招商'
    job = 'YL-260920-093000-DESK-AAAA1111'
    stem = dn.full_stem(base, job)

    occupied = os.path.join(out, stem)
    f = make_real_draft(occupied)
    h = sha_of(f)

    r = dn.plan_target(out, base, job_id=job)
    check('2a 命中冲突并换名', r['name'] == stem + '-2' and r['path'] != occupied,
          r['name'])
    check('2b collision=True', r['collision'] is True)
    check('2c 原草稿未被触碰', sha_of(f) == h and os.path.isfile(f))
    check('2d conflicts 里记了原因', any('已有草稿' in c for c in r['conflict_with']),
          str(r['conflict_with']))


def t3_explicit_overwrite(td):
    """3. 显式 --overwrite + 指定名：唯一允许沿用的路径。"""
    out = os.path.join(td, 't3')
    name = '我的固定草稿名'
    d = os.path.join(out, name)
    f = make_real_draft(d)

    # 不给 overwrite → 必须换名
    r_no = dn.plan_target(out, 'x', job_id='', explicit_name=name, overwrite=False)
    check('3a 未给 overwrite 时换名', r_no['name'] != name, r_no['name'])

    # 给了 overwrite → 沿用
    r_ow = dn.plan_target(out, 'x', job_id='', explicit_name=name, overwrite=True)
    check('3b overwrite 时沿用指定目录', r_ow['path'] == d and r_ow['reused'] is True,
          r_ow['reason'])
    check('3c 沿用后内容仍在（由调用方负责写）', os.path.isfile(f))


def t4_empty_leftover(td):
    """4. 空目录残留：换名走，不删不改。"""
    out = os.path.join(td, 't4')
    base = '260920-苏州-竖-实拍'
    job = 'YL-260920-093000-DESK-BBBB2222'
    stem = dn.full_stem(base, job)
    empty = os.path.join(out, stem)
    os.makedirs(empty)                      # 纯空目录，无预留标记

    r = dn.plan_target(out, base, job_id=job)
    check('4a 空目录不占用该名字', r['name'] != stem, r['name'])
    check('4b 空目录未被删除', os.path.isdir(empty))
    check('4c 空目录仍为空', os.listdir(empty) == [])


def t5_same_job_resume(td):
    """5. 同任务中断后重跑 → 复用；并发/新鲜预留 → 绝不复用。"""
    out = os.path.join(td, 't5')
    base = '260920-贵阳-横-招商'
    job = 'YL-260920-093000-DESK-CCCC3333'

    r1 = dn.plan_target(out, base, job_id=job)
    check('5a 首次为 reserved', r1['reserved'] is True)
    check('5b 预留标记已写下',
          os.path.isfile(os.path.join(r1['path'], dn.RESERVE_MARK)))

    # 本进程刚写的预留 = 可能还在写 → 不许复用（否则同一目录两个写入者）
    r2 = dn.plan_target(out, base, job_id=job)
    check('5c 本进程新鲜预留不复用', r2['path'] != r1['path'], r2['name'])

    # 模拟「上一个进程中断留下、已过冷却期」的预留 → 可以复用
    out2 = os.path.join(td, 't5b')
    stale = os.path.join(out2, dn.full_stem(base, job))
    os.makedirs(stale)
    import json as _json
    import time as _time
    with open(os.path.join(stale, dn.RESERVE_MARK), 'w', encoding='utf-8') as f:
        _json.dump({'schema_version': 1, 'job_id': job, 'pid': os.getpid() + 4242,
                    'reserved_at': _time.strftime(
                        '%Y-%m-%d %H:%M:%S',
                        _time.localtime(_time.time() - dn.RESERVE_STALE_SECONDS - 30))}, f)
    r_stale = dn.plan_target(out2, base, job_id=job)
    check('5d 上轮中断的过期预留可复用',
          r_stale['path'] == stale and r_stale['reused'] is True, r_stale['reason'])

    # 别人的任务 → 必须换名（不抢别人的预留）
    r3 = dn.plan_target(out, base, job_id='YL-260920-093000-DESK-DDDD4444')
    check('5e 其他任务不复用', r3['path'] != r1['path'], r3['name'])


def t6_tail_and_idempotent(td):
    """6. 尾码长度与 full_stem 幂等。"""
    new_job = 'YL-260920-093000-DESKTOP58JFBM3-A1B2C3D4'
    old_job = 'YL-260920-093000-FBM3-7E07'
    check('6a 新8位任务ID取8位', dn.job_tail(new_job) == 'A1B2C3D4', dn.job_tail(new_job))
    check('6b 旧4位任务ID兼容', dn.job_tail(old_job) == '7E07', dn.job_tail(old_job))
    check('6c 空任务ID返回空', dn.job_tail('') == '')
    base = '260920-嘉兴-横-招商'
    once = dn.full_stem(base, new_job)
    twice = dn.full_stem(once, new_job)
    check('6d full_stem 幂等', once == twice, '%s vs %s' % (once, twice))
    check('6e 尾码只出现一次', once.count('A1B2C3D4') == 1, once)


def t7_suffix_exhausted(td):
    """7. 后缀耗尽 → 换新尾码（retail）。"""
    out = os.path.join(td, 't7')
    base = '260920-南京-竖-招商'
    job = 'YL-260920-093000-DESK-EEEE5555'
    stem = dn.full_stem(base, job)

    # 把 stem 与 stem-2..stem-6 都占掉（用真草稿），再限制 max_suffix=6
    for nm in [stem] + ['%s-%d' % (stem, i) for i in range(2, 7)]:
        make_real_draft(os.path.join(out, nm))

    r = dn.reserve_draft_dir(out, base, job_id=job, max_suffix=6)
    check('7a 后缀耗尽后换新名', r['name'] not in (
        [stem] + ['%s-%d' % (stem, i) for i in range(2, 7)]), r['name'])
    check('7b 新名仍在 stem 前缀下', r['name'].startswith(stem), r['name'])
    check('7c 冲突记录非空', len(r['conflict_with']) >= 6, str(len(r['conflict_with'])))


def t8_shared_algorithm(td):
    """8. GUI 与 CLI 共用同一命名算法（不存在第二份实现）。"""
    import inspect
    src = inspect.getsource(dn)
    check('8a 0式含方向位', dn.base_name('0', '260920', '嘉兴', '横', '招商')
          == '260920-嘉兴-横-招商', dn.base_name('0', '260920', '嘉兴', '横', '招商'))
    check('8b 0式不传方向保持旧格式',
          dn.base_name('0', '260920', '嘉兴', '', '招商') == '260920-嘉兴-招商')
    check('8c 7式', dn.base_name('7', '260920', '两河', '竖', '', '铠畅')
          == '260920-两河-竖-铠畅', dn.base_name('7', '260920', '两河', '竖', '', '铠畅'))
    # 源码里不应该再有第二处 os.makedirs(..., exist_ok=True) 拼草稿名
    check('8d draft_naming 内预留走 os.mkdir',
          'os.mkdir(path)' in src and 'exist_ok=True' not in src.split(
              'def _claim')[1].split('def _candidates')[0])


def t9_sanitize(td):
    """9. sanitize 边界。"""
    check('9a 非法字符', dn.sanitize('a<b>c:d"e/f\\g|h?i*j') == 'a_b_c_d_e_f_g_h_i_j',
          dn.sanitize('a<b>c:d"e/f\\g|h?i*j'))
    check('9b 保留设备名加前缀', dn.sanitize('CON') == '_CON', dn.sanitize('CON'))
    check('9c 保留名带扩展也拦', dn.sanitize('NUL.txt') == '_NUL.txt',
          dn.sanitize('NUL.txt'))
    check('9d 空串', dn.sanitize('   ') == '未命名', dn.sanitize('   '))
    check('9e 尾点去掉', dn.sanitize('名字...') == '名字', dn.sanitize('名字...'))


def t10_concurrent(td):
    """10. 并发抢同一名字：各得其所，绝不共用目录。"""
    out = os.path.join(td, 't10')
    base = '260920-嘉兴-横-招商'
    job = 'YL-260920-093000-DESK-FFFF6666'
    results = []
    lock = threading.Lock()

    def worker():
        r = dn.reserve_draft_dir(out, base, job_id=job)
        with lock:
            results.append(r['path'])

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]

    check('10a 8 个并发都拿到目录', len(results) == 8, str(len(results)))
    check('10b 无重复目录', len(set(results)) == 8, str(len(set(results))))
    check('10c 磁盘上确有 8 个目录',
          len([d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d))]) == 8)


def main():
    print('== P05 §1 草稿目录独占预留 / 防覆盖 回归 ==')
    td = tempfile.mkdtemp(prefix='p05_collision_')
    try:
        for fn in (t1_continuous_two_drafts, t2_existing_draft_no_overwrite,
                   t3_explicit_overwrite, t4_empty_leftover, t5_same_job_resume,
                   t6_tail_and_idempotent, t7_suffix_exhausted,
                   t8_shared_algorithm, t9_sanitize, t10_concurrent):
            print('-- %s' % fn.__doc__.strip().split('\n')[0])
            fn(td)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    print('\n通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for x in FAILED:
            print('  FAILED: %s' % x)
        raise SystemExit(1)
    print('PASS draft collision')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
