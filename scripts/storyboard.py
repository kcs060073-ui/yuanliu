# -*- coding: utf-8 -*-
"""分镜计划与候选素材。

预览只读素材库，不记录使用频次；确认后的 plan.json 可交给 make_park_video.py
生成草稿，确保人工选择不会在生成阶段被重新随机匹配。
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from atomic_io import atomic_write_json
import draft_builder
import material_matcher as mm
import material_governance as mg
import subtitle_split
from material_manifest import asset_id_for, load_manifest as load_material_manifest, verify as verify_materials


def _script_hash(text):
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


def _read_script(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('gbk', errors='replace')


def _orientation(value):
    return {'竖屏': 'portrait', '竖版': 'portrait', 'portrait': 'portrait',
            '横屏': 'landscape', '横版': 'landscape', 'landscape': 'landscape'}.get(
                (value or '').strip().lower())


def _candidate_score(item, need_us, bucket_rank, orientation, usage, prev_path=''):
    reasons = []
    score = 100 - bucket_rank * 12
    if bucket_rank == 0:
        reasons.append('语义首选桶')
    else:
        reasons.append('语义回退%d级' % bucket_rank)
    dur = item.get('duration') or 0
    if item.get('kind') == 'image':
        score -= 8
        reasons.append('图片素材')
    elif dur and need_us:
        ratio = dur / float(need_us)
        if 0.5 <= ratio <= 2.0:
            score += 12; reasons.append('时长适配')
        elif ratio < 0.5:
            score -= 25; reasons.append('时长偏短')
        else:
            score += 4; reasons.append('可安全截取')
    if orientation and item.get('orientation') == orientation:
        score += 8; reasons.append('方向一致')
    elif orientation and item.get('orientation') not in (None, orientation):
        score -= 18; reasons.append('方向回退')
    key = os.path.normpath(os.path.abspath(item['path'])).replace('\\', '/')
    used = int((usage.get(key) or {}).get('n', 0))
    score -= min(used, 10) * 2
    if used == 0:
        reasons.append('近期未使用')
    elif used:
        reasons.append('累计使用%d次' % used)
    if prev_path and item['path'] == prev_path:
        score -= 30; reasons.append('与前镜头重复')
    return max(0, min(100, int(round(score)))), reasons


def create_plan(script, materials_dir, style='3', park='', orientation='', candidate_count=3):
    manifest_path = os.path.join(os.path.abspath(materials_dir), 'materials_manifest.json')
    if os.path.isfile(manifest_path):
        health = verify_materials(materials_dir, load_material_manifest(manifest_path), full_hash=False)
        if health['missing'] or health['changed']:
            raise ValueError('本机素材库不完整：缺失%d，大小异常%d；请先安装对应素材包' %
                             (len(health['missing']), len(health['changed'])))
    styles = draft_builder._load_json(os.path.join(draft_builder.CONFIG_DIR, 'default_styles.json'))
    skey, style_cfg = draft_builder.resolve_style(style, styles['styles'])
    subtitles = subtitle_split.split_subtitles(script, style_cfg)
    sub_durs = [int(s['tgt_dur_ms'] * 1000) for s in subtitles]
    orient = _orientation(orientation)

    actual_dir, park_folder, decided_by = materials_dir, park or '', '直接目录'
    if mm._pr and materials_dir:
        pd, pf, how = mm._pr.resolve_park_dir(materials_dir, park or None, script)
        if pd:
            actual_dir, park_folder, decided_by = pd, pf, how
        elif mm._pr.looks_like_root(materials_dir):
            raise ValueError('素材总目录下有多个园区，但无法锁定本次园区')
    items = mm.scan_materials(actual_dir, park=park_folder or None)
    items = [x for x in items if not (x.get('duration') and x['duration'] < mm.MIN_MATERIAL_US)]
    usage = mg.load_usage()
    by_bucket = {}
    for item in items:
        by_bucket.setdefault(item.get('bucket'), []).append(item)

    scenes, prev = [], ''
    for no, block in enumerate(mm.split_semantic_blocks(subtitles), 1):
        idxs = block['idxs']
        text = ''.join(subtitles[i].get('text', '') for i in idxs)
        need_us = sum(sub_durs[i] for i in idxs)
        btype = block['type']
        buckets = (mm.location_buckets(text) if btype == 'location'
                   else mm.support_buckets(text) if btype == 'support'
                   else mm.BLOCK_BUCKETS.get(btype, ['factory']))
        ranked = []
        for rank, bucket in enumerate(buckets):
            for item in by_bucket.get(bucket, []):
                score, reasons = _candidate_score(item, need_us, rank, orient, usage, prev)
                ranked.append((score, item, reasons))
        if not ranked:
            for item in by_bucket.get(None, []):
                score, reasons = _candidate_score(item, need_us, len(buckets), orient, usage, prev)
                reasons.insert(0, '未分类兜底')
                ranked.append((score, item, reasons))
        if not ranked:
            # 极小素材库没有目标桶时仍给人工候选，但明确标注低置信度；
            # 不能在预览界面悄悄留下空镜头。
            for item in items:
                score, reasons = _candidate_score(item, need_us, len(buckets) + 1,
                                                   orient, usage, prev)
                reasons.insert(0, '全库兜底·需人工确认')
                ranked.append((score, item, reasons))
        ranked.sort(key=lambda x: (-x[0], x[1]['path'].lower()))
        candidates = []
        seen = set()
        for score, item, reasons in ranked:
            if item['path'] in seen:
                continue
            seen.add(item['path'])
            rel = os.path.relpath(item['path'], actual_dir).replace('\\', '/')
            candidates.append({
                'asset_id': asset_id_for(rel), 'path': os.path.abspath(item['path']),
                'relative_path': rel, 'name': os.path.basename(item['path']),
                'bucket': item.get('bucket'), 'kind': item.get('kind', 'video'),
                'duration_us': item.get('duration') or 0, 'score': score,
                'reasons': reasons,
            })
            if len(candidates) >= max(1, int(candidate_count)):
                break
        selected = candidates[0]['asset_id'] if candidates else ''
        if candidates:
            prev = candidates[0]['path']
        scenes.append({
            'scene_no': no, 'subtitle_indexes': idxs, 'text': text,
            'type': btype, 'need_us': need_us, 'selected_asset_id': selected,
            'candidates': candidates,
        })
    return {
        'schema_version': 1, 'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'status': 'DRAFT', 'script_sha256': _script_hash(script), 'style': skey,
        'park': park_folder or park or '', 'park_decided_by': decided_by,
        'materials_dir': os.path.abspath(actual_dir) if actual_dir else '',
        'orientation': orient or '', 'subtitles': subtitles, 'scenes': scenes,
    }


def validate_plan(plan, script=None):
    if plan.get('schema_version') != 1 or not isinstance(plan.get('scenes'), list):
        raise ValueError('分镜计划格式不受支持')
    if script is not None and plan.get('script_sha256') != _script_hash(script):
        raise ValueError('分镜计划与当前脚本不一致，请重新生成预览')
    for scene in plan['scenes']:
        chosen = scene.get('selected_asset_id')
        cands = {c.get('asset_id'): c for c in scene.get('candidates', [])}
        if chosen and chosen not in cands:
            raise ValueError('分镜%d选择的素材不在候选列表' % scene.get('scene_no', 0))
        if chosen and not os.path.isfile(cands[chosen]['path']):
            raise ValueError('分镜%d素材不存在：%s' % (scene.get('scene_no', 0), cands[chosen]['path']))
    return True


def select_candidate(plan, scene_no, candidate):
    scene = next((s for s in plan['scenes'] if int(s['scene_no']) == int(scene_no)), None)
    if not scene:
        raise ValueError('没有分镜 %s' % scene_no)
    for c in scene.get('candidates', []):
        if candidate in (str(c.get('asset_id')), str(c.get('name')), str(c.get('path'))):
            scene['selected_asset_id'] = c['asset_id']
            plan['status'] = 'CONFIRMED'
            return c
    raise ValueError('候选素材不存在：%s' % candidate)


def matched_from_plan(plan, subtitles, script=None):
    validate_plan(plan, script=script)
    matched = [None] * len(subtitles)
    for scene in plan['scenes']:
        cands = {c['asset_id']: c for c in scene.get('candidates', [])}
        chosen = cands.get(scene.get('selected_asset_id'))
        if not chosen:
            continue
        item = {'path': chosen['path'], 'name': os.path.splitext(chosen['name'])[0],
                'bucket': chosen.get('bucket'), 'kind': chosen.get('kind', 'video'),
                'duration': chosen.get('duration_us') or 0, 'orientation': None,
                'dir_chain': []}
        for idx in scene.get('subtitle_indexes', []):
            if 0 <= int(idx) < len(matched):
                matched[int(idx)] = item
    return matched


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd')
    c = sub.add_parser('create'); c.add_argument('script'); c.add_argument('--materials', required=True)
    c.add_argument('--style', default='3'); c.add_argument('--park', default='')
    c.add_argument('--orientation', default='竖屏'); c.add_argument('--candidates', type=int, default=3)
    c.add_argument('--out', required=True)
    s = sub.add_parser('select'); s.add_argument('plan'); s.add_argument('--scene', type=int, required=True)
    s.add_argument('--candidate', required=True)
    v = sub.add_parser('verify'); v.add_argument('plan')
    args = ap.parse_args()
    if args.cmd == 'create':
        plan = create_plan(_read_script(args.script), args.materials, args.style, args.park,
                           args.orientation, args.candidates)
        atomic_write_json(args.out, plan); print('分镜计划已生成：%s（%d 段）' % (args.out, len(plan['scenes']))); return 0
    if args.cmd == 'select':
        plan = json.load(open(args.plan, encoding='utf-8'))
        chosen = select_candidate(plan, args.scene, args.candidate)
        atomic_write_json(args.plan, plan); print('分镜%d → %s' % (args.scene, chosen['name'])); return 0
    if args.cmd == 'verify':
        plan = json.load(open(args.plan, encoding='utf-8')); validate_plan(plan)
        print('分镜计划通过：%d 段' % len(plan['scenes'])); return 0
    ap.print_help(); return 1


if __name__ == '__main__':
    raise SystemExit(main())
