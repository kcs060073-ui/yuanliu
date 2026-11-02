# -*- coding: utf-8 -*-
"""P05 §5：复制素材到草稿时的「同名不同内容」与「同源只复制一次」回归。

要求（原话）：「草稿内素材文件名唯一（原文件名+路径哈希或素材ID）；
同源只复制一次；同步草稿JSON 与 draft_meta_info.json 路径；
临时文件+原子替换。新增两个不同目录同名不同内容回归测试。」

单独运行：
    python tests/test_make_copy.py
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import make_park_video as mpv

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        print('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        print('  [FAIL] %s %s' % (name, extra))


def write(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, 'wb') as f:
        f.write(data if isinstance(data, bytes) else data.encode('utf-8'))


def sha(p):
    with io.open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def make_draft(paths_and_ids):
    """构造一个最小草稿：videos[].path/id + meta.draft_materials[].value[]。"""
    videos, items = [], []
    for i, (p, mid) in enumerate(paths_and_ids):
        videos.append({'id': mid, 'path': p, 'material_name': os.path.basename(p)})
        items.append({'id': mid, 'file_Path': p})
    draft = {'materials': {'videos': videos}}
    meta = {'draft_materials': [{'type': 0, 'value': items}]}
    return draft, meta


def main():
    print('== P05 §5 素材复制去重 回归 ==')
    td = tempfile.mkdtemp(prefix='p05_copy_')
    try:
        # ---- 两个不同目录、同名、不同内容 ----
        a_dir = os.path.join(td, '素材A', '厂房内部')
        b_dir = os.path.join(td, '素材B', '厂房内部')
        a_file = os.path.join(a_dir, 'DJI_0001.mp4')
        b_file = os.path.join(b_dir, 'DJI_0001.mp4')
        write(a_file, b'AAAA-content-from-A' * 40)
        write(b_file, b'BBBB-different-content-B' * 40)
        check('1 两个同名源文件内容确实不同', sha(a_file) != sha(b_file))

        draft, meta = make_draft([(a_file, 'idA'), (b_file, 'idB')])
        mat_dir = os.path.join(td, 'draft1', 'materials')
        mapping, n = mpv.copy_materials_into(draft, meta, mat_dir)

        names = sorted(os.listdir(mat_dir))
        check('2a 复制出 2 个文件（没被同名覆盖）', len(names) == 2, str(names))
        check('2b 两个文件名不同', len(set(names)) == 2, str(names))
        check('2c 两个副本内容分别对上原文件',
              sha(os.path.join(mat_dir, names[0])) in (sha(a_file), sha(b_file)) and
              sha(os.path.join(mat_dir, names[1])) in (sha(a_file), sha(b_file)))
        check('2d 两份内容不相同（不是同一份拷两遍）',
              sha(os.path.join(mat_dir, names[0])) != sha(os.path.join(mat_dir, names[1])))
        check('2e 返回映射 2 条 / 复制数 2', len(mapping) == 2 and n == 2,
              '%d/%d' % (len(mapping), n))

        # ---- 草稿 JSON 与 meta 都同步到新路径 ----
        new_paths = {v['id']: v['path'] for v in draft['materials']['videos']}
        check('3a 草稿 videos 路径已改写为草稿内路径',
              all(os.path.normcase(p).startswith(os.path.normcase(mat_dir))
                  for p in new_paths.values()), str(new_paths))
        meta_paths = {i['id']: i['file_Path']
                      for g in meta['draft_materials'] for i in g['value']}
        check('3b meta 路径与草稿 JSON 一致',
              meta_paths == new_paths, '%s vs %s' % (meta_paths, new_paths))
        check('3c 落盘文件真实存在',
              all(os.path.isfile(p) for p in new_paths.values()), str(new_paths))

        # ---- 同源只复制一次 ----
        draft2, meta2 = make_draft([(a_file, 'idX'), (a_file, 'idY'), (b_file, 'idZ')])
        mat_dir2 = os.path.join(td, 'draft2', 'materials')
        mapping2, n2 = mpv.copy_materials_into(draft2, meta2, mat_dir2)
        names2 = sorted(os.listdir(mat_dir2))
        check('4a 同源引用两次只落 2 个文件', len(names2) == 2, str(names2))
        check('4b 复制数 = 2（去重后）', n2 == 2, str(n2))
        p_x = draft2['materials']['videos'][0]['path']
        p_y = draft2['materials']['videos'][1]['path']
        check('4c 同源两个 material 指向同一文件', p_x == p_y, '%s / %s' % (p_x, p_y))

        # ---- 幂等：重复调用不产生第二份 ----
        draft3, meta3 = make_draft([(a_file, 'idA')])
        mat_dir3 = os.path.join(td, 'draft3', 'materials')
        mpv.copy_materials_into(draft3, meta3, mat_dir3)
        n_first = sorted(os.listdir(mat_dir3))
        mpv.copy_materials_into(draft3, meta3, mat_dir3)
        check('5 重复调用不新增文件（名字由路径哈希决定，幂等）',
              sorted(os.listdir(mat_dir3)) == n_first, str(sorted(os.listdir(mat_dir3))))

        # ---- 目标目录里已有第三方同名文件：绝不覆盖 ----
        draft4, meta4 = make_draft([(a_file, 'idA')])
        mat_dir4 = os.path.join(td, 'draft4', 'materials')
        os.makedirs(mat_dir4, exist_ok=True)
        squatter = os.path.join(mat_dir4, 'DJI_0001.mp4')
        write(squatter, b'IM-A-DIFFERENT-FILE-DO-NOT-OVERWRITE')
        squatter_sha = sha(squatter)
        mpv.copy_materials_into(draft4, meta4, mat_dir4)
        check('6a 未覆盖占位文件', sha(squatter) == squatter_sha)
        check('6b 另存了带哈希的新名字',
              len([f for f in os.listdir(mat_dir4) if f != 'DJI_0001.mp4']) == 1,
              str(sorted(os.listdir(mat_dir4))))
        check('6c 草稿指向新名字而非占位文件',
              draft4['materials']['videos'][0]['path'].endswith(
                  [f for f in os.listdir(mat_dir4) if f != 'DJI_0001.mp4'][0]))

        # ---- 同名 + 同大小 + 不同内容（最容易骗过大小校验的组合）----
        c_dir = os.path.join(td, '素材C', '厂房外部')
        seed_a = os.path.join(a_dir, 'DJI_0002.mp4')
        seed_c = os.path.join(c_dir, 'DJI_0002.mp4')
        write(seed_a, b'X' * 1024)
        write(seed_c, b'Y' * 1024)
        check('5x 同名且字节数相同但内容不同',
              os.path.getsize(seed_a) == os.path.getsize(seed_c)
              and sha(seed_a) != sha(seed_c))
        draft5x, meta5x = make_draft([(seed_a, 'idSA'), (seed_c, 'idSC')])
        mat5x = os.path.join(td, 'draft5x', 'materials')
        mpv.copy_materials_into(draft5x, meta5x, mat5x)
        got5x = sorted(os.listdir(mat5x))
        check('5y 同名同大小也分成两个文件', len(got5x) == 2, str(got5x))
        check('5z 两份内容分别对上各自的源',
              sorted(sha(os.path.join(mat5x, f)) for f in got5x)
              == sorted([sha(seed_a), sha(seed_c)]),
              str([sha(os.path.join(mat5x, f)) for f in got5x]))

        # ---- 不存在源：跳过、不报错、不捏造文件 ----
        draft5, meta5 = make_draft([(os.path.join(td, '不存在.mp4'), 'idN')])
        mat_dir5 = os.path.join(td, 'draft5', 'materials')
        _, n5 = mpv.copy_materials_into(draft5, meta5, mat_dir5)
        check('7a 不存在的源被跳过', n5 == 0, str(n5))
        check('7b 目标目录没有凭空生成文件',
              not os.listdir(mat_dir5) if os.path.isdir(mat_dir5) else True,
              str(os.listdir(mat_dir5) if os.path.isdir(mat_dir5) else []))

        # ---- 没有临时文件残留 ----
        leftovers = []
        for d, _s, fs in os.walk(td):
            leftovers += [f for f in fs if f.endswith('.tmp')]
        check('8 无 .tmp 残留（原子复制收尾干净）', not leftovers, str(leftovers))
    finally:
        shutil.rmtree(td, ignore_errors=True)

    print('\n通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for x in FAILED:
            print('  FAILED: %s' % x)
        raise SystemExit(1)
    print('PASS make copy materials')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
