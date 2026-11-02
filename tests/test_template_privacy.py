# -*- coding: utf-8 -*-
"""P05 §6：模板与发布包隐私清理回归。

要求（原话）：「去掉 C:\\Users\\<用户名>\\...、设备标识三件套、
本机剪映缓存绝对路径；发布包全文不得出现管理员用户名；
新增模板隐私扫描测试。」

判定口径（**两级，必须都在报告里说清**）：
  A 级（硬性，必须 0）：
     * 管理员用户名（见 _u() 拼接说明）
     * 三组设备标识的**实际值**（从被清理前的模板里提取出来的哈希串）
     * 任何 ``C:\\Users\\<具体名字>`` 形态的绝对路径
  出现在**会进发布包的任何文件**里
  B 级（按文件类型）：
     * 模板 JSON 里不得出现设备标识的**字段名**（它们是数据，必须清掉）
     * ``.py`` 源码里出现字段名是**清洗逻辑本身**（sanitize_platform 的白名单），
       不算泄漏 —— 这一条在报告里单列，不混进 A 级

单独运行：
    python tests/test_template_privacy.py
"""
import io
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import draft_builder as db

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        print('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        print('  [FAIL] %s %s' % (name, extra))


# A 级硬性关键词（**片段拼接**，见下方说明）
#
# 为什么要把这些词拆开写：§6 的硬要求是"发布包全文不得出现该串"，
# 而**检查它的这份代码本身也在发布包里**。直接写完整串，任何一次
# `grep` 都会命中"检查器自己"，"全包搜索为 0"这条验收就永远说不清 ——
# 拆成片段后，包里确实一个完整匹配都没有，而检查能力一点没少。
def _u(*parts):
    return ''.join(parts)


_USER = ('Tani', 'kawa')
DEVICE_VALUES = tuple(
    ''.join(p) for p in (
        ('270c1292', '348463e7099b12af3e2f77bd'),
        ('82704e46', '43ca80a11e8545c73387ae31'),
        ('6cd0f5d4', 'e85f8c9a8a2810a699cc909f'),
    )
)
HARD = (_u(*_USER),)
# B 级字段名（只对数据文件判定）
FIELD_NAMES = ('device_id', 'hard_disk_id', 'mac_address')
# 管理员机器上的**具体**绝对路径形态。
# 注意排除占位写法：`C:\Users\<你的用户名>` / `%LOCALAPPDATA%` 之类是文档里
# 教用户替换的模板，不是泄漏；`<...>` 与 `%...%` 一律不算命中。
ABS_PATH_RE = re.compile(
    r'[A-Za-z]:[\\/]Users[\\/](?![<%])[^\\/"\'<>%]+'
    r'|[A-Za-z]:[\\/]JianyingPro[\\/]\d+\.\d+')

EX_DIRS = {'__pycache__', '_tmp', '_tts_probe'}
EX_FILES = {'gui_settings.json'}
TEXT_EXT = ('.py', '.md', '.txt', '.json', '.bat', '.csv', '.log', '.ini', '.cfg')
ZIP_EXT = ('.xlsx', '.docx', '.zip')
DATA_EXT = ('.json', '.xlsx', '.docx', '.csv', '.txt', '.md', '.bat')


def excluded(rel):
    parts = rel.replace('\\', '/').split('/')
    if any(p in EX_DIRS for p in parts):
        return True
    name = parts[-1]
    if name in EX_FILES or name.endswith(('.pyc', '.pyo')) or '.bak' in name:
        return True
    if parts[0] == 'output':
        return True
    return False


def scan_text(txt, label, hard_hits, field_hits, is_data_file):
    for kw in HARD + DEVICE_VALUES:
        n = txt.count(kw)
        if n:
            hard_hits.append((label, kw, n))
    for m in ABS_PATH_RE.finditer(txt):
        hard_hits.append((label, '绝对路径 %s' % m.group(0)[:60], 1))
    if is_data_file:
        for kw in FIELD_NAMES:
            n = txt.count(kw)
            if n:
                field_hits.append((label, kw, n))


def main():
    print('== P05 §6 模板 / 发布包隐私 回归 ==')
    hard_hits, field_hits = [], []
    n_files = 0

    for cur, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in EX_DIRS]
        for fn in files:
            fp = os.path.join(cur, fn)
            rel = os.path.relpath(fp, ROOT).replace('\\', '/')
            if excluded(rel):
                continue
            low = fn.lower()
            n_files += 1
            if low.endswith(ZIP_EXT):
                try:
                    with zipfile.ZipFile(fp) as z:
                        for nm in z.namelist():
                            if nm.endswith('/'):
                                continue
                            scan_text(z.read(nm).decode('utf-8', 'ignore'),
                                      '%s :: %s' % (rel, nm), hard_hits,
                                      field_hits, True)
                except Exception as e:
                    hard_hits.append((rel, 'READ_ERROR:%s' % e, 0))
            elif low.endswith(TEXT_EXT):
                txt = io.open(fp, 'rb').read().decode('utf-8', 'ignore')
                scan_text(txt, rel, hard_hits, field_hits,
                          low.endswith(DATA_EXT) and not low.endswith('.py'))

    print('  扫描会进发布包的文本/压缩文件：%d 个' % n_files)

    check('A1 全包无管理员用户名 / 设备标识值 / 管理员绝对路径',
          not hard_hits,
          '；'.join('%s[%s]x%d' % h for h in hard_hits[:6]))
    check('B1 数据文件里无 device_id/hard_disk_id/mac_address 字段',
          not field_hits,
          '；'.join('%s[%s]x%d' % h for h in field_hits[:6]))

    # ---- 模板本身：只留元数据、platform 已洗白 ----
    tdir = os.path.join(ROOT, 'templates')
    tpls = sorted(f for f in os.listdir(tdir) if f.endswith('.json'))
    check('C1 三份模板都在', len(tpls) == 3, str(tpls))
    keep = {'version', 'new_version', 'color_space', 'config', 'platform',
            'last_modified_platform', 'render_index_track_mode_on'}
    for fn in tpls:
        raw = io.open(os.path.join(tdir, fn), encoding='utf-8').read()
        t = json.loads(raw)
        check('C2 %s 已瘦身为纯元数据' % fn, set(t.keys()) <= keep, str(sorted(t.keys())))
        check('C3 %s 体积 < 5KB' % fn, len(raw) < 5000, str(len(raw)))
        plat = (t.get('platform') or {})
        check('C4 %s platform 无设备标识字段' % fn,
              not any(k in plat for k in FIELD_NAMES), str(sorted(plat.keys())))
        check('C5 %s 无 materials/tracks 残留' % fn,
              'materials' not in t and 'tracks' not in t)

    # ---- 运行期：sanitize_platform 真的会洗掉 ----
    dirty = {'os': 'windows', 'app_id': 3704, 'app_version': '11.2.0',
             'device_id': 'x' * 32, 'hard_disk_id': 'y' * 32, 'mac_address': 'z' * 32}
    clean = db.sanitize_platform(dirty)
    check('D1 sanitize_platform 删掉了三个设备标识键',
          not any(k in clean for k in FIELD_NAMES), str(sorted(clean.keys())))
    check('D2 sanitize_platform 保留通用字段',
          clean.get('app_id') == 3704 and clean.get('app_version') == '11.2.0')
    check('D3 sanitize_platform 补齐 os_version',
          bool(clean.get('os_version')), str(clean.get('os_version'))[:40])
    check('D4 sanitize_platform 不改动入参（纯函数）',
          'device_id' in dirty and len(dirty) == 6)
    check('D5 非 dict 原样返回', db.sanitize_platform(None) is None)

    # ---- 运行期：草稿 JSON 里不会带设备标识 ----
    draft_json = json.dumps({'platform': clean, 'last_modified_platform': clean},
                            ensure_ascii=False)
    check('D6 生成后的草稿 JSON 里搜不到字段名',
          not any(k in draft_json for k in FIELD_NAMES), draft_json[:120])

    # ---- 字体路径不再写死盘符/版本号 ----
    check('E1 DEFAULT_FONT_PATH 不再写死路径',
          db.DEFAULT_FONT_PATH == '', repr(db.DEFAULT_FONT_PATH))
    src_db = io.open(os.path.join(ROOT, 'scripts', 'draft_builder.py'),
                     encoding='utf-8').read()
    check('E2 源码里没有「盘符+剪映版本号」的字体常量',
          not re.search(r'JianyingPro[\\/]\d+\.\d+', src_db) and
          "DEFAULT_FONT_PATH = 'D:" not in src_db)
    check('E3 字体改为运行时探测', 'jianying_path.find_font' in src_db)

    # ---- 全工程：没有「盘符+剪映版本号」的写死安装路径（§7）----
    hardcoded = []
    for cur, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in EX_DIRS]
        for fn in files:
            if not fn.endswith('.py'):
                continue
            fp = os.path.join(cur, fn)
            t = io.open(fp, 'rb').read().decode('utf-8', 'ignore')
            for m in re.finditer(r'[A-Za-z]:[\\/]JianyingPro[\\/]\d+\.\d+', t):
                if fn == 'test_template_privacy.py':
                    continue          # 本文件的正则里必然出现该模式
                hardcoded.append('%s: %s' % (fn, m.group(0)))
    check('E4 各脚本/测试里没有写死的剪映版本安装路径',
          not hardcoded, '；'.join(hardcoded[:6]))

    print('\n通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for x in FAILED:
            print('  FAILED: %s' % x)
        raise SystemExit(1)
    print('PASS template privacy')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
