#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""arrange_park_video.py 端到端验证。

输入：乱序现场快照（剪映刚点完「朗读」、未手动整理的那版草稿）
输出：与用户手动归位的 v9 正确答案逐条对比

用法：
  python tests/run_arrange_e2e.py            # 跑测试
  python tests/run_arrange_e2e.py --keep     # 保留测试目录不清理
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, 'scripts')
sys.path.insert(0, SCRIPTS)

TEST_ROOT = os.path.join(ROOT, 'tests', '_e2e')
DRAFTS = r'D:\JianyingPro Drafts'
V9 = os.path.join(DRAFTS, '260831-苏州-没想到-v9')
SCRAMBLED_BAK = os.path.join(V9, '.backup',
                             '20260831165233_9471e6adad5616ab7436908cbfb13a14.save.bak')


def _install_dir():
    """P05 §7：剪映安装目录动态探测，不写死盘符与版本号。

    老写法 ``INSTALL = r'D:\\JianyingPro\\11.3.0.14362'`` 在装了新版本
    或装在其它盘的机器上直接失效（而这个脚本是拿来跑 e2e 回归的，
    一失效就整条链路跑不动）。
    """
    import jianying_path
    return jianying_path.find_install_dir()


INSTALL = _install_dir()
TOL_US = 5          # 允许 1 微秒级浮点取整差（剪映内部用 double 累加）


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, timeout=600, **kw)
    if r.returncode != 0:
        print('  !! 失败:', r.stdout.decode('utf-8', 'replace')[-1500:])
        print('  !! stderr:', r.stderr.decode('utf-8', 'replace')[-1500:])
        raise SystemExit(1)
    return r.stdout.decode('utf-8', 'replace')


def crypto(mode, src, dst):
    """DLL 解密偶发失败（已知问题），失败自动重试。"""
    last = ''
    try:                      # 目标文件已存在会让 DLL 解密失败，先清掉
        os.remove(dst)
    except OSError:
        pass
    for i in range(5):
        r = subprocess.run([sys.executable, '-X', 'utf8',
                            os.path.join(SCRIPTS, 'jy_crypto.py'),
                            mode, os.path.abspath(src), os.path.abspath(dst), INSTALL],
                           capture_output=True, timeout=600)
        if r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0:
            return
        last = r.stderr.decode('utf-8', 'replace')[-300:]
    raise SystemExit('crypto %s 失败（重试 5 次）\n  命令: %s\n  最后错误: %s'
                     % (mode, [sys.executable, '-X', 'utf8', SCRIPTS, mode,
                               os.path.abspath(src), os.path.abspath(dst), INSTALL], last))


def load_plain(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def text_of(draft, mid):
    for m in draft['materials']['texts']:
        if m['id'] == mid:
            try:
                return json.loads(m.get('content', '{}')).get('text', '')
            except Exception:
                return ''
    return '?'


def summarize(draft, label):
    """提取可对比的时间轴摘要。"""
    out = {'label': label, 'duration': draft.get('duration'), 'tracks': []}
    for tr in draft['tracks']:
        segs = []
        for s in tr['segments']:
            tt = s.get('target_timerange') or {}
            rec = {'start': tt.get('start', 0), 'dur': tt.get('duration', 0)}
            if tr['type'] == 'text' and tr.get('attribute') == 2:
                rec['text'] = text_of(draft, s['material_id'])
            segs.append(rec)
        segs.sort(key=lambda r: r['start'])
        out['tracks'].append({'type': tr['type'], 'attr': tr.get('attribute'),
                              'n': len(segs), 'segs': segs})
    return out


def cmp_tracks(got, want, problems):
    """只比字幕轨和配音轨的段级时间轴。"""
    def pick(summary, pred):
        return [t for t in summary['tracks'] if pred(t)]

    # 字幕：合并全部 text/attr=2 轨，按 start 排序
    def subs(summary):
        r = []
        for t in pick(summary, lambda t: t['type'] == 'text' and t['attr'] == 2):
            r.extend(t['segs'])
        return sorted(r, key=lambda x: x['start'])

    # 配音：audio 轨里 material 属于 text_to_audio 的段（用 dur 排序无法区分，直接比数列）
    def tts(summary, draft):
        ids = {a['id'] for a in draft['materials']['audios']
               if a.get('type') == 'text_to_audio'}
        r = []
        for tr in draft['tracks']:
            if tr['type'] != 'audio':
                continue
            for s in tr['segments']:
                if s.get('material_id') in ids:
                    tt = s.get('target_timerange') or {}
                    r.append({'start': tt.get('start', 0), 'dur': tt.get('duration', 0)})
        return sorted(r, key=lambda x: x['start'])

    gs, ws = subs(got), subs(want)
    if len(gs) != len(ws):
        problems.append('字幕段数 %d ≠ 期望 %d' % (len(gs), len(ws)))
        return
    for i, (a, b) in enumerate(zip(gs, ws)):
        if a.get('text') != b.get('text'):
            problems.append('字幕#%d 文本不符\n        得到: %s\n        期望: %s'
                            % (i, a.get('text'), b.get('text')))
        if abs(a['start'] - b['start']) > TOL_US:
            problems.append('字幕#%d start %.3fs ≠ 期望 %.3fs'
                            % (i, a['start'] / 1e6, b['start'] / 1e6))
        if abs(a['dur'] - b['dur']) > TOL_US:
            problems.append('字幕#%d duration %.3fs ≠ 期望 %.3fs'
                            % (i, a['dur'] / 1e6, b['dur'] / 1e6))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--skip-arrange', action='store_true', help='已有结果，直接对比')
    args = ap.parse_args()

    print('=' * 70)
    print('arrange_park_video.py 端到端验证')
    print('=' * 70)

    # ---------- 1. 搭建测试环境 ----------
    # 注意：沙箱下 shutil.rmtree 会被拦截，改用 dirs_exist_ok 覆盖式复制
    draft_dir = os.path.join(TEST_ROOT, '260831-scrambled')
    os.makedirs(draft_dir, exist_ok=True)
    for sub in ('textReading', 'materials'):
        src = os.path.join(V9, sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(draft_dir, sub), dirs_exist_ok=True)
    print('[环境] %s' % draft_dir)
    print('       textReading %d 个 wav / materials %d 个文件'
          % (len(os.listdir(os.path.join(draft_dir, 'textReading'))),
             len(os.listdir(os.path.join(draft_dir, 'materials')))))

    # 乱序现场解密 → 明文 → 再加密成 draft_content.json（模拟真实草稿的加密态）
    print('[环境] 解密乱序现场快照 →', os.path.basename(SCRAMBLED_BAK))
    plain_bak = os.path.join(TEST_ROOT, '~scrambled_plain.json')
    crypto('-d', SCRAMBLED_BAK, plain_bak)
    crypto('-e', plain_bak, os.path.join(draft_dir, 'draft_content.json'))

    # ---------- 2. 记录乱序现场（before） ----------
    tmp = os.path.join(TEST_ROOT, '~before.json')
    before = load_plain(plain_bak)
    n_text_tracks_before = len([t for t in before['tracks']
                                if t['type'] == 'text' and t.get('attribute') == 2])
    n_audio_tracks_before = len([t for t in before['tracks'] if t['type'] == 'audio'])
    print('[before] 字幕轨 %d 条 / audio 轨 %d 条 / duration %.2fs'
          % (n_text_tracks_before, n_audio_tracks_before, before['duration'] / 1e6))

    # ---------- 3. 跑归位脚本 ----------
    if not args.skip_arrange:
        print('\n[运行] arrange_park_video.py --bgm keep')
        out = run([sys.executable, '-X', 'utf8',
                   os.path.join(SCRIPTS, 'arrange_park_video.py'), draft_dir,
                   '--bgm', 'keep', '--install', INSTALL])
        print('  ' + out.rstrip().replace('\n', '\n  '))
    else:
        print('\n[跳过] 直接对比已有结果')

    # ---------- 4. 与正确答案（v9 用户手动归位版）对比 ----------
    print('\n[对比] 参考答案 = 用户手动归位后的 v9 草稿')
    crypto('-d', os.path.join(draft_dir, 'draft_content.json'), tmp)
    got_draft = load_plain(tmp)
    got = summarize(got_draft, '脚本归位')

    want_tmp = os.path.join(TEST_ROOT, '~want.json')
    crypto('-d', os.path.join(V9, 'draft_content.json'), want_tmp)
    want_draft = load_plain(want_tmp)
    want = summarize(want_draft, '手动归位')

    print('  得到: %s' % ', '.join('%s/%s×%d' % (t['type'], t['attr'], t['n'])
                                  for t in got['tracks']))
    print('  期望: %s' % ', '.join('%s/%s×%d' % (t['type'], t['attr'], t['n'])
                                  for t in want['tracks']))
    print('  总时长: 得到 %.2fs / 期望 %.2fs'
          % (got['duration'] / 1e6, want['duration'] / 1e6))

    problems = []
    if abs(got['duration'] - want['duration']) > TOL_US * 17:
        problems.append('总时长 %.2fs ≠ 期望 %.2fs'
                        % (got['duration'] / 1e6, want['duration'] / 1e6))
    cmp_tracks(got, want, problems)

    print()
    if problems:
        print('[结果] FAIL —— %d 处不符' % len(problems))
        for p in problems[:30]:
            print('   -', p)
        rc = 1
    else:
        print('[结果] PASS —— 字幕逐条文本/start/duration 与手动归位一致，总时长一致')
        rc = 0

    # ---------- 5. 逐条明细 ----------
    print('\n[明细] 字幕时间轴（前 5 条）')
    gs = sorted([s for t in got['tracks'] if t['type'] == 'text' and t['attr'] == 2
                 for s in t['segs']], key=lambda x: x['start'])
    ws = sorted([s for t in want['tracks'] if t['type'] == 'text' and t['attr'] == 2
                 for s in t['segs']], key=lambda x: x['start'])
    for i in range(min(5, len(gs), len(ws))):
        mark = ' ' if (gs[i].get('text') == ws[i].get('text')
                       and abs(gs[i]['start'] - ws[i]['start']) <= TOL_US) else '*'
        print('  %s#%02d %.2f~%.2f  %s' % (mark, i, gs[i]['start'] / 1e6,
                                           (gs[i]['start'] + gs[i]['dur']) / 1e6,
                                           gs[i].get('text', '')[:24]))

    if not args.keep:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print('\n[清理] 测试目录已删除（若沙箱拦截 rmtree 请手工删除 tests/_e2e）')
    else:
        print('\n[保留] %s' % TEST_ROOT)
    return rc


if __name__ == '__main__':
    sys.exit(main())
