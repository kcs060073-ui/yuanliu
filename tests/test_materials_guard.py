# -*- coding: utf-8 -*-
"""P05 §3：空素材库默认拦截 —— 共用判据回归。

要求（原话）：「GUI 生成/批量前检查素材库路径与至少一个视频；空库用明确中文
拦截；高级设置加"允许无画面调试稿"默认关；CLI 默认拦截、仅
--allow-empty-materials 放行；分镜预览与正式生成同判据。」

核心是**只有一个判据**：``material_matcher.validate_materials_dir()``。
本测试既验它本身，也验 GUI / CLI / 分镜预览三处都在用它（静态检查），
避免"三个地方各写一套空目录判断"的历史问题复发。

单独运行：
    python tests/test_materials_guard.py
"""
import io
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import material_matcher as mm

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        print('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        print('  [FAIL] %s %s' % (name, extra))


def main():
    print('== P05 §3 空素材库拦截 回归 ==')
    td = tempfile.mkdtemp(prefix='p05_mguard_')
    try:
        # 1) 路径问题
        r = mm.validate_materials_dir('')
        check('1a 空路径 → 拦停', r['ok'] is False and r['code'] == 'NO_PATH', str(r))
        check('1b 拦停信息是中文且说得清',
              any('\u4e00' <= ch <= '\u9fff' for ch in r['msg']) and len(r['msg']) > 6,
              r['msg'])

        r = mm.validate_materials_dir(os.path.join(td, '不存在的目录'))
        check('1c 路径不存在 → NOT_DIR / NO_PATH',
              r['ok'] is False and r['code'] in ('NOT_DIR', 'NO_PATH'), str(r))

        f = os.path.join(td, '这是个文件.txt')
        io.open(f, 'w', encoding='utf-8').write('x')
        r = mm.validate_materials_dir(f)
        check('1d 传进来是个文件 → 拦停', r['ok'] is False, str(r))

        # 2) 空目录
        empty = os.path.join(td, '空素材库')
        os.makedirs(empty)
        r = mm.validate_materials_dir(empty)
        check('2a 空目录 → EMPTY 拦停', r['ok'] is False and r['code'] == 'EMPTY', str(r))
        check('2b 空目录信息里给出计数', r['videos'] == 0 and r['images'] == 0, str(r))

        # 3) 只有子目录、没有媒体文件
        nested = os.path.join(empty, '厂房内部')
        os.makedirs(nested)
        r = mm.validate_materials_dir(empty)
        check('3a 只有目录没有媒体文件 → 仍然拦停',
              r['ok'] is False and r['code'] == 'EMPTY', str(r))

        # 4) 有一个视频 → 放行
        os.makedirs(os.path.join(empty, '厂房外部'))
        vid = os.path.join(empty, '厂房外部', '厂房外部_横版.mp4')
        io.open(vid, 'wb').write(b'fake-mp4' * 10)
        r = mm.validate_materials_dir(empty)
        check('4a 有 1 个视频 → 放行', r['ok'] is True and r['code'] == 'OK', str(r))
        check('4b 统计到 1 个视频', r['videos'] == 1, str(r))
        check('4c 返回绝对路径', os.path.isabs(r['path']), r['path'])

        # 5) 只有图片**不算**可用（§3 原文是"至少一个**视频**"）
        #    理由：信息流成片以视频段为主，纯图片库出来的片子不是可交付物。
        #    判据必须和"有画面"这件事对齐，不能只看"目录里有没有东西"。
        img_dir = os.path.join(td, '只有图片')
        os.makedirs(img_dir)
        io.open(os.path.join(img_dir, '全景_横版.jpg'), 'wb').write(b'jpg')
        r = mm.validate_materials_dir(img_dir)
        check('5a 只有图片 → 仍然拦停（要求至少 1 个视频）',
              r['ok'] is False and r['code'] == 'EMPTY', str(r))
        check('5b 但要把图片数量说清楚，别让人以为目录是空的',
              r['images'] == 1 and '1 张图片' in r['msg'], r['msg'])
        check('5c 拦停信息里给出放行办法',
              'allow-empty-materials' in r['msg'], r['msg'])

        # 5d) 图片 + 1 个视频 → 放行，且两者都被统计
        mixed = os.path.join(td, '图视混合')
        os.makedirs(mixed)
        io.open(os.path.join(mixed, '全景_横版.jpg'), 'wb').write(b'jpg')
        io.open(os.path.join(mixed, '厂房外部_横版.mp4'), 'wb').write(b'mp4')
        r = mm.validate_materials_dir(mixed)
        check('5d 图片+视频 → 放行且两者都统计到',
              r['ok'] is True and r['videos'] == 1 and r['images'] == 1, str(r))

        # 6) 非媒体文件不算数
        junk = os.path.join(td, '只有杂物')
        os.makedirs(junk)
        io.open(os.path.join(junk, 'readme.txt'), 'w', encoding='utf-8').write('x')
        io.open(os.path.join(junk, '清单.json'), 'w', encoding='utf-8').write('{}')
        r = mm.validate_materials_dir(junk)
        check('6a 只有 txt/json → 拦停', r['ok'] is False and r['code'] == 'EMPTY', str(r))

        # 7) 深层目录也能统计（递归）
        deep = os.path.join(td, '深层', 'a', 'b', 'c')
        os.makedirs(deep)
        io.open(os.path.join(deep, '素材_竖版.mov'), 'wb').write(b'mov')
        r = mm.validate_materials_dir(os.path.join(td, '深层'))
        check('7a 递归统计到深层素材', r['ok'] is True and r['videos'] == 1, str(r))

        # 8) 四个返回字段固定（GUI/CLI/预览都按这套取）
        check('8a 返回结构固定 {ok,code,path,videos,images,msg}',
              set(r.keys()) == {'ok', 'code', 'path', 'videos', 'images', 'msg'},
              str(sorted(r.keys())))
        check('8b code 取值受限',
              all(mm.validate_materials_dir(p)['code'] in
                  ('OK', 'NO_PATH', 'NOT_DIR', 'EMPTY')
                  for p in ('', empty, junk)))

        # 9) 只有一个判据：三处调用方都用它（静态检查）
        src_gui = io.open(os.path.join(ROOT, 'workbench', 'workbench_gui.py'),
                          encoding='utf-8').read()
        src_cli = io.open(os.path.join(ROOT, 'scripts', 'make_park_video.py'),
                          encoding='utf-8').read()
        src_sb = io.open(os.path.join(ROOT, 'workbench', 'storyboard_gui.py'),
                         encoding='utf-8').read()
        check('9a GUI 用它做生成前检查',
              'material_matcher.validate_materials_dir' in src_gui)
        check('9b GUI 分镜预览也用它（同判据）',
              src_gui.count('_empty_materials_blocked') >= 3, str(src_gui.count('_empty_materials_blocked')))
        check('9c CLI 用它做默认拦截',
              'material_matcher.validate_materials_dir' in src_cli)
        check('9d CLI 有 --allow-empty-materials 放行开关',
              '--allow-empty-materials' in src_cli and
              'args.allow_empty_materials' in src_cli)
        check('9e 库层（draft_builder）兜底也用它',
              'material_matcher.validate_materials_dir' in io.open(
                  os.path.join(ROOT, 'scripts', 'draft_builder.py'),
                  encoding='utf-8').read())
        check('9f 分镜预览走同一判据',
              'material_matcher' in src_sb or 'validate_materials_dir' in src_sb)
        check('9g GUI 高级设置里「允许无画面调试稿」默认关',
              'v_allow_empty = tk.BooleanVar(value=False)' in src_gui)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    print('\n通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for x in FAILED:
            print('  FAILED: %s' % x)
        raise SystemExit(1)
    print('PASS materials guard')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
