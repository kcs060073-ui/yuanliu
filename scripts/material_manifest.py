# -*- coding: utf-8 -*-
"""剪映云端素材包的清单、校验与本地增量安装。

剪映负责上传/下载，本工具不自动登录云端。管理员对已筛选素材生成 manifest；
剪辑者下载完整包或增量包后，用本脚本校验并原子安装到本地素材库。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import zipfile

from atomic_io import FileLock, atomic_write_json

MEDIA_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.jfif')
MANIFEST_NAME = 'materials_manifest.json'
ASSET_RE = re.compile(r'^([A-Z0-9]+(?:-[A-Z0-9]+){2,})[_-]')


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def asset_id_for(relpath):
    stem = os.path.splitext(os.path.basename(relpath))[0].upper()
    m = ASSET_RE.match(stem)
    if m:
        return m.group(1)
    digest = hashlib.sha1(relpath.replace('\\', '/').encode('utf-8')).hexdigest()[:10].upper()
    return 'YL-AUTO-' + digest


def build_manifest(root, version, kind='full', base_version='', output=None):
    root = os.path.abspath(root)
    assets = []
    ids = set()
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('.staging', '__pycache__')]
        for fn in sorted(files):
            if not fn.lower().endswith(MEDIA_EXTS):
                continue
            fp = os.path.join(cur, fn)
            rel = os.path.relpath(fp, root).replace('\\', '/')
            aid = asset_id_for(rel)
            if aid in ids:
                raise ValueError('素材ID重复：%s。请在文件名前加入唯一素材ID。' % aid)
            ids.add(aid)
            st = os.stat(fp)
            assets.append({
                'asset_id': aid, 'path': rel, 'size': st.st_size,
                'sha256': sha256_file(fp), 'status': 'active',
            })
    assets.sort(key=lambda x: x['path'].lower())
    manifest = {
        'schema_version': 1, 'library_version': version, 'package_kind': kind,
        'base_version': base_version or '',
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'asset_count': len(assets), 'assets': assets,
    }
    out = output or os.path.join(root, MANIFEST_NAME)
    atomic_write_json(out, manifest)
    return out, manifest


def load_manifest(path_or_root):
    p = path_or_root
    if os.path.isdir(p):
        p = os.path.join(p, MANIFEST_NAME)
    with open(p, encoding='utf-8') as f:
        data = json.load(f)
    if data.get('schema_version') != 1 or not isinstance(data.get('assets'), list):
        raise ValueError('素材清单结构不受支持：%s' % p)
    return data


def verify(root, manifest=None, full_hash=True):
    root = os.path.abspath(root)
    manifest = manifest or load_manifest(root)
    missing, changed, ok = [], [], 0
    for rec in manifest['assets']:
        fp = os.path.join(root, rec['path'].replace('/', os.sep))
        if not os.path.isfile(fp):
            missing.append(rec['path'])
            continue
        if os.path.getsize(fp) != int(rec.get('size', -1)):
            changed.append(rec['path'])
            continue
        if full_hash and sha256_file(fp) != rec.get('sha256'):
            changed.append(rec['path'])
            continue
        ok += 1
    return {'ok': ok, 'missing': missing, 'changed': changed,
            'expected': len(manifest['assets']), 'version': manifest.get('library_version', '')}


def _safe_extract(zpath, dst):
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            target = os.path.abspath(os.path.join(dst, info.filename))
            if not (target == os.path.abspath(dst) or target.startswith(os.path.abspath(dst) + os.sep)):
                raise ValueError('压缩包包含不安全路径：%s' % info.filename)
        z.extractall(dst)


def _find_package_root(root):
    if os.path.isfile(os.path.join(root, MANIFEST_NAME)):
        return root
    found = []
    for cur, _, files in os.walk(root):
        if MANIFEST_NAME in files:
            found.append(cur)
    if len(found) != 1:
        raise ValueError('素材包内必须且只能有一个 %s' % MANIFEST_NAME)
    return found[0]


def install_package(package, target):
    """安装下载好的完整/增量素材包。校验全部通过后才写目标库。"""
    cleanup = None
    if os.path.isfile(package) and package.lower().endswith('.zip'):
        cleanup = tempfile.mkdtemp(prefix='yuanliu_material_')
        _safe_extract(package, cleanup)
        src = _find_package_root(cleanup)
    elif os.path.isdir(package):
        src = _find_package_root(package)
    else:
        raise ValueError('素材包必须是目录或 zip：%s' % package)
    try:
        manifest = load_manifest(src)
        checked = verify(src, manifest, full_hash=True)
        if checked['missing'] or checked['changed']:
            raise ValueError('素材包校验失败：缺失 %d，内容异常 %d' %
                             (len(checked['missing']), len(checked['changed'])))
        target = os.path.abspath(target)
        os.makedirs(target, exist_ok=True)
        installed = 0
        with FileLock(os.path.join(target, '.yuanliu-material-install'), timeout=60):
            for rec in manifest['assets']:
                src_fp = os.path.join(src, rec['path'].replace('/', os.sep))
                dst_fp = os.path.join(target, rec['path'].replace('/', os.sep))
                if (os.path.isfile(dst_fp) and os.path.getsize(dst_fp) == rec['size']
                        and sha256_file(dst_fp) == rec['sha256']):
                    continue
                os.makedirs(os.path.dirname(dst_fp), exist_ok=True)
                fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(dst_fp) + '.',
                                           suffix='.part', dir=os.path.dirname(dst_fp))
                os.close(fd)
                try:
                    shutil.copy2(src_fp, tmp)
                    if sha256_file(tmp) != rec['sha256']:
                        raise IOError('复制后哈希不一致：%s' % rec['path'])
                    os.replace(tmp, dst_fp)
                    installed += 1
                finally:
                    if os.path.exists(tmp):
                        os.remove(tmp)
            local_manifest = dict(manifest)
            local_manifest['installed_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            atomic_write_json(os.path.join(target, MANIFEST_NAME), local_manifest)
        return {'version': manifest.get('library_version'), 'installed': installed,
                'total': len(manifest['assets']), 'target': target}
    finally:
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd')
    b = sub.add_parser('build', help='管理员：为已筛选素材生成版本清单')
    b.add_argument('root'); b.add_argument('--version', required=True)
    b.add_argument('--kind', choices=('full', 'incremental'), default='full')
    b.add_argument('--base-version', default='')
    v = sub.add_parser('verify', help='校验本地素材库完整性')
    v.add_argument('root'); v.add_argument('--fast', action='store_true')
    i = sub.add_parser('install', help='安装从剪映云空间下载的素材包')
    i.add_argument('package'); i.add_argument('--target', required=True)
    args = ap.parse_args()
    if args.cmd == 'build':
        p, m = build_manifest(args.root, args.version, args.kind, args.base_version)
        print('清单已生成：%s（%d 个素材，版本 %s）' % (p, len(m['assets']), args.version)); return 0
    if args.cmd == 'verify':
        r = verify(args.root, full_hash=not args.fast)
        print('版本 %s：%d/%d 通过，缺失 %d，异常 %d' %
              (r['version'], r['ok'], r['expected'], len(r['missing']), len(r['changed'])))
        return 0 if not r['missing'] and not r['changed'] else 1
    if args.cmd == 'install':
        r = install_package(args.package, args.target)
        print('素材包 %s 安装完成：更新 %d/%d → %s' %
              (r['version'], r['installed'], r['total'], r['target'])); return 0
    ap.print_help(); return 1


if __name__ == '__main__':
    raise SystemExit(main())
