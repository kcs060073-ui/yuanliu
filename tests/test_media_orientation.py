# -*- coding: utf-8 -*-
"""朝向/尺寸探测回归：tkhd v0/v1 偏移、旋转矩阵、标注冲突时以画面为准。

2026-11-02 起因：竖版草稿会混入横屏素材（南京 专业设施 桶横竖混装），
根因是方向只看目录名/文件名；顺带查出 tkhd v0 偏移少算 8 字节（尺寸全错）。
"""
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import draft_builder as db
import material_matcher as mm

FIX = 65536


def _box(name, payload):
    return struct.pack('>I', 8 + len(payload)) + name + payload


def _tkhd_v0(w, h, rot=0):
    """构造 v0 tkhd：矩阵在 box[48:84]，宽高在 box[84:92]。"""
    p = bytearray(84)
    p[0] = 0                       # version 0
    m = [FIX, 0, 0, 0, FIX, 0, 0, 0, 0x40000000]
    if rot == 90:
        m = [0, FIX, 0, -FIX, 0, 0, 0, 0, 0x40000000]
    elif rot == 180:
        m = [-FIX, 0, 0, 0, -FIX, 0, 0, 0, 0x40000000]
    for i, v in enumerate(m):
        p[40 + i * 4:44 + i * 4] = struct.pack('>i', v)
    p[76:80] = struct.pack('>I', w * FIX)
    p[80:84] = struct.pack('>I', h * FIX)
    return _box(b'tkhd', bytes(p))


def _tkhd_v1(w, h):
    """构造 v1 tkhd：矩阵在 box[60:96]，宽高在 box[96:104]。"""
    p = bytearray(96)
    p[0] = 1                       # version 1
    m = [FIX, 0, 0, 0, FIX, 0, 0, 0, 0x40000000]
    for i, v in enumerate(m):
        p[52 + i * 4:56 + i * 4] = struct.pack('>i', v)
    p[88:92] = struct.pack('>I', w * FIX)
    p[92:96] = struct.pack('>I', h * FIX)
    return _box(b'tkhd', bytes(p))


def _fake_mp4(path, tkhd):
    data = _box(b'ftyp', b'isom' + b'\x00\x00\x02\x00' + b'isomiso2')
    data += _box(b'moov', _box(b'trak', tkhd))
    with open(path, 'wb') as f:
        f.write(data)


def main():
    with tempfile.TemporaryDirectory() as td:
        # ① v0 偏移：以前 v0 写成 76，读出 (0, 16384)
        p = os.path.join(td, 'v0_land.mp4')
        _fake_mp4(p, _tkhd_v0(1920, 1080, 0))
        assert db._tkhd_size(_tkhd_v0(1920, 1080, 0)) == (1920, 1080), 'v0 偏移错误'
        assert db.probe_media_size(p) == (1920, 1080), 'v0 尺寸探测错误'
        assert db.probe_media_orientation(p) == 'landscape'

        # ② v1 偏移（历史实现本来就对，防回归）
        assert db._tkhd_size(_tkhd_v1(3840, 2160)) == (3840, 2160), 'v1 偏移错误'

        # ③ 编码 1920x1080 + 旋转 90° = 真实竖屏
        pr = os.path.join(td, 'v0_rot90.mp4')
        _fake_mp4(pr, _tkhd_v0(1920, 1080, 90))
        assert db._tkhd_rotation(_tkhd_v0(1920, 1080, 90)) == 90
        assert db.probe_media_orientation(pr) == 'portrait', '旋转 90° 未识别为竖屏'
        assert db.probe_media_display_size(pr) == (1080, 1920), '显示尺寸未交换宽高'

        # ④ 旋转 180° 仍是横屏
        p180 = os.path.join(td, 'v0_rot180.mp4')
        _fake_mp4(p180, _tkhd_v0(1920, 1080, 180))
        assert db.probe_media_orientation(p180) == 'landscape'

        # ⑤ 标注与实际画面冲突 → 以画面为准
        mm._ORIENT_CONFLICTS.clear()
        assert mm._resolve_orientation(pr, 'landscape', 'video') == 'portrait'
        assert len(mm._ORIENT_CONFLICTS) == 1
        assert mm._resolve_orientation(p, 'portrait', 'video') == 'landscape'
        mm._print_orientation_conflicts()
        mm._ORIENT_CONFLICTS.clear()

        # ⑥ 探测失败时退回标注（不能让无信息的文件变成"无方向"）
        bogus = os.path.join(td, 'empty.mp4')
        open(bogus, 'wb').close()
        assert db.probe_media_orientation(bogus) is None
        assert mm._resolve_orientation(bogus, 'portrait', 'video') == 'portrait'

    print('PASS media orientation')
    return 0


if __name__ == '__main__':
    sys.exit(main())
