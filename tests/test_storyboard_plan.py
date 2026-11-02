# -*- coding: utf-8 -*-
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import storyboard


def touch(path, content=b'x'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f: f.write(content)


def main():
    with tempfile.TemporaryDirectory() as td:
        touch(os.path.join(td, '园区门口', 'JD-ENTRANCE-0001-v1_大门_竖版.mp4'))
        touch(os.path.join(td, '厂房内部', 'JD-INSIDE-0001-v1_内景_竖版.mp4'))
        touch(os.path.join(td, '宿舍', 'JD-DORM-0001-v1_公寓_竖版.mp4'))
        touch(os.path.join(td, '高速', 'JD-HIGHWAY-0001-v1_高速口_竖版.mp4'))
        script = ('嘉定智造园火热招商中。距高速口仅5分钟。'
                  '5000平方米高标厂房，层高10米。人才公寓月租500元起。点击链接预约看园。')
        plan = storyboard.create_plan(script, td, style='2', orientation='竖屏')
        assert len(plan['scenes']) >= 3
        assert all(s['candidates'] for s in plan['scenes'])
        scene = plan['scenes'][0]
        storyboard.select_candidate(plan, 1, scene['candidates'][0]['asset_id'])
        storyboard.validate_plan(plan, script)
        assert plan['status'] == 'CONFIRMED'
    print('PASS storyboard plan')


if __name__ == '__main__':
    main()
