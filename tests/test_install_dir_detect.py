# -*- coding: utf-8 -*-
"""剪映安装目录探测回归（2026-09-16 Apps\\ 布局事故 + P05 §7 兜底策略变更）。

背景：探测逻辑原先在 5 个模块各抄一份、都只下探一层，遇到剪映新版
``<base>\\Apps\\<版本>\\`` 布局全部失败 —— 本机装了 11.4.2.14459，
doctor 却报「未找到剪映安装目录」。现已收敛到 jianying_path 单一实现。

本测试锁两件事：
  1) 两种布局都能命中、按版本号倒序、hint 优先；
  2) 各调用方在"本机没有剪映"时的行为 —— **P05 §7 起这条契约变了**：

     ❌ 旧契约：``replace_seg`` / ``verify_draft`` 会退到一个写死的
        ``D:\\JianyingPro\\<版本>``。它把"找不到剪映"伪装成"能继续跑",
        最后报出来的是"解密失败"，排查时完全看不出真正原因。
     ✅ 新契约：探测不到就返回空串；调用方要么明确报错（并提示用
        ``--install`` 指定），要么把空串往上抛。**任何模块都不再持有
        写死的 ``INSTALL`` 常量。**

测试全程把 BASES 指到临时目录，不依赖本机是否装了剪映。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import jianying_path as jp


def _mk_dir(base, rel, *filenames):
    d = os.path.join(base, rel)
    os.makedirs(d, exist_ok=True)
    for fn in filenames:
        with open(os.path.join(d, fn), 'wb') as f:
            f.write(b'stub')
    return d


def _check_detection(td):
    """纯探测层：布局、取值顺序、hint 优先级。"""
    # 布局 1：旧布局 <base>\<版本>\
    old = os.path.join(td, 'old')
    want_old = _mk_dir(old, '11.3.0.14362', 'videoeditor.dll', 'ffmpeg.exe')

    # 布局 2：新布局 <base>\Apps\<版本>\（本机真实情况）
    new = os.path.join(td, 'new')
    want_new = _mk_dir(new, os.path.join('Apps', '11.4.2.14459'),
                       'videoeditor.dll', 'ffmpeg.exe')
    # 干扰项：新布局根下还有 User Data\Projects，不能被误判成安装目录
    os.makedirs(os.path.join(new, 'User Data', 'Projects'), exist_ok=True)

    # 两种布局并存 → 取版本号更大的那个
    both = os.path.join(td, 'both')
    _mk_dir(both, '11.3.0.14362', 'videoeditor.dll')
    want_both = _mk_dir(both, os.path.join('Apps', '11.4.2.14459'), 'videoeditor.dll')

    # 同层多版本：11.9 与 11.10 不能用字符串比（'11.9' > '11.10' 是错的）
    multi = os.path.join(td, 'multi')
    _mk_dir(multi, '11.9.0.14362', 'videoeditor.dll')
    want_multi = _mk_dir(multi, '11.10.0.14459', 'videoeditor.dll')

    jp.BASES = (old,)
    assert jp.find_install_dir() == want_old, '旧布局 <base>/<版本> 未命中'

    jp.BASES = (new,)
    assert jp.find_install_dir() == want_new, '新布局 <base>/Apps/<版本> 未命中'
    assert jp.find_file_in_install('ffmpeg.exe') == want_new, 'ffmpeg 未适配新布局'

    jp.BASES = (both,)
    assert jp.find_install_dir() == want_both, '两布局并存未取最新版本'

    jp.BASES = (multi,)
    assert jp.find_install_dir() == want_multi, '11.9 / 11.10 版本号比较错误（用了字符串序）'

    # 显式 hint 优先于自动探测
    assert jp.find_install_dir(want_old) == want_old, '显式 hint 未优先'

    jp.BASES = (os.path.join(td, 'nope'),)
    assert jp.find_install_dir() == '', '没有剪映时应返回空串'
    assert jp.find_install_dir(os.path.join(td, 'nope')) == '', '非法 hint 应返回空串'


def _check_callers(td):
    """各调用方在「本机确实没有剪映」时的行为（P05 §7 新契约）。

    把 BASES 指到空目录，模拟「本机确实没有剪映」。
    """
    jp.BASES = (os.path.join(td, 'nope'),)

    # 1) 明确报错派：探测不到就抛，不假装能继续
    import make_park_video as mpv
    try:
        mpv.find_install_dir('')
        raise AssertionError('make_park_video 探测不到时应抛 FileNotFoundError')
    except FileNotFoundError:
        pass

    # 2) 返回空串派：**不再有写死的 INSTALL 兜底**
    import replace_seg as rs
    assert rs.find_install_dir('') == '', 'replace_seg 不应再回退到写死盘符'
    assert not hasattr(rs, 'INSTALL'), 'replace_seg 的写死 INSTALL 常量应已删除'

    import arrange_park_video as apv
    assert apv.find_install_dir('') == '', 'arrange 未保留「找不到返回空串」的约定'

    import verify_draft as vd
    assert vd.find_install_dir('') == '', 'verify_draft 不应再回退到写死盘符'
    assert not hasattr(vd, 'INSTALL'), 'verify_draft 的写死 INSTALL 常量应已删除'

    import draft_builder as db
    assert db._find_ffmpeg() == '', 'draft_builder 找不到 ffmpeg 时应返回空串'

    # 3) hint 真的指向可用安装目录时，各调用方都必须认它
    good = _mk_dir(td, os.path.join('好剪映', '11.4.2.14459'), 'videoeditor.dll')
    assert rs.find_install_dir(good) == good, 'replace_seg 未认显式 hint'
    assert vd.find_install_dir(good) == good, 'verify_draft 未认显式 hint'
    assert apv.find_install_dir(good) == good, 'arrange 未认显式 hint'

    # 4) 全工程不得再出现写死的「盘符 + 剪映版本号」安装路径
    import io as _io
    import re as _re
    bad = []
    for fn in os.listdir(os.path.join(ROOT, 'scripts')):
        if not fn.endswith('.py'):
            continue
        src = _io.open(os.path.join(ROOT, 'scripts', fn), encoding='utf-8').read()
        for m in _re.finditer(r'[A-Za-z]:[\\/]JianyingPro[\\/]\d+\.\d+', src):
            bad.append('%s:%s' % (fn, m.group(0)))
    assert not bad, '仍有写死的剪映版本安装路径：%s' % bad


def main():
    saved = jp.BASES
    try:
        with tempfile.TemporaryDirectory() as td:
            _check_detection(td)
            _check_callers(td)
    finally:
        jp.BASES = saved
    print('PASS install dir detect')
    print('OK: 剪映目录探测全部通过（两种布局 / 版本倒序 / hint 优先 / '
          '4 处调用方在“没有剪映”时的行为 + 无写死版本路径）')


if __name__ == '__main__':
    main()
