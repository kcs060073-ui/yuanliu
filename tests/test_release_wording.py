# -*- coding: utf-8 -*-
"""用户可见旧版本文案扫描（P05 二轮补丁 三）。

背景（用户原话）
----------------
「修复所有会直接展示给用户、可能让同事误以为安装错版本的文案」，
并点名三处：
  * ``00-部署说明.txt`` 仍写着 ``安装或升级P05.bat / install_p03.py``
  * ``scripts/doctor.py`` 的修复建议仍提示「使用 P03 完整安装包升级」
  * ``SKILL.md`` 标题仍是「P03 团队版运行原则」

**扫描范围必须包含压缩包外层部署文件**（00-部署说明.txt / install_pXX.py /
安装或升级PXX.bat），不能只扫 skill 子目录 —— 外层才是同事双击前看的东西。

判定口径：只挡"会让人当成当前版本去操作"的写法（安装器文件名、诊断建议、
用户可见标题、frontmatter 版本号）；历史叙述（"P04 及以前…"、升级来源版本）
显式放行 —— 用户明确说这类可以保留。

单独运行：
    python tests/test_release_wording.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # skill 根
PKG_ROOT = os.path.dirname(ROOT)                                     # 发布包根
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import pack_release

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        print('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        print('  [FAIL] %s %s' % (name, extra))


def read_text(p):
    try:
        return io.open(p, 'rb').read().decode('utf-8', 'ignore')
    except OSError:
        return ''


def skill_texts():
    """技能本体里**用户可见**的文本文件。"""
    exts = ('.py', '.md', '.txt', '.json', '.bat', '.csv')
    out = {}
    for cur, dirs, fs in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', '_tmp', '_tts_probe',
                                                'output', 'state', '_e2e')]
        for fn in fs:
            if fn.lower().endswith(exts):
                fp = os.path.join(cur, fn)
                out[os.path.relpath(fp, ROOT).replace('\\', '/')] = read_text(fp)
    return out


def outer_texts():
    """压缩包**外层**文件（同事双击前看到的东西）。"""
    out = {}
    for fn in sorted(os.listdir(PKG_ROOT)):
        low = fn.lower()
        if not (low.endswith(('.txt', '.py', '.bat', '.md')) or fn.startswith('install_')):
            continue
        fp = os.path.join(PKG_ROOT, fn)
        if os.path.isfile(fp):
            out['<包根>/' + fn] = read_text(fp)
    return out


def main():
    print('== 用户可见旧版本文案 扫描 ==')

    # ---------------- 1. 外层部署文件 ----------------
    # 只有"发布包根"才检查外层。已安装到 ~/.workbuddy/skills/ 时父目录不是包根，
    # 这时跳过外层检查（不能算失败 —— install_pXX 自检会在这个布局下跑全部测试）。
    outer = outer_texts()
    is_pkg_root = os.path.isfile(os.path.join(PKG_ROOT, '00-部署说明.txt'))
    if is_pkg_root:
        check('1a 找到外层部署文件（%d 个）' % len(outer), len(outer) >= 3,
              str(sorted(outer)))
    else:
        print('  [SKIP] 当前不是发布包布局（%s 下没有 00-部署说明.txt），跳过外层检查'
              % PKG_ROOT)

    deploy = [v for k, v in outer.items() if k.endswith('00-部署说明.txt')]
    check('1b 00-部署说明.txt 存在（包外）', bool(deploy) or not is_pkg_root)

    if deploy:
        dt = deploy[0]
        check('2a 部署说明里的安装器就是当前那一版（install_p05.py）',
              'install_p05.py' in dt and 'install_p03.py' not in dt,
              [l for l in dt.splitlines() if 'install_p' in l][:3])
        check('2b 部署说明不再出现旧安装器批处理名',
              '安装或升级P03.bat' not in dt)
        check('2c 部署说明首部的版本串是当前版本',
              bool(re.search(r'P0[1-4]', dt.splitlines()[0])) is False,
              dt.splitlines()[0] if dt.splitlines() else '')
        check('2d 部署说明尾部"怎么重新打包"示例里的版本也是当前版本',
              'YYMMDD-P0' not in dt or re.search(r'YYMMDD-P0[1-4]', dt) is None,
              [l for l in dt.splitlines() if 'pack_release' in l][:2])

    # 安装器自身与它的文件名/内容一致
    ins = [k for k in outer if os.path.basename(k).startswith('install_p')]
    check('3a 包根只提供一个安装脚本', len(ins) == 1 or not is_pkg_root, str(ins))
    if ins:
        body = outer[ins[0]]
        check('3b 安装脚本名与版本一致（install_p05.py 里不含 p03）',
              'p03' not in os.path.basename(ins[0]).lower() and
              'install_p03' not in body, os.path.basename(ins[0]))
        check('3c 安装脚本的兜底版本串不是旧版',
              not re.search(r"FALLBACK_VERSION\s*=\s*'260916-P0[1-4]'", body))

    # ---------------- 4. 全量文案扫描（外层 + 技能本体）----------------
    allhits = []
    for label, txt in list(outer.items()) + list(skill_texts().items()):
        allhits += pack_release.find_stale_wording(txt, label)
    check('4 全包没有"指向旧版本的有效操作指令"', not allhits,
          '；'.join(allhits[:8]))

    # ---------------- 5. 逐处点名复核 ----------------
    doctor = read_text(os.path.join(ROOT, 'scripts', 'doctor.py'))
    check('5a doctor 的修复建议不再写死 P03',
          'P03 完整安装包' not in doctor and 'P0' + '3 完整安装包' not in doctor)
    check('5b doctor 的修复建议是动态生成的（读清单/扫安装器）',
          '_upgrade_hint' in doctor and
          ('release_version' in doctor or 'install_p' in doctor))
    check('5c doctor 动态建议里带"回滚"说明（用户能安心）',
          '回滚' in doctor)

    skill = read_text(os.path.join(ROOT, 'SKILL.md'))
    check('6a SKILL.md 不再有带旧版本的用户可见标题',
          not re.search(r'^#{1,6}[^\n]*\bP0[1-4]\b', skill, re.M),
          [l for l in skill.splitlines() if re.search(r'^#{1,6}[^\n]*\bP0[1-4]\b', l)][:3])
    check('6b SKILL.md 的「团队版运行原则」标题已中性化',
          '团队版运行原则' in skill)
    check('6c SKILL.md frontmatter 版本是当前版本',
          not re.search(r'^\s*version:\s*\d{6}-P0[1-4]\b', skill, re.M),
          [l for l in skill.splitlines() if l.strip().startswith('version:')][:2])

    # ---------------- 6. 版本同步函数本身要认全四种写法 ----------------
    fake = ('安装或升级P03.bat / install_p03.py\n'
            '（260916-P03 团队版）\n'
            'P03 团队版运行原则\n'
            'python scripts/pack_release.py --version YYMMDD-P03\n')
    syn = pack_release.sync_deploy_text(fake.encode('utf-8'), '260916-P05').decode('utf-8')
    check('7a 同步覆盖「安装或升级Pxx.bat」', '安装或升级P05.bat' in syn, syn)
    check('7b 同步覆盖「install_pXX.py」（老正则漏掉的小写文件名）',
          'install_p05.py' in syn and 'install_P05.py' not in syn, syn)
    check('7c 同步覆盖长版本串', '260916-P05' in syn, syn)
    check('7d 同步覆盖光秃秃的 Pxx', 'P03 团队版' not in syn and 'P05 团队版' in syn, syn)
    check('7e 同步后自检：不残留旧版安装器指令',
          not pack_release.find_stale_wording(syn, 'synthetic'), str(syn))

    print('\n通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for x in FAILED:
            print('  FAILED: %s' % x)
        raise SystemExit(1)
    print('PASS release wording')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
