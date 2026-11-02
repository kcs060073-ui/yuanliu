# -*- coding: utf-8 -*-
"""一键升级（P1-③）：把新版 skill 套件升级到本机，保留本地资产。

解决什么
--------
改一条语义规则/修一个 bug，以前要打包 15 份 zip 挨个发。现在负责人打包一份
「新版目录」（或 zip），15 人各跑一条命令完成升级 —— 机器自动做备份、保留
本地资产、跑回归，失败自动回滚。

本地资产（**绝不覆盖**）
------------------------
- output/production_log.csv   产出台账（15 人排查的原始凭证）
- output/material_usage.json  素材使用频次
- config/gui_settings.json    个人设置（素材路径/剪辑者…）
- config/materials_baseline.json  团队基准（虽然它本该随新包发，但保底不覆盖）

config/semantics.json 的本地改动
--------------------------------
人人可改的配置。升级若发现**新旧不同**：把当前版本备份成
config/semantics.json.pre-upgrade-<时间戳> 再覆盖 —— 这样你随手改的词不会丢，
官方新版也会生效。若发现新版没有动 semantics.json，则保留本地改动不覆盖。

用法
----
    python scripts/upgrade.py --from D:\\pack\\park-video-v3.6    # 目录
    python scripts/upgrade.py --from D:\\pack\\park-video-v3.6.zip # zip
    python scripts/upgrade.py --from <源> --dry-run               # 只预览不动手
    python scripts/upgrade.py --rollback                          # 回到最近一次备份

升级流程
--------
备份 → 比对（保护/覆盖清单）→ 复制 → 自检（py_compile + 语义配置 + 回归）
→ 任一失败自动回滚，出问题也不至于把能用的旧版弄坏。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

from atomic_io import atomic_write_bytes
import release_manifest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
SKILL_NAME = os.path.basename(SKILL_DIR)
BACKUP_ROOT = os.path.join(os.path.dirname(SKILL_DIR), '_backup')

# 相对 SKILL_DIR 的官方文件集合（这些由新版包提供）
# 只列出顶层目录；递归复制
OFFICIAL_DIRS = ('scripts', 'tests', 'templates', 'config', 'workbench')
OFFICIAL_FILES = ('SKILL.md', 'PROJECT_STATUS.md', '语义规则分配表.xlsx',
                  'release_manifest.json')

# 绝不覆盖的本地资产（相对路径前缀）
PROTECT_PREFIX = ('output' + os.sep,)
PROTECT_FILES = (
    'config' + os.sep + 'gui_settings.json',
    'config' + os.sep + 'materials_baseline.json',
)


def log(msg):
    print('[升级] ' + msg)


# ---------------------------------------------------------------------------
# 收集源文件
# ---------------------------------------------------------------------------
def resolve_source(src):
    """把 zip 或目录解析成 (root_dir, tmp_cleanup_callable)。"""
    if os.path.isdir(src):
        return src, None
    if os.path.isfile(src) and src.lower().endswith('.zip'):
        import tempfile
        tmp = tempfile.mkdtemp(prefix='park_upgrade_')
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        # zip 里可能包了一层同名目录，也可能直接是 scripts/ 等
        inner = os.path.join(tmp, SKILL_NAME)
        root = inner if os.path.isdir(inner) else tmp
        return root, (lambda: shutil.rmtree(tmp, ignore_errors=True))
    raise SystemExit('源不是目录也不是 zip：%s' % src)


def verify_release_source(root):
    """发布包必须带清单，且所有官方文件哈希一致。"""
    mf = os.path.join(root, 'release_manifest.json')
    if not os.path.isfile(mf):
        raise SystemExit('新版包缺少 release_manifest.json，拒绝升级')
    try:
        with open(mf, encoding='utf-8') as f:
            manifest = json.load(f)
    except Exception as e:
        raise SystemExit('版本清单无法读取：%s' % e)
    errors = release_manifest.verify(root, manifest)
    if errors:
        raise SystemExit('版本清单校验失败：\n  ' + '\n  '.join(errors[:20]))
    return manifest


def collect_files(root):
    """收集源里的官方文件 → [(relpath, abs_path)]（相对 skill 根）。

    排除口径必须与 release_manifest.collect / pack_release 一致：只收
    "会被发出去的文件"。口径不一致会让清单里出现不存在的文件，
    同事装完校验直接失败（P04 的 ``tests/_tmp`` 事故）。
    """
    out = []
    for d in OFFICIAL_DIRS:
        if d in release_manifest.EXCLUDE_TOP:
            continue
        sd = os.path.join(root, d)
        if not os.path.isdir(sd):
            continue
        for cur, dirs, fnames in os.walk(sd):
            dirs[:] = [x for x in dirs if x not in release_manifest.EXCLUDE_DIRS]
            for fn in fnames:
                if fn.endswith(('.pyc', '.pyo')) or '__pycache__' in cur:
                    continue
                fp = os.path.join(cur, fn)
                rel = os.path.relpath(fp, root).replace('\\', '/')
                if any(x in rel for x in release_manifest.EXCLUDE):
                    continue
                out.append((rel, fp))
    for f in OFFICIAL_FILES:
        fp = os.path.join(root, f)
        if os.path.isfile(fp):
            out.append((f, fp))
    return out


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------
def same_file(a, b):
    try:
        with open(a, 'rb') as fa, open(b, 'rb') as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def plan(src_files, dry=False):
    """生成计划：to_copy / protect_kept / local_only / semantics_action。"""
    to_copy, changed, kept = [], [], 0
    for rel, src_abs in src_files:
        dst = os.path.join(SKILL_DIR, rel)
        if rel in PROTECT_FILES or rel.startswith(PROTECT_PREFIX):
            kept += 1
            continue
        if os.path.isfile(dst) and same_file(dst, src_abs):
            continue          # 没变化不复制
        to_copy.append((rel, src_abs))
        if rel != os.path.join('config', 'semantics.json') and os.path.isfile(dst):
            changed.append(rel)
    # 本地独有（旧版有、新版没有）的 scripts 文件 —— 提示不删除
    local_only = []
    old_files = set()
    for d in OFFICIAL_DIRS:
        sd = os.path.join(SKILL_DIR, d)
        if os.path.isdir(sd):
            for cur, _, fnames in os.walk(sd):
                for fn in fnames:
                    if '__pycache__' not in cur and not fn.endswith(('.pyc', '.pre-upgrade')):
                        old_files.add(os.path.relpath(os.path.join(cur, fn), SKILL_DIR))
    new_rels = {rel for rel, _ in src_files}
    local_only = sorted(r for r in old_files - new_rels - set(PROTECT_FILES)
                        if '_tmp' not in r and '_tts_probe' not in r
                        and '__pycache__' not in r)

    sem_rel = os.path.join('config', 'semantics.json')
    sem_src = next((s for r, s in src_files if r == sem_rel), None)
    sem_action = 'none'
    if sem_src:
        sem_local = os.path.join(SKILL_DIR, sem_rel)
        if not os.path.isfile(sem_local) or not same_file(sem_local, sem_src):
            sem_action = 'override_with_backup'
    return {'to_copy': to_copy, 'kept': kept, 'changed': changed,
            'local_only': local_only, 'sem_action': sem_action,
            'new_count': len(src_files)}


# ---------------------------------------------------------------------------
# 备份 / 回滚
# ---------------------------------------------------------------------------
def make_backup(tag='pre-upgrade'):
    """备份整个 skill（跳过 __pycache__/_tmp/output）。返回备份路径。"""
    ts = time.strftime('%Y%m%d-%H%M%S')
    dst = os.path.join(BACKUP_ROOT, '%s-%s-%s' % (SKILL_NAME, tag, ts))
    os.makedirs(dst, exist_ok=True)
    n = 0
    for cur, dirs, fnames in os.walk(SKILL_DIR):
        dirs[:] = [d for d in dirs
                   if d not in ('__pycache__', '_tmp', 'output', '_tts_probe')]
        for fn in fnames:
            if fn.endswith(('.pyc', '.log')):
                continue
            sp = os.path.join(cur, fn)
            rp = os.path.relpath(sp, SKILL_DIR)
            dp = os.path.join(dst, rp)
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copy2(sp, dp)
            n += 1
    log('已备份 %d 个文件 → %s' % (n, dst))
    return dst


def rollback_to(backup_dir):
    """用备份恢复整个 skill。

    不依赖"整目录删除重建"（沙箱/占用会拦 rmtree）：
    先删目标里备份没有的多余文件（删不掉仅警告，主体靠复制恢复），
    再逐个复制备份文件覆盖。
    """
    if not os.path.isdir(backup_dir):
        raise SystemExit('备份目录不存在：%s' % backup_dir)
    bfiles = set()
    for cur, _, fnames in os.walk(backup_dir):
        for fn in fnames:
            bfiles.add(os.path.relpath(os.path.join(cur, fn), backup_dir))
    removed = kept = 0
    for d in OFFICIAL_DIRS:
        sd = os.path.join(SKILL_DIR, d)
        if not os.path.isdir(sd):
            continue
        for cur, _, fnames in os.walk(sd):
            for fn in fnames:
                if fn.endswith(('.pyc', '.pyo')):
                    continue
                rel = os.path.relpath(os.path.join(cur, fn), SKILL_DIR)
                if rel not in bfiles:
                    try:
                        os.remove(os.path.join(cur, fn))
                        removed += 1
                    except OSError:
                        kept += 1   # 删不掉仅警告；原文件已由下面复制恢复
    for cur, _, fnames in os.walk(backup_dir):
        for fn in fnames:
            sp = os.path.join(cur, fn)
            rp = os.path.relpath(sp, backup_dir)
            dp = os.path.join(SKILL_DIR, rp)
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copy2(sp, dp)
    if kept:
        log('已回滚到升级前状态（%d 个多余文件未能自动删除，不影响使用，可手动清理）' % kept)
    else:
        log('已回滚到升级前状态，%d 个多余文件已清除 → %s' % (removed, backup_dir))


def _refresh_desktop_shortcut():
    """升级后把桌面「源流工作台」快捷方式补齐/更新（幂等；失败静默）。

    用户可能删了桌面图标、换了机器、或 install.bat 版本更新 —— 升级顺手
    重跑一次 install（桌面已存在快捷方式时 install 幂等跳过，不会重复建）。
    """
    bat = os.path.join(os.path.dirname(HERE), 'workbench', 'install.bat')
    if not os.path.isfile(bat):
        return
    try:
        subprocess.Popen(['cmd', '/c', bat],
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        log('桌面快捷方式已刷新（workbench/install.bat，幂等）')
    except Exception:
        pass


def latest_backup():
    if not os.path.isdir(BACKUP_ROOT):
        return None
    subs = sorted(os.listdir(BACKUP_ROOT), reverse=True)
    for s in subs:
        p = os.path.join(BACKUP_ROOT, s)
        if os.path.isdir(p):
            return p
    return None


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def verify_tree(root):
    """对指定目录自检：编译 + 配置 + 全部 test_*.py。"""
    errs = []
    py = sys.executable
    for sub in ('scripts', 'workbench'):
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.py'):
                continue
            r = subprocess.run([py, '-m', 'py_compile', os.path.join(d, fn)],
                               capture_output=True)
            if r.returncode != 0:
                errs.append('编译失败：%s' % fn)
    if errs:
        return errs
    # 2) 语义配置
    scripts_dir = os.path.join(root, 'scripts')
    r = subprocess.run([py, os.path.join(scripts_dir, 'semantics_config.py'), '--check'],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    if r.returncode != 0:
        errs.append('语义配置校验失败')
    env = os.environ.copy()
    env['YUANLIU_RUN_MODE'] = 'test'
    import tempfile
    env['YUANLIU_TEST_ROOT'] = tempfile.mkdtemp(prefix='yuanliu_upgrade_test_')
    try:
        tests_dir = os.path.join(root, 'tests')
        for fn in sorted(os.listdir(tests_dir)):
            if not (fn.startswith('test_') and fn.endswith('.py')):
                continue
            r = subprocess.run([py, os.path.join(tests_dir, fn)], capture_output=True,
                               text=True, encoding='utf-8', errors='replace', env=env)
            if r.returncode != 0:
                errs.append('回归未通过：%s' % fn)
    finally:
        shutil.rmtree(env['YUANLIU_TEST_ROOT'], ignore_errors=True)
    return errs


def verify_after():
    return verify_tree(SKILL_DIR)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from', dest='src', help='新版包：目录或 zip')
    ap.add_argument('--dry-run', action='store_true', help='只预览不动手')
    ap.add_argument('--rollback', action='store_true', help='回滚到最近一次备份')
    args = ap.parse_args()

    if args.rollback:
        b = latest_backup()
        if not b:
            print('没有可回滚的备份')
            return 1
        rollback_to(b)
        return 0

    if not args.src:
        print(__doc__)
        return 1

    root, cleanup = resolve_source(args.src)
    try:
        manifest = verify_release_source(root)
        log('发布清单校验通过：%s（%d 个官方文件）' %
            (manifest.get('release_version', '-'), len(manifest.get('files', []))))
        preflight_errors = verify_tree(root)
        if preflight_errors:
            raise SystemExit('新版暂存区预检失败，未修改当前版本：\n  ' +
                             '\n  '.join(preflight_errors))
        log('暂存区预检通过（编译 + 配置 + 全量回归）')
        src_files = collect_files(root)
        if not src_files:
            raise SystemExit('源里没有找到 scripts/config 等官方文件，确认打包正确？')
        p = plan(src_files)
        print('升级计划：')
        print('  新增/变更 %d 个文件' % len(p['to_copy']))
        for rel, _ in p['to_copy']:
            print('    → %s' % rel)
        print('  保留本地  %d 个（output 台账/usage、gui_settings、基准等）' % p['kept'])
        if p['sem_action'] == 'override_with_backup':
            print('  语义配置  config/semantics.json 新旧不同：先备份本地改动再覆盖'
                  '（新版语义规则要生效）')
        elif p['sem_action'] == 'none':
            print('  语义配置  新版未改动 semantics.json，保留你本地的')
        if p['local_only']:
            print('  本地独有（新版没有，不动）%s' % ', '.join(p['local_only'][:8]))
        if args.dry_run:
            print('\n[干跑] 未做任何改动。去掉 --dry-run 执行。')
            return 0

        if not p['to_copy'] and p['sem_action'] != 'override_with_backup':
            log('没有需要更新的文件 —— 你已经在最新版。')
            return 0

        # 执行
        bak = make_backup()
        try:
            for rel, src_abs in p['to_copy']:
                dst = os.path.join(SKILL_DIR, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(src_abs, 'rb') as sf:
                    atomic_write_bytes(dst, sf.read())
            # semantics 本地改动备份
            if p['sem_action'] == 'override_with_backup':
                sem_local = os.path.join(SKILL_DIR, 'config', 'semantics.json')
                if os.path.isfile(sem_local):
                    ts = time.strftime('%Y%m%d-%H%M%S')
                    bf = sem_local + '.pre-upgrade-' + ts
                    shutil.copy2(sem_local, bf)
                    log('本地 semantics.json 已备份 → %s' % os.path.basename(bf))
            log('文件复制完成，跑自检…')
            errs = verify_after()
            if errs:
                raise RuntimeError('；'.join(errs))
            log('升级成功 ✅ 自检通过（编译 + 语义配置 + 回归）')
            log('提示：改完语义配置记得跑 python tests/test_semantic_blocks.py')
            _refresh_desktop_shortcut()
        except Exception as e:
            log('升级失败：%s' % e)
            log('自动回滚到升级前状态…')
            rollback_to(bak)
            return 1
        finally:
            if cleanup:
                cleanup()
        return 0
    finally:
        if cleanup:
            cleanup()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
