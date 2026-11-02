#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""局部替换素材：只换草稿里第 N 段视频画面，其他段/字幕/BGM 一律不动。

用法（用户 2026-09-04 需求"换素材"）：
  python scripts/replace_seg.py <草稿目录> <段号 1-based> \
      --materials "E:/园区素材/新疆园区/两河园区" \
      [--orientation 竖屏] [--seed N] [--install 剪映目录]

流程：
  1. 解密 draft_content.json，按 target start 排视频段，定位第 N 段
  2. 取该段覆盖的第一条字幕文本 → classify_subtitle 得语义 bucket
  3. 从素材库扫同 bucket + 同方向候选（排除当前素材），随机抽一个
  4. 按新素材真实时长重算 speed/source_timerange/scale，只改该段 material
  5. 备份 → 回加密写回 → 自检（帧网格/连续性/定格）
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import draft_builder as db
import material_matcher as mm
import jianying_path

MACRO_RE = re.compile(r'^##_draftpath_placeholder_[0-9A-Fa-f-]+_##')


def find_install_dir(hint=''):
    """定位剪映安装目录（P05 §7：探测不到就返回空串，不再退到写死的盘符）。

    老写法 ``... or hint or r'D:\\JianyingPro'`` 会在探测失败时返回一个
    **不含 videoeditor.dll 的目录**，把"找不到剪映"变成"解密失败"，
    排查时完全看不出真正原因。现在由 crypto() 明确报错。
    """
    if hint and os.path.isfile(os.path.join(hint, 'videoeditor.dll')):
        return hint
    return jianying_path.find_install_dir(hint)


def crypto(mode, src, dst, install_dir):
    r = subprocess.run([sys.executable, '-X', 'utf8',
                        os.path.join(ROOT, 'scripts', 'jy_crypto.py'),
                        mode, os.path.abspath(src), os.path.abspath(dst), install_dir],
                       capture_output=True, timeout=600)
    return r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0


def text_content(m):
    try:
        return json.loads(m.get('content', '{}')).get('text', '')
    except Exception:
        return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('draft_dir')
    ap.add_argument('seg_idx', type=int, help='要替换的视频段序号（1-based，从左到右）')
    ap.add_argument('--materials', required=True, help='素材库根目录（--orientation 同成片）')
    ap.add_argument('--orientation', default='', help='成片方向：竖屏/横屏（与生成草稿时一致）')
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--install', default='')
    args = ap.parse_args()

    install = find_install_dir(args.install)
    if not install:
        raise SystemExit('未找到剪映安装目录（找不到 videoeditor.dll）。'
                         '请用 --install "<剪映安装目录>" 指定。')
    orient = {'竖屏': 'portrait', '竖版': 'portrait', '横屏': 'landscape',
              '横版': 'landscape'}.get((args.orientation or '').strip(), None)

    draft_dir = os.path.abspath(args.draft_dir).rstrip('/\\')
    content_path = os.path.join(draft_dir, 'draft_content.json')
    meta_path = os.path.join(draft_dir, 'draft_meta_info.json')
    plain = os.path.join(draft_dir, '_replace_plain.json')
    if not crypto('-d', content_path, plain, install):
        raise SystemExit('解密失败: ' + content_path)
    draft = json.load(open(plain, encoding='utf-8'))
    vmat = {m['id']: m for m in draft['materials'].get('videos', [])}
    tmat = {m['id']: m for m in draft['materials'].get('texts', [])}

    # 1) 视频段按 start 排序
    vtracks = [t for t in draft['tracks'] if t.get('type') == 'video']
    if not vtracks:
        raise SystemExit('草稿没有视频轨')
    vsegs = sorted(vtracks[0]['segments'],
                   key=lambda s: (s.get('target_timerange') or {}).get('start', 0))
    n = len(vsegs)
    if not 1 <= args.seg_idx <= n:
        raise SystemExit('段号 %d 越界（共 %d 段）' % (args.seg_idx, n))
    seg = vsegs[args.seg_idx - 1]
    tt = seg.get('target_timerange') or {}
    seg_start, seg_end = tt.get('start', 0), tt.get('start', 0) + tt.get('duration', 0)
    old_mat = vmat.get(seg.get('material_id'))
    if not old_mat:
        raise SystemExit('第 %d 段找不到视频素材' % args.seg_idx)

    # 2) 该段覆盖的字幕 → 语义块类型 → bucket 候选列表（按优先级降级）
    #    （2026-09-07 语义块规则落地后，classify_subtitle 已删，改用块规则）
    subs = []
    for t in draft['tracks']:
        if t.get('type') != 'text':
            continue
        for s in t['segments']:
            m = tmat.get(s.get('material_id'))
            if not m or m.get('sub_type') != 4:
                continue
            st = (s.get('target_timerange') or {}).get('start', 0)
            if seg_start <= st < seg_end:
                subs.append((st, text_content(m)))
    subs.sort()
    if not subs:
        raise SystemExit('第 %d 段没覆盖到字幕，无法推断语义（可改用整稿重抽）' % args.seg_idx)
    blk_type = mm.tag_subtitle(''.join(x[1] for x in subs))
    bucket_pref = mm.BLOCK_BUCKETS.get(blk_type, ['factory'])
    print('第 %d 段 %s~%ss 覆盖字幕: "%s"' % (args.seg_idx, seg_start / 1e6, seg_end / 1e6, subs[0][1][:20]))
    print('语义块: %s -> 候选bucket %s' % (blk_type, bucket_pref))

    # 3) 扫描候选（按 bucket 优先级取第一个有素材的 + 同方向，排除当前）
    items = mm.scan_materials(args.materials)
    items = [it for it in items
             if it.get('duration') and it['duration'] >= mm.MIN_MATERIAL_US
             and (orient is None or it.get('orientation') in (None, orient))
             and it['path'] != old_mat.get('path')]
    by_bucket = {}
    for it in items:
        by_bucket.setdefault(it['bucket'], []).append(it)
    pool = []
    bucket = None
    for bk in bucket_pref:
        if by_bucket.get(bk):
            pool = by_bucket[bk]
            bucket = bk
            break
    if not pool:
        raise SystemExit('素材库没有同语义(%s)且同方向的备选素材，可换 --seed 整稿重抽'
                         % '/'.join(bucket_pref))
    rng = random.Random(args.seed)
    rng.shuffle(pool)
    canvas = draft.get('canvas_config') or {}
    cw, ch = canvas.get('width', 1920), canvas.get('height', 3414)
    target_us = tt.get('duration', 0)
    new_item, speed, src_us, note, ok = None, 1.0, target_us, '', False
    for it in pool:
        file_dur = it['duration']
        speed, src_us, note, ok = db.fit_clip_speed(file_dur, target_us)
        if ok:                       # 只要有一段撑得住就停
            new_item = it
            break
    if not ok or new_item is None:
        raise SystemExit('同语义素材都太短（0.5x 也撑不满 %.1fs），建议整稿重抽或加素材'
                         % (target_us / 1e6))

    # 4) 局部替换：素材存在则复用，否则新建（引用原路径，不复制）
    existing = next((m for m in draft['materials']['videos']
                     if m.get('path') == new_item['path']), None)
    if existing:
        seg['material_id'] = existing['id']
    else:
        wh = db.probe_media_display_size(new_item['path'])
        w, h = (wh if wh and wh[0] > 0 and wh[1] > 0 else (1920, 1080))
        vm = db.make_video_material(new_item['path'], new_item['duration'] or target_us, w, h)
        draft['materials']['videos'].append(vm)
        seg['material_id'] = vm['id']
    scale = max(cw / w, ch / h, 1.0)
    seg['source_timerange'] = {'duration': src_us}
    seg['speed'] = round(float(speed), 4)
    seg['clip']['scale'] = {'x': round(scale, 4), 'y': round(scale, 4)}
    print('已替换 -> %s' % os.path.basename(new_item['path']))
    if note:
        print('  适配: %s' % note)

    # 5) meta 同步（可选新增条目，media 库能显示）
    if os.path.isfile(meta_path):
        meta_plain = os.path.join(draft_dir, '_replace_meta.json')
        if crypto('-d', meta_path, meta_plain, install):
            meta = json.load(open(meta_plain, encoding='utf-8'))
            new_id = seg['material_id']
            # 给 type=0 的组补一条 file_Path（媒体库能显示新素材）
            for g in meta.get('draft_materials', []):
                if g.get('type') == 0:
                    vals = g.get('value') or []
                    if not any(v.get('id') == new_id for v in vals if isinstance(v, dict)):
                        vals.append({'id': new_id,
                                     'file_Path': new_item['path'].replace('\\', '/'),
                                     'type': 0})
                    break
            json.dump(meta, open(meta_plain, 'w', encoding='utf-8'),
                      ensure_ascii=False, separators=(',', ':'))
            crypto('-e', meta_plain, meta_path, install)

    json.dump(draft, open(plain, 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    # 备份原文件后回加密
    for old, new in ((content_path, content_path + '.pre-replace.bak'),):
        if not os.path.exists(new):
            try:
                os.replace(old, new)
            except OSError:
                pass
    if not crypto('-e', plain, content_path, install):
        raise SystemExit('回加密失败（原稿已备份为 .pre-replace.bak，可手动恢复）')
    for p in (plain, os.path.join(draft_dir, '_replace_meta.json')):
        try:
            os.remove(p)
        except OSError:
            pass
    print('完成：第 %d 段已换素材，其他轨道未动' % args.seg_idx)
    print('自检命令: python tests/verify_draft.py "%s"' % draft_dir)


if __name__ == '__main__':
    main()
