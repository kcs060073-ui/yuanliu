# -*- coding: utf-8 -*-
"""生成/校验 Skill 发布清单。"""
import argparse
import hashlib
import json
import os
import time

from atomic_io import atomic_write_json

OFFICIAL_DIRS = ('scripts', 'tests', 'templates', 'config', 'workbench')
OFFICIAL_FILES = ('SKILL.md', 'PROJECT_STATUS.md', '语义规则分配表.xlsx')

# 排除口径必须与 pack_release.py 的收录口径**完全一致**。
# 曾经不一致 → 清单里写进了 tests/_tmp/verify_16568.json（测试跑完的临时产物），
# 而打包时 _tmp 被排除 → 同事装完跑清单校验必然报「缺少文件」→ 安装自检失败
# → 自动回滚。清单里只要列了一个不会发出去的文件，整条安装链就会断。
EXCLUDE = ('__pycache__', '.pyc', '.pyo', '.bak', 'gui_settings.json')
EXCLUDE_DIRS = ('__pycache__', '_tmp', '_tts_probe')
# 顶层目录：本机资产，各机器各留一份，不进包也不进清单
EXCLUDE_TOP = ('output', 'state')

# **受保护的本地资产**：安装/升级时用户数据优先，绝不覆盖。它们因此
# **不能进发布清单** —— 清单的意思是"这些文件的内容必须与包一致"，
# 而这类文件装完必然还是用户自己那份，哈希注定对不上。
#
# 2026-09-20 实测事故：``config/materials_baseline.json`` 同时被
#   ① collect() 收进清单（记下包内占位符的哈希）
#   ② install_p05.py 当 PROTECTED_FILES 保留（不覆盖用户的基准）
# 于是升级装完跑清单校验 → "文件校验失败：config/materials_baseline.json"
# → 自检不通过 → **自动回滚**。任何设过素材库基准的同事升级 P05 都会失败。
PROTECTED_LOCAL = ('config/gui_settings.json', 'config/materials_baseline.json')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def collect(root):
    out = []
    for d in OFFICIAL_DIRS:
        if d in EXCLUDE_TOP:
            continue
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for cur, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x not in EXCLUDE_DIRS]
            for fn in files:
                rel = os.path.relpath(os.path.join(cur, fn), root).replace('\\', '/')
                if any(x in rel for x in EXCLUDE):
                    continue
                if rel in PROTECTED_LOCAL:
                    continue          # 用户资产：装了也不覆盖，绝不能进清单
                if rel.split('/')[0] in EXCLUDE_TOP:
                    continue
                out.append(rel)
    for fn in OFFICIAL_FILES:
        if os.path.isfile(os.path.join(root, fn)):
            out.append(fn)
    return sorted(set(out))


def build(root, version, min_upgrade_from='260916-P02'):
    root = os.path.abspath(root)
    files = []
    for rel in collect(root):
        fp = os.path.join(root, rel.replace('/', os.sep))
        files.append({'path': rel, 'size': os.path.getsize(fp), 'sha256': sha256_file(fp),
                      'policy': 'official'})
    data = {
        'schema_version': 1, 'release_version': version,
        'min_upgrade_from': min_upgrade_from, 'data_schema_version': 1,
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'protected_paths': ['output/', 'state/', 'config/gui_settings.json',
                            'config/materials_baseline.json'],
        'files': files,
        'tests': ['tests/test_*.py'],
    }
    atomic_write_json(os.path.join(root, 'release_manifest.json'), data)
    return data


def verify(root, manifest=None):
    root = os.path.abspath(root)
    if manifest is None:
        with open(os.path.join(root, 'release_manifest.json'), encoding='utf-8') as f:
            manifest = json.load(f)
    errors = []
    if manifest.get('schema_version') != 1:
        errors.append('不支持的发布清单版本')
    for rec in manifest.get('files', []):
        fp = os.path.join(root, rec['path'].replace('/', os.sep))
        if not os.path.isfile(fp):
            errors.append('缺少文件：' + rec['path']); continue
        if os.path.getsize(fp) != rec.get('size') or sha256_file(fp) != rec.get('sha256'):
            errors.append('文件校验失败：' + rec['path'])
    return errors


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root'); ap.add_argument('--build', metavar='VERSION')
    args = ap.parse_args()
    if args.build:
        m = build(args.root, args.build)
        print('发布清单已生成：%s（%d 个文件）' % (m['release_version'], len(m['files']))); return 0
    errs = verify(args.root)
    if errs:
        print('\n'.join(errs)); return 1
    print('发布清单校验通过'); return 0


if __name__ == '__main__':
    raise SystemExit(main())
