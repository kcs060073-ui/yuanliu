# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import material_manifest as mm


def main():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, 'pack'); dst = os.path.join(td, 'library')
        os.makedirs(os.path.join(src, '嘉定', '厂房内部'))
        fp = os.path.join(src, '嘉定', '厂房内部', 'JD-FACTORY-0001-v1_内景.mp4')
        with open(fp, 'wb') as f: f.write(b'video-one')
        _, mf = mm.build_manifest(src, '2026.09.16-1')
        assert mf['asset_count'] == 1
        r = mm.verify(src); assert r['ok'] == 1 and not r['missing']
        installed = mm.install_package(src, dst)
        assert installed['installed'] == 1
        assert mm.verify(dst)['ok'] == 1
        # 同一版本重装不重复复制
        assert mm.install_package(src, dst)['installed'] == 0
        # 内容被改动必须被发现
        local = os.path.join(dst, mf['assets'][0]['path'].replace('/', os.sep))
        with open(local, 'ab') as f: f.write(b'changed')
        assert mm.verify(dst)['changed']
    print('PASS material manifest')


if __name__ == '__main__':
    main()
