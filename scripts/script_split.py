# -*- coding: utf-8 -*-
"""脚本切分：把粘贴框里的文本切成「一条或多条脚本」—— 全工程单一实现（P05 §4）。

为什么单独成模块
----------------
P04 的切分规则是「优先一行 ``---``，没有就用**连续空行**」，藏在
``workbench/workbench_gui.split_multi_scripts()`` 里。两个后果：

1. **误切分**：一条脚本内部为了排版好看留一个空行（很常见），
   就被当成两条脚本，批量流程凭空多出一条「半截脚本」，
   而且它还会被单独识别园区/风格 → 识别结果乱跳。
2. **无法测试**：切分规则长在 GUI 文件里，测试一导入就要拉 tkinter 和显示器。

P05 的新规则（§4 明确要求）
---------------------------
* **只有**「单独一行、至少 3 个短横线、允许前后空白」的 ``---`` 才是分隔符。
* **普通空行不再是分隔符**，原样保留在脚本里，交给下游的段落逻辑处理
  （段落 = 字幕分句的天然边界，这正是「保留为单篇段落」的意义）。
* 没有任何 ``---`` 时，整段文本就是**一条**脚本。
* 若用户显然在用空行当分隔（≥2 个空行分段、且没有 ``---``），
  不算错，但 ``blank_separator_suspected()`` 返回 True，由 GUI 给出提示，
  提醒改用 ``---`` —— 避免"我明明分了段，它却当成一条"的困惑。
"""
import re

# 分隔行：整行只有 >=3 个短横线（允许前后空白）
SEPARATOR_RE = re.compile(r'^[ \t]*-{3,}[ \t]*$', re.M)

SPLIT_HINT = ('多条脚本请用「单独一行 ---」分隔；'
              '空行不再作为分隔，会保留为同一条脚本内的段落。')


def split_scripts(text):
    """把文本切成脚本列表。

    * 有 ``---`` 分隔行 → 按分隔行切，逐段 strip，丢弃空段。
    * 没有分隔行 → 整段文本（strip 后）作为**一条**脚本；空文本返回 ``[]``。
    """
    t = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    if not t.strip():
        return []
    if SEPARATOR_RE.search(t):
        parts = SEPARATOR_RE.split(t)
        return [p.strip() for p in parts if p.strip()]
    return [t.strip()]


def count_scripts(text):
    """切分后的脚本条数（0 / 1 / N）。"""
    return len(split_scripts(text))


def has_separator(text):
    """文本里有没有显式的 ``---`` 分隔行。"""
    return bool(SEPARATOR_RE.search(
        (text or '').replace('\r\n', '\n').replace('\r', '\n')))


def blank_separator_suspected(text, min_blocks=2):
    """用户是不是**在用空行当分隔**（≥min_blocks 个空行分段且没有 ``---``）。

    仅用于 GUI 提示，不改变切分结果。
    """
    t = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    if not t.strip() or has_separator(t):
        return False
    blocks = [b for b in re.split(r'\n[ \t]*\n', t) if b.strip()]
    return len(blocks) >= int(min_blocks)


def separator_report(text):
    """给 GUI / 日志用的一行摘要。"""
    n = count_scripts(text)
    if n == 0:
        return '未检测到脚本内容'
    if n == 1:
        if blank_separator_suspected(text):
            return '识别为 1 条脚本（检测到空行分段；%s）' % SPLIT_HINT
        return '识别为 1 条脚本'
    return '识别为 %d 条脚本（按 --- 分隔）' % n
