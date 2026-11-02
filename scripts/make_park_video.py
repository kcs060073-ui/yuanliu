# -*- coding: utf-8 -*-
r"""主流程：脚本文本 → 三基础风格切分 → 素材匹配 → 剪映草稿（加密写入草稿库）。

用法（纯对话式交互由 Agent 调用本脚本，无需用户敲命令）：
  python make_park_video.py <脚本.txt> \
      --style 3                      # 1三连否 / 2招商 / 3没想到（或中文名）
      --materials "D:/素材目录"        # 素材目录（缺省则无视频素材，后续手动补）
      --tone 激昂解说                  # 可选，指定音色（缺省按风格随机）
      --name "260901-苏州-没想到"      # 草稿名（缺省自动生成）
      --out "D:/JianyingPro Drafts"   # 草稿根目录
      --install "<剪映安装目录>"        # 缺省自动探测（jianying_path）
      --copy-materials                # 把素材复制进草稿 materials/（自包含）
      --bgm random                    # BGM：random 本机音乐池随机选（默认）/ fixed 风格默认曲
      --bgm-seed 42                   # 可选，随机选曲种子（复现同一首）
      --overwrite                     # 仅配合 --name 显式指定目标时允许覆盖该目录
      --allow-empty-materials         # 允许在空素材库上生成无画面调试稿

流程：读脚本 → 切字幕 → 配素材 → 选音色 → 生成明文草稿 →
      jy_crypto 回加密 → 写入 <out>/<草稿名>/draft_content.json + draft_meta_info.json

草稿目录命名与防覆盖（P05 §1）
------------------------------
草稿名与草稿目录由 ``draft_naming`` **独占预留**，工作台与命令行共用同一实现。
同一个基础名重复生成时，第二条会自动落到新目录（或带递增后缀），
**绝不会**覆盖第一条已经存在的草稿。文件锁只负责"不写坏"，
目录预留才负责"不撞车"—— 两件事，别混。
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import draft_builder
import draft_naming
import bgm_pool
import jianying_path
import production_log
import material_governance
from atomic_io import FileLock, atomic_write_bytes, atomic_write_json
from runtime_context import RunContext
from job_receipt import JobReceipt, selected_materials_from_info
from jy_crypto import EncryptUtils  # 复制到本目录或加入 sys.path

HERE = os.path.dirname(os.path.abspath(__file__))
_ACTIVE_RECEIPT = None


def find_install_dir(hint=''):
    r"""定位剪映安装目录。

    实际探测在 jianying_path（单一实现，支持 <版本>\ 与 Apps\<版本>\ 两种布局）。
    """
    d = jianying_path.find_install_dir(hint)
    if not d:
        raise FileNotFoundError('未找到剪映安装目录，请用 --install 指定')
    return d


def resolve_tone(spec, style_name=''):
    """按 中文名/tone_speaker 从音色池解析；返回 tone_cfg 或 None。"""
    if not spec:
        return None
    spec = str(spec).strip()
    pool = draft_builder._load_json(os.path.join(draft_builder.CONFIG_DIR, 'tone_pool.json'))
    cands = pool.get('confirmed', []) + pool.get('reference', [])
    for t in cands:
        if spec in (t.get('tone_speaker', ''), t.get('tone_cn_name', ''), t.get('tone_type', '')):
            cfg = {
                'tone_type': t.get('tone_type') or t.get('tone_cn_name', ''),
                'tone_speaker': t.get('tone_speaker', ''),
                'resource_id': t.get('resource_id', ''),
                'tone_effect_name': t.get('tone_type') or t.get('tone_cn_name', ''),
                'tone_platform': t.get('tone_platform', 'sami'),
                'tone_emotion_name_key': 'voice_style_default',
            }
            if not cfg['resource_id']:
                print('[警告] 音色「%s」缺少 resource_id，剪映可能无法合成配音，建议改用 confirmed 音色' % spec)
            return cfg
    print('[警告] 未在音色池找到「%s」，回退到风格默认随机' % spec)
    return None


def unique_copy_name(src, used, mat_dir):
    """给「要复制进草稿的素材」算一个**草稿内唯一**的目标文件名。

    为什么不能直接用 basename（P05 §5）
    ----------------------------------
    素材库里不同文件夹经常存在同名文件（``video(1).mp4`` / ``1楼开门-横版.mp4``
    在多个园区目录里都有）。老实现 `dst = mat_dir/basename(src)` 直接 copy2，
    后复制的会把先复制的**覆盖掉**，而草稿 JSON 里两条 segment 指向同一个路径 ——
    成片里两段画面变成同一条，且不报任何错。

    策略：**名字永远带路径哈希** —— ``<原文件名去扩展名>__<路径哈希8位><扩展名>``。

    为什么不做"不冲突就用 basename"的优化（曾经这么写过，被测试打回）
    --------------------------------------------------------------
    只要存在"名字不带来源信息"的候选，就会出现这一幕：草稿里恰好已有一个
    第三方同名文件时，名字看起来"没人占用"，于是代码认为可以直接用 ——
    但复制环节看到目标已存在就**跳过复制**，结果草稿指向了一个完全不相干的
    视频文件。这是静默错片，比覆盖更难查（覆盖至少看得见文件没了）。
    名字带上源路径哈希后，"这个名字代表哪个源文件"是可以从名字本身验证的，
    再用文件大小做一次交叉确认，就不会把别人的文件当自己的素材。

    哈希取自**规范化后的绝对路径**，因此同一源文件永远得到同一个名字（幂等）、
    不同源文件即使 basename 相同也必然不同名。
    """
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    key = os.path.normcase(os.path.abspath(src)).replace('\\', '/')
    h = _path_hash8(key)
    try:
        src_size = os.path.getsize(src)
    except OSError:
        src_size = -1

    def taken(path):
        """目标名已被占用，且**不是**我们要的这份源（拿大小做交叉确认）。"""
        if not os.path.isfile(path):
            return False
        try:
            return os.path.getsize(path) != src_size
        except OSError:
            return True

    cand = '%s__%s%s' % (stem, h, ext)
    n = 1
    while used.get(cand, key) != key or taken(os.path.join(mat_dir, cand)):
        n += 1
        cand = '%s__%s_%d%s' % (stem, h, n, ext)
    used[cand] = key
    return os.path.join(mat_dir, cand)


def _path_hash8(key):
    import hashlib
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]


def _copy_atomic(src, dst):
    """临时文件 + os.replace 复制：中断也不会留下半个文件冒充成品。"""
    import tempfile
    d = os.path.dirname(dst)
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(dst) + '.', suffix='.tmp', dir=d)
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return dst


def _is_within(path, folder):
    """path 是否就在 folder 里面（含相等）。"""
    p = os.path.normcase(os.path.abspath(path))
    d = os.path.normcase(os.path.abspath(folder))
    return p == d or p.startswith(d.rstrip('\\/') + os.sep)


def copy_materials_into(draft, meta, mat_dir):
    """把草稿引用的视频素材复制进 ``mat_dir``，并同步改写所有路径引用。

    返回 ``(映射 dict, 复制数)``；映射为 ``原绝对路径(规范化) -> 目标绝对路径``。

    要点：
    * 同一源文件被多个 segment/material 引用时**只复制一次**；
    * 不同源文件即使 basename 相同也保存为两个不同文件；
    * 复制用临时文件 + 原子替换；
    * 草稿 JSON（``materials.videos[].path``）与 ``draft_meta_info.json``
      （``draft_materials[].value[].file_Path``）都要改到，否则剪映里素材会丢。
    """
    os.makedirs(mat_dir, exist_ok=True)
    used, mapping = {}, {}
    vids = draft['materials'].get('videos', [])
    for v in vids:
        src = v.get('path', '')
        if not src:
            continue
        key = os.path.normcase(os.path.abspath(src)).replace('\\', '/')
        if key in mapping:                       # 同源复用：只复制一次
            v['path'] = mapping[key].replace('\\', '/')
            continue
        if _is_within(src, mat_dir):
            # 源本来就在草稿 materials/ 里（重复调用同一份草稿，或素材就是
            # 上一次拷进来的）：直接用，不再复制 —— 否则会"复印件再复印"，
            # 每调用一次就多一层，路径也越改越深。
            mapping[key] = os.path.abspath(src)
            v['path'] = os.path.abspath(src).replace('\\', '/')
            continue
        if not os.path.isfile(src):
            print('[复制] 跳过不存在的素材：%s' % src)
            continue
        dst = unique_copy_name(src, used, mat_dir)
        if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
            if not os.path.isfile(dst):
                _copy_atomic(src, dst)
        mapping[key] = os.path.abspath(dst)
        v['path'] = os.path.abspath(dst).replace('\\', '/')

    # 同步 meta 里的素材路径（按 material id 精确匹配，避免同名误改）
    by_id = {m['id']: m['path'] for m in vids if m.get('id') and m.get('path')}
    n_fix = 0
    for g in meta.get('draft_materials', []):
        if g.get('type') != 0:
            continue
        for item in g.get('value', []):
            newp = by_id.get(item.get('id'))
            if newp:
                item['file_Path'] = newp
                n_fix += 1
    print('[复制] 视频素材 %d 条 → %d 个文件（去重后）写入 %s'
          % (len(vids), len(set(mapping.values())), mat_dir))
    return mapping, len(set(mapping.values()))


def write_draft(draft, meta, draft_dir, install_dir, copy_materials=False):
    """把明文草稿加密写入草稿目录，返回 (content_path, meta_path)。

    草稿目录必须**已经由 draft_naming 独占预留**（调用方负责）。这里只做
    「写入 + 原子替换」，不再用 ``os.makedirs(..., exist_ok=True)`` 兜底创建 ——
    那正是 P04 之前"两个任务悄悄落进同一个目录"的来源。
    """
    draft_dir = os.path.abspath(draft_dir)
    if not os.path.isdir(draft_dir):
        raise RuntimeError('草稿目录未被预留：%s' % draft_dir)
    text_reading = os.path.join(draft_dir, 'textReading')
    os.makedirs(text_reading, exist_ok=True)

    if copy_materials:
        copy_materials_into(draft, meta, os.path.join(draft_dir, 'materials'))

    # 序列化为紧凑 JSON（与剪映明文格式一致：无空格、无换行）
    content_plain = json.dumps(draft, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    meta_plain = json.dumps(meta, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    install_dir = os.path.abspath(install_dir)
    # 注意：连续复用同一个 EncryptUtils 实例加密两个不同数据时，
    # 第二次加密偶尔会返回明文（DLL 内部状态问题）。这里分别实例化规避。
    content_enc = EncryptUtils(install_dir).encrypt(content_plain)
    meta_enc = EncryptUtils(install_dir).encrypt(meta_plain)

    content_path = os.path.join(draft_dir, 'draft_content.json')
    meta_path = os.path.join(draft_dir, 'draft_meta_info.json')

    # 辅助文件：时间线布局（让草稿库识别主时间线）
    timeline = {
        'activeTimeline': draft['id'],
        'dockItems': [{
            'dockIndex': 0, 'ratio': 1,
            'timelineIds': [draft['id']],
            'timelineNames': ['时间线01'],
        }],
        'layoutOrientation': 1,
    }
    with FileLock(os.path.join(draft_dir, '.yuanliu-draft'), timeout=30):
        atomic_write_bytes(content_path, content_enc)
        atomic_write_bytes(meta_path, meta_enc)
        atomic_write_json(os.path.join(draft_dir, 'timeline_layout.json'), timeline, indent=None)
    return content_path, meta_path


def self_check_semantics():
    """启动自检（P0-②）：语义配置必须加载成功且校验通过。

    宁可停下来，也不带着一份错配置出片 —— 配置错的表现是画面配错，
    成片导出后才发现，比现在停 2 秒难查得多。
    """
    try:
        import semantics_config
        semantics_config.load()
        return True
    except Exception as e:
        print('[语义配置错误] %s' % e)
        print('   修好 config/semantics.json 后重跑；改完记得跑回归：')
        print('   python tests/test_semantic_blocks.py')
        return False


def run_verify(draft_dir, install_dir):
    """跑一遍草稿自检，返回台账要记的结果字符串。

    自检失败**不影响出片**：草稿已经写好了，这里只是把结论记进台账，
    供批量结束后统一排查。任何异常都降级成 SKIP，绝不打断流程。
    """
    script = os.path.join(HERE, '..', 'tests', 'verify_draft.py')
    try:
        p = subprocess.run(
            [sys.executable, script, draft_dir, '--install', install_dir],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=300)
        if p.returncode == 0:
            return 'PASS'
        probs = [l.strip(' -') for l in (p.stdout or '').splitlines()
                 if l.strip().startswith('-') or l.strip().startswith('   -')]
        probs = [x for x in probs if x]
        if not probs:
            # 自检以 SystemExit 报错时（如解密失败），原因在 stderr，台账不能只剩 rc=1
            tail = [l.strip() for l in (p.stderr or '').splitlines() if l.strip()]
            tail += [l.strip() for l in (p.stdout or '').splitlines() if l.strip()]
            probs = tail[-2:]
        return 'FAIL:' + ('; '.join(probs[:3]) if probs else 'rc=%d' % p.returncode)
    except Exception as e:
        return 'SKIP:%s' % type(e).__name__


def main():
    global _ACTIVE_RECEIPT
    ap = argparse.ArgumentParser(description='生成园区信息流短视频剪映草稿')
    ap.add_argument('script', nargs='?', default='', help='脚本文本文件（.txt）')
    ap.add_argument('--style', default='3', help='风格：1三连否/2招商/3没想到（或中文名）')
    ap.add_argument('--materials', default='', help='素材根目录（含 _shared/_parks 的集中根目录，'
                                                  '或普通素材文件夹）。配合 --park 指定园区')
    ap.add_argument('--park', default='', help='园区名：从 --materials 根目录的 _parks/<名> '
                                             '取专属素材，缺失自动回退 _shared 通用素材')
    ap.add_argument('--orientation', default='', dest='video_orientation',
                    help='成片方向：竖屏/竖版/portrait 或 横屏/横版/landscape。'
                         '带方向标注的素材只能用于同方向成片，无标注素材通用；'
                         '不传则不过滤（用户每次剪辑前告知方向）')
    ap.add_argument('--tone', default='', help='指定 TTS 音色（音色名或 tone_speaker）')
    ap.add_argument('--with-tts', action='store_true',
                    help='生成占位配音轨（供在剪映点「朗读」+ 阶段二归位）。'
                         '默认不生成：纯字幕视频（字幕+画面+BGM，无人声）')
    ap.add_argument('--name', default='', help='草稿名（缺省按命名方式自动生成）')
    ap.add_argument('--overwrite', action='store_true',
                    help='允许覆盖「已存在且已含有效草稿」的目标目录。'
                         '**只在同时显式传入 --name 时生效**：自动命名的草稿永不覆盖。'
                         '默认关闭；工作台不提供此能力')
    ap.add_argument('--allow-empty-materials', action='store_true', dest='allow_empty_materials',
                    help='允许素材库为空时生成「无画面调试稿」。'
                         '默认拦停：空素材库出片会得到一条成功但没有画面的草稿')
    ap.add_argument('--naming', type=int, choices=(0, 7), default=0,
                    help='命名方式：0式(默认) YYMMDD-城市/园区-风格名（嘉兴/嘉定/苏州/南京/贵阳）'
                         '；7式 YYMMDD-园区-横/竖-剪辑者（新疆/北京等实拍园区）。'
                         '7式需配合 --editor 与 --orientation')
    ap.add_argument('--editor', default='', help='剪辑者姓名（7式命名用；跑新疆/北京前先询问）')
    ap.add_argument('--park-name', default='', help='园区显示名（7式命名用；缺省取 --park 或素材目录末段）')
    ap.add_argument('--out', default=r'D:\JianyingPro Drafts', help='草稿根目录')
    ap.add_argument('--install', default='', help='剪映安装目录')
    ap.add_argument('--copy-materials', action='store_true', help='复制素材进草稿目录')
    ap.add_argument('--sub-y', type=float, default=0.0, dest='sub_y_off',
                    help='字幕位置全局偏移（画布归一化，正=上移，如 0.06；0=模板原位置）')
    ap.add_argument('--sub-size', type=float, default=None, dest='sub_font_size',
                    help='覆盖字幕字号（默认用风格模板值，如 8.0）')
    ap.add_argument('--bgm', default='random',
                    help="BGM 模式：random 从本机音乐池随机选（默认）/ fixed 风格默认曲")
    ap.add_argument('--bgm-seed', type=int, default=None, help='随机选曲种子（复现用）')
    ap.add_argument('--plan', default='', help='已确认的分镜 plan.json；传入后严格按计划选素材')
    ap.add_argument('--job-id', default='', help='任务ID；缺省自动生成')
    ap.add_argument('--jobs-root', default='', help='独立任务回执根目录')
    # P05：默认值必须是**空**，让 RunContext 去读环境变量 YUANLIU_RUN_MODE。
    # 老写法 ``default='production'`` 会把这个环境变量**架空** ——
    # `YUANLIU_RUN_MODE=test python make_park_video.py ...` 表面上进了测试模式，
    # 实际上 args.run_mode='production' 一路传下去，仍然往生产 output/ 写
    # 任务回执、产出台账和素材使用次数。SKILL.md 一直写着"测试写生产会被硬阻断"，
    # 但这条阻断在 CLI 入口就被默认值绕过了（2026-09-20 做连续出片 E2E 时实测发现）。
    ap.add_argument('--run-mode', choices=('production', 'test'), default='',
                    help='production=正式出片；test=状态写入隔离临时目录'
                         '（留空则跟随环境变量 YUANLIU_RUN_MODE，缺省 production）')
    args = ap.parse_args()

    if not self_check_semantics():
        sys.exit(2)

    # P1-① 素材库基准漂移提示：不拦出片，只在素材与团队基准不一致时提醒
    # （同一脚本上周正常、这周画面不对，最常见原因就是素材被换过）
    if args.materials:
        try:
            import material_governance as _mg_chk
            ck = _mg_chk.check_baseline(args.materials)
            if ck['status'] == 'DRIFT':
                print('[素材库] 注意：%s 与团队基准不一致（本地 %s / 基准 %s）'
                      % (ck['name'], ck['local'], ck['baseline']))
                print('         素材可能被换过或拷贝不完整；确认后重新拷贝，'
                      '或让负责人重设基准（--set-baseline）')
        except Exception:
            pass

    if not args.script:
        print('错误：请提供脚本文本文件')
        sys.exit(1)
    script_path = os.path.abspath(args.script)
    if not os.path.isfile(script_path):
        print('错误：找不到脚本文件', script_path)
        sys.exit(1)
    with open(script_path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            script = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        script = raw.decode('gbk', errors='replace')
        print('[提示] 脚本编码无法识别，已按 GBK 读取（可能乱码）')

    ctx = RunContext(args.run_mode or None, args.job_id or None,
                     args.jobs_root or None)
    _ACTIVE_RECEIPT = JobReceipt(
        ctx, script=script, editor=args.editor.strip(), park=args.park or args.park_name,
        versions={'skill': production_log.skill_version(),
                  'materials': production_log.materials_version(args.materials)})
    _ACTIVE_RECEIPT.update('ANALYZING')
    print('[任务] %s（%s）' % (ctx.job_id, ctx.mode))

    # ---- 园区锁定（2026-09-11 用户拍板）----
    # 规则：先识别脚本里的园区地名，只在该园区文件夹里选素材。
    # 识别不到、而 --materials 又是挂着多个园区的素材总根 → 拦停。
    # 背景：贵阳片就是把总根当素材目录，5 个园区 900 条混池，贵阳只占 4.6%，
    #       7 段画面全被别园区抽走。
    import park_rules as _prv
    park_dir, park_folder, park_how = (None, None, '未指定素材目录')
    if args.materials:
        park_dir, park_folder, park_how = _prv.resolve_park_dir(
            args.materials, args.park or None, script,
            park_name=(args.park_name or None))
        if park_dir:
            print('[园区锁定] %s（依据：%s）→ 素材目录 %s' % (park_folder, park_how, park_dir))
            # 界面/命令行填的园区名与锁定结果不一致 → 醒目告警
            # （2026-09-14 新仓事故：界面填了"新仓"，脚本里"嘉兴"出现更早锁成嘉兴，
            #   用户以为按新仓出片，实际素材全是嘉兴的）
            _want = (args.park_name or '').strip()
            _disp = (_prv.load().get('parks', {}).get(park_folder, {}) or {}).get('display') or park_folder
            if _want and _want not in (park_folder, _disp):
                print('[!] 注意：你填的园区是「%s」，但本次锁定的是「%s」（依据：%s）。'
                      % (_want, _disp, park_how))
                print('    若确认要按「%s」出片，请核对园区识别词，或把 --materials 直接指到该园区目录。'
                      % _want)
        elif _prv.looks_like_root(args.materials):
            raise SystemExit(
                '素材目录 %s 下挂着多个园区，但%s，无法确定用哪个园区。\n'
                '  请三选一：① 脚本里写明园区地名；② 加 --park <园区名>；'
                '③ 把 --materials 直接指到园区目录。\n'
                '  （已拦停：避免多园区素材混用）' % (args.materials, park_how))
        else:
            print('[园区锁定] 未识别到园区，按普通素材文件夹处理：%s' % args.materials)

    # ---- 空素材库拦截（P05 §3）----
    # 判据与工作台「生成 / 批量生成 / 分镜预览」共用 material_matcher.validate_materials_dir，
    # 避免"界面说没问题、命令行却是另一套判据"。
    import material_matcher as _mm_chk
    warnings = []
    _mat_target = park_dir or args.materials
    _mv = _mm_chk.validate_materials_dir(_mat_target)
    if _mv['ok']:
        print('[素材库] %s' % _mv['msg'])
    elif args.allow_empty_materials:
        warnings.append(_mv['msg'].splitlines()[0])
        print('[素材库] 警告：本机没有可用画面 —— %s' % _mv['msg'].replace('\n', '\n         '))
        print('[素材库] 已按 --allow-empty-materials 继续：本次生成的是**无画面调试稿**，'
              '不要把这条当成成品。')
    else:
        raise SystemExit(
            '素材库不可用，已拦停（避免生成一条"成功但没有画面"的草稿）：\n  %s'
            % _mv['msg'].replace('\n', '\n  '))

    # 先解析风格（用于生成草稿名、解析音色）
    styles = draft_builder._load_json(os.path.join(draft_builder.CONFIG_DIR, 'default_styles.json'))['styles']
    skey, style = draft_builder.resolve_style(args.style, styles)
    style_name = style['name']

    tone_cfg = resolve_tone(args.tone, style_name)

    install_dir = find_install_dir(args.install)
    out_root = (os.path.join(ctx.state_root, 'drafts') if ctx.mode == 'test'
                else os.path.abspath(args.out))

    # ---- BGM 动态解析（P05 §7 + 补丁）：只认本机真实存在的音频文件 ----
    # 不写死管理员机器的剪映缓存路径；找不到就明确告警，不伪装成"成功"。
    #
    # 补丁要点（2026-09-20 二轮）：
    #   * 必须把 **styles[].bgm**（`name` / `resource_id`）传下去，否则 fixed 模式
    #     根本不知道要找哪首，只能傻拿候选列表第一个 —— "固定选择"名存实亡。
    #   * `warnings=warnings`：降级（没匹配到默认曲）也要进警告，不能只打日志。
    style_bgm = style.get('bgm', {}) if isinstance(style, dict) else {}
    bgm_rec, bgm_note = bgm_pool.resolve_bgm(
        mode=args.bgm, out_root=out_root, install_dir=install_dir, seed=args.bgm_seed,
        style_bgm=style_bgm, warnings=warnings)
    if bgm_rec:
        print('[BGM] %s：%s' % (bgm_note, bgm_pool.summarize(bgm_rec)))
        if args.bgm.lower() == 'fixed' and '降级' in bgm_note:
            print('[BGM] ⚠ 固定选择未命中，已安全降级（不会影响出片）')
    else:
        warnings.append(bgm_note)
        print('[BGM] ⚠ %s' % bgm_note)
        print('[BGM] ⚠ 本次将生成**无 BGM 草稿**（不会写入任何不存在的路径）')

    # 成片方向（用户 2026-09-04 规则）：归一化中文别名 → portrait/landscape
    orient_raw = (args.video_orientation or '').strip().lower()
    orientation = {'竖屏': 'portrait', '竖版': 'portrait', 'portrait': 'portrait',
                   '横屏': 'landscape', '横版': 'landscape', 'landscape': 'landscape',
                   }.get(orient_raw, None)
    orient_cn = {'portrait': '竖', 'landscape': '横'}.get(orientation, '')
    if orient_raw and not orientation:
        raise SystemExit('--orientation 只接受 竖屏/竖版/横屏/横版（或 portrait/landscape），收到: %s'
                         % args.video_orientation)

    # ---- 草稿命名 + 草稿目录独占预留（P05 §1）----
    # 命名与预留统一走 draft_naming（工作台与命令行共用同一实现，不再各写一套）。
    explicit = args.name.strip()
    if not explicit:
        # 命名方式：0式 = YYMMDD-城市/园区[-横/竖]-风格名；7式 = YYMMDD-园区-横/竖-剪辑者
        # （2026-09-04 用户定义：嘉兴/嘉定/苏州/南京/贵阳用 0式；
        #  新疆/北京等实拍园区用 7式，剪辑者每次先询问）
        if args.naming == 7:
            editor = args.editor.strip()
            if not editor:
                raise SystemExit('7式命名需要 --editor 剪辑者姓名 —— 跑新疆/北京前请先询问剪辑者是谁')
            if not orient_cn:
                raise SystemExit('7式命名需要 --orientation 横/竖')
            park = args.park_name.strip() or args.park.strip() or park_folder
            if not park and args.materials:
                park = os.path.basename(os.path.abspath(args.materials).rstrip('/\\'))
            base = draft_naming.base_name(naming='7', seg=park or '园区',
                                          orient_cn=orient_cn, editor=editor)
        else:
            # 0式：城市段取 --park-name（GUI/命令行都可传），缺省保持旧行为 '-园区-'。
            # 方向位 2026-09-09 用户拍板加入；不传 --orientation 时仍输出旧格式。
            seg = args.park_name.strip()
            if not seg and park_folder:
                seg = _prv.load().get('parks', {}).get(park_folder, {}).get('display') or park_folder
            base = draft_naming.base_name(naming='0', seg=seg or '园区',
                                          orient_cn=orient_cn, style_name=style_name)
    else:
        base = explicit
        # 显式命名 + 显式覆盖 = 唯一允许覆盖已存在草稿的路径（P05 补丁 §2.1）。
        # 这里必须把三件事说清楚，方便事后审计"谁覆盖了哪条草稿"：
        #   目标目录叫什么、它现在存不存在、里面原来有没有东西。
        if args.overwrite:
            target = os.path.join(os.path.abspath(out_root),
                                  draft_naming.sanitize(explicit))
            if os.path.isdir(target):
                old = draft_naming.draft_markers_present(target)
                print('[覆盖] 显式 --name + --overwrite：将**原地覆盖**已有目录：%s'
                      % os.path.basename(target))
                if old:
                    print('[覆盖] 该目录里原有一条草稿（%s）—— 它的 draft_content.json / '
                          'draft_meta_info.json 会被本次新草稿替换。' % '、'.join(old[:3]))
                else:
                    print('[覆盖] 该目录存在，但不是一条完整草稿（空目录或只有残留文件）')
            else:
                print('[覆盖] 显式 --name + --overwrite：目标目录尚不存在，正常新建：%s'
                      % os.path.basename(target))
        elif explicit:
            print('[命名] 显式 --name「%s」但**未传 --overwrite**：'
                  '若同名目录已存在，将自动加唯一尾码另建，不动已有草稿。' % explicit)

    reserve = draft_naming.plan_target(
        out_root, base, job_id=ctx.job_id, explicit_name=explicit,
        overwrite=bool(args.overwrite and explicit),
        extra={'park': park_folder or '', 'style': style_name, 'orientation': orient_cn})
    draft_dir = reserve['path']
    name = reserve['name']
    if reserve['reused'] and explicit and args.overwrite:
        print('[覆盖] 已沿用指定目录（%s）' % name)
    elif reserve['collision']:
        print('[防覆盖] 目标名「%s」已有占用 → 本次落到新目录：%s'
              % (explicit or base, name))
        print('[防覆盖] 已存在且未被动过：%s' % '；'.join(reserve['conflict_with'][:5]))
    else:
        print('[防覆盖] 草稿目录已独占预留：%s' % draft_dir)
    if not explicit and args.overwrite:
        # 自动命名路径下 --overwrite 一律不生效（P05 §2.3）。写死"忽略"，
        # 让日志里能查到"这条命令确实传了 --overwrite 但被忽略了"。
        print('[覆盖] 注意：未显式传入 --name，--overwrite 不生效（自动命名的草稿永不覆盖）')

    # P05 §1.6：把**真实最终目录**用机器可读的一行打出来。
    # 工作台（GUI）原来自己"预测"草稿目录给自检和弹窗用；一旦目录因冲突
    # 自动换成 -2 / 换尾码，预测值就是错的 —— 自检会去校验一个不存在的目录，
    # 日志和弹窗也跟着错。改成认这一行，谁生成谁说了算。
    print('DRAFT_DIR=%s' % draft_dir.replace('\\', '/'))

    # 园区锁定信息：**位置很重要**，见下方 _write_park_lock 的调用点。
    # （原实现把 park_lock.json 写在生成草稿之前；一旦后面 build_draft 或加密失败，
    #   被 --overwrite 复用的那个目标目录里就会多出一个"半成品痕迹"。
    #   挪到草稿原子落盘之后写，失败的运行就不会在别人的草稿里留下任何东西。）
    def _write_park_lock():
        if not (park_folder or args.materials):
            return
        try:
            # 目录已由 draft_naming 独占预留，这里**不再** makedirs(exist_ok=True)：
            # 那正是"两个任务悄悄落进同一个目录"的来源。
            atomic_write_json(os.path.join(draft_dir, 'park_lock.json'), {
                    'park': park_folder or '',
                    'materials_arg': args.materials or '',
                    'materials_dir': park_dir or args.materials or '',
                    'decided_by': park_how,
                    'rules_version': _prv.load().get('version'),
                    'cross_buckets': sorted(_prv.cross_buckets()),
                    'job_id': ctx.job_id,
                    'created': time.strftime('%Y-%m-%d %H:%M:%S'),
                })
        except Exception as e:
            print('[提示] 园区锁定信息写入失败（不影响出片）：%s' % e)

    # 配音：no_tts（默认）不生成配音轨 = 纯字幕视频（字幕+画面+BGM，无人声）；
    # --with-tts 才生成占位轨供在剪映点「朗读」+ 阶段二归位。edge-tts 路径已移除。
    no_tts = not args.with_tts
    tts_fn = None

    material_plan = None
    if args.plan:
        with open(args.plan, encoding='utf-8') as f:
            material_plan = json.load(f)
        atomic_write_json(ctx.path('plan.json'), material_plan)
        _ACTIVE_RECEIPT.update('PLAN_CONFIRMED', plan_path=ctx.path('plan.json'))
    else:
        _ACTIVE_RECEIPT.update('MATCHING')

    draft, info = draft_builder.build_draft(
        script, style_key=skey, materials_dir=(park_dir or args.materials),
        tone_cfg=tone_cfg, draft_name=name,
        bgm_choice=bgm_rec,
        tts_fn=tts_fn,
        no_tts=no_tts,
        park=(park_folder or args.park or None),
        video_orientation=orientation,
        sub_y_off=args.sub_y_off,
        sub_font_size=args.sub_font_size,
        material_plan=material_plan,
        record_material_usage=False,
        allow_empty_materials=bool(args.allow_empty_materials),
    )
    # 草稿内部记录的草稿名 = 真实最终目录名（§1.7：冲突重试后不能再用旧名）
    info['draft_name'] = name
    info['draft_dir'] = draft_dir
    info['warnings'] = list(warnings)
    info['bgm']['available'] = bool(bgm_rec and bgm_rec.get('path'))
    if bgm_rec and bgm_rec.get('path') and not os.path.isfile(bgm_rec['path']):
        info['bgm']['available'] = False
    if not info['bgm']['available']:
        msg = '本机未找到可用BGM，已生成无BGM草稿'
        if msg not in info['warnings']:
            info['warnings'].append(msg)

    meta = draft_builder.build_meta_info(draft, name, draft_dir.replace('\\', '/'), out_root)
    meta['draft_is_ai_packaging_used'] = False

    _ACTIVE_RECEIPT.update('WRITING_DRAFT', draft_dir=draft_dir)
    content_path, meta_path = write_draft(draft, meta, draft_dir, install_dir,
                                          copy_materials=args.copy_materials)

    # 草稿已原子落盘 → 现在才写园区锁定信息（给 verify_draft 的来源审计用，
    # 也便于事后追查"这条片子用的哪个园区素材、依据是什么"）。
    # 放在这里的原因：**失败的运行不该在目标目录里留下任何痕迹**。
    # --overwrite 复用的正是别人的草稿目录，若在写草稿之前就落 park_lock.json，
    # 一旦中途失败（加密异常 / 计划文件读不到），原草稿会被"改了一半"。
    _write_park_lock()

    # 预览阶段不记频次；只有草稿原子落盘成功后才累计实际使用素材。
    material_governance.record_usage_atomic(
        [m.get('path') for m in info.get('video_materials', []) if m.get('path')])

    # ---- 产出台账（P0-③）----
    # 15 人批量出片时，「谁的哪一条出问题」是最贵的一个问题。
    # 自检失败只记账、不回滚 —— 草稿已经写好，让人自己判断要不要重出。
    verify_result = run_verify(draft_dir, install_dir)
    log_path = production_log.append_record(
        timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
        editor=args.editor.strip() or production_log.default_editor(),
        host=socket.gethostname(),
        # 2026-09-14：原先优先记 args.park/park_name，导致"界面填新仓、实际用嘉兴素材"
        # 时台账仍显示新仓，事后追查被误导。改为**永远记实际锁定的园区**。
        park=((_prv.load().get('parks', {}).get(park_folder, {}).get('display')
               if park_folder else '')
              or (os.path.basename(os.path.abspath(args.materials).rstrip('/\\'))
                  if args.materials else '')
              or args.park or args.park_name or ''),
        style=style_name,
        draft_name=name,
        duration_s=info['total_dur_s'],
        # 台账记"实际用的素材目录"，不是命令行传入的目录 —— 2026-09-11 贵阳事故
        # 排查时就是被这个字段误导（记的是总根，实际混了 5 个园区）。
        materials_version=production_log.materials_version(park_dir or args.materials),
        materials_dir=(park_dir or args.materials),
        orientation=orient_cn or '(未指定)',
        subtitles=len(info['subtitles']),
        videos=len(info['video_materials']),
        bgm=info['bgm']['name'],
        verify=verify_result,
        skill_version=production_log.skill_version(),
    )
    if log_path:
        print('[台账] 自检 %s → 已记入 %s' % (verify_result, log_path))
    receipt_path = _ACTIVE_RECEIPT.finish(
        verify_result.startswith('PASS'), draft_dir=draft_dir, verify=verify_result,
        materials=selected_materials_from_info(info), production_log=log_path or '',
        # warnings 为**新增**字段（P05 §7 要求"任务回执必须标记本机未找到可用BGM"）。
        # 其余字段与 P04 完全一致，读旧回执的程序不受影响。
        warnings=list(info.get('warnings') or []),
        error='' if verify_result.startswith('PASS') else verify_result)
    print('[回执] %s' % receipt_path)
    _ACTIVE_RECEIPT = None

    print()
    print('=' * 56)
    print('草稿生成完成')
    if info.get('warnings'):
        for w in info['warnings']:
            print('  ⚠ 警告   : %s' % w)
    print('  草稿名   :', name)
    print('  风格     :', style_name)
    print('  字幕数   :', len(info['subtitles']), '条 | 总时长', info['total_dur_s'], 's')
    if no_tts:
        print('  配音     : 无（纯字幕模式：字幕+画面+BGM）')
    else:
        print('  TTS 音色 :', info['tone'].get('tone_type'), '/', info['tone'].get('tone_speaker'))
    print('  视频素材 :', len(info['video_materials']), '个 | 视频段', info['video_segments'])
    print('  画布     :', draft['canvas_config']['width'], 'x', draft['canvas_config']['height'])
    if orientation:
        print('  成片方向 :', '竖版（仅用竖屏/竖版及无标注素材）' if orientation == 'portrait'
              else '横版（仅用横屏/横版及无标注素材）')
    if info['bgm'].get('available'):
        print('  BGM      :', info['bgm']['name'], '|', info['bgm']['segments'], '段 |',
              os.path.basename(info['bgm']['path'] or ''))
    else:
        print('  BGM      : ⚠ 本机未找到可用BGM —— 本条为无BGM草稿')
    print('  草稿位置 :', draft_dir)
    print('  加密文件 : draft_content.json / draft_meta_info.json')
    print()
    if no_tts:
        print('纯字幕模式：无配音，打开剪映直接预览/导出，无需任何人工步骤。')
        print('字幕停留 280ms/字（用户拍板：宁长勿短），纯字幕默读节奏留足时间。')
    else:
        print('接下来 3 步（约 2 分钟人工）：')
        print('  1. 打开剪映 → Ctrl+A 全选字幕 → 「文本·朗读」→ 选音色 → 开始朗读')
        print('     （顺便逐句检查断句，不满意可先改文字再朗读）')
        print('  2. 保存并【关闭】草稿')
        print('  3. 一键闭合空隙（字幕/配音归位 + 重切画面 + BGM 铺满）：')
        print('     python arrange_park_video.py "%s"' % draft_dir)
    print('=' * 56)


def _fmt_elapsed(sec):
    """耗时显示成"X 分 Y 秒"（不足 1 分钟只给秒，带 1 位小数）。"""
    if sec >= 60:
        return '%d 分 %02d 秒' % (int(sec // 60), int(round(sec % 60)))
    return '%.1f 秒' % sec


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    _t0 = time.time()
    try:
        main()
    except SystemExit as e:
        if _ACTIVE_RECEIPT is not None:
            try:
                _ACTIVE_RECEIPT.finish(False, error='SystemExit: %s' % e)
            except Exception:
                pass
        # 拦停/参数错误也报耗时与明确状态（exit code 原样保留）
        print()
        print('=' * 56)
        print('❌ 未生成草稿   耗时 %s' % _fmt_elapsed(time.time() - _t0))
        print('   上面的原因处理完后重跑即可')
        print('=' * 56)
        raise
    except Exception as e:
        if _ACTIVE_RECEIPT is not None:
            try:
                _ACTIVE_RECEIPT.finish(False, error='%s: %s' % (type(e).__name__, e))
            except Exception:
                pass
        print()
        print('=' * 56)
        print('❌ 生成异常   耗时 %s' % _fmt_elapsed(time.time() - _t0))
        print('   %s: %s' % (type(e).__name__, e))
        print('=' * 56)
        raise
    print()
    print('=' * 56)
    print('✅ 草稿生成完成   总耗时 %s' % _fmt_elapsed(time.time() - _t0))
    print('   下一步：打开剪映预览，确认无误后导出')
    print('=' * 56)
