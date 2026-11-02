# -*- coding: utf-8 -*-
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import draft_builder as db


def main():
    speed, src, note, ok = db.fit_clip_speed(4_000_000, 4_000_000)
    assert (speed, src, ok) == (1.0, 4_000_000, True)
    speed, src, note, ok = db.fit_clip_speed(3_000_000, 4_000_000)
    assert round(speed, 2) == 0.75 and src == 3_000_000 and ok
    speed, src, note, ok = db.fit_clip_speed(12_000_000, 4_000_000)
    assert speed == 1.0 and src == 4_000_000 and '保持 1 倍速' in note
    speed, src, note, ok = db.fit_clip_speed(1_000_000, 4_000_000)
    assert not ok and speed == 0.5
    seg = db.make_video_segment('m1', 0, 4_000_000, 1.0,
                                src_dur=4_000_000, speed=1.0, src_start=4_000_000)
    assert seg['source_timerange']['start'] == 4_000_000
    print('PASS cutting policy')


if __name__ == '__main__':
    main()
