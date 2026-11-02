#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配音后自动归位（第二阶段）。

标准工作流：
  1. make_park_video.py 生成草稿（阶段一：拆解脚本 + 估算时间轴 + 占位 TTS）
  2. 剪映打开草稿 → Ctrl+A 全选字幕 → 「文本-朗读」批量生成配音（语速 1.09）
     （剪映此时会把字幕/配音拆散到多条轨道——这是它的正常行为）
  3. 保存并关闭草稿
  4. 运行本脚本，自动完成：
     a. 按拆解顺序（段 start 排序）把 17 条字幕归位成一条连续轨道
     b. 真实配音按 text_id 配对归位成一条连续轨道，位置与字幕逐条对齐
     c. 删除阶段一遗留的占位 TTS 轨/素材（无声垃圾段）
     d. 总时长 = 全部真实配音时长之和（1.09 倍速后的净时长）
     e. 重跑素材语义匹配，按新时间轴重切画面
     f. BGM 随机换曲并循环铺满新总时长
     g. 免责花字延长到新总时长，回加密写回

用法：
  python arrange_park_video.py <草稿目录> [--materials 素材目录]
      [--bgm random|keep] [--bgm-seed N] [--install 剪映安装目录] [--dry-run]

注意：运行前请确保剪映里已关闭该草稿（避免文件写冲突）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import draft_builder as db
import material_matcher
import bgm_pool
import jianying_path

MACRO_RE = re.compile(r'^##_draftpath_placeholder_[0-9A-Fa-f-]+_##')
DEFAULT_SPEED = 1.09
DEFAULT_FPS = 30          # 剪映默认 30fps；实测 v9 草稿每段时长都落在 30fps 帧网格上


def snap_to_frame(us, fps=DEFAULT_FPS):
    """把微秒时长对齐到帧网格。

    剪映的行为（实测 v9 手动归位草稿）：配音段/字幕段的 target duration 永远是
    整数帧（30fps → 33333.33us 的整数倍）。不这么做的话每段会有几毫秒误差，
    17 段累积后总时长比剪映的偏长约 40ms，字幕与口型/配音也会渐进错位。
    """
    frame = 1e6 / fps
    return int(round(round(us / frame) * frame))


# ---------- 工具 ----------

def resolve_path(p, draft_dir):
    """把剪映的 ##_draftpath_placeholder_...## 宏解析成草稿目录下的真实路径。"""
    if not p:
        return ''
    p = MACRO_RE.sub('', p.replace('\\', '/')).lstrip('/')
    if not p:
        return ''
    # 去掉宏后剩下的 'textReading/xx.wav' 是草稿相对路径，必须以草稿目录为基准拼接
    if not re.match(r'^[A-Za-z]:/', p):
        p = os.path.join(draft_dir, p)
    return os.path.normpath(p).replace('\\', '/')


def find_install_dir(hint=''):
    """定位剪映安装目录（P05 §7：探测不到返回空串，不再退到写死的盘符）。"""
    if hint and os.path.isfile(os.path.join(hint, 'videoeditor.dll')):
        return hint
    return jianying_path.find_install_dir(hint)


def crypto(mode, infile, outfile, install_dir):
    """子进程调用 jy_crypto（DLL 不进主进程，避免破坏素材探测）。"""
    cmd = [sys.executable, '-X', 'utf8',
           os.path.join(SCRIPT_DIR, 'jy_crypto.py'), mode,
           os.path.abspath(infile), os.path.abspath(outfile), install_dir]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0:
        err = r.stderr.decode('utf-8', errors='replace')[-500:]
        raise RuntimeError('jy_crypto %s 失败: %s' % (mode, err))


def set_target(seg, start_us, dur_us):
    tt = {'duration': int(dur_us)}
    if start_us:
        tt['start'] = int(start_us)
    seg['target_timerange'] = tt


def text_content(tm):
    try:
        return json.loads(tm.get('content', '{}')).get('text', '')
    except Exception:
        return ''


# ---------- 归位主逻辑 ----------

def arrange(draft, draft_dir, materials_dir, bgm_mode, bgm_seed, install_dir,
            fps=DEFAULT_FPS):
    texts = {m['id']: m for m in draft['materials']['texts']}

    # 1) 分类 TTS 素材：真实（wav 存在）vs 占位（假路径）
    audios = draft['materials']['audios']
    real_by_text, real_ids, placeholder_ids = {}, set(), set()
    for a in audios:
        if a.get('type') != 'text_to_audio':
            continue
        p = resolve_path(a.get('path'), draft_dir)
        if p and os.path.isfile(p):
            real_ids.add(a['id'])
            real_by_text.setdefault(a.get('text_id'), a)  # 一条字幕一段配音
        else:
            placeholder_ids.add(a['id'])
    if not real_ids:
        raise SystemExit('未发现任何真实配音（textReading 里没有可用 wav）。\n'
                         '请先在剪映中打开草稿，Ctrl+A 全选字幕 → 文本-朗读 → 开始朗读，\n'
                         '保存并关闭后再运行本脚本。')

    # 2) 收集全部字幕段（剪映可能拆成多条 text 轨道），按 start 排序还原脚本顺序
    sub_tracks = [t for t in draft['tracks']
                  if t['type'] == 'text' and t.get('attribute') == 2]
    if not sub_tracks:
        raise SystemExit('草稿里没有字幕轨道（text/attribute=2）。')
    text_order = {m['id']: i for i, m in enumerate(draft['materials']['texts'])}
    entries = []
    for ti, tr in enumerate(sub_tracks):
        for s in tr['segments']:
            start = (s.get('target_timerange') or {}).get('start', 0) or 0
            entries.append((start, text_order.get(s['material_id'], 0), s))
    entries.sort(key=lambda e: (e[0], e[1]))
    n = len(entries)
    print('[归位] 收集到 %d 条字幕段（分布在 %d 条轨道），按 start 还原脚本顺序' % (n, len(sub_tracks)))

    # 3) 收集真实配音音频段（可能在多条 audio 轨道里）
    real_seg_by_mat = {}
    for tr in draft['tracks']:
        if tr['type'] != 'audio':
            continue
        for s in tr['segments']:
            if s.get('material_id') in real_ids:
                real_seg_by_mat[s['material_id']] = s

    # 4) 连续重排：字幕与配音逐条对齐，游标推进
    tts_track_template = next((t for t in draft['tracks'] if t['type'] == 'audio'), None)
    track_render = 2
    if tts_track_template is not None:
        for s in tts_track_template.get('segments', []):
            track_render = max(track_render, s.get('track_render_index', 2) or 2)

    def attach_audio_aux(seg, with_beats=False):
        mats = draft['materials']
        refs = []
        for grp, maker in (('speeds', db.make_speed_material),
                           ('placeholder_infos', db.make_placeholder_info_material)):
            m = maker()
            mats[grp].append(m)
            refs.append(m['id'])
        if with_beats:
            m = db.make_beats_material()
            mats['beats'].append(m)
            refs.append(m['id'])
        for grp, maker in (('sound_channel_mappings', db.make_sound_channel_mapping_material),
                           ('vocal_separations', db.make_vocal_separation_material)):
            m = maker()
            mats[grp].append(m)
            refs.append(m['id'])
        seg['extra_material_refs'] = refs
        return seg

    cursor = 0
    items = []
    missing = []
    for start, _, seg in entries:
        tm = texts.get(seg['material_id'])
        if tm is None:
            raise SystemExit('字幕段引用了不存在的文本素材：%s' % seg['material_id'])
        am = real_by_text.get(tm['id'])
        if am is None:
            missing.append(text_content(tm))
            continue
        rseg = real_seg_by_mat.get(am['id'])
        speed = (rseg or {}).get('speed') or DEFAULT_SPEED
        dur = snap_to_frame(am['duration'] / speed, fps)
        if rseg is None:
            # 极少见：配音素材存在但音频段缺失 → 新建一段
            rseg = db.make_tts_audio_segment(db._uuid(), am['id'], cursor,
                                             am['duration'], dur, speed, track_render)
            rseg = attach_audio_aux(rseg)
        else:
            rseg['source_timerange'] = {'duration': am['duration']}
        set_target(seg, cursor, dur)
        set_target(rseg, cursor, dur)
        # 文本素材的关联只保留真实配音 id
        if tm.get('text_to_audio_ids'):
            tm['text_to_audio_ids'] = [i for i in tm['text_to_audio_ids'] if i in real_ids] \
                or [am['id']]
        items.append({'text_seg': seg, 'audio_seg': rseg, 'mat': am, 'start': cursor, 'dur': dur})
        cursor += dur
    if missing:
        raise SystemExit('以下字幕没有对应配音（需在剪映里补齐朗读后重跑）：\n  ' + '\n  '.join(missing))
    total_us = cursor
    print('[归位] %d 条字幕/配音已对齐成连续轨道，净总时长 %.2f s' % (len(items), total_us / 1e6))

    # 5) 清理占位 TTS 素材
    audios[:] = [a for a in audios if a['id'] not in placeholder_ids]
    print('[清理] 删除占位 TTS 素材 %d 个' % len(placeholder_ids))

    # 6) 重切画面：重跑语义匹配，按新时间轴铺视频段
    canvas = draft.get('canvas_config') or {}
    canvas_w = canvas.get('width', 1080)
    canvas_h = canvas.get('height', 1920)
    subs_texts = [{'text': text_content(texts[e[2]['material_id']])} for e in entries]
    # 传真实时长，让分配算法能做容量约束（素材撑不住就提前换素材）
    # 园区锁定（2026-09-11）：阶段二沿用草稿里既有素材所属的园区，避免总根混池。
    # 草稿里已有的画面来自哪个园区文件夹，就继续用哪个园区重切。
    park_hint = None
    try:
        import park_rules as _prv2
        known = _prv2.load().get('parks', {})
        for vm in (draft['materials'].get('videos') or []):
            p = str(vm.get('path') or '').replace('\\', '/')
            for folder in known:
                if '/' + folder + '/' in p:
                    park_hint = folder
                    break
            if park_hint:
                break
    except Exception:
        park_hint = None
    try:
        matched = material_matcher.match_materials(
            subs_texts, materials_dir,
            sub_durations=[it['dur'] for it in items],
            park=park_hint)
    except ValueError as e:
        print('[警告] 未锁定园区，画面保留原样：%s' % e)
        matched = [None] * len(subs_texts)
    if any(m and m.get('path') for m in matched):
        # 匹配到的 path 可能是相对路径 → 归一成绝对路径
        for m in matched:
            if m and m.get('path') and not os.path.isabs(m['path']):
                m['path'] = os.path.normpath(os.path.join(materials_dir, m['path']))

        def attach_video_aux(seg):
            mats = draft['materials']
            refs = []
            for grp, maker in (('speeds', db.make_speed_material),
                               ('placeholder_infos', db.make_placeholder_info_material),
                               ('canvases', db.make_canvas_material),
                               ('material_animations', db.make_material_animation_material),
                               ('sound_channel_mappings', db.make_sound_channel_mapping_material),
                               ('material_colors', db.make_material_color_material),
                               ('vocal_separations', db.make_vocal_separation_material)):
                m = maker()
                mats[grp].append(m)
                refs.append(m['id'])
            seg['extra_material_refs'] = refs
            return seg

        video_materials, video_segments, by_path = [], [], {}
        i = 0
        while i < n:
            m = matched[i] if i < len(matched) else None
            if not m or not m.get('path'):
                i += 1
                continue
            path = m['path']
            run_end = i
            while run_end < n and matched[run_end] is not None \
                    and matched[run_end].get('path') == path:
                run_end += 1
            seg_start = items[i]['start']
            seg_dur = items[run_end - 1]['start'] + items[run_end - 1]['dur'] - seg_start
            if path not in by_path:
                wh = db.probe_media_display_size(path)
                w, h = (wh if wh else (1920, 1080))
                scale = max(canvas_w / w, canvas_h / h, 1.0)
                file_dur = db.probe_media_duration(path)
                by_path[path] = (w, h, scale, file_dur)
            else:
                w, h, scale, file_dur = by_path[path]
            vm = db.make_video_material(path, file_dur or seg_dur, w, h)
            video_materials.append(vm)
            # 容量适配：素材装不下就放慢，素材太长就 2 倍速，避免定格
            speed, src_dur, note, ok = db.fit_clip_speed(file_dur or 0, seg_dur)
            if note:
                print('      [%s] %s' % (os.path.basename(path)[:26], note))
            video_segments.append(attach_video_aux(
                db.make_video_segment(vm['id'], seg_start, seg_dur, scale, 0,
                                      src_dur=src_dur, speed=speed)))
            i = run_end
        draft['materials']['videos'] = video_materials
        video_track = next((t for t in draft['tracks'] if t['type'] == 'video'), None)
        if video_track is None:
            video_track = {'id': db._uuid(), 'type': 'video', 'attribute': 1,
                           'is_default_name': True, 'segments': []}
        video_track['segments'] = video_segments
        print('[画面] 语义匹配重切：%d 个素材 %d 段，铺满 %.2f s' % (
            len(video_materials), len(video_segments), total_us / 1e6))
    else:
        print('[画面] 未提供素材目录或无可用素材，保留原视频轨（时长不再对齐）')
        video_track = next((t for t in draft['tracks'] if t['type'] == 'video'), None)

    # 7) BGM：随机换曲或保留，循环铺满新总时长
    bgm_track = None
    old_bgm = next((a for a in draft['materials']['audios'] if a.get('type') == 'music'), None)
    bgm_mat = None
    if bgm_mode == 'random':
        drafts_root = os.path.dirname(draft_dir.rstrip('/\\')) or bgm_pool.DEFAULT_DRAFTS_DIR
        pool = bgm_pool.scan_pool(drafts_root, install_dir)
        rec = bgm_pool.pick_random(pool, seed=bgm_seed,
                                    exclude_paths=({resolve_path(old_bgm.get('path'), draft_dir)}
                                                   if old_bgm else set()))
        if rec:
            bgm_mat = db.make_bgm_audio_material({}, rec)
            print('[BGM] 随机选曲：%s（%.0f s）' % (rec.get('name'), (rec.get('duration') or 0) / 1e6))
        else:
            print('[BGM] 音乐池为空，保留原 BGM')
    if bgm_mat is None and old_bgm is not None:
        bgm_mat = old_bgm
    if bgm_mat is not None:
        bgm_segs = [attach_audio_aux(s, with_beats=False) for s in
                    db.make_bgm_audio_segments(bgm_mat['id'], bgm_mat.get('duration'),
                                               total_us, track_render + 2)]
        # 音乐比视频短时会循环成多段，分段点必须落在帧网格上，否则末尾会切出半帧
        for s in bgm_segs:
            tt = s.get('target_timerange') or {}
            s['target_timerange'] = {
                'start': snap_to_frame(tt.get('start', 0), fps),
                'duration': snap_to_frame(tt.get('duration', 0), fps)}
        for tr in draft['tracks']:
            if tr['type'] == 'audio':
                for s in list(tr['segments']):
                    if s.get('material_id') == (old_bgm or {}).get('id') and old_bgm is not bgm_mat:
                        tr['segments'].remove(s)
        # 替换素材表里的 BGM
        if bgm_mat is not old_bgm and old_bgm is not None:
            draft['materials']['audios'][:] = [
                a for a in draft['materials']['audios']
                if a.get('id') != old_bgm['id']]
            draft['materials']['audios'].append(bgm_mat)
        bgm_track = {'id': db._uuid(), 'type': 'audio', 'is_default_name': True,
                     'segments': bgm_segs}
        print('[BGM] 铺满总时长：%d 段' % len(bgm_segs))

    # 8) 免责花字延长到新总时长
    disclaimer_track = None
    for tr in draft['tracks']:
        if tr['type'] == 'text' and tr.get('attribute') != 2:
            for s in tr['segments']:
                set_target(s, 0, total_us)
            disclaimer_track = tr

    # 9) 重组轨道：视频 / 字幕 / 配音 / BGM / 免责
    sub_track = dict(sub_tracks[0])
    sub_track['segments'] = [it['text_seg'] for it in items]
    tts_track = {'id': db._uuid(), 'type': 'audio', 'is_default_name': True,
                 'segments': [it['audio_seg'] for it in items]}
    new_tracks = [t for t in (video_track, sub_track, tts_track, bgm_track, disclaimer_track)
                  if t is not None]
    draft['tracks'] = new_tracks
    draft['duration'] = total_us
    print('[轨道] 重组完成：%s' % ', '.join('%s/%s×%d' % (t['type'], t.get('attribute'), len(t['segments']))
                                          for t in new_tracks))
    return total_us, len(items)


def validate(draft, draft_dir, total_us, n_items):
    """解密回读后的自检。"""
    problems = []
    # 轨道唯一性与顺序
    sub_tracks = [t for t in draft['tracks'] if t['type'] == 'text' and t.get('attribute') == 2]
    if len(sub_tracks) != 1 or len(sub_tracks[0]['segments']) != n_items:
        problems.append('字幕轨数量/段数异常')
    # 每条轨道顺序无重叠
    for tr in draft['tracks']:
        prev_end = 0
        for s in tr['segments']:
            tt = s.get('target_timerange') or {}
            start, dur = tt.get('start', 0), tt.get('duration', 0)
            if start < prev_end - 1:
                problems.append('轨道 %s 段重叠' % tr['type'])
            prev_end = max(prev_end, start + dur)
            if start + dur > draft['duration'] + 1:
                problems.append('段超出总时长')
    # 引用完整性
    mat_ids = {m['id'] for g in draft['materials'].values() if isinstance(g, list)
               for m in g if isinstance(m, dict) and 'id' in m}
    for tr in draft['tracks']:
        for s in tr['segments']:
            if s.get('material_id') not in mat_ids:
                problems.append('悬空 material_id：%s' % s['material_id'])
            for r in s.get('extra_material_refs', []):
                if r not in mat_ids:
                    problems.append('悬空 extra ref')
    # 占位素材清零
    n_ph = sum(1 for a in draft['materials']['audios']
               if a.get('type') == 'text_to_audio'
               and not os.path.isfile(resolve_path(a.get('path'), draft_dir)))
    if n_ph:
        problems.append('仍有 %d 个占位 TTS 素材' % n_ph)
    # 视频与 BGM 覆盖总时长
    for want_type in ('video',):
        vt = [t for t in draft['tracks'] if t['type'] == want_type]
        if vt:
            end = max((s['target_timerange'].get('start', 0) + s['target_timerange']['duration'])
                      for s in vt[0]['segments'])
            if abs(end - draft['duration']) > 100000:
                problems.append('视频轨末尾 %.2fs ≠ 总时长 %.2fs' % (end / 1e6, draft['duration'] / 1e6))
    return problems


def main():
    ap = argparse.ArgumentParser(description='配音后自动归位（阶段二）')
    ap.add_argument('draft_dir', help='剪映草稿目录（如 D:/JianyingPro Drafts/260901-xx）')
    ap.add_argument('--materials', default='', help='素材目录（缺省用草稿内 materials/）')
    ap.add_argument('--bgm', choices=['random', 'keep'], default='random',
                    help='BGM：random=随机换曲（默认），keep=保留现有')
    ap.add_argument('--bgm-seed', type=int, default=None, help='随机种子（复现选曲）')
    ap.add_argument('--install', default='', help='剪映安装目录（缺省自动探测）')
    ap.add_argument('--fps', type=int, default=DEFAULT_FPS,
                    help='时间轴帧率，用于把段时长对齐到整数帧（默认 30）')
    ap.add_argument('--dry-run', action='store_true', help='只归位不写回')
    args = ap.parse_args()

    draft_dir = os.path.abspath(args.draft_dir).rstrip('/\\')
    content = os.path.join(draft_dir, 'draft_content.json')
    if not os.path.isfile(content):
        raise SystemExit('找不到 %s' % content)
    materials_dir = os.path.abspath(args.materials) if args.materials else \
        os.path.join(draft_dir, 'materials')
    if not os.path.isdir(materials_dir):
        print('[警告] 素材目录不存在，画面将保留原样：', materials_dir)
        materials_dir = ''
    install_dir = find_install_dir(args.install)
    if not install_dir:
        raise SystemExit('未找到剪映安装目录（找不到 videoeditor.dll）。'
                         '请用 --install "<剪映安装目录>" 指定。')

    # 解密（子进程，避免 DLL 污染主进程）
    tmp_plain = os.path.join(draft_dir, '~arrange_plain.json')
    crypto('-d', content, tmp_plain, install_dir)
    with open(tmp_plain, 'r', encoding='utf-8') as f:
        draft = json.load(f)

    total_us, n_items = arrange(draft, draft_dir, materials_dir,
                                args.bgm, args.bgm_seed, install_dir, args.fps)

    # 写回（先备份原始加密文件）
    if args.dry_run:
        print('[dry-run] 不写回，以下为将要发生的结果预览')
    else:
        bak = os.path.join(draft_dir, 'draft_content.json.pre-arrange.bak')
        if not os.path.isfile(bak):
            shutil.copy2(content, bak)
        with open(tmp_plain, 'w', encoding='utf-8') as f:
            json.dump(draft, f, ensure_ascii=False)
        crypto('-e', tmp_plain, content, install_dir)
        print('[写回] %s（原文件备份为 draft_content.json.pre-arrange.bak）' % content)

    # 回读校验
    if not args.dry_run:
        crypto('-d', content, tmp_plain, install_dir)
        with open(tmp_plain, 'r', encoding='utf-8') as f:
            check = json.load(f)
        problems = validate(check, draft_dir, total_us, n_items)
        if problems:
            print('[校验] FAIL：')
            for p in problems:
                print('   -', p)
            sys.exit(1)
        print('[校验] PASS —— 字幕/配音单轨连续、无重叠、引用完整、视频铺满 %.2f s'
              % (check['duration'] / 1e6))
    try:
        os.remove(tmp_plain)
    except OSError:
        pass


if __name__ == '__main__':
    main()
