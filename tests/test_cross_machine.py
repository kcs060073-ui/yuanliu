# -*- coding: utf-8 -*-
"""P05 §7：跨电脑的 BGM / 字体 / 剪映安装目录解析回归。

要求（原话）：「BGM 优先本机草稿+剪映缓存，所有本地路径经 os.path.isfile()
校验，找不到不伪装成功、醒目标记"本机未找到可用BGM"；字体复用
jianying_path.py 动态探测，禁止写死盘符与版本号；新增不同盘符/版本测试。」

本测试用**临时目录伪造**两种剪映布局（<root>\\<版本> 与 <root>\\Apps\\<版本>）
与一个假的音乐缓存目录，不依赖本机真的装了什么。

在 P05 二轮补丁里，``bgm_pool`` 会**同时**扫「剪映安装目录反推的根」和
``%LOCALAPPDATA%\\JianyingPro``（剪映装在 D 盘时缓存常常在用户目录下）。
所以本测试必须把 ``LOCALAPPDATA`` 指到一个空临时目录，
否则**跑测试的这台机器**自带的音乐缓存会漏进来，让"全无可用曲目"之类的用例
变成"有时候过、有时候不过"。

单独运行：
    python tests/test_cross_machine.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import bgm_pool
import jianying_path

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        print('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        print('  [FAIL] %s %s' % (name, extra))


def touch(p, data=b''):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, 'wb') as f:
        f.write(data)
    return p


def fake_mp3(path, size=256 * 1000):
    """造一个 MPEG1 Layer3 128kbps/44100Hz 的帧头，用于时长估算。

    大小决定估算时长（size*8/(128*1000) 秒）：**必须够长**。2026-09-20 起
    `resolve_bgm` 会剔除 <15s 的候选（剪映缓存里的试听碎片），128KB≈8s 会被
    当成碎片剔除。256KB≈16s 刚过下限。
    """
    head = b'\xff\xfb\x90\x00'
    return touch(path, head + b'\x00' * max(0, size - len(head)))


def main():
    print('== P05 §7 跨电脑 BGM / 字体 / 安装目录 回归 ==')
    td = tempfile.mkdtemp(prefix='p05_xmachine_')
    saved_la = os.environ.get('LOCALAPPDATA')
    # 把 %LOCALAPPDATA% 钉到空临时目录：只让**本测试伪造的**缓存目录被扫到，
    # 不掺入这台机器真实的音乐缓存（否则结果不可复现）。
    os.environ['LOCALAPPDATA'] = os.path.join(td, 'localappdata')
    try:
        # ---------------- 1) 安装目录两种布局 ----------------
        root = os.path.join(td, '盘符D', 'JianyingPro')
        old_layout = os.path.join(root, '11.3.0.14362')          # <root>\<版本>
        new_layout = os.path.join(root, 'Apps', '11.4.2.14459')  # <root>\Apps\<版本>
        touch(os.path.join(old_layout, 'videoeditor.dll'), b'x')
        touch(os.path.join(new_layout, 'videoeditor.dll'), b'x')

        got = jianying_path._latest_by_mid(root, 'videoeditor.dll')
        check('1a 新旧布局并存时取版本号更大的那个', got == new_layout,
              '%s vs %s' % (got, new_layout))
        check('1b 显式 hint 优先',
              jianying_path.find_file_in_install(
                  'videoeditor.dll', hint=old_layout) == old_layout)
        check('1c hint 不存在时不误用',
              jianying_path.find_file_in_install(
                  'videoeditor.dll', hint=os.path.join(td, '不存在')) !=
              os.path.join(td, '不存在'))
        check('1d 版本号按数字段比较（11.10 > 11.9）',
              jianying_path._verkey('11.10.0') > jianying_path._verkey('11.9.0'))
        check('1e 空目录返回空串',
              jianying_path._latest_by_mid(os.path.join(td, '空'), 'videoeditor.dll') == '')

        # ---------------- 2) 字体探测 ----------------
        fdir = os.path.join(new_layout, 'Resources', 'Font', 'SystemFont')
        zh = touch(os.path.join(fdir, 'zh-hans.ttf'), b'fake-font')
        check('2a 剪映资源目录里的字体被找到',
              jianying_path.find_font(install_dir=new_layout) == os.path.abspath(zh),
              jianying_path.find_font(install_dir=new_layout))

        # 空安装目录：必须退到 Windows 中文字体，或返回空串；
        # 关键不变量：**绝不返回不存在的路径**
        empty_install = os.path.join(td, '空剪映', '9.9.9')
        os.makedirs(empty_install, exist_ok=True)
        p = jianying_path.find_font(install_dir=empty_install)
        check('2b 探测不到时要么给真实存在的字体、要么给空串',
              p == '' or os.path.isfile(p), repr(p))
        check('2c 不会把不存在路径当结果', not (p and not os.path.isfile(p)), repr(p))

        # hint 指到别处：hint 本身就是真文件时按 hint 走
        huhint = touch(os.path.join(td, '我的字体.ttf'), b'fake')
        check('2d 显式 hint 优先于探测',
              jianying_path.find_font(install_dir=new_layout, hint=huhint)
              == os.path.abspath(huhint))
        check('2e hint 指向不存在的文件时忽略它',
              jianying_path.find_font(install_dir=new_layout,
                                      hint=os.path.join(td, '没有.ttf'))
              == os.path.abspath(zh))

        # ---------------- 3) jianying_root 反推 ----------------
        check('3a 旧布局反推到根',
              os.path.normcase(bgm_pool.jianying_root(old_layout))
              == os.path.normcase(root), bgm_pool.jianying_root(old_layout))
        check('3b 新布局 Apps\\<版本> 也反推到根',
              os.path.normcase(bgm_pool.jianying_root(new_layout))
              == os.path.normcase(root), bgm_pool.jianying_root(new_layout))
        check('3c 空入参返回空串', bgm_pool.jianying_root('') == '')

        # ---------------- 4) BGM：剪映音乐缓存 ----------------
        cache = os.path.join(root, 'User Data', 'Cache', 'music')
        mus1 = fake_mp3(os.path.join(cache, 'aaaa.mp3'))
        mus2 = fake_mp3(os.path.join(cache, 'bbbb.mp3'))
        # 非音频扩展名必须被忽略
        touch(os.path.join(cache, 'note.txt'), b'not audio')
        drafts_none = os.path.join(td, '没有这个草稿库')     # 让 scan_pool 立刻返回空

        cands = bgm_pool.collect_local_bgm(drafts_none, new_layout)
        names = sorted(r['name'] for r in cands)
        check('4a 从音乐缓存里收集到 2 首', names == ['aaaa', 'bbbb'], str(names))
        check('4b 非音频文件被忽略', 'note' not in names, str(names))
        check('4c 所有候选路径都真实存在',
              all(os.path.isfile(r['path']) for r in cands), str(cands))
        check('4d 候选时长被估算出来（>0）',
              all((r.get('duration') or 0) > 0 for r in cands),
              str([r.get('duration') for r in cands]))
        check('4e 路径绝对化', all(os.path.isabs(r['path']) for r in cands))

        # 删除一个文件后，它必须从候选里消失（不伪装）
        os.remove(mus2)
        cands2 = bgm_pool.collect_local_bgm(drafts_none, new_layout)
        check('4f 被删掉的文件不再出现在候选里',
              [r['name'] for r in cands2] == ['aaaa'], str([r['name'] for r in cands2]))

        # ---------------- 5) resolve_bgm ----------------
        rec, note = bgm_pool.resolve_bgm('random', drafts_none, new_layout, seed=7)
        check('5a 有本机曲目时返回记录', isinstance(rec, dict) and os.path.isfile(rec['path']),
              str(rec))
        rec_b, _ = bgm_pool.resolve_bgm('random', drafts_none, new_layout, seed=7)
        check('5b seed 相同 → 选曲可复现', rec == rec_b)

        rec_f, note_f = bgm_pool.resolve_bgm('fixed', drafts_none, new_layout,
                                             style_bgm={'resource_id': '不存在的ID',
                                                        'name': '不存在的曲名'})
        check('5c fixed 模式本机没有默认曲时用本机可用曲目',
              isinstance(rec_f, dict) and os.path.isfile(rec_f['path']), note_f)

        # 完全找不到：必须 (None, 明确说明)
        rec_none, note_none = bgm_pool.resolve_bgm(
            'random', drafts_none, os.path.join(td, '空的剪映', '1.0.0'))
        check('5d 全无可用曲目 → None', rec_none is None, str(rec_none))
        check('5e 全无可用曲目 → 说明里出现「本机未找到可用BGM」',
              '本机未找到可用BGM' in note_none, note_none)

        # ---------------- 6) MP3 时长估算 ----------------
        d = bgm_pool._mp3_duration_us(mus1)
        expect = int(os.path.getsize(mus1) * 8 * 1e6 / (128 * 1000))
        check('6a MP3 时长估算接近 CBR 理论值',
              abs(d - expect) < 5000, '%d vs %d' % (d, expect))
        check('6b 非音频文件估算为 0',
              bgm_pool._mp3_duration_us(os.path.join(cache, 'note.txt')) == 0)

        # ---------------- 7) summarize ----------------
        check('7a 无 BGM 时说明里明确写"无 BGM"，且不假装有回退',
              '无 BGM' in bgm_pool.summarize(None) and
              '回退' not in bgm_pool.summarize(None), bgm_pool.summarize(None))

        # ---------------- 8) 草稿层：无 BGM 就不建轨 ----------------
        import draft_builder as db
        warn = []
        draft, info = db.build_draft(
            '园区厂房招商，配套齐全。', style_key='3', materials_dir='',
            bgm_choice=None, allow_empty_materials=True, warnings=warn,
            install_dir=new_layout)
        audio_tracks = [t for t in draft['tracks'] if t['type'] == 'audio']
        check('8a 无可用 BGM → 不生成任何 audio 轨', audio_tracks == [],
              str(len(audio_tracks)))
        check('8b 无 BGM 时曲目名/路径留空（不伪装）',
              info['bgm']['name'] == '' and info['bgm']['path'] == '',
              str(info['bgm']))
        check('8c info.bgm.available 为 False', info['bgm']['available'] is False)
        check('8d 有「本机未找到可用BGM」警告',
              any('未找到可用BGM' in w for w in warn), str(warn))
        check('8e materials.audios 里没有 music 素材',
              not [a for a in draft['materials']['audios'] if a.get('type') == 'music'],
              str(draft['materials']['audios']))
        check('8f platform 已洗白（无设备标识字段）',
              not any(k in (draft.get('platform') or {})
                      for k in ('device_id', 'hard_disk_id', 'mac_address')),
              str(sorted((draft.get('platform') or {}).keys())))
        check('8g 草稿 JSON 里没有写死的剪映盘符字体路径',
              'Resources/Font/SystemFont' not in json.dumps(draft, ensure_ascii=False)
              or all(os.path.isfile(t.get('font_path') or '')
                     for t in draft['materials']['texts']))

        # 有真实 BGM 时应当建轨
        warn2 = []
        draft2, info2 = db.build_draft(
            '园区厂房招商，配套齐全。', style_key='3', materials_dir='',
            bgm_choice=cands2[0], allow_empty_materials=True, warnings=warn2,
            install_dir=new_layout)
        at2 = [t for t in draft2['tracks'] if t['type'] == 'audio']
        check('8h 有可用 BGM → 建 1 条 audio 轨', len(at2) == 1, str(len(at2)))
        check('8i BGM 素材路径落在草稿里且真实存在',
              any(os.path.isfile(a.get('path', '')) for a in draft2['materials']['audios']),
              str([a.get('path') for a in draft2['materials']['audios']]))
        check('8j info.bgm.available 为 True', info2['bgm']['available'] is True)

        # 指向不存在文件的记录 → 视同没有（不伪装）
        warn3 = []
        draft3, info3 = db.build_draft(
            '园区厂房招商，配套齐全。', style_key='3', materials_dir='',
            bgm_choice={'name': '假的', 'path': os.path.join(td, '没有.mp3')},
            allow_empty_materials=True, warnings=warn3, install_dir=new_layout)
        check('8k 记录了不存在的 BGM → 不建轨',
              [t for t in draft3['tracks'] if t['type'] == 'audio'] == [])
        check('8l 并记下警告', any('不存在' in w for w in warn3), str(warn3))

        # ---------------- 9) 草稿里的字体路径（§7「不写死」的落点）----------------
        fonts = [t.get('font_path') for t in draft.get('materials', {}).get('texts', [])]
        fonts = [f for f in fonts if f]
        check('9a 草稿里的字体路径若不是空就一定真实存在',
              all(os.path.isfile(f) for f in fonts), str(fonts))
        check('9b 不会出现「D 盘 + 剪映版本目录」那种写死字体路径',
              not any(f.lower().startswith('d:') and 'resources/font' in f.lower()
                      for f in fonts), str(fonts))
        check('9c 模板元数据里没有遗留管理员缓存路径字段',
              all('_template_path' not in io.open(
                      os.path.join(ROOT, 'templates', fn), encoding='utf-8').read()
                  for fn in os.listdir(os.path.join(ROOT, 'templates'))
                  if fn.endswith('.json')))
    finally:
        if saved_la is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = saved_la
        shutil.rmtree(td, ignore_errors=True)

    print('\n通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for x in FAILED:
            print('  FAILED: %s' % x)
        raise SystemExit(1)
    print('PASS cross machine')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
