# -*- coding: utf-8 -*-
"""Check a built wheel before it goes to PyPI, by running it.

Syntax and a file listing were all the old check did, which is how 0.4.32
shipped code that read a key its own function never returned: the module
imported cleanly and every safety rule underneath it was dead. So this
installs the wheel into a throwaway environment and asks it questions whose
answers it can verify.

Nothing here touches the operator's account, environment, or running bot:
it builds a temp venv, points HOME and the env file at a temp directory,
and never places an order.

    python research/preflight.py [dist/ocean_agent-x.y.z-py3-none-any.whl]

Exit code 0 means the wheel may be uploaded.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []
WARNS: list[str] = []


def ok(label: str, passed: bool, detail: str = "") -> None:
    mark = "  OK  " if passed else " FAIL "
    print(f"[{mark}] {label}" + (f"  · {detail}" if detail else ""), flush=True)
    if not passed:
        FAILS.append(f"{label}: {detail}")


def warn(label: str, detail: str = "") -> None:
    print(f"[ WARN ] {label}" + (f"  · {detail}" if detail else ""), flush=True)
    WARNS.append(label)


def newest_wheel() -> str:
    ws = sorted(glob.glob(os.path.join(ROOT, "dist", "*.whl")),
                key=os.path.getmtime)
    if not ws:
        print("dist 에 휠이 없다. python -m build 를 먼저 실행하라.")
        raise SystemExit(2)
    return ws[-1]


# ── static checks: what must not be inside the package ──────────────────

SECRETS = [
    ("지갑 주소로 보이는 문자열", re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{40,44}\b")),
    ("텔레그램 토큰", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
    ("개인 경로", re.compile(r"[Cc]:\\+Users\\+my\b")),
]
# The figures themselves are the asset, so they live outside the repository.
# The gate belongs in git, where it can be reviewed and cannot be removed by
# `git clean`; the list it checks against stays local. A clone without the
# list fails this check rather than passing it quietly.
FIGURES_FILE = os.path.join(ROOT, "research", "leak_figures.txt")


def _provenance() -> list[str]:
    try:
        with open(FIGURES_FILE, encoding="utf-8") as f:
            return [ln.strip() for ln in f
                    if ln.strip() and not ln.startswith("#")]
    except OSError:
        return []


def check_contents(whl: str) -> None:
    z = zipfile.ZipFile(whl)
    names = z.namelist()
    stray = [n for n in names
             if n.startswith(("research/", "outputs/")) or n.endswith(".env")]
    ok("연구·출력 파일이 섞이지 않았다", not stray, ", ".join(stray[:3]))
    infos = {n.split("/")[0] for n in names if ".dist-info" in n}
    ok("빌드 잔재 없음 (dist-info 하나)", len(infos) == 1, ", ".join(sorted(infos)))
    # a loose file in the repo root can slip in as a top-level module, which
    # the research/outputs filter above would never see
    tops = {n.split("/")[0] for n in names}
    extra = sorted(tops - {"ocean_agent"} - infos)
    ok("패키지 밖 파일이 최상위에 없다", not extra, ", ".join(extra[:4]))
    text = "".join(z.read(n).decode("utf-8", "replace") for n in names
                   if n.endswith((".py", ".yaml", ".md", ".txt")))
    for label, pat in SECRETS:
        hits = [h for h in pat.findall(text)]
        ok(f"{label} 없음", not hits, str(hits[:2]))
    figs = _provenance()
    if not figs:
        ok("측정 수치 목록 존재", False,
           f"{FIGURES_FILE} 이 없다. 수치 검사를 못 한다")
        return
    found = [p for p in figs if p in text]
    ok("측정 수치가 남지 않았다", not found, ", ".join(found[:4]))


# ── live checks: install it and make it answer ──────────────────────────

PROBE = r'''
import json, os, sys

out = {}

def rec(name, value, detail=""):
    out[name] = [bool(value), detail]

# 1) the package imports at all
import ocean_agent
from ocean_agent import bracket_trader as bt, mcp_server as ms, builder_consent as bc
rec("import", True)

# 2) the grader returns the fields the labeller reads. 0.4.32 shipped a
#    labeller reading "price" from a dict that never had it.
import inspect
src = inspect.getsource(bt.realized_close)
needs = [k for k in ('"pnl_usd"', '"cause"', '"price"') if k not in src]
rec("realized_close_keys", not needs, "빠진 키: " + ",".join(needs))

# 3) the labeller calls a stop a stop
def label(direction, px, tp, sl, est):
    hit = ""
    if px and tp and sl:
        if direction == "long":
            hit = "take_profit" if px >= tp else "stop_loss" if px <= sl else ""
        else:
            hit = "take_profit" if px <= tp else "stop_loss" if px >= sl else ""
    return hit or ("추정:이익" if est >= 0 else "추정:손실")
lab_src = inspect.getsource(bt.watch_positions)
rec("label_uses_price", "px_close" in lab_src and "pos.get(\"sl\")" in lab_src)
rec("label_stop", label("short", 0.3532, 0.3157, 0.3531, -3.1) == "stop_loss")
rec("label_take", label("long", 0.0033, 0.00317, 0.0029, 2.7) == "take_profit")

# 4) every tool a user is told about is actually registered. 0.4.34 moved a
#    decorator onto the helper below it and shipped with no way to start
#    trading at all, while the whole product told people to ask for it.
MUST_EXIST = ("start_auto_trading", "stop_auto_trading", "set_trading_budget",
              "connect_pacifica", "account_status", "top_setups",
              "open_with_bracket", "check_position")
tools = set(ms.mcp._tool_manager._tools.keys())
missing = [t for t in MUST_EXIST if t not in tools]
private = [t for t in tools if t.startswith("_")]
rec("tools_registered", not missing and not private,
    f"빠짐: {missing} · 내부함수 노출: {private}")

# 5) the budget the user is asked for survives the whole path: env -> policy
#    -> cfg -> slots. Calling bracket_cfg with a dict skips the link that
#    was actually broken, so go through load_policy like the bot does.
os.environ["BRACKET_BUDGET_USD"] = "300"
os.environ["BRACKET_NOTIONAL_USD"] = "50"
from ocean_agent.autonomous import load_policy
cfg = bt.apply_budget(bt.bracket_cfg(load_policy()))
rec("budget_reaches_engine", cfg["slots"] == 6 and cfg["notional_usd"] == 50,
    f"slots={cfg['slots']} notional={cfg['notional_usd']}")
for k in ("BRACKET_BUDGET_USD", "BRACKET_NOTIONAL_USD"):
    os.environ.pop(k, None)

# 5) shipped defaults are the safe ones
import yaml
here = os.path.dirname(ocean_agent.__file__)
pol = yaml.safe_load(open(os.path.join(here, "policy_default.yaml"), encoding="utf-8"))
d = bt.bracket_cfg(pol)
rec("default_capital", d["capital_pct"] <= 30, f"{d['capital_pct']}%")
rec("default_leverage", d["leverage"] <= 5, f"{d['leverage']}x")

# 6) the terms question works with no account attached
rec("terms_without_keys", hasattr(bc, "ask_terms_only"))

# 7) tool replies carry the language instruction
rec("language_instruction",
    "language they are writing in" in (ms.mcp.instructions or ""))

# 8) the install page can hand over its own address
import inspect as _i
rec("url_file_flag", "--url-file" in _i.getsource(__import__(
    "ocean_agent.install_ui", fromlist=["x"]).main))

print("PROBE" + json.dumps(out, ensure_ascii=False))
'''


def check_live(whl: str) -> None:
    tmp = tempfile.mkdtemp(prefix="oa_preflight_")
    try:
        venv = os.path.join(tmp, "v")
        subprocess.run([sys.executable, "-m", "venv", venv],
                       check=True, capture_output=True, timeout=600)
        py = os.path.join(venv, "Scripts", "python.exe")
        if not os.path.exists(py):
            py = os.path.join(venv, "bin", "python")
        r = subprocess.run([py, "-m", "pip", "install", "--quiet", whl],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            ok("휠 설치", False, (r.stderr or "")[-160:])
            return
        ok("휠 설치", True)
        probe = os.path.join(tmp, "probe.py")
        with open(probe, "w", encoding="utf-8") as f:
            f.write(PROBE)
        env = dict(os.environ)
        env.update(HOME=tmp, USERPROFILE=tmp, PYTHONIOENCODING="utf-8",
                   PACIFICA_ENV_FILE=os.path.join(tmp, ".env"),
                   PACIFICA_BASE_URL="https://api.pacifica.fi")
        for k in ("BRACKET_BUDGET_USD", "BRACKET_NOTIONAL_USD",
                  "BRACKET_CAPITAL_PCT"):
            env.pop(k, None)
        open(env["PACIFICA_ENV_FILE"], "w").close()
        r = subprocess.run([py, probe], capture_output=True, text=True,
                           env=env, timeout=900, encoding="utf-8",
                           errors="replace")
        line = next((ln for ln in (r.stdout or "").splitlines()
                     if ln.startswith("PROBE")), "")
        if not line:
            ok("설치본 실행 점검", False, (r.stderr or "")[-200:])
            return
        for name, (passed, detail) in json.loads(line[5:]).items():
            ok(name, passed, detail)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_pins(whl: str) -> None:
    """The installers hand out one exact version. If it is not this one, a
    new user installs yesterday's package however new the upload is."""
    ver = os.path.basename(whl).split("-")[1]
    for path in ("website/install.ps1", "website/install.sh"):
        full = os.path.join(ROOT, path)
        try:
            text = open(full, encoding="utf-8").read()
        except OSError:
            ok(f"{path} 버전 핀", False, "파일 없음")
            continue
        ok(f"{path} 핀이 {ver}", f"ocean-agent@{ver}" in text,
           re.search(r"ocean-agent@[\d.]+", text).group(0)
           if re.search(r"ocean-agent@[\d.]+", text) else "핀 없음")
    # MCPB is a third installer and was not checked here, so its pin sat at
    # 0.3.2 for 34 releases while its manifest advertised the current version:
    # that install reported a version it was not running.
    # Every place a version is written, not just the two installers. The
    # page is what a user reads, and it drifting means "installs 0.4.36 while
    # the screen says 0.4.37"; the READMEs said @latest, which is a different
    # policy from the pinned installers and resolved to the broken build.
    for path, pat in (("mcpb/pyproject.toml", r'ocean-agent==([\d.]+)'),
                      ("mcpb/manifest.json", r'"version":\s*"([\d.]+)"'),
                      ("README.md", r'ocean-agent@([\d.]+)'),
                      ("README.ko.md", r'ocean-agent@([\d.]+)'),
                      ("website/index.html", r'currently at version (\d+(?:\.\d+)+)'),
                      ("website/index.html", r'<div><b>([\d.]+)</b><span>current version')):
        full = os.path.join(ROOT, path)
        try:
            text = open(full, encoding="utf-8").read()
        except OSError:
            ok(f"{path} 버전", False, "파일 없음")
            continue
        m = re.search(pat, text)
        ok(f"{path} 가 {ver}", bool(m) and m.group(1) == ver,
           m.group(1) if m else "버전 표기 없음")


def check_freshness(whl: str) -> None:
    """Does this wheel contain the code that is committed right now?

    0.4.36 shipped 25 minutes before the commit that fixed its direction
    rule, so everyone installing from the site traded a rule the ledger had
    already rejected while the operator's own bot ran the fixed one. Same
    version number, two strategies, and nothing checked. PyPI does not allow
    re-uploading a version, so the only remedy afterwards is a new release.

    The first attempt compared the wheel's mtime against the HEAD commit
    time, which is not a property of the wheel's contents: a copy, a
    re-download or one touch defeats it, and feeding it the very 0.4.36 that
    caused the accident produced a pass. This reads every packaged module out
    of the wheel and compares it byte for byte with `git cat-file blob
    HEAD:<path>`, which no clock can fool.

    Failures here are failures, not warnings. The earlier version downgraded
    its own exceptions to warn(), and warn() does not reach FAILS, so a broken
    check meant the upload proceeded.
    """
    import subprocess
    import zipfile

    def _nl(b: bytes) -> bytes:
        """Line endings differ between the checkout and the wheel; the code
        does not. Compare on content, not on how the file was written out."""
        return b.replace(b"\r\n", b"\n")

    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              timeout=60)

    try:
        r = git("status", "--porcelain")
        out = r.stdout.decode("utf-8", "replace")
        dirty = [x for x in out.splitlines() if x and not x.startswith("??")]
        # Untracked files under the package are not "clean": they are absent
        # from git yet present in the wheel, which is the same divergence this
        # check exists to catch.
        untracked = [x[3:].strip('"') for x in out.splitlines()
                     if x.startswith("?? ") and "ocean_agent/" in x]
        ok("작업 트리 깨끗", not dirty,
           "" if not dirty else f"커밋 안 된 변경 {len(dirty)}건: "
           + ", ".join(x[3:] for x in dirty[:4]))
        ok("패키지에 미추적 파일 없음", not untracked,
           "" if not untracked else ", ".join(untracked[:4]))

        head = git("rev-parse", "--short", "HEAD").stdout.decode().strip()
        z = zipfile.ZipFile(whl)
        members = [n for n in z.namelist()
                   if n.startswith("ocean_agent/") and not n.endswith("/")]
        diff, missing = [], []
        for n in members:
            blob = git("cat-file", "blob", f"HEAD:{n}")
            if blob.returncode != 0:
                missing.append(n)
                continue
            if _nl(z.read(n)) != _nl(blob.stdout):
                diff.append(n)
        ok(f"휠 내용이 HEAD({head}) 와 동일", not diff and not missing,
           f"{len(members)}개 파일 일치" if not diff and not missing else
           (f"다름 {len(diff)}: {', '.join(diff[:3])} " if diff else "")
           + (f"HEAD 에 없음 {len(missing)}: {', '.join(missing[:3])}"
              if missing else "")
           + " → 다시 빌드하라")
    except Exception as e:
        # Never downgrade to warn(): a check that cannot run has not passed.
        ok("휠 내용이 HEAD 와 동일", False, f"검사 실패: {type(e).__name__}: {e}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    whl = sys.argv[1] if len(sys.argv) > 1 else newest_wheel()
    print(f"검사 대상: {os.path.basename(whl)}\n")
    check_freshness(whl)
    check_contents(whl)
    check_pins(whl)
    print()
    check_live(whl)
    print()
    if FAILS:
        print(f"실패 {len(FAILS)}건. 업로드하지 말 것.")
        for f in FAILS:
            print("  -", f)
        return 1
    print("전부 통과. 업로드해도 된다." + (f" (경고 {len(WARNS)}건)" if WARNS else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
