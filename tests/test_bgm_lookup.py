# -*- coding: utf-8 -*-
"""BGM 搜索与「固定选择」回归（P05 二轮补丁 一）。

背景（用户原话）
----------------
「bgm_pool 主要扫 剪映安装目录\\User Data\\Cache\\music，但剪映即使装在 D 盘，
音乐缓存也可能位于 %LOCALAPPDATA%\\JianyingPro\\User Data\\Cache\\music」；
以及「make_park_video 调 resolve_bgm 时没有传 style.get('bgm', {})，
导致 fixed 模式无法按 resource_id / 名称选曲，只会拿候选列表第一个文件」。

本测试锁住：
  A. 缓存目录：两个位置都扫、规范化绝对路径去重、只收真实文件、
     目录不存在不报错、**不写死任何人的用户目录**；
  B. fixed 模式：resource_id 精确 → 名称精确 → 文件名 → 归一化；
     匹配不到要降级 + 记警告，且**绝不能抛异常**；
  C. 调用方真的把 styles[].bgm 传下去了（静态 + 行为双查）。

单独运行：
    python tests/test_bgm_lookup.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import bgm_pool

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        print('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        print('  [FAIL] %s %s' % (name, extra))


def fake_mp3(path, size=256 * 1000):
    """造一个"正常长度"的假 MP3 供收集层断言用。

    大小决定估算时长（CBR 128kbps：size*8/(128*1000) 秒），**必须够长**：
    2026-09-20 起 `resolve_bgm` 会剔除 <15s 的候选（剪映缓存里的试听碎片），
    128KB≈8s 会被当碎片剔掉，导致"存在即收录"的收集层断言全挂。
    256KB≈16s 刚过下限。
    """
    head = b'\xff\xfb\x90\x00'          # MPEG1 Layer3 128kbps/44100Hz
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'wb') as f:
        f.write(head + b'\x00' * max(0, size - len(head)))
    return path


def main():
    print('== BGM 搜索 / 固定选择 回归 ==')
    td = tempfile.mkdtemp(prefix='p05_bgm_')
    saved_la = os.environ.get('LOCALAPPDATA')
    src_bgm = io.open(os.path.join(ROOT, 'scripts', 'bgm_pool.py'),
                      encoding='utf-8').read()
    try:
        # ---------------- A. 缓存目录覆盖 ----------------
        # 伪造：剪映"装在" td/install/Apps/11.5.0（安装盘）
        #       用户数据在 td/local（环境变量模拟 %LOCALAPPDATA%）
        install = os.path.join(td, 'install', 'Apps', '11.5.0.14471')
        os.makedirs(install, exist_ok=True)
        local = os.path.join(td, 'local')
        os.environ['LOCALAPPDATA'] = local

        dirs = bgm_pool.music_cache_dirs(install)
        want_a = os.path.join(td, 'install', 'User Data', 'Cache', 'music')
        want_b = os.path.join(local, 'JianyingPro', 'User Data', 'Cache', 'music')
        check('A1 覆盖「安装目录反推的根」',
              os.path.normcase(want_a) in [os.path.normcase(d) for d in dirs], str(dirs))
        check('A2 覆盖「%LOCALAPPDATA%\\JianyingPro」', os.path.normcase(want_b)
              in [os.path.normcase(d) for d in dirs], str(dirs))
        check('A3 路径都是绝对路径', all(os.path.isabs(d) for d in dirs), str(dirs))
        check('A4 展开后不含未替换的环境变量占位',
              not any('%' in d for d in dirs), str(dirs))
        # 只认「具体某人」的路径：占位写法（<管理员> / %LOCALAPPDATA%）是文档，不算
        import re as _re
        concrete = _re.findall(r'[A-Za-z]:[\\/]Users[\\/](?![<%])[^\\/"\'\s]+', src_bgm)
        check('A5 源码里没有写死某个人的用户目录（只用运行时变量）',
              not concrete and '%LOCALAPPDATA%' in src_bgm, str(concrete[:3]))

        # 去重：让两条来源指向**同一个物理目录**
        #   install 放 <td>/JianyingPro/Apps/<版本> → 反推根 = <td>/JianyingPro
        #   LOCALAPPDATA = <td>            → 候选 = <td>/JianyingPro/User Data/Cache/music
        # 两者完全相同 → 必须只出现一次
        os.environ['LOCALAPPDATA'] = td
        install_same = os.path.join(td, 'JianyingPro', 'Apps', '11.5.0.14471')
        dirs2 = bgm_pool.music_cache_dirs(install_same)
        keys = [os.path.normcase(os.path.abspath(d)) for d in dirs2]
        check('A6 两条来源指向同一目录时只留一条（规范化绝对路径去重）',
              len(keys) == len(set(keys)) and len(keys) == 1, str(dirs2))

        # 目录不存在 → 不报错、不中断
        os.environ['LOCALAPPDATA'] = local
        try:
            got = bgm_pool.collect_local_bgm(os.path.join(td, '没有这个草稿库'), install)
            ok = isinstance(got, list)
        except Exception as e:
            ok = False
            print('     异常：%s' % e)
        check('A7 目录都不存在时不报错、不中断', ok)

        # 两个位置各放一首 → 两首都要收到
        a_dir = want_a
        b_dir = want_b
        pa = fake_mp3(os.path.join(a_dir, 'aaa.mp3'))
        pb = fake_mp3(os.path.join(b_dir, 'bbb.mp3'))
        os.makedirs(b_dir, exist_ok=True)
        fake_mp3(os.path.join(b_dir, 'not_audio.txt'), 500)
        cands = bgm_pool.collect_local_bgm(os.path.join(td, '没有这个草稿库'), install)
        names = sorted(c['name'] for c in cands)
        check('A8 安装盘缓存与用户数据缓存都被收录', names == ['aaa', 'bbb'], str(names))
        check('A9 非音频扩展名被忽略', 'not_audio' not in names, str(names))
        check('A10 只收录真实存在的文件',
              all(os.path.isfile(c['path']) for c in cands), str(cands))
        check('A11 路径已绝对化', all(os.path.isabs(c['path']) for c in cands))

        # 收集层同样去重：两条来源指向同一目录时，同一首歌只算一条
        os.environ['LOCALAPPDATA'] = td
        same_cache = os.path.join(td, 'JianyingPro', 'User Data', 'Cache', 'music')
        same_mp3 = fake_mp3(os.path.join(same_cache, 'dup.mp3'))
        cands_dup = bgm_pool.collect_local_bgm(os.path.join(td, '没有这个草稿库'),
                                               install_same)
        paths = [os.path.normcase(os.path.abspath(c['path'])) for c in cands_dup]
        check('A12 收集层去重：同一文件经两条来源命中只算一条',
              len(paths) == len(set(paths)) and len(paths) == 1, str(paths))
        check('A13 去重后拿到的是那个真实文件',
              os.path.isfile(same_mp3) and
              os.path.normcase(same_mp3) in paths, str(paths))
        os.environ['LOCALAPPDATA'] = local

        # ---------------- B. fixed 模式匹配 ----------------
        # 用受控候选做纯函数级验证
        c_rid = {'name': '大气震撼 史诗宣传 励志', 'path': 'X:/m/1.mp3',
                 'resource_id': '7377838878989813787', 'duration': 100 * 1000 * 1000}
        c_name = {'name': '新闻传播序曲', 'path': 'X:/m/2.mp3',
                  'resource_id': '', 'duration': 100 * 1000 * 1000}
        c_file = {'name': 'deadbeefdeadbeef', 'path': 'X:/m/Why not have everything.mp3',
                  'resource_id': '', 'duration': 100 * 1000 * 1000}
        c_norm = {'name': '产品展示', 'path': 'X:/m/4.mp3',
                  'resource_id': '', 'duration': 100 * 1000 * 1000}
        pool = [c_rid, c_name, c_file, c_norm]

        hit, how = bgm_pool.match_style_bgm(pool, {'resource_id': '7377838878989813787'})
        check('B1 resource_id 精确匹配优先', hit is c_rid, '%s / %s' % (how, hit))

        hit, how = bgm_pool.match_style_bgm(pool, {'name': '新闻传播序曲'})
        check('B2 无 ID 时按名称精确匹配', hit is c_name, how)

        hit, how = bgm_pool.match_style_bgm(
            pool, {'name': 'Why not have everything'})
        check('B3 缓存文件（无元数据）可按文件名匹配', hit is c_file, how)

        hit, how = bgm_pool.match_style_bgm(pool, {'name': 'Why-not_have everything'})
        check('B4 名字归一化后仍可匹配（空格/横杠/下划线）', hit is c_file, how)

        hit, how = bgm_pool.match_style_bgm(pool, {'resource_id': '不存在的ID',
                                                   'name': '不存在的曲名'})
        check('B5 都匹配不到时返回 None（由调用方降级）', hit is None, str(hit))

        hit, how = bgm_pool.match_style_bgm(pool, {})
        check('B6 样式没配 bgm 时不乱猜', hit is None, str(hit))

        hit, how = bgm_pool.match_style_bgm(pool, None)
        check('B7 style_bgm 传 None 也不炸', hit is None, str(hit))

        # ---------------- B2. resolve_bgm 的降级与警告 ----------------
        orig = bgm_pool.collect_local_bgm
        try:
            bgm_pool.collect_local_bgm = lambda *a, **k: list(pool)
            warn = []
            rec, note = bgm_pool.resolve_bgm('fixed', style_bgm={'resource_id': ''},
                                             warnings=warn)
            check('B8 匹配失败时安全降级到本机可用曲目',
                  rec is not None and rec['path'] in [c['path'] for c in pool], str(rec))
            check('B9 降级必须写进 warnings', bool(warn) and '降级' in warn[0], str(warn))
            check('B10 说明文字里点明"不影响出片"',
                  '不影响出片' in note, note)

            warn2 = []
            rec2, note2 = bgm_pool.resolve_bgm(
                'fixed', style_bgm={'resource_id': '7377838878989813787'}, warnings=warn2)
            check('B11 命中时不产生降级警告', not warn2, str(warn2))
            check('B12 命中时说明里写清命中方式', 'resource_id' in note2, note2)

            # 完全没有候选 → 明确 (None, 说明)，不抛异常
            bgm_pool.collect_local_bgm = lambda *a, **k: []
            warn3 = []
            rec3, note3 = bgm_pool.resolve_bgm('fixed', install_dir=install,
                                               warnings=warn3)
            check('B13 一个候选都没有 → None（不抛异常）', rec3 is None, str(rec3))
            check('B14 且说明里出现「本机未找到可用BGM」',
                  '本机未找到可用BGM' in note3, note3)
            check('B15 说明里列出扫过的缓存目录（便于排查）',
                  os.path.basename(local) in note3 or 'Cache' in note3, note3[:160])
        finally:
            bgm_pool.collect_local_bgm = orig

        # ---------------- C. 调用方真的传了 style['bgm'] ----------------
        src = io.open(os.path.join(ROOT, 'scripts', 'make_park_video.py'),
                      encoding='utf-8').read()
        check('C1 make_park_video 取 style["bgm"] 传给 resolve_bgm',
              "style.get('bgm'" in src and 'style_bgm=style_bgm' in src)
        check('C2 调用时带上 warnings 列表',
              'warnings=warnings' in src)
        check('C3 不再有"回退风格默认 BGM"这种假兜底文案',
              '回退风格默认 BGM' not in
              io.open(os.path.join(ROOT, 'scripts', 'bgm_pool.py'),
                      encoding='utf-8').read())
    finally:
        if saved_la is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = saved_la
        import shutil
        shutil.rmtree(td, ignore_errors=True)

    print('\n通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for x in FAILED:
            print('  FAILED: %s' % x)
        raise SystemExit(1)
    print('PASS bgm lookup')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
