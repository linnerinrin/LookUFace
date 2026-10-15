"""启动脚本（start.bat）的静态检查。

**为什么需要它**：``start.bat`` 曾因行尾是 LF 而不是 CRLF 而导致「双击后窗口
一闪而过」。cmd.exe 的批处理解析器要求 CRLF —— LF-only 文件里的 ``REM`` 注释
和命令会被逐字节拆开，报出 ``'为统一入口（与' is not recognized``、
``'nv' is not recognized`` 之类错误后立刻退出，根本走不到任何 ``pause``。

这类问题：
  * Python/JS 的语法检查完全发现不了；
  * 只有在 Windows 上真正双击运行才会暴露；
  * 极易被编辑器或代码生成工具重新引入。

因此用测试把约束固化下来。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 批处理扩展名，必须使用 CRLF
BATCH_SUFFIXES = (".bat", ".cmd")


def _line_ending_stats(path: Path) -> tuple[int, int]:
    """返回 ``(CRLF 数量, 单独的 LF 数量)``。"""
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return 0, 0  # 二进制，跳过
    crlf = raw.count(b"\r\n")
    lone_lf = raw.replace(b"\r\n", b"").count(b"\n")
    return crlf, lone_lf


def test_batch_files_exist() -> None:
    batches = [p for p in PROJECT_ROOT.glob("*") if p.suffix.lower() in BATCH_SUFFIXES]
    assert batches, "项目根目录应有启动脚本"


@pytest.mark.parametrize("suffix", BATCH_SUFFIXES)
def test_no_batch_file_uses_lf_only(suffix: str) -> None:
    """**核心回归**：批处理文件绝不能是 LF-only，否则 cmd.exe 会瞬间退出。

    这是「start.bat 双击闪退」的直接原因。
    """
    offenders: list[str] = []
    checked = 0

    for path in PROJECT_ROOT.rglob(f"*{suffix}"):
        if any(part in {"venv", ".venv", ".git"} for part in path.parts):
            continue
        checked += 1
        crlf, lone_lf = _line_ending_stats(path)
        if lone_lf > 0:
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}: CRLF={crlf} 单独LF={lone_lf}")

    assert not offenders, (
        "以下批处理文件使用了 LF 行尾，双击会立即闪退（cmd.exe 要求 CRLF）：\n"
        + "\n".join(offenders)
    )
    if checked == 0:
        pytest.skip(f"没有 {suffix} 文件")


def test_batch_files_have_no_bom() -> None:
    """批处理文件不应带 UTF-8 BOM。

    带 BOM 时 cmd.exe 会把 ``@echo off`` 当成未知命令并先打印一次错误，
    且可能影响首行解析。
    """
    for path in PROJECT_ROOT.rglob("*.bat"):
        if any(part in {"venv", ".venv", ".git"} for part in path.parts):
            continue
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), (
            f"{path.name} 含有 UTF-8 BOM，cmd.exe 可能解析首行失败"
        )


def test_gitattributes_pins_crlf_for_batch() -> None:
    """``.gitattributes`` 必须把批处理文件固定为 CRLF。

    这样即使编辑器把文件改成 LF，Git 检出时也会写回 CRLF。
    """
    gitattributes = PROJECT_ROOT / ".gitattributes"
    assert gitattributes.exists(), "缺少 .gitattributes，行尾约束无法通过 Git 传递"

    lines = [
        line.strip()
        for line in gitattributes.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    batch_rules = [line for line in lines if line.startswith(("*.bat", "*.cmd"))]
    assert batch_rules, ".gitattributes 中缺少 *.bat / *.cmd 的 eol 规则"
    for rule in batch_rules:
        assert "eol=crlf" in rule, f"规则未指定 CRLF：{rule}"


def test_start_bat_invokes_venv_python_directly() -> None:
    """启动脚本必须显式使用 venv 的 python，而不是裸 ``python``。

    实测该机器上 PATH 里的 ``python`` 指向一个**无关的第三方虚拟环境**
    （astrbot launcher 的 venv）。若脚本用裸 ``python`` 去跑 pip，
    依赖会被装进错误的解释器，项目仍无法启动。
    """
    script = (PROJECT_ROOT / "start.bat").read_text(encoding="utf-8", errors="replace")

    assert "venv\\Scripts\\python.exe" in script, "未显式使用 venv 的解释器"

    # 裸 python 调用只允许出现在「挑选基础解释器」的 venv 创建分支里
    bare_calls = re.findall(r'^\s*(?!"?%)(python(?:\.exe)?)\s+-', script, re.MULTILINE)
    assert not bare_calls, (
        f"start.bat 存在裸 python 调用，可能命中错误的解释器：{bare_calls}"
    )


def test_start_bat_does_not_kill_processes() -> None:
    """不得自动 taskkill 占用端口的进程（旧版会误杀无关程序）。"""
    script = (PROJECT_ROOT / "start.bat").read_text(encoding="utf-8", errors="replace")
    lowered = script.lower()
    assert "taskkill" not in lowered, "start.bat 不应自动杀进程"
    assert "already in use" in lowered, "应改为提示端口占用"


def test_start_bat_reads_port_from_env() -> None:
    """端口必须从 .env 读取，不能在脚本里硬编码。

    **真实问题**：脚本原先把 8000 写死在三处（netstat 检查、错误提示、打印的访问地址）。
    用户把 ``PORT`` 改成 8835 后，脚本仍然去检查 8000 并报
    「Port 8000 is already in use」，同时打印的访问地址也是错的 ——
    参数与提示不一致，非常误导。

    现在改为 ``for /f`` 从 ``.env`` 解析 ``PORT``，并明确打印解析结果。
    """
    script = (PROJECT_ROOT / "start.bat").read_text(encoding="utf-8", errors="replace")

    # 必须从 .env 解析
    assert ".env" in script, "未从 .env 读取配置"
    assert re.search(r'if /i "%%a"=="PORT"', script), "未解析 .env 里的 PORT"
    assert '%PORT%' in script, "端口未用变量引用"

    # 端口检查与打印地址都必须使用变量，而不是字面量端口
    assert re.search(r'findstr\s+":%PORT%\s*"', script), "netstat 检查未使用 %PORT%"
    assert re.search(r'already in use by PID', script)
    assert re.search(r'http://localhost:%PORT%', script), "打印的访问地址未使用 %PORT%"

    # 不允许再出现 "检查/报告 8000" 这类硬编码端口的行为（注释里说明历史原因不算）
    code_lines = [
        line for line in script.splitlines()
        if not line.strip().upper().startswith("REM")
    ]
    body = "\n".join(code_lines)
    assert not re.search(r'findstr\s+":\d+\s*"', body), (
        "端口检查里仍存在硬编码端口字面量"
    )
    assert not re.search(r'http://localhost:\d+', body), (
        "访问地址仍硬编码了端口字面量"
    )


def test_start_bat_surfaces_exit_code() -> None:
    """服务异常退出时必须保留退出码并提示，而不是直接关窗。"""
    script = (PROJECT_ROOT / "start.bat").read_text(encoding="utf-8", errors="replace")
    assert "EXITCODE" in script, "未捕获服务退出码"
    assert "pause" in script.lower(), "缺少 pause，出错时窗口会闪退"


def test_python_sources_use_lf() -> None:
    """Python/JS 源码应使用 LF（避免混合行尾造成 diff 噪声）。"""
    mixed: list[str] = []
    for path in list(PROJECT_ROOT.rglob("*.py")) + list(PROJECT_ROOT.rglob("*.js")):
        if any(part in {"venv", ".venv", ".git", "__pycache__"} for part in path.parts):
            continue
        crlf, lone_lf = _line_ending_stats(path)
        if crlf > 0 and lone_lf > 0:
            mixed.append(f"{path.relative_to(PROJECT_ROOT)}: CRLF={crlf} LF={lone_lf}")

    assert not mixed, "以下文件混用 CRLF 与 LF：\n" + "\n".join(mixed)
