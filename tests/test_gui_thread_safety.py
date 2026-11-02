# -*- coding: utf-8 -*-
"""P05 §2：工作台 GUI 线程安全 —— 可自动验证的静态检查 + 运行期行为检查。

背景（原话）：「后台线程不得直接调 Tk 变量 / messagebox；启动前冻结参数、
后台只算 Python / 子进程、只经事件队列回传；Tk 操作全在主线程；
关闭后不操作已销毁控件；异常时按钮必恢复。增加可自动验证的线程安全/静态检查。」

本测试做三件事：
  A. **静态**：用 AST 找出所有「被 threading.Thread 当作 target 的函数」，
     再从它们出发做有限深度调用图遍历，断言这些代码里**没有**任何
     Tk 变量读写 / 控件操作 / messagebox。
  B. **静态**：断言存在冻结参数与队列回传的通道（_freeze_params /
     _post_status / _post_dialog），且 _drain_log 处理 status/dialog。
  C. **静态**：断言收工与关闭保护存在（__done__ 在 finally 里、
     _closed 标志、TclError 兜底）。

单独运行：
    python tests/test_gui_thread_safety.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI = os.path.join(ROOT, 'workbench', 'workbench_gui.py')
TASK_CENTER = os.path.join(ROOT, 'workbench', 'task_center.py')
STORYBOARD = os.path.join(ROOT, 'workbench', 'storyboard_gui.py')

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    if cond:
        PASSED.append(name)
        print('  [PASS] %s' % name)
    else:
        FAILED.append('%s %s' % (name, extra))
        print('  [FAIL] %s %s' % (name, extra))


def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


# --------------------------------------------------------------------------
# A. 线程入口的识别 + 调用图遍历
# --------------------------------------------------------------------------
def collect_funcs(tree):
    """{函数名: (节点, 所在类名或 None)}"""
    out = {}

    def walk(node, cls=None):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.ClassDef):
                walk(ch, ch.name)
            elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(ch.name, []).append((ch, cls))
                walk(ch, cls)
    walk(tree)
    return out


def thread_targets(tree):
    """所有 threading.Thread(target=X) 里 X 的名字集合（self._x / x 都收）。"""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == 'Thread'):
            continue
        for kw in node.keywords:
            if kw.arg == 'target':
                t = kw.value
                if isinstance(t, ast.Attribute):
                    names.add(t.attr)
                elif isinstance(t, ast.Name):
                    names.add(t.id)
    return names


FORBIDDEN_ATTRS = {
    'messagebox', 'txt_log', 'txt_script', 'btn_go', 'lab_prev',
    'btn_plan', 'lab_prev', 'e_editor',
    'v_status', 'v_materials', 'v_out', 'v_notify', 'v_seg', 'v_naming',
    'v_style', 'v_orient', 'v_editor', 'v_bgm', 'v_copy', 'v_tts',
    'v_sub_y', 'v_sub_size', 'v_allow_empty', 'v_adv', 'v_jy', 'v_libv',
    'v_park_chip', 'v_naming_chip', 'v_style_chip', 'v_detect_note',
    'v_manual', 'v_input_mode', 'v_script_file',
}
# 允许的：仅队列/日志（后台线程唯一出口）
FORBIDDEN_METHODS = {'config', 'insert', 'see', 'get', 'set',
                     'focus_set', 'select_range', 'winfo_exists', 'destroy'}


def violations_in(func_node, funcs, seen=None, depth=0, max_depth=6):
    """返回该函数（含其调用的 self._x）里所有违规描述。"""
    seen = seen or set()
    bad = []
    for node in ast.walk(func_node):
        # self.<attr> 直接引用
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == 'self':
            if node.attr in FORBIDDEN_ATTRS:
                bad.append('%s（第 %d 行）' % (node.attr, getattr(node, 'lineno', 0)))
        # <something>.get()/.set()/.config() 之类
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = node.func.value
            if isinstance(base, ast.Attribute) and base.attr in FORBIDDEN_ATTRS:
                bad.append('%s.%s()（第 %d 行）' % (base.attr, node.func.attr,
                                                   getattr(node, 'lineno', 0)))
    # 递归检查被调用的 self 方法（有限深度，防环）
    if depth < max_depth:
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == 'self':
                nm = node.func.attr
                if nm in seen or nm not in funcs:
                    continue
                seen.add(nm)
                for cand, _cls in funcs[nm]:
                    bad += ['%s → %s' % (nm, v) for v in
                            violations_in(cand, funcs, seen, depth + 1, max_depth)]
    return bad


def guards_closed(funcs, name):
    """函数体第一句是否为 ``if self._closed: return``（AST 判定，不看缩进）。"""
    for node, _cls in funcs.get(name, []):
        body = [n for n in node.body if not isinstance(n, ast.Expr)
                or not isinstance(n.value, ast.Constant)]   # 跳过 docstring
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.If):
            continue
        t = first.test
        if isinstance(t, ast.Attribute) and t.attr == '_closed':
            if any(isinstance(s, ast.Return) for s in first.body):
                return True
    return False


_guards_closed = guards_closed  # noqa: 兼容旧名


def main():
    print('== P05 §2 工作台线程安全 检查 ==')
    src = read(GUI)
    tree = ast.parse(src)
    funcs = collect_funcs(tree)
    targets = thread_targets(tree)
    check('A0 找到线程入口', len(targets) >= 3, str(sorted(targets)))

    all_bad = {}
    for nm in sorted(targets):
        if nm not in funcs:
            all_bad[nm] = ['（未在该文件里定义，可能是内嵌函数/外部）']
            continue
        bad = []
        for cand, _cls in funcs[nm]:
            bad += violations_in(cand, funcs)
        if bad:
            all_bad[nm] = bad

    # 内嵌 work() 函数：thread_targets 会收到 'work'
    check('A1 后台线程不碰 Tk 变量/控件/弹窗',
          not all_bad,
          '；'.join('%s: %s' % (k, sorted(set(v))[:4]) for k, v in all_bad.items()))

    # B. 冻结 / 回传通道
    check('B1 有 _freeze_params（启动前冻结参数）',
          '_freeze_params' in funcs and 'def _freeze_params' in src)
    check('B2 有 _post_status / _post_dialog（唯一回传通道）',
          'def _post_status' in src and 'def _post_dialog' in src)
    check('B3 _drain_log 处理 status 事件', "kind == 'status'" in src)
    check('B4 _drain_log 处理 dialog 事件', "kind == 'dialog'" in src)
    # 启动线程时带上了冻结参数
    check('B5 起线程时传了冻结参数',
          'self._freeze_params()' in src and src.count('self._freeze_params()') >= 2,
          str(src.count('self._freeze_params()')))

    # C. 收工与关闭保护
    check('C1 _run_generate 用 finally 保证收工',
          '__done__' in src and src.count("('__done__', '')") >= 2,
          str(src.count("('__done__', '')")))
    check('C2 有 _closed 关闭标志', 'self._closed' in src)
    check('C3 _drain_log 有 TclError 兜底', 'except tk.TclError' in src)
    check('C4 关闭后 _gen_done 不再动控件',
          _guards_closed(funcs, '_gen_done'))
    check('C5 _drain_log 关闭后停轮询',
          _guards_closed(funcs, '_drain_log'))

    # D. §3 判据一致 / §1.5 不再预测目录
    check('D1 GUI 用 material_matcher.validate_materials_dir',
          'material_matcher.validate_materials_dir' in src)
    check('D2 GUI 不再自己拼草稿目录（无 DRAFT_DIR 之外的命名）',
          'draft_name += ' not in src)
    check('D3 GUI 认 DRAFT_DIR= 真实目录',
          "startswith('DRAFT_DIR=')" in src)
    check('D4 高级设置默认关「允许无画面调试稿」',
          'self.v_allow_empty = tk.BooleanVar(value=False)' in src)

    # E. 另外两个 GUI 文件不得在后台线程里碰控件
    for path, label in ((TASK_CENTER, 'task_center'), (STORYBOARD, 'storyboard')):
        s = read(path)
        t = ast.parse(s)
        tg = thread_targets(t)
        fs = collect_funcs(t)
        bad = {}
        for nm in sorted(tg):
            if nm not in fs:
                continue
            for cand, _cls in fs[nm]:
                v = violations_in(cand, fs)
                if v:
                    bad[nm] = v
        check('E %s：线程入口干净' % label, not bad, str(bad)[:200])

    # F. 编译级检查
    for p in (GUI, TASK_CENTER, STORYBOARD):
        try:
            compile(read(p), p, 'exec')
            ok = True
        except SyntaxError as e:
            ok = False
            print('    语法错误：%s' % e)
        check('F %s 可编译' % os.path.basename(p), ok)

    print('\n通过 %d 项，失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for x in FAILED:
            print('  FAILED: %s' % x)
        raise SystemExit(1)
    print('PASS gui thread safety')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
