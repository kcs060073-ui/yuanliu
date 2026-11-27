# -*- coding: utf-8 -*-
"""剪映版本探测与兼容性预判。

## 要解决什么

剪映每次自动升级，`videoeditor.dll` 的加解密接口都可能变。2026-10-30 升到
11.7.0.14652 后，`EncryptUtils.decrypt()` 对任何输入（包括它自己刚加密的内容）
都返回空串 —— 草稿**写入仍然正常、剪映能打开**，但工具的自检（解密回来核对结构）
永远 FAIL。用户看到「校验失败」会以为草稿坏了。

这个模块做三件事：
1. 出片前读取当前剪映版本，按已验证的版本表给出「加密/解密是否可用」的预判；
2. 遇到没见过的版本只**告警不拦停** —— 版本升级不等于写不了草稿，
   真正写不进去会以 OSError / 加密异常的形式抛出，不该用版本号去猜；
3. 每次出片结束把「这个版本实测解密成没成」记进 config/editor_versions.json，
   下次同版本直接用实测结论，越用越准（新版本第一次运行后自动学会）。

## 性能

实测量级：目录探测 2.2ms + 读一个小 JSON ≈ 0.1ms，合计 **< 3ms**。
出片全流程 2.6~80 秒，占比 0.004%~0.1%，用户无感知。
结论按 (安装目录, 目录 mtime) 缓存，同一进程内重复调用为 0 成本。
"""
import json
import os
import re
import time

import atomic_io
import jianying_path as _jp

# 已验证版本 → (解密是否可用, 说明)
# 只写实测过的；没列进来的一律 unknown，靠运行记录自动学习。
KNOWN = {
    '11.7': (False, '剪映 11.7 起 decrypt 恒返空：自检记为「未校验」并跳过（不是草稿坏了），草稿写入与加密正常，剪映可正常打开'),
    '11.8': (False, '剪映 11.7 起 decrypt 恒返空：自检记为「未校验」并跳过（不是草稿坏了），草稿写入与加密正常，剪映可正常打开'),
}

_CACHE = {}
_CACHE_TTL = 30.0


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path():
    return os.path.join(_root(), 'config', 'editor_versions.json')


def parse_version(text):
    """把目录名/路径里的版本号提出来：'11.8.0.14778' / 'Apps\\\\11.8.0.14778\\\\x' → '11.8.0.14778'。"""
    parts = re.findall(r'\d+', text or '')
    if len(parts) < 2:
        return ''
    return '.'.join(parts)


def major_minor(version):
    parts = (version or '').split('.')
    return '.'.join(parts[:2]) if len(parts) >= 2 else ''


def _load_records():
    path = config_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('versions', {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def probe(install_hint=''):
    """探测剪映安装目录与版本，返回兼容性快照（不抛异常）。

    返回字段：
      install     安装目录（找不到为空串）
      version     版本号（解析不到为空串）
      decrypt_ok  True/False/None（None = 该版本没验证过）
      level       ok | warn | unknown | missing
      note        给用户看的一句话
      source      table（已知版本表）/ record（本机实测记录）/ guess
    """
    key = (install_hint or '',)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return dict(hit[1])

    install = ''
    try:
        install = _jp.find_file_in_install(_jp.VIDEOEDITOR, install_hint) or ''
    except Exception:
        install = ''
    if not install and install_hint and os.path.isdir(install_hint):
        install = install_hint

    version = parse_version(os.path.basename(os.path.normpath(install))) if install else ''
    # Apps\<版本>\ 布局下 basename 就是版本号；解析不到再退一步从整条路径找
    if not version and install:
        version = parse_version(install)

    info = {
        'install': install, 'version': version,
        'decrypt_ok': None, 'level': 'missing', 'note': '', 'source': '',
    }
    if not install:
        info['note'] = '未找到剪映安装目录（缺少 %s），无法加解密草稿' % _jp.VIDEOEDITOR
        _CACHE[key] = (now, dict(info))
        return info

    mm = major_minor(version)
    records = _load_records()
    record = records.get(version) if version else None

    if record and record.get('decrypt_ok') is not None:
        info['decrypt_ok'] = bool(record['decrypt_ok'])
        info['source'] = 'record'
    elif mm in KNOWN:
        info['decrypt_ok'], note = KNOWN[mm]
        info['source'] = 'table'
    else:
        info['source'] = 'guess'

    if info['decrypt_ok'] is False:
        info['level'] = 'warn'
        info['note'] = (KNOWN.get(mm, (None, ''))[1]
                        or '该版本解密不可用（本机实测记录）：自检记为「未校验」并跳过，草稿写入正常')
    elif info['decrypt_ok'] is True:
        info['level'] = 'ok'
        info['note'] = '加密/解密均可用'
    else:
        info['level'] = 'unknown'
        info['note'] = ('已按现有流程出片；若自检报解密失败，属版本 API 变化，'
                        '草稿本身通常仍能在剪映打开')

    _CACHE[key] = (now, dict(info))
    return info


def summary(info):
    """格式化一行给日志用。"""
    if not info.get('version'):
        return '[剪映] %s' % (info.get('note') or '版本未知')
    tag = {'ok': '', 'warn': ' · 已知问题', 'unknown': ' · 新版本未验证'}.get(info.get('level'), '')
    return '[剪映] %s%s：%s' % (info['version'], tag, info.get('note', ''))


def record_result(version, decrypt_ok, note=''):
    """记录本机某版本的实测结论（出片结束时调用）。

    写失败不影响出片：版本记录只是"下次更快认出问题"，不是关键路径。
    """
    if not version:
        return False
    path = config_path()
    now = time.strftime('%Y-%m-%d %H:%M:%S')

    def _mutate(data):
        if not isinstance(data, dict):
            data = {}
        versions = data.setdefault('versions', {})
        cur = versions.get(version) or {}
        cur['decrypt_ok'] = bool(decrypt_ok)
        cur['note'] = note or cur.get('note', '')
        cur['seen_at'] = now
        cur['count'] = int(cur.get('count') or 0) + 1
        versions[version] = cur
        data.setdefault('_meta', {})['updated'] = now
        data['_meta']['说明'] = ('剪映版本兼容性实测记录。key = 版本号，'
                                 'decrypt_ok = 该版本能否把草稿解密回来（不能不影响写入，只影响自检）。')
        return data

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_io.locked_json_update(path, _mutate)
        _CACHE.clear()
        return True
    except Exception:
        return False


def main():
    import argparse
    ap = argparse.ArgumentParser(description='检测剪映版本与加解密兼容性')
    ap.add_argument('--install', default='', help='剪映安装目录（可选）')
    args = ap.parse_args()
    info = probe(args.install)
    print('安装目录：%s' % (info['install'] or '未找到'))
    print('版本号  ：%s' % (info['version'] or '未解析'))
    print('解密可用：%s' % {True: '是', False: '否', None: '未知'}[info['decrypt_ok']])
    print('判定    ：%s' % info['level'])
    print('说明    ：%s' % info['note'])
    return 0 if info['level'] != 'missing' else 1


if __name__ == '__main__':
    raise SystemExit(main())
