# -*- coding: utf-8 -*-
"""显式 ``--name`` / ``--overwrite`` 契约回归（P05 二轮补丁 二）。

背景（用户原话）
----------------
「真实命令传 ``--name 固定草稿 --overwrite``，仍可能因为任务 ID 尾码而写入
``固定草稿-A1B2C3D4``，而不是覆盖已经存在的 ``固定草稿``」——
即「显式覆盖」契约没兑现。根因：``reserve_draft_dir()`` 无条件用
``full_stem(base, job_id)`` 给**所有**名字追加任务尾码，显式名字也被加了。

老测试的盲区：``job_id`` 传的是空串，``full_stem`` 不加尾码，于是"碰巧"通过。
真实命令行永远有非空 job_id —— 本测试就用**非空 job_id** + **真实 CLI**跑。

四类断言（对应用户列出的 5 条）：
  A. ``--name`` + ``--overwrite`` → 精确命中该目录（不加尾码），原子覆盖
  B. ``--name`` 无 ``--overwrite`` → 新目录，原目录内容不变
  C. 只有 ``--overwrite`` 没有 ``--name`` → 不覆盖任何已有草稿
  D. GUI 正常生成流程不传 ``--overwrite``（静态）
  E. 写入失败时不留半成品、不动原草稿

单独运行：
    python tests/test_explicit_overwrite.py
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import draft_naming as dn
import jianying_path

PY = sys.executable
# 素材库根：**不能写死**某个人的绝对路径 —— 这个文件会随包发出去，
# 写死 = 泄漏管理员机器的用户名与目录结构（tests/test_template_privacy.py 会拦）。
# 用 expanduser('~') 在运行时解析，并允许用环境变量覆盖。
SRC_MATERIALS = (os.environ.get('YUANLIU_TEST_MATERIALS')
                 or os.path.join(os.path.expanduser('~'), 'Desktop', '园区素材'))
SENTINEL = b'SENTINEL-ORIGINAL-DRAFT-DO-NOT-TOUCH'

PASSED, FAILED = [], []
LINES = []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        LINES.append('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        LINES.append('  [FAIL] %s %s' % (name, extra))


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def small_videos(n=1):
    rows = []
    for cur, _d, fs in os.walk(SRC_MATERIALS):
        for f in fs:
            if f.lower().endswith(('.mp4', '.mov')):
                p = os.path.join(cur, f)
                try:
                    rows.append((os.path.getsize(p), p))
                except OSError:
                    pass
    rows.sort()
    return [p for _s, p in rows[:n]]


def plant_sentinel_draft(root, name):
    """造一个"已存在的草稿"目录，里面放可识别的哨兵内容。"""
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, 'textReading'), exist_ok=True)
    with open(os.path.join(d, 'draft_content.json'), 'wb') as f:
        f.write(SENTINEL)
    with open(os.path.join(d, 'draft_meta_info.json'), 'wb') as f:
        f.write(SENTINEL + b'-META')
    return d


def snapshot(root):
    out = {}
    for cur, _d, fs in os.walk(root):
        for fn in fs:
            fp = os.path.join(cur, fn)
            try:
                out[os.path.relpath(fp, root)] = sha_file(fp)
            except OSError:
                pass
    return out


# ---------------------------------------------------------------------------
def t_unit_nonempty_job_id(td):
    """单元层：非空 job_id 下，显式名的行为必须与自动名不同。"""
    LINES.append('-- A/B/C 单元层（非空 job_id）')
    out = os.path.join(td, 'u')
    os.makedirs(out, exist_ok=True)
    job = 'YL-260920-101500-TEST-ABCD1234'
    sentinel_dir = plant_sentinel_draft(out, '固定草稿')
    sent_before = sha_file(os.path.join(sentinel_dir, 'draft_content.json'))

    # A. 显式 + overwrite → 精确命中，不加尾码
    r = dn.plan_target(out, '固定草稿', job_id=job, explicit_name='固定草稿',
                       overwrite=True)
    check('A1 显式+overwrite 命中准确名字（不追加任务尾码）',
          r['name'] == '固定草稿' and r['path'] == sentinel_dir, str(r))
    check('A2 标记为沿用已有目录', r['reused'] is True, str(r))

    # 自动命名（同一 job_id）必须仍然带尾码 —— 证明"不加尾码"是显式名专属
    r_auto = dn.plan_target(out, '固定草稿', job_id=job, explicit_name=None,
                            overwrite=True)
    check('A3 自动命名仍然带 8 位任务尾码（overwrite 被忽略）',
          r_auto['name'] == '固定草稿-ABCD1234' and r_auto['reused'] is False,
          str(r_auto))

    # B. 显式但无 overwrite → 换新目录，原目录一字不动
    r2 = dn.plan_target(out, '固定草稿', job_id=job, explicit_name='固定草稿',
                        overwrite=False)
    check('B1 显式无 overwrite → 换名，不动原目录', r2['path'] != sentinel_dir, str(r2))
    check('B2 新名是「固定草稿-2」（唯一尾码）', r2['name'] == '固定草稿-2', r2['name'])
    check('B3 原目录哨兵内容未变',
          sha_file(os.path.join(sentinel_dir, 'draft_content.json')) == sent_before)

    # B4. 精确名被占用（含空目录）时也不许写进去
    empty = os.path.join(out, '空目录名')
    os.makedirs(empty, exist_ok=True)
    r3 = dn.plan_target(out, '空目录名', job_id=job, explicit_name='空目录名',
                        overwrite=False)
    check('B4 精确名是空目录时也不写进去（显式名=不覆盖）',
          r3['path'] != empty, str(r3))

    # B5. 显式名带非法字符 → 清洗后再判定
    r4 = dn.plan_target(out, 'a/b:c', job_id=job, explicit_name='a/b:c')
    check('B5 显式名会先做 Windows 非法字符清洗',
          r4['name'] == dn.sanitize('a/b:c') and '/' not in r4['name'], r4['name'])


def t_unit_atomic_on_failure(td):
    """E. 写入失败不留半成品：写失败后原草稿必须完好。"""
    LINES.append('-- E 写入失败保护（单元层）')
    out = os.path.join(td, 'e')
    os.makedirs(out, exist_ok=True)
    d = plant_sentinel_draft(out, '固定草稿')
    before = snapshot(out)
    r = dn.plan_target(out, '固定草稿', job_id='YL-260920-101500-TEST-AAAA1111',
                       explicit_name='固定草稿', overwrite=True)
    check('E1 覆盖路径拿到的是准确目录', r['path'] == d, str(r))

    # 模拟"写入到一半崩了"：原子写是先写临时文件再 os.replace，
    # 所以中途失败时目标文件根本没被碰过。这里直接验证前置条件。
    from atomic_io import atomic_write_bytes
    import os as _os
    try:
        atomic_write_bytes(os.path.join(d, 'draft_content.json'), b'NEW')
    except Exception as e:
        LINES.append('     atomic_write_bytes 异常：%s' % e)
    check('E2 原子写成功后内容被替换',
          sha_file(os.path.join(d, 'draft_content.json')) == sha_bytes(b'NEW'))

    src_make = io.open(os.path.join(ROOT, 'scripts', 'make_park_video.py'),
                       encoding='utf-8').read()
    check('E3 草稿写入走 atomic_write_bytes + 文件锁（不会留半成品）',
          'atomic_write_bytes(content_path' in src_make and
          'atomic_write_bytes(meta_path' in src_make and
          'FileLock(os.path.join(draft_dir' in src_make)


def run_cli(args, env, timeout=900):
    p = subprocess.run([PY, os.path.join(ROOT, 'scripts', 'make_park_video.py')] + args,
                       capture_output=True, timeout=timeout, env=env)
    return p.returncode, (p.stdout + p.stderr).decode('utf-8', 'replace')


def draft_dir_of(out):
    hits = [l.split('=', 1)[1].strip() for l in out.splitlines()
            if l.startswith('DRAFT_DIR=')]
    return hits[-1] if hits else ''


def t_real_cli(td):
    """真实命令行层（非空 job_id、真加解密）。"""
    LINES.append('-- 真实 CLI（真加解密、非空任务ID）')
    install = jianying_path.find_install_dir()
    if not install:
        LINES.append('  [SKIP] 本机未找到剪映安装目录，跳过真实 CLI 用例'
                     '（单元层已覆盖同一契约）')
        return
    picked = small_videos(1)
    if not picked:
        LINES.append('  [SKIP] 本机素材库不可用，跳过真实 CLI 用例')
        return

    mats = os.path.join(td, 'materials')
    bucket = os.path.join(mats, '嘉兴园区', '厂房外部')
    os.makedirs(bucket, exist_ok=True)
    shutil.copy2(picked[0], os.path.join(bucket, '厂房外部_横版.mp4'))
    script = os.path.join(td, '脚本.txt')
    io.open(script, 'w', encoding='utf-8').write(
        '嘉兴产业园，长三角核心区位。\n厂房层高六米。\n欢迎实地考察！\n')

    env = os.environ.copy()
    env.update({'YUANLIU_RUN_MODE': 'test', 'YUANLIU_TEST_ROOT': td,
                'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    common = [script, '--style', '3', '--materials', mats, '--park-name', '嘉兴',
              '--orientation', '横版', '--run-mode', 'test',
              '--out', os.path.join(td, 'ignored')]
    out_root = os.path.join(td, 'state', 'drafts')

    # 先造一个"已存在的草稿"（模拟用户上次出的片子）
    sentinel_dir = plant_sentinel_draft(out_root, '固定草稿')
    sent_before = sha_file(os.path.join(sentinel_dir, 'draft_content.json'))

    # B. --name 固定草稿（无 overwrite）→ 新目录，原目录不动
    rc, out = run_cli(common + ['--name', '固定草稿'], env)
    d_b = draft_dir_of(out)
    check('B6 真实 CLI：--name 无 overwrite 时生成新目录',
          rc == 0 and d_b and os.path.basename(d_b) != '固定草稿', '%s / %s' % (rc, d_b))
    check('B7 真实 CLI：原「固定草稿」内容一字未变',
          sha_file(os.path.join(sentinel_dir, 'draft_content.json')) == sent_before)
    check('B8 真实 CLI：新目录名带唯一尾码',
          os.path.basename(d_b).startswith('固定草稿-') and
          len(os.path.basename(d_b)) > len('固定草稿-'), os.path.basename(d_b))

    # A. --name 固定草稿 --overwrite → 精确命中那个目录
    rc2, out2 = run_cli(common + ['--name', '固定草稿', '--overwrite'], env)
    d_a = draft_dir_of(out2)
    check('A4 真实 CLI：显式+overwrite 返回 0', rc2 == 0, 'rc=%s' % rc2)
    if rc2 != 0:
        LINES.append(out2[-2500:])
    check('A5 真实 CLI：草稿目录就是「固定草稿」本身（不是 固定草稿-XXXXXXXX）',
          os.path.basename(d_a) == '固定草稿', d_a)
    check('A6 真实 CLI：目标目录确实是那个原目录',
          os.path.normcase(d_a) == os.path.normcase(sentinel_dir), d_a)
    check('A7 真实 CLI：草稿文件已被真正覆盖（不再是哨兵内容）',
          os.path.isfile(os.path.join(sentinel_dir, 'draft_content.json')) and
          sha_file(os.path.join(sentinel_dir, 'draft_content.json')) != sent_before)
    check('A8 覆盖后的是可自检的完整草稿（两个文件都在）',
          os.path.isfile(os.path.join(sentinel_dir, 'draft_content.json')) and
          os.path.isfile(os.path.join(sentinel_dir, 'draft_meta_info.json')))
    check('A9 日志里说明了"原地覆盖"（可审计）',
          '[覆盖]' in out2 and '显式 --name + --overwrite' in out2)

    # 自检覆盖后的草稿
    p = subprocess.run([PY, os.path.join(ROOT, 'tests', 'verify_draft.py'), sentinel_dir],
                       capture_output=True, timeout=900, env=env)
    check('A10 覆盖后的草稿能通过 verify_draft 自检', p.returncode == 0,
          (p.stdout + p.stderr).decode('utf-8', 'replace').strip().splitlines()[-1:])

    # C. 只有 --overwrite、没有 --name → 不得动任何已有草稿
    snap_before = snapshot(out_root)
    rc3, out3 = run_cli(common + ['--overwrite'], env)
    d_c = draft_dir_of(out3)
    snap_after = snapshot(out_root)
    changed = [k for k in set(snap_before) & set(snap_after)
               if snap_before[k] != snap_after[k]]
    added = sorted(set(snap_after) - set(snap_before))
    check('C1 真实 CLI：无 --name 时 --overwrite 仍能正常出片', rc3 == 0, 'rc=%s' % rc3)
    check('C2 已有草稿内容零改动（没有任何文件被覆盖）', not changed, str(changed[:6]))
    check('C3 只在草稿根下新增了一个目录',
          len(added) > 0 and len({a.split(os.sep)[0] for a in added}) == 1,
          str(sorted({a.split(os.sep)[0] for a in added})[:4]))
    check('C4 新目录不是「固定草稿」本身',
          d_c and os.path.basename(d_c) != '固定草稿', d_c)
    check('C5 日志明确写出 --overwrite 被忽略',
          '--overwrite 不生效' in out3)

    # 只有 --overwrite 时，连"看起来同名"的目录都不许碰
    check('C6 原「固定草稿」在 C 步之后依然存在', os.path.isdir(sentinel_dir))


def t_fail_keeps_previous(td):
    """E4：第二个任务**中途失败**时，第一个任务的草稿内容必须保持不变。

    失败点怎么造：``--plan <不存在的文件>`` 是在**预留目录之后、写草稿之前**
    才去读的（真实执行顺序就是这么排的），所以它精确地命中
    「第二个任务已经拿到目录、但还没写完成」这个窗口 ——
    这正是"覆盖别人草稿"最危险的时刻。
    """
    LINES.append('-- E4 第二个任务中途失败 → 第一个任务的草稿必须完好')
    install = jianying_path.find_install_dir()
    if not install:
        LINES.append('  [SKIP] 无剪映，跳过')
        return
    picked = small_videos(1)
    if not picked:
        LINES.append('  [SKIP] 无素材，跳过')
        return
    td2 = os.path.join(td, 'failcase')
    os.makedirs(td2, exist_ok=True)
    mats = os.path.join(td2, 'materials')
    bucket = os.path.join(mats, '嘉兴园区', '厂房外部')
    os.makedirs(bucket, exist_ok=True)
    shutil.copy2(picked[0], os.path.join(bucket, '厂房外部_横版.mp4'))
    script = os.path.join(td2, '脚本.txt')
    io.open(script, 'w', encoding='utf-8').write('嘉兴产业园。\n欢迎考察！\n')
    env = os.environ.copy()
    env.update({'YUANLIU_RUN_MODE': 'test', 'YUANLIU_TEST_ROOT': td2,
                'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    base = [script, '--style', '3', '--materials', mats, '--park-name', '嘉兴',
            '--orientation', '横版', '--run-mode', 'test', '--out',
            os.path.join(td2, 'o')]

    # 任务 1：成功，落到「固定草稿」
    rc1, out1 = run_cli(base + ['--name', '固定草稿'], env)
    d1 = draft_dir_of(out1)
    check('E4a 任务1 成功且目录精确', rc1 == 0 and
          os.path.basename(d1) == '固定草稿', 'rc=%s / %s' % (rc1, d1))
    if not d1 or rc1 != 0:
        LINES.append(out1[-2000:])
        return
    f_content = os.path.join(d1, 'draft_content.json')
    f_meta = os.path.join(d1, 'draft_meta_info.json')
    before = snapshot(d1)
    h_content, h_meta = sha_file(f_content), sha_file(f_meta)

    # 任务 2：**覆盖同一个目录**，但在"写草稿之前"失败
    rc2, out2 = run_cli(base + ['--name', '固定草稿', '--overwrite',
                                '--plan', os.path.join(td2, '没有这个计划.json')], env)
    check('E4b 任务2 确实失败了（预留之后、写草稿之前）', rc2 != 0, 'rc=%s' % rc2)
    check('E4c 任务1 的 draft_content.json 逐字节未变',
          sha_file(f_content) == h_content)
    check('E4d 任务1 的 draft_meta_info.json 逐字节未变',
          sha_file(f_meta) == h_meta)
    after = snapshot(d1)
    changed = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    check('E4e 目标目录里没有任何文件被改动/新增（失败的运行不留痕迹）',
          not changed, str(changed[:6]))

    # 任务 1 的草稿仍然是一份能通过自检的完整草稿
    p = subprocess.run([PY, os.path.join(ROOT, 'tests', 'verify_draft.py'), d1],
                       capture_output=True, timeout=900, env=env)
    check('E4f 任务1 的草稿仍能通过 verify_draft 自检', p.returncode == 0,
          (p.stdout + p.stderr).decode('utf-8', 'replace').strip().splitlines()[-1:])


def t_gui_boundary():
    """D. GUI 正常流程不得传 --overwrite。"""
    LINES.append('-- D GUI 安全边界')
    gui = io.open(os.path.join(ROOT, 'workbench', 'workbench_gui.py'),
                  encoding='utf-8').read()
    sb = io.open(os.path.join(ROOT, 'workbench', 'storyboard_gui.py'),
                 encoding='utf-8').read()
    check('D1 workbench_gui 不传 --overwrite', '--overwrite' not in gui)
    check('D2 storyboard_gui 不传 --overwrite', '--overwrite' not in sb)
    check('D3 GUI 不自己拼草稿目录名（不再预测，只认 DRAFT_DIR=）',
          "startswith('DRAFT_DIR=')" in gui and 'draft_name += ' not in gui)
    check('D4 GUI 走 plan_target 的自动命名路径（explicit_name 只为空）',
          'explicit_name' not in gui)


def t_existing_suite():
    """50 并发等原有测试必须继续通过。"""
    LINES.append('-- 原有并发/防覆盖测试')
    p = subprocess.run([PY, os.path.join(ROOT, 'tests', 'test_draft_collision.py')],
                       capture_output=True, timeout=600)
    out = (p.stdout + p.stderr).decode('utf-8', 'replace')
    check('T1 test_draft_collision.py 仍然全部通过（含 50 并发创建）',
          p.returncode == 0,
          [l for l in out.strip().splitlines()[-3:]])
    if p.returncode:
        LINES.append(out[-2500:])


def main():
    LINES.append('== 显式 --name / --overwrite 契约 回归 ==')
    td = tempfile.mkdtemp(prefix='p05_ow_')
    try:
        t_unit_nonempty_job_id(td)
        t_unit_atomic_on_failure(td)
        t_real_cli(td)
        t_fail_keeps_previous(td)
        t_gui_boundary()
        t_existing_suite()
    finally:
        shutil.rmtree(td, ignore_errors=True)

    LINES.append('')
    LINES.append('通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    for f in FAILED:
        LINES.append('  FAILED: %s' % f)
    print('\n'.join(LINES))
    if FAILED:
        raise SystemExit(1)
    print('PASS explicit overwrite')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
