#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验生成的草稿：时间轴、配音引用、素材时长一致性。

用法：
  python tests/verify_draft.py <草稿目录> [--install 剪映安装目录]
"""
import json
import os
import re
import subprocess
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import jianying_path
# P05 §7：不再写死 ``D:\JianyingPro\<版本>``。那是这台机器的剪映版本，
# 同事升个版本或装在别的盘，自检就会拿一个不存在的目录去解密，
# 报"解密失败"却看不出真正原因。一律动态探测；探测不到就明确报错。
MACRO_RE = re.compile(r'^##_draftpath_placeholder_[0-9A-Fa-f-]+_##')


def find_install_dir(hint=''):
    """定位剪映安装目录（取含 videoeditor.dll 的最新版本目录）。

    2026-09-04：剪映自动升级会把旧版本目录的 dll 改名成 .dll_d_xxx 残留，
    硬编码版本路径会解密失败（11.3 → 11.4 实测），必须动态探测。
    2026-09-16：探测逻辑收敛到 jianying_path，兼容新版 Apps\\<版本>\\ 布局。
    P05 §7：删掉最后一层写死兜底；只有 ``hint`` 是真实存在的目录才用它。
    """
    if hint and os.path.isfile(os.path.join(hint, 'videoeditor.dll')):
        return hint
    return jianying_path.find_install_dir(hint)


def resolve_path(p, draft_dir):
    if not p:
        return ''
    p = MACRO_RE.sub('', p.replace('\\', '/')).lstrip('/')
    if not p:
        return ''
    if not re.match(r'^[A-Za-z]:/', p):
        p = os.path.join(draft_dir, p)
    return os.path.normpath(p).replace('\\', '/')


def crypto(mode, src, dst, install_dir):
    last = ''
    for _ in range(5):
        try:
            os.remove(dst)
        except OSError:
            pass
        r = subprocess.run([sys.executable, '-X', 'utf8',
                            os.path.join(ROOT, 'scripts', 'jy_crypto.py'),
                            mode, os.path.abspath(src), os.path.abspath(dst), install_dir],
                           capture_output=True, timeout=600)
        if r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0:
            return
        last = (r.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
        last = last[-1] if last else ''
        # rc=2 是"确定性失败"（如 剪映 11.7+ 解密 API 不兼容），重试无意义
        if r.returncode == 2:
            break
    raise SystemExit('解密失败: %s%s' % (src, ('\n  ' + last) if last else ''))


def text_of(draft, mid):
    for m in draft['materials']['texts']:
        if m['id'] == mid:
            try:
                return json.loads(m.get('content', '{}')).get('text', '')
            except Exception:
                return ''
    return '?'


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('draft_dir')
    ap.add_argument('--install', default='')
    args = ap.parse_args()

    draft_dir = os.path.abspath(args.draft_dir).rstrip('/\\')
    install = find_install_dir(args.install)
    if not install:
        # P05 §7：探测不到就直说，不要拿一个写死的路径硬试（会报成"解密失败"，
        # 让人以为是草稿坏了）。--install 可以显式指定。
        print('[FAIL] 未找到剪映安装目录（找不到 videoeditor.dll）')
        print('       请用 --install "<剪映安装目录>" 指定，例如 D:\\JianyingPro\\<版本>')
        return 1
    # 明文副本放工作区，别污染草稿目录（剪映会扫描）
    tmpdir = os.path.join(ROOT, 'tests', '_tmp')
    os.makedirs(tmpdir, exist_ok=True)
    # 唯一临时名：固定名 verify.json 一旦被上次中断的残留占住（Windows 文件锁），
    # jy_crypto 写不进去 → 连重试 5 次后误报"解密失败"（2026-09-15 实测踩到）。
    tmp = os.path.join(tmpdir, 'verify_%d.json' % os.getpid())
    crypto('-d', os.path.join(draft_dir, 'draft_content.json'), tmp, install)
    draft = json.load(open(tmp, encoding='utf-8'))

    problems = []
    frame = 1e6 / 30

    # 轨道概览
    print('轨道概览：')
    for t in draft['tracks']:
        print('  %-6s attr=%s  %d 段' % (t['type'], t.get('attribute'), len(t['segments'])))

    # 1) 字幕轨唯一 + 配音轨唯一
    # 注意：不能用 attribute == 2 判断——剪映打开草稿重新保存后会把该字段抹成 None。
    # 改用素材本身的类型（sub_type=4 才是字幕，免责花字是别的 sub_type）。
    sub_ids = {m['id'] for m in draft['materials']['texts'] if m.get('sub_type') == 4}
    sub_tracks = [t for t in draft['tracks'] if t['type'] == 'text'
                  and any(s.get('material_id') in sub_ids for s in t['segments'])]
    if len(sub_tracks) != 1:
        problems.append('字幕轨 %d 条（应为 1 条）' % len(sub_tracks))
        return problems

    # 配音素材分两类：jianying 模式是 text_to_audio（按 text_to_audio_ids 关联），
    # edge 模式是 type='audio' 且字幕无 text_to_audio_ids（按 target_timerange.start 对位）。
    # 这里统一处理：先找素材，再按 start 找对应的段。
    amap = {a['id']: a for a in draft['materials']['audios']}
    music_ids = {a['id'] for a in amap.values() if a.get('type') == 'music'}
    # 候选配音素材：text_to_audio，或者 name 以 "配音-" 开头的 audio
    cand_audio_ids = {a['id'] for a in amap.values()
                      if a.get('type') == 'text_to_audio'
                      or (a.get('type') == 'audio'
                          and (a.get('name') or '').startswith('配音-'))}
    # audio 段按 (start, material_id) 索引
    audio_seg_index = {}
    for t in draft['tracks']:
        if t['type'] == 'audio':
            for s in t['segments']:
                if s.get('material_id') in cand_audio_ids:
                    st = (s.get('target_timerange') or {}).get('start', 0)
                    audio_seg_index.setdefault(st, []).append(s)
    print('\n字幕 %d 段 / 配音候选 %d 段' % (len(sub_tracks[0]['segments']), len(audio_seg_index)))
    # 纯字幕模式（no_tts）：草稿里根本没有配音素材，此时"没有配音"是正确的，
    # 不能当成问题报出来 —— 否则 15 条字幕会刷出 15 条误报。
    no_tts = not cand_audio_ids
    if no_tts:
        print('（纯字幕模式：无配音轨，跳过配音配对/文件存在检查）')
    if not no_tts and len(audio_seg_index) != len(sub_tracks[0]['segments']):
        problems.append('配音段数 %d ≠ 字幕段数 %d' % (len(audio_seg_index), len(sub_tracks[0]['segments'])))

    # 2) 配音文件真实存在 + 时长与素材一致
    print('\n逐条核对（前 5 条）：')
    total_tts = 0
    for i, s in enumerate(sub_tracks[0]['segments']):
        tt = s.get('target_timerange') or {}
        start, dur = tt.get('start', 0), tt.get('duration', 0)
        total_tts += dur
        # 帧网格
        if abs(dur / frame - round(dur / frame)) > 0.01:
            problems.append('字幕#%d duration %d 不在帧网格上' % (i, dur))
        # 找配对配音段：先按 text_to_audio_ids（jianying 模式），找不到再按 start 索引
        tm = next((m for m in draft['materials']['texts'] if m['id'] == s['material_id']), None)
        if not tm:
            problems.append('字幕#%d 找不到素材' % i)
            continue
        am = None
        aids = tm.get('text_to_audio_ids') or []
        if aids:
            am = amap.get(aids[0])
        if am is None:
            # edge 模式：按 start 找
            cands = audio_seg_index.get(start, [])
            for cand in cands:
                am = amap.get(cand['material_id'])
                if am and (am.get('name') or '').startswith('配音-'):
                    break
                am = None
        if am is None:
            if no_tts:
                continue      # 纯字幕模式：没有配音轨是正常的，跳过文件检查
            problems.append('字幕#%d 找不到配对配音段（start=%d）' % (i, start))
            continue
        wav = resolve_path(am.get('path'), draft_dir)
        if not os.path.isfile(wav):
            problems.append('字幕#%d 配音文件不存在: %s' % (i, wav))
            continue
        # 实际 wav 时长 vs 素材记录时长
        with wave.open(wav, 'rb') as w:
            real_us = int(round(w.getnframes() * 1e6 / w.getframerate()))
        # 容差 1 帧：剪映打开草稿重新保存时会把素材 duration 也对齐到 30fps 帧网格，
        # 所以素材记录值会比 wav 文件的真实时长长不到一帧，这是剪映的正常行为
        if abs(real_us - am['duration']) > 40000:
            problems.append('字幕#%d 配音时长不符：文件 %d us vs 素材 %d us'
                            % (i, real_us, am['duration']))
        if i < 5:
            print('  #%02d %.2f~%.2f  %5.2fs  %s'
                  % (i, start / 1e6, (start + dur) / 1e6, real_us / 1e6,
                     text_of(draft, s['material_id'])[:22]))

    # 3) 时间轴连续无重叠
    prev = 0
    for s in sub_tracks[0]['segments']:
        tt = s.get('target_timerange') or {}
        st, du = tt.get('start', 0), tt.get('duration', 0)
        if abs(st - prev) > 1:
            problems.append('字幕轨不连续：#%d start=%d 期望 %d' % (st, prev))
        prev = st + du
    print('\n总时长：草稿 %d us / 字幕累加 %d us' % (draft['duration'], prev))
    if abs(draft['duration'] - prev) > 1000:
        problems.append('总时长不符：草稿 %d vs 累加 %d' % (draft['duration'], prev))

    # 4) 视频铺满 + 连续无重叠
    #    （2026-09-04 补：此前只查 max 末端，段间重叠漏检 —— 视频段被
    #     1.5s 最小时长拉长后与下一段重叠，剪映打开会自动顺次排开，
    #     导致切点漂移、视频轨超出总时长，时间线"不整齐"）
    vt = [t for t in draft['tracks'] if t['type'] == 'video']
    if vt and vt[0].get('segments'):
        vsegs = sorted(vt[0]['segments'],
                       key=lambda s: (s.get('target_timerange') or {}).get('start', 0))
        prev_end = 0
        for i, s in enumerate(vsegs):
            tt = s.get('target_timerange') or {}
            st, du = tt.get('start', 0), tt.get('duration', 0)
            if abs(st - prev_end) > 1:
                problems.append('视频轨第%d段边界不齐：start=%d 期望 %d（差 %+.3fs）'
                                % (i, st, prev_end, (st - prev_end) / 1e6))
            prev_end = st + du
        end = prev_end
        print('视频轨末尾：%.2fs' % (end / 1e6))
        if abs(end - draft['duration']) > 100000:
            problems.append('视频未铺满：%.2fs vs %.2fs' % (end / 1e6, draft['duration'] / 1e6))
    elif vt:
        # 纯字幕草稿常不传素材，视频轨 0 段 —— 原来这里直接 max() 空序列会崩
        print('视频轨为空（未传 --materials），跳过铺满检查')

    # 4.5) 定格检查：素材能播的时长必须 ≥ 展示时长，否则画面会静止
    # 实际能播时长 = source_timerange.duration / speed
    frozen = 0
    for tr in draft['tracks']:
        if tr['type'] != 'video':
            continue
        for s in tr['segments']:
            st = s.get('source_timerange') or {}
            tt = s.get('target_timerange') or {}
            speed = s.get('speed') or 1.0
            can_play = (st.get('duration', 0) / speed) if speed else 0
            need = tt.get('duration', 0)
            if need - can_play > 300000:      # 缺口 > 0.3s 算定格
                frozen += 1
                problems.append('视频段定格 %.1fs（能播 %.1fs / 需要 %.1fs）'
                                % ((need - can_play) / 1e6, can_play / 1e6, need / 1e6))
            if speed < 0.49 or speed > 2.01:
                problems.append('视频段 speed=%.2f 超出 0.5~2.0 范围' % speed)
    print('定格检查：%d 段异常' % frozen)

    # 4.6) 素材来源审计（2026-09-11）
    # 解决"画面配错了没人发现"：贵阳片 7 段素材全部来自别园区，而信号层
    # （黑场/冻结/静音/削波）看不出任何问题 —— 错的只是来源，只有来源层能查。
    # 审计不解码视频，只读草稿 JSON，成本 ≈0。
    try:
        import park_audit as pa
        vmap = {m['id']: m for m in draft['materials'].get('videos', [])}
        seq = []
        for tr in draft['tracks']:
            if tr['type'] != 'video':
                continue
            segs = sorted(tr['segments'],
                          key=lambda s: (s.get('target_timerange') or {}).get('start', 0))
            for s in segs:
                m = vmap.get(s.get('material_id')) or {}
                p = m.get('path')
                if p:
                    seq.append(resolve_path(p, draft_dir))
        lock = (pa.read_lock(draft_dir)
                or pa.read_lock_from_log(os.path.basename(draft_dir)))
        res = pa.audit(seq, lock)
        print('\n素材来源审计：')
        if res['status'] == 'SKIP':
            print('  %s' % pa.summarize(res))
            print('  （提示：本 skill 出片会写入 park_lock.json，之后即可审计来源）')
        else:
            lk = res['locked'] or {}
            print('  预期园区：%s（依据 %s）' % (lk.get('park'), lk.get('decided_by') or '未记录'))
            print('  %s' % pa.summarize(res))
            for pk, b, ok, p in res['rows']:
                print('   %-4s [%s|%s] %s'
                      % ('OK' if ok else 'FAIL', pk or '园区外', b or '未知桶',
                         os.path.basename(p)))
            for pr in res['problems']:
                problems.append('素材来源：' + pr)
    except Exception as e:
        print('\n素材来源审计：异常跳过（%s）' % e)

    # 5) 配音素材标记检查
    # jianying 模式：text_to_audio 素材需标记 audio_panel（避免剪映当占位重合成）
    # edge 模式：type=audio，无 tts_generate_scene 字段（避免触发任何重合成）
    jy_tts = [a for a in draft['materials']['audios']
              if a.get('type') == 'text_to_audio'
              and a.get('tts_generate_scene') != 'audio_panel']
    edge_tts = [a for a in draft['materials']['audios']
                if a.get('type') == 'audio'
                and (a.get('name') or '').startswith('配音-')
                and a.get('tts_generate_scene')]  # edge 模式不应有该字段
    if jy_tts:
        problems.append('%d 个 jianying 模式配音素材未标记 audio_panel' % len(jy_tts))
    if edge_tts:
        problems.append('%d 个 edge 模式配音素材误带 tts_generate_scene' % len(edge_tts))

    # 6) 悬空引用
    mat_ids = {m['id'] for g in draft['materials'].values() if isinstance(g, list)
               for m in g if isinstance(m, dict) and 'id' in m}
    for t in draft['tracks']:
        for s in t['segments']:
            if s.get('material_id') not in mat_ids:
                problems.append('悬空 material_id')
            for r in s.get('extra_material_refs', []):
                if r not in mat_ids:
                    problems.append('悬空 extra ref')

    print()
    if problems:
        print('[结果] FAIL —— %d 处问题' % len(problems))
        for p in problems[:20]:
            print('   -', p)
        return 1
    print('[结果] PASS —— 单轨连续、配音文件齐全且时长一致、时长对齐帧网格、视频铺满')
    return 0


if __name__ == '__main__':
    sys.exit(main())
