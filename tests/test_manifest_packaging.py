# -*- coding: utf-8 -*-
"""发布清单 与 打包口径 必须完全一致（P04 的 tests/_tmp 事故）。

事故回顾
--------
``release_manifest.collect()`` 曾经只排除 ``__pycache__``，而
``pack_release.excluded()`` additionally 排除了 ``_tmp`` / ``_tts_probe`` /
``output`` / ``state``。结果：测试跑完留下的 ``tests/_tmp/verify_16568.json``
被写进了 ``release_manifest.json``，但**不会被打进 zip**。

同事侧的后果是致命的：
  解包 → ``install_p05.py`` 覆盖安装 → 跑 ``release_manifest.py verify``
  → 报「缺少文件：tests/_tmp/verify_16568.json」→ 自检失败 → **自动回滚**。
也就是"包看着没问题，装完必回滚"。

本测试锁死这条不变量：
  * 清单里列的每个文件，打包时都必须会被收进去（否则是幽灵文件）；
  * 磁盘上每个会被打包的文件，都必须在清单里（否则升级时哈希校验漏项）。

单独运行：
    python tests/test_manifest_packaging.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import pack_release
import release_manifest

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        print('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        print('  [FAIL] %s %s' % (name, extra))


def would_be_packed(rel):
    """pack_release 会不会把这个相对路径打进 zip。"""
    rel = rel.replace('\\', '/')
    if rel.split('/')[0] in pack_release.EX_TOP:
        return False
    return not pack_release.excluded(rel)


def is_protected_local(rel):
    """受保护的本地资产（装了也不覆盖用户那份）—— 不进清单是**正常的**。

    ``config/materials_baseline.json`` 会随包发出去（给全新安装当占位），
    但安装/升级时绝不覆盖用户已有的基准，所以它的哈希不能进清单：
    清单要求"包内内容 == 安装后内容"，而这类文件装完必然还是用户自己那份。
    """
    return rel.replace('\\', '/') in release_manifest.PROTECTED_LOCAL


def main():
    print('== 发布清单 / 打包口径 一致性 ==')
    manifest = release_manifest.collect(ROOT)
    check('1 清单非空（%d 个文件）' % len(manifest), len(manifest) > 30)

    # 1) 幽灵文件：清单里有、打包不会带
    ghosts = [r for r in manifest if not would_be_packed(r)]
    check('2 清单里没有"不会被打包"的幽灵文件', not ghosts,
          '；'.join(ghosts[:8]))

    # 2) 漏项：打包会带、清单里没有
    on_disk = []
    for cur, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in release_manifest.EXCLUDE_DIRS]
        for fn in files:
            fp = os.path.join(cur, fn)
            rel = os.path.relpath(fp, ROOT).replace('\\', '/')
            top = rel.split('/')[0]
            if top in release_manifest.EXCLUDE_TOP:
                continue
            if top not in release_manifest.OFFICIAL_DIRS + ('',):
                # 顶层文件只收 OFFICIAL_FILES
                if rel not in release_manifest.OFFICIAL_FILES:
                    continue
            if any(x in rel for x in release_manifest.EXCLUDE):
                continue
            if not would_be_packed(rel):
                continue
            if is_protected_local(rel):
                continue          # 用户资产：可能进包，但不进清单是设计如此
            on_disk.append(rel)
    missing = sorted(set(on_disk) - set(manifest))
    check('3 每个会打包的官方文件都在清单里', not missing, '；'.join(missing[:8]))
    check('3b 受保护的本地资产确实不在清单里',
          not [r for r in manifest if is_protected_local(r)],
          str([r for r in manifest if is_protected_local(r)]))

    # 3) 关键排除项逐个确认
    probes = {
        'tests/_tmp/verify_1.json': False,
        'output/jobs/x/receipt.json': False,
        'state/drafts/x.json': False,
        'config/gui_settings.json': False,
        'config/materials_baseline.json': False,   # 用户资产，不进清单
        'config/semantics.json': True,             # 官方配置，必须进清单
        'scripts/__pycache__/a.pyc': False,
        'scripts/make_park_video.py': True,
        'templates/style1_jiaxing.json': True,
        'SKILL.md': True,
        '语义规则分配表.xlsx': True,
        'release_manifest.json': False,   # 顶层非官方文件，不进清单
    }
    bad = []
    for rel, in_manifest in probes.items():
        got = rel in manifest
        if got != in_manifest:
            bad.append('%s 期望%s 实为%s' % (rel, in_manifest, got))
    check('4 关键路径的收录/排除符合预期', not bad, '；'.join(bad))

    # 4) 两侧的排除实现必须"同名单"（防止以后只改一边）
    dirs_m = set(release_manifest.EXCLUDE_DIRS)
    dirs_p = set(pack_release.EX_DIRS)
    check('5 目录级排除名单一致', dirs_m == dirs_p,
          'manifest=%s pack=%s' % (sorted(dirs_m), sorted(dirs_p)))
    check('6 顶层资产排除名单一致',
          set(release_manifest.EXCLUDE_TOP) == set(pack_release.EX_TOP),
          'manifest=%s pack=%s' % (sorted(release_manifest.EXCLUDE_TOP),
                                   sorted(pack_release.EX_TOP)))

    # 5) 真的能装：模拟"清单 vs 解包后的文件"，用现成清单逐条核对磁盘
    errs = release_manifest.verify(ROOT)
    check('7 当前磁盘与清单完全一致（verify 无错）', not errs, '；'.join(errs[:5]))

    # 6) 打包 + 解包后清单仍能校验通过（最接近同事侧的验证）
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 't.zip')
        # 只打技能本体那部分：复用 pack_release 的收录规则。
        # release_manifest.json 自己不在清单里（清单不给自己记哈希），
        # 但它确实会进 zip —— 所以这里照实把它一起打进去。
        import zipfile
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for rel in manifest + ['release_manifest.json']:
                fp = os.path.join(ROOT, rel.replace('/', os.sep))
                if os.path.isfile(fp):
                    z.write(fp, rel)
        ex = os.path.join(td, 'x')
        with zipfile.ZipFile(out) as z:
            z.extractall(ex)
        errs2 = release_manifest.verify(ex)
        check('8 按清单打包再解包，校验仍通过（同事侧的等价验证）',
              not errs2, '；'.join(errs2[:5]))
        check('9 解包后清单文件本身也在',
              os.path.isfile(os.path.join(ex, 'release_manifest.json')))

    print('\n通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for x in FAILED:
            print('  FAILED: %s' % x)
        raise SystemExit(1)
    print('PASS manifest packaging')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
