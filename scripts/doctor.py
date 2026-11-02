# -*- coding: utf-8 -*-
r"""环境自检 doctor（P1-②）：15 人环境各异，一人一坑，先查再出片。

查什么
------
1. Python：能跑 tkinter（GUI 依赖）
2. 剪映安装目录：能探测到（Windows 加解密依赖 videoeditor.dll；macOS 为 libvideoeditor.dylib；兼容 <版本>\ 与 Apps\<版本>\ 布局）
3. 草稿根目录：存在且可写（缺省读 config/gui_settings.json，再缺省 D:/JianyingPro Drafts）
4. 素材库路径：存在；不存在时给出提示（缺省读 config/gui_settings.json）
5. 语义配置：能加载且结构校验通过（P0-② 的启动自检）
6. 核心模块：scripts 下关键 .py 都能 import（少文件立刻暴露）
7. 素材库基准：本地与团队基准是否一致（P1-①）

用法
----
    python scripts/doctor.py                    # 全量检查，打印报告
    python scripts/doctor.py --quiet            # 只打印有问题/警告的项
    python scripts/doctor.py --json             # 机器可读输出

退出码：0 = 全部 PASS；1 = 有 FAIL（会拦出片）；2 = 只有 WARN（可出片但建议处理）
"""
import argparse
import json
import os
import platform
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
CONFIG_DIR = os.path.join(SKILL_DIR, 'config')
WORKBENCH_DIR = os.path.join(SKILL_DIR, 'workbench')
DEFAULT_OUT = r'D:\JianyingPro Drafts'


def _python_here():
    return sys.executable


def _sys_path_setup():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------
def ch_python():
    """Python 版本（必须 3.8+）+ tkinter（只对工作台 GUI 必要）。

    注意：命令行出片（make_park_video.py）不需要 tkinter —— 很多精简
    Python 环境没有它也能正常出片。因此 tkinter 缺失只算 WARN，不算 FAIL，
    避免误拦 CLI 用户。只有「要开工作台」的人需要处理。
    """
    ver = sys.version.split()[0]
    major, minor = (int(x) for x in ver.split('.')[:2])
    if (major, minor) < (3, 8):
        return {'name': 'Python 版本', 'status': 'FAIL', 'msg': '%s（需 3.8+）' % ver,
                'fix': '安装 python.org 官方 Python 3.10+，勾选 Add to PATH'}
    try:
        import tkinter  # noqa
        return {'name': 'Python', 'status': 'PASS', 'msg': '%s（含 tkinter）' % ver}
    except Exception:
        return {'name': 'Python tkinter', 'status': 'WARN',
                'msg': '%s 缺 tkinter —— 命令行出片不受影响，但打不开工作台 GUI' % ver,
                'fix': '要开工作台就装官方 Python（勾选 tcl/tk 组件）；只命令行出片可忽略'}


def ch_jianying(hint=''):
    """剪映安装目录探测（复用 make_park_video 逻辑，避免两套）。

    加解密库名走平台抽象（Windows=videoeditor.dll / macOS=libvideoeditor.dylib），
    不再写死 .dll，否则 Mac 上必然误报「dll 缺失」。
    """
    try:
        import make_park_video as mpv
        import platform_paths as _pp
        d = mpv.find_install_dir(hint)
        ver = os.path.basename(os.path.dirname(d))
        lib = os.path.join(d, _pp.native_lib_name())
        return {'name': '剪映安装目录', 'status': 'PASS',
                'msg': '%s（%s）' % (ver, '库存在' if os.path.isfile(lib) else '库缺失'),
                'fix': ''}
    except Exception as e:
        return {'name': '剪映安装目录', 'status': 'FAIL',
                'msg': str(e),
                'fix': '安装剪映专业版；或在生成/校验时用 --install 指定目录'}


def ch_drafts_dir(out_dir=None):
    d = out_dir or DEFAULT_OUT
    try:
        ok_dir = os.path.isdir(d)
        ok_w = os.access(d, os.W_OK) if ok_dir else False
        if ok_dir and ok_w:
            return {'name': '草稿根目录', 'status': 'PASS', 'msg': d, 'fix': ''}
        return {'name': '草稿根目录', 'status': 'WARN',
                'msg': '%s（%s）' % (d, '不存在' if not ok_dir else '不可写'),
                'fix': '创建该目录，或在工作台/命令里改 --out 指向真实草稿库'}
    except Exception as e:
        return {'name': '草稿根目录', 'status': 'FAIL', 'msg': str(e), 'fix': ''}


def ch_materials(materials_dir=None, verbose=True):
    if not materials_dir:
        return {'name': '素材库路径', 'status': 'WARN', 'msg': '未配置',
                'fix': '工作台里选一个素材库目录，或传 --materials'}
    if not os.path.isdir(materials_dir):
        return {'name': '素材库路径', 'status': 'FAIL', 'msg': materials_dir,
                'fix': '检查路径是否存在；素材库每人本地一份，拷贝完整后再跑'}
    vids = sum(1 for r, _, fs in os.walk(materials_dir)
               for f in fs if f.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')))
    imgs = sum(1 for r, _, fs in os.walk(materials_dir)
               for f in fs if f.lower().endswith(
                   ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.jfif')))
    n = vids
    msg = '%s（%d 个视频 / %d 张图片）' % (materials_dir, vids, imgs)
    if verbose:
        try:
            import production_log
            msg += '  指纹 ' + production_log.materials_version(materials_dir)
        except Exception:
            pass
    if n == 0:
        return {'name': '素材库路径', 'status': 'WARN', 'msg': msg + '（空库）',
                'fix': '素材库里没有视频文件，拷漏了？'}
    return {'name': '素材库路径', 'status': 'PASS', 'msg': msg, 'fix': ''}


def ch_semantics():
    try:
        import semantics_config
        semantics_config.load()
        return {'name': '语义配置', 'status': 'PASS', 'msg': 'config/semantics.json 校验通过',
                'fix': ''}
    except Exception as e:
        return {'name': '语义配置', 'status': 'FAIL', 'msg': str(e)[:120],
                'fix': '修复 config/semantics.json；改完跑 python tests/test_semantic_blocks.py'}


def ch_modules():
    mods = ['material_matcher', 'subtitle_split', 'draft_builder', 'jy_crypto',
            'semantics_config', 'production_log', 'material_governance',
            'bgm_pool', 'make_park_video']
    bad = []
    for m in mods:
        try:
            __import__(m)
        except Exception as e:
            bad.append('%s(%s)' % (m, type(e).__name__))
    if bad:
        return {'name': '核心模块', 'status': 'FAIL', 'msg': '导入失败：%s' % ' '.join(bad),
                'fix': 'skill 目录不完整（漏文件？），重新解压一份完整的'}
    return {'name': '核心模块', 'status': 'PASS', 'msg': '%d 个模块均可导入' % len(mods),
            'fix': ''}


def ch_baseline(materials_dir=None):
    if not materials_dir or not os.path.isdir(materials_dir):
        return {'name': '素材库基准', 'status': 'SKIP', 'msg': '未配置素材库', 'fix': ''}
    try:
        import material_governance as mg
        ck = mg.check_baseline(materials_dir)
        if ck['status'] == 'OK':
            return {'name': '素材库基准', 'status': 'PASS',
                    'msg': '%s 与团队基准一致' % ck['name'], 'fix': ''}
        if ck['status'] == 'DRIFT':
            return {'name': '素材库基准', 'status': 'WARN',
                    'msg': '%s 与基准不一致：本地 %s / 基准 %s' % (ck['name'], ck['local'],
                                                             ck['baseline']),
                    'fix': '素材被换过或拷漏：重新拷贝素材，或让负责人重设基准'}
        return {'name': '素材库基准', 'status': 'SKIP',
                'msg': '%s 未设基准（负责人跑 --set-baseline）' % ck['name'], 'fix': ''}
    except Exception as e:
        return {'name': '素材库基准', 'status': 'SKIP', 'msg': str(e)[:80], 'fix': ''}


def ch_usage_writable():
    probe_path = ''
    try:
        import tempfile
        # P05：走 runtime_context.output_root()，测试模式下落到隔离根，
        # 不再为了做一次写探针而在生产 output/ 下建目录。
        import runtime_context
        root = runtime_context.output_root()
        os.makedirs(root, exist_ok=True)
        fd, probe_path = tempfile.mkstemp(prefix='.write_probe_', dir=root)
        with os.fdopen(fd, 'wb') as f:
            f.write(b'ok')
        os.remove(probe_path)
        probe_path = ''
        return {'name': '频次记录', 'status': 'PASS',
                'msg': '%s 可写' % os.path.basename(root.rstrip('/\\')) + '/', 'fix': ''}
    except Exception as e:
        return {'name': '频次记录', 'status': 'WARN', 'msg': str(e)[:80],
                'fix': '不影响出片，但素材降权与使用统计会失效'}
    finally:
        if probe_path:
            try:
                os.remove(probe_path)
            except OSError:
                pass


def _release_version():
    """当前安装的版本号（读发布清单；读不到返回空串）。"""
    try:
        with open(os.path.join(SKILL_DIR, 'release_manifest.json'), encoding='utf-8') as f:
            return str((json.load(f) or {}).get('release_version') or '').strip()
    except Exception:
        return ''


def _upgrade_hint():
    """「版本清单有问题」时给出的修复建议 —— **绝不写死版本号**。

    这个补丁之前，这里硬写着"用旧版本的完整安装包升级"，升到 P04/P05 后忘了改：
    同事照着看会以为**自己装的是旧版**、或者要去找一个已经没人发的安装包。
    用户可见文案一旦写死版本就必然过期，能推出来就别写。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.dirname(os.path.dirname(here)),   # 发布包根（含安装器）
              os.path.dirname(here)):                   # skill 根
        try:
            names = [fn for fn in os.listdir(d)
                     if (fn.lower().startswith('install_p') and fn.lower().endswith('.py'))
                     or (fn.startswith('安装或升级') and fn.lower().endswith('.bat'))]
        except OSError:
            continue
        if names:
            names.sort(key=lambda s: (not s.lower().endswith('.py'), s))
            return '用发布包里的 %s 重新安装（会自动备份，失败自动回滚）' % names[0]
    ver = _release_version()
    if ver:
        return ('用「%s」这一版的完整发布包重新安装（跑包里的安装器；会自动备份、'
                '失败自动回滚）' % ver)
    return '用最新完整发布包重新安装（会自动备份、失败自动回滚）'


def ch_release_manifest():
    try:
        import release_manifest
        errs = release_manifest.verify(SKILL_DIR)
        if errs:
            return {'name': '版本清单', 'status': 'WARN', 'msg': errs[0],
                    'fix': _upgrade_hint()}
        data = json.load(open(os.path.join(SKILL_DIR, 'release_manifest.json'), encoding='utf-8'))
        return {'name': '版本清单', 'status': 'PASS',
                'msg': '%s，%d 个官方文件' %
                       (data.get('release_version', '-'), len(data.get('files', []))), 'fix': ''}
    except Exception as e:
        return {'name': '版本清单', 'status': 'WARN', 'msg': str(e)[:100],
                'fix': _upgrade_hint()}


def ch_material_manifest(materials_dir):
    if not materials_dir or not os.path.isdir(materials_dir):
        return {'name': '素材清单', 'status': 'SKIP', 'msg': '未设置素材库', 'fix': ''}
    p = os.path.join(materials_dir, 'materials_manifest.json')
    if not os.path.isfile(p):
        return {'name': '素材清单', 'status': 'WARN', 'msg': '未发现 materials_manifest.json',
                'fix': '从剪映团队云空间下载管理员发布的完整素材包'}
    try:
        import material_manifest
        r = material_manifest.verify(materials_dir, full_hash=False)
        if r['missing'] or r['changed']:
            return {'name': '素材清单', 'status': 'FAIL',
                    'msg': '版本%s：缺失%d，大小异常%d' %
                           (r['version'], len(r['missing']), len(r['changed'])),
                    'fix': '重新下载并使用「安装素材包」补齐'}
        return {'name': '素材清单', 'status': 'PASS',
                'msg': '版本%s：%d/%d 完整' % (r['version'], r['ok'], r['expected']), 'fix': ''}
    except Exception as e:
        return {'name': '素材清单', 'status': 'FAIL', 'msg': str(e)[:100],
                'fix': '重新安装素材包'}


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def run_all(materials_dir=None, jy_hint='', drafts_dir=None, verbose=True):
    _sys_path_setup()
    return [ch_python(), ch_jianying(jy_hint), ch_drafts_dir(drafts_dir),
            ch_materials(materials_dir, verbose), ch_semantics(), ch_modules(),
            ch_baseline(materials_dir), ch_usage_writable(), ch_release_manifest(),
            ch_material_manifest(materials_dir)]


def print_report(results, quiet=False):
    order = {'PASS': 0, 'WARN': 1, 'FAIL': 2, 'SKIP': 3}
    results = sorted(results, key=lambda r: order[r['status']])
    lines = ['环境自检报告：']
    n_fail = n_warn = 0
    for r in results:
        st = r['status']
        if st == 'PASS':
            mark = '  OK'
        elif st == 'WARN':
            mark = ' ⚠'
            n_warn += 1
        elif st == 'FAIL':
            mark = ' ✗'
            n_fail += 1
        else:
            mark = ' ·'
        if st == 'PASS' and quiet:
            continue
        lines.append('%-2s %-8s %s' % (mark, st, r['name']))
        lines.append('      %s' % r['msg'])
        if r.get('fix'):
            lines.append('      → %s' % r['fix'])
    lines.append('')
    lines.append('汇总：%d 项 FAIL / %d 项 WARN' % (n_fail, n_warn))
    return '\n'.join(lines), n_fail, n_warn


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--materials', default='', help='素材库路径（缺省读工作台配置）')
    ap.add_argument('--out', default='', help='草稿根目录（缺省读工作台配置，再缺省 D:/JianyingPro Drafts）')
    ap.add_argument('--install', default='', help='剪映安装目录提示')
    ap.add_argument('--quiet', action='store_true', help='只显示 WARN/FAIL')
    ap.add_argument('--json', action='store_true', dest='as_json', help='机器可读 JSON 输出')
    args = ap.parse_args()

    # 素材库和草稿目录都是工作台里填的个人设置（config/gui_settings.json），
    # 两个都要读 —— 只读素材库会让「草稿根目录」一直拿硬编码默认值误报不存在。
    materials = args.materials
    drafts = args.out
    if not materials or not drafts:
        cfgp = os.path.join(CONFIG_DIR, 'gui_settings.json')
        try:
            with open(cfgp, encoding='utf-8') as f:
                cfg = json.load(f)
            materials = materials or cfg.get('materials', '')
            drafts = drafts or cfg.get('out', '')
        except Exception:
            pass

    results = run_all(materials_dir=materials or None,
                      jy_hint=args.install, drafts_dir=drafts or None,
                      verbose=not args.as_json)
    if args.as_json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        code = 1 if any(r['status'] == 'FAIL' for r in results) else 0
        return code
    report, n_fail, n_warn = print_report(results, quiet=args.quiet)
    print(report)
    return 1 if n_fail else (2 if n_warn else 0)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
