# -*- coding: utf-8 -*-
"""P05 §4：多脚本切分回归测试。

要求（原话）：「普通空行保留为单篇段落；仅单独一行 ---（允许前后空格、
至少 3 个短横线）分隔；GUI 提示；新增测试。」

单独运行：
    python tests/test_script_split.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import script_split as ss

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        print('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        print('  [FAIL] %s %s' % (name, extra))


S1 = '嘉兴产业园，位于长三角核心。\n\n交通便利，配套齐全。'
S2 = '苏州厂房招商，首付低。\n即买即用。'


def main():
    print('== P05 §4 脚本切分 回归 ==')

    # 1) 普通空行不再切分
    out = ss.split_scripts(S1)
    check('1a 含空行仍是 1 条', len(out) == 1, str(len(out)))
    check('1b 空行原样保留', '\n\n' in out[0], repr(out[0]))
    check('1c 段数不变（2 段）', len([p for p in out[0].split('\n\n') if p.strip()]) == 2)

    # 2) --- 分隔
    out2 = ss.split_scripts(S1 + '\n---\n' + S2)
    check('2a 切成 2 条', len(out2) == 2, str(len(out2)))
    check('2b 第一条无尾部分隔符', not out2[0].endswith('-'), repr(out2[0][-8:]))
    check('2c 第二条内容正确', out2[1] == S2, repr(out2[1]))

    # 3) --- 允许前后空格
    out3 = ss.split_scripts(S1 + '\n   ---   \n' + S2)
    check('3a 带空格的 --- 也分隔', len(out3) == 2, str(len(out3)))

    # 4) 不足 3 个横线不分隔
    out4 = ss.split_scripts(S1 + '\n--\n' + S2)
    check('4a 两个横线不分隔', len(out4) == 1, str(len(out4)))

    # 5) 行内 --- 不分隔
    out5 = ss.split_scripts(S1 + '\n价格 --- 面议\n' + S2)
    check('5a 行内 --- 不分隔', len(out5) == 1, str(len(out5)))

    # 6) 4 个及更多横线也分隔
    out6 = ss.split_scripts(S1 + '\n-----\n' + S2)
    check('6a 5 个横线分隔', len(out6) == 2, str(len(out6)))

    # 7) 空 / 纯分隔符
    check('7a 空文本返回空表', ss.split_scripts('') == [])
    check('7b 纯空白返回空表', ss.split_scripts('   \n  \n ') == [])
    check('7c 只有 --- 返回空表', ss.split_scripts('---\n---') == [])
    check('7d None 安全', ss.split_scripts(None) == [])

    # 8) 连续 --- 与首尾 ---
    out8 = ss.split_scripts('---\n' + S1 + '\n---\n---\n' + S2 + '\n---')
    check('8a 首尾/连续分隔符不产生空条', len(out8) == 2, str(len(out8)))

    # 9) CRLF 兼容（Windows 记事本粘贴）
    out9 = ss.split_scripts(S1.replace('\n', '\r\n') + '\r\n---\r\n' + S2)
    check('9a CRLF 下仍切成 2 条', len(out9) == 2, str(len(out9)))
    check('9b CRLF 已归一', '\r' not in out9[0], repr(out9[0]))

    # 10) 空行分隔「疑似」提示
    check('10a 空行分段且无 --- → 疑似', ss.blank_separator_suspected(S1) is True)
    check('10b 有 --- 时不算疑似',
          ss.blank_separator_suspected(S1 + '\n---\n' + S2) is False)
    check('10c 单段文本不算疑似',
          ss.blank_separator_suspected('嘉兴产业园招商，配套齐全。') is False)

    # 11) count / has_separator / 摘要
    check('11a count_scripts', ss.count_scripts(S1 + '\n---\n' + S2) == 2)
    check('11b has_separator', ss.has_separator(S1 + '\n---\n' + S2) is True)
    check('11c 摘要含条数', '2 条' in ss.separator_report(S1 + '\n---\n' + S2),
          ss.separator_report(S1 + '\n---\n' + S2))
    check('11d 提示语提到 ---', '---' in ss.SPLIT_HINT, ss.SPLIT_HINT)

    # 12) GUI 内不存在第二份切分实现
    gui = os.path.join(ROOT, 'workbench', 'workbench_gui.py')
    src = open(gui, encoding='utf-8').read()
    check('12a GUI 改为调用 script_split',
          'script_split.split_scripts' in src)
    check('12b GUI 内不再有按空行切分的正则',
          "re.split(r'\\n\\s*\\n'" not in src and r"re.split(r'\n\s*\n'" not in src)

    print('\n通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for x in FAILED:
            print('  FAILED: %s' % x)
        raise SystemExit(1)
    print('PASS script split')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
