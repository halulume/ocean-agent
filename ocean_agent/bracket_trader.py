# -*- coding: utf-8 -*-
"""The bracket trader: the measured operating plan, and nothing else.

Born 2026-08-11 from 430 measured variants. What it trades is NOT frozen
here: every number below lives in policy and has moved since, so read the
policy file, not this paragraph. As of 2026-08-28 it is

  · the top picks of the hourly seal by trade_rank, five seats in the
    operator's policy and eight in the shipped default
  · exit at TP 1.0x / SL 1.3x of the pick's expected move (08-26; it was
    0.3/1.0 at birth and 1.5/1.0 in between), or the 24h expiry
  · leverage capped by policy, isolated margin

Everything on the way out rests as a limit and none of it takes the market
(08-27): the stop triggers into a limit at its own price and a reduce-only
limit follows the mark if that is left behind, and the expiry exit rests
and re-posts. Market orders survive only where the position is already
unprotected or the user asked to flatten.

Deliberately absent, having been measured and rejected: swaps, a 12h
re-look (dilutes per-trade), trailing, partial TP. Intraday refills were
in that list until 08-14, when hourly seals replaced the daily one; empty
seats are now refilled, never exceeding the seat count.

Safety posture:
  · TP/SL ride on the exchange with the entry order, so a dead bot or PC
    cannot orphan a position without its lines.
  · The pre-registered breakers (liquidation, a bad running average, stops
    filling past their line) warn loudly instead of halting: 08-19 showed
    a halt is a silence, not a brake, because it waits for a console
    nobody reads while the account's real protection sits on the exchange.
  · Dry runs touch NOTHING on disk (review 5, BR1/BR4: a dry run once burned
    the day's seal and could even flip the halt flag; now it only logs).
  · Grading of closed trades reads the exchange's own realized pnl and cause,
    not a mark-price guess (BR3/BR11); the guess remains only as a fallback
    and errs to the stop, never the target.
  · The old EV bot (autonomous.py) is untouched; never run both at once. On
    entry, any symbol that already has a live exchange position - whoever
    opened it - is skipped (BR2).

Modes (2026-08-12): one account, two measured plans.
  · base - the validated plan (~80% TP-first-touch, ~60% profitable days)
  · hard - fixed ~3% target, wider swings (~56% win rate, larger total),
    NOT forward-validated yet; its settings live commented in
    policy_default.yaml
Each mode keeps its own state/record file so results never mix, and a live
run publishes a heartbeat so the other mode refuses to start next to it -
the same reasoning as the X1 guard against the old EV bot.

Run:  python -m ocean_agent.bracket_trader [--once] [--dry] [--status]
                                           [--resume] [--close-all]
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys
import time

from .api_client import PacificaError
from .autonomous import load_policy, make_client, log, equity, record_equity
from .position import _round_down_to_lot, _round_to_tick
from . import data_file, notify

KST = dt.timezone(dt.timedelta(hours=9))
# All safety-critical files live under the project root next to the package,
# never under whatever folder the process happened to start in. A cwd-relative
# path here silently disabled the cross-bot guards when the bot was launched
# from another directory. (review N1)
OUTPUTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
# Mode-dependent paths. Base keeps the historical file name so a run already
# in progress is never orphaned; use_mode() repoints both at startup.
MODE = "base"
MODES = ("base", "hard")
STATE_PATH = os.path.join(OUTPUTS_DIR, "bracket_state.json")
HEARTBEAT_PATH = os.path.join(OUTPUTS_DIR, "bracket_alive_base.json")
HEARTBEAT_FRESH_MIN = 10            # same window as the X1 guard (old EV bot)
LOOP_MIN = 30

# ── pre-registered circuit breakers (round-3 review, 2026-08-11) ──
HALT_ON_LIQUIDATION = True          # exchange-confirmed liquidation = warn
HALT_AVG_AFTER = 30                 # trades before the average is judged
# Re-derived 2026-08-28 from the live book, which is what the previous note
# asked for once 30 real trades existed. There are 150 of them. Wins and
# stops are both a few percent and close to symmetric, not the lopsided
# pair the old derivation assumed for a 1.5x target, so the expectation it
# was built on was wrong. Two standard deviations below a roughly
# breakeven expectation, over a 30 trade window, lands near this floor.
# Closes under the current geometry alone point slightly tighter; that
# sample is too thin to use, and this should tighten toward it as the
# geometry accumulates its own 30.
HALT_AVG_FLOOR = -1.3               # % per trade; -2 sigma of the expectation
DEMOTE_SLIP_EVENTS = 2              # stop fills worse than line by ...
DEMOTE_SLIP_PCT = 0.5               # ... this many %p, twice -> warn.
# Not "lower the leverage" any more: with a fixed notional per pick that
# moves the margin posted, not the loss a slipped stop takes. Thin books
# are the cause, so the answer is a smaller notional or bracket_skip_syms.

# Seals a dry run has already logged, per process. Deliberately NOT persisted:
# dry must leave no trace in the state file (BR1), this only stops log spam.
_dry_logged: set[str] = set()

# When an unconsumed seal stays open after a failed entry pass (M16), the
# 30-second seal poll must not become a hot retry loop: one 429 burst would
# re-run the whole entry pass every poll, thousands of requests an hour,
# each failure feeding the next. Entry attempts that leave the seal open
# arm this cooldown; the seal wakeup honors it. (review P4)
_retry_not_before: float = 0.0
ENTRY_RETRY_COOLDOWN_SEC = 300
# How long a warning key stays quiet after ringing (2026-08-21 user choice).
# The two ends were both wrong: comparing message text re-sent every cycle
# because the text carries a running average, and suppressing on the key
# alone silenced it forever, since nothing clears `warned`.
WARN_COOLDOWN_SEC = 6 * 3600
# How far back a history query reaches before a position's own opened_at.
# The venue writes its opening row when the order is sent and the book writes
# opened_at when the fill is booked, about two seconds later, so a window that
# starts at opened_at misses the row carrying the opening fee.
OPEN_SKEW_MS = 60_000
# Taker round trip, in percentage points of notional. Taken from the venue's
# own fee rows rather than a published rate: an opening row and a closing row
# each carry about 0.04% of notional as a negative pnl, so both sides have to
# be counted.
FEE_RT_PCT = 0.08


def _now():
    return dt.datetime.now(KST)


def bracket_mode(policy: dict) -> str:
    """Which measured plan this process runs: 'base' or 'hard'.

    An unknown name falls back to base rather than inventing a third set of
    files that nothing else would ever read again.
    """
    m = str(policy.get("bracket_mode", "base")).strip().lower()
    if m not in MODES:
        log(f"알 수 없는 모드 '{m}', 베이스 모드로 실행합니다")
        return "base"
    return m


def use_mode(mode: str, dry: bool = False) -> None:
    """Point the state and heartbeat files at this mode's own files.

    Base and hard trade different plans, so their positions and closed
    records must never share one file. Base keeps the original path.

    A dry run is given its own names on top of that. Today it writes nothing
    at all (BR1), so the separation costs nothing; the day someone lets a dry
    run keep a ledger, it will land beside the live one instead of on top of
    it.
    """
    global MODE, STATE_PATH, HEARTBEAT_PATH
    MODE = mode
    suffix = "" if mode == "base" else f"_{mode}"
    if dry:
        suffix += "_dry"
    STATE_PATH = os.path.join(OUTPUTS_DIR, f"bracket_state{suffix}.json")
    HEARTBEAT_PATH = heartbeat_path(mode, dry)


def heartbeat_path(mode: str, dry: bool = False) -> str:
    return os.path.join(OUTPUTS_DIR,
                        f"bracket_alive_{mode}{'_dry' if dry else ''}.json")


def write_heartbeat() -> None:
    """Publish "this mode is alive" for the mutual lock.

    Live runs only: a dry run must touch nothing on disk (BR1), and it holds
    no margin, so it needs no claim on the account either.
    """
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    tmp = HEARTBEAT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"mode": MODE, "pid": os.getpid(),
                   "at": _now().isoformat()}, f, ensure_ascii=False)
    os.replace(tmp, HEARTBEAT_PATH)


def clear_heartbeat() -> None:
    """Release the claim on a clean exit; a crash lets it expire instead."""
    try:
        os.remove(HEARTBEAT_PATH)
    except OSError:
        pass


def _pid_alive(pid) -> bool:
    """Is that process id still running? Unknown counts as running.

    No psutil: POSIX asks the kernel with signal 0, Windows opens the
    process handle and reads its exit code. A pid that cannot be checked is
    reported alive, because refusing to start is the safe direction and the
    freshness window releases it anyway.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True                  # exists, owned by someone else
        except OSError:
            return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
        h = k.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not h:
            return False                 # gone, or no rights to look at it
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return True
            return code.value == 259     # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except (OSError, AttributeError, ValueError):
        return True


def running_bots(include_dry: bool = False):
    """PIDs of bracket trader processes now running, or None if unknowable.

    None and [] are different answers and callers must not merge them: []
    means "looked, nothing there", None means "could not look", and telling
    a user their trading is off when we never checked is the one outcome
    worth refusing. Dry runs place no orders, so they are excluded unless
    asked for. No psutil: CIM on Windows, ps elsewhere.
    """
    import subprocess
    me = os.getpid()
    try:
        if os.name == "nt":
            q = ("Get-CimInstance Win32_Process | Where-Object { "
                 "$_.Name -match 'python' -and "
                 "$_.CommandLine -match 'ocean_agent.bracket_trader'")
            if not include_dry:
                q += " -and $_.CommandLine -notmatch '--dry'"
            q += " } | ForEach-Object { $_.ProcessId }"
            r = subprocess.run(["powershell", "-NoProfile", "-Command", q],
                               capture_output=True, text=True, timeout=40,
                               creationflags=0x08000000)
            if r.returncode != 0:
                return None
            pids = [int(x) for x in r.stdout.split() if x.strip().isdigit()]
        else:
            r = subprocess.run(["ps", "-eo", "pid=,args="],
                               capture_output=True, text=True, timeout=40)
            if r.returncode != 0:
                return None
            pids = []
            for ln in r.stdout.splitlines():
                ln = ln.strip()
                head, _, rest = ln.partition(" ")
                if not head.isdigit():
                    continue
                if "ocean_agent.bracket_trader" not in rest:
                    continue
                if not include_dry and "--dry" in rest:
                    continue
                pids.append(int(head))
        return [x for x in pids if x != me]
    except Exception:                                       # noqa: BLE001
        return None


def mark_stopped_by_user() -> None:
    """Write the intent down for every mode, not just the one that ran.

    A stop that lives only in a dead process is not a stop: anything that
    asks "is a bot running?" starts one again. Recorded per mode because
    the operator's file layout keeps one state file each.
    """
    keep = MODE
    for mode in MODES:
        try:
            use_mode(mode)
            st = load_state()
            st["stopped_by_user"] = True
            st["stopped_at"] = _now().isoformat(timespec="seconds")
            save_state(st)
        except Exception:                                   # noqa: BLE001
            pass
    use_mode(keep)


def stop_all_bots(wait_s: float = 20.0) -> dict:
    """Kill every running bracket bot, then prove it. Never assume.

    Dry runs are killed too. They place no orders, so an earlier version
    left them alone, but "stop" means stop: a process the user did not ask
    for and cannot see is not something to leave behind on their machine.
    (2026-08-26 user: "드라이봇은 왜 돌아가? 필요없어 이제")

    Returns {checked, before, left}. `checked` is False only when the
    process list could not be read at all, and then `left` is unknown
    rather than empty. A kill request returning success is not evidence the
    process died, so this polls until the list is empty or the clock runs
    out, with one more round of kills partway through.
    (2026-08-26 user instruction: stop unconditionally, then verify.)
    """
    import subprocess
    before = running_bots(include_dry=True)
    if before is None:
        return {"checked": False, "before": None, "left": None}

    def _kill(pids):
        """Force, always. There is no polite variant of this call.

        The user asking to stop is not asking us to request a stop: on
        Windows /F /T takes the tree, and POSIX goes straight to SIGKILL.
        SIGTERM would let a bot in the middle of a network call linger for
        the length of that call, which is exactly the window in which it
        can still place an order. (2026-08-26 user: "무조건 강제종료해")
        """
        for pid in pids:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                                   capture_output=True, timeout=30)
                else:
                    import signal
                    os.kill(int(pid), signal.SIGKILL)
            except Exception:                               # noqa: BLE001
                pass

    _kill(before)
    deadline = time.time() + max(3.0, wait_s)
    left = before
    while time.time() < deadline:
        time.sleep(1.0)
        left = running_bots(include_dry=True)
        if left is None:
            break                        # cannot verify; reported as such
        if not left:
            break
        _kill(left)                      # keep hitting it until it is gone
    for mode in MODES:
        for dry in (False, True):
            try:
                os.remove(heartbeat_path(mode, dry))
            except OSError:
                pass
    mark_stopped_by_user()
    return {"checked": left is not None, "before": before, "left": left}


def live_bracket() -> tuple[str, float] | None:
    """A bracket instance still holding the account: (mode, minutes ago).

    One account means one margin pool, so two bracket instances must never
    run at once - the same reason the X1 guard refuses to start beside the
    old EV bot. Own-mode files count too: a second copy of the same mode is
    just as bad.

    Freshness used to be the only test, which meant a bot that was killed
    kept the account locked for HEARTBEAT_FRESH_MIN minutes even though
    nothing was running. That is five minutes of no trading after every
    restart, and it bit the operator on 2026-08-26. The heartbeat already
    carries the pid, so a fresh file whose process is gone is a corpse and
    is cleared on sight. A fresh file whose process is alive still refuses,
    which is the case the lock exists for. An unreadable or unparseable
    file is treated as a live claim and left to expire. (2026-08-26)
    """
    for other in MODES:
        path = heartbeat_path(other)
        if not os.path.exists(path):
            continue
        age_min = (time.time() - os.path.getmtime(path)) / 60
        if age_min >= HEARTBEAT_FRESH_MIN:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                pid = json.load(f).get("pid")
        except (OSError, ValueError, AttributeError):
            return other, age_min        # cannot read it: assume it is live
        if pid is not None and pid != os.getpid() and not _pid_alive(pid):
            log(f"{other} 모드 흔적(pid {pid})은 이미 꺼진 봇입니다. "
                f"자리를 비우고 계속합니다")
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        return other, age_min
    return None


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"positions": {}, "closed": [], "entered_seals": [],
            "halted": False, "halt_reason": "", "slip_events": 0}


def save_state(st: dict) -> None:
    """Write the ledger, keeping any closes another process booked.

    The whole file is rewritten from memory, so two bots (or a bot and a
    repair) overwrite each other's closed trades: on 08-19 a bot started
    at 21:59 saved at 22:33 and erased three closes written in between.
    Closed records only ever get appended, so the union of what is on
    disk and what is in memory is the truth.
    """
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            on_disk = json.load(f).get('closed', [])
    except (OSError, ValueError):
        on_disk = []
    # keyed on the entry, not on the writer's clock: two processes grading
    # the same close stamp different closed_at values and would each keep a row
    def key_of(c):
        return (c.get('sym'), c.get('opened_at') or c.get('closed_at'))

    if on_disk:
        seen = {key_of(c) for c in st['closed']}
        extra = [c for c in on_disk if key_of(c) not in seen]
        if extra:
            st['closed'] = sorted(st['closed'] + extra,
                                  key=lambda c: c.get('closed_at', ''))
            log(f'다른 프로세스가 기록한 청산 {len(extra)}건을 합칩니다')
    # The same key twice inside one list is the same trade booked twice: a
    # flatten that wrote no sym, or a repair run against a live bot. The
    # merge above already treats the key as the identity of an entry, so
    # apply it here too rather than letting the average count it twice.
    once, dedup = set(), []
    for c in st['closed']:
        k = key_of(c)
        if k in once:
            log(f'같은 청산이 두 번 기록돼 하나로 합칩니다: {k[0] or "?"}')
            continue
        once.add(k)
        dedup.append(c)
    st['closed'] = dedup
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def bracket_cfg(policy: dict) -> dict:
    """Bracket settings, read from policy.yaml with the measured defaults.

    Week one deliberately runs leverage 3 (execution shakedown); raise
    bracket_leverage to 8 in policy.yaml only after the forward gates pass.
    """
    return {
        "slots": int(policy.get("bracket_slots", 3)),
        "leverage": int(policy.get("bracket_leverage", 3)),
        "deploy_pct": float(policy.get("bracket_deploy_pct", 0.97)),
        # share of spendable margin the user lets the bot use; 100 = all of
        # it, exactly as before this key existed
        "capital_pct": max(0.0, min(100.0,
                                    float(policy.get("bracket_capital_pct", 100)))),
        "tp_mult": float(policy.get("bracket_tp_mult", 0.3)),
        # fixed TP distance in % (hard mode); 0 keeps the measured multiple
        "tp_pct": max(0.0, float(policy.get("bracket_tp_pct", 0))),
        "sl_mult": float(policy.get("bracket_sl_mult", 1.0)),
        "vol_floor_pct": float(policy.get("bracket_vol_floor_pct", 0.8)),
        # How far the STOP may sit, in percent. The instruction it comes
        # from was about the stop and not the expected move: "anything whose
        # stop line goes past 5%, take it out" (2026-08-25, after one PUMP
        # stop of -5.5% on a 5.8% expected move ate three and a half take
        # profits). It was written down as a cap on the expected move, which
        # is the same thing only while SL is 1.0x. The multiple went to 1.3x
        # on 08-26 and the cap did not, so stops out to 6.5% were passing
        # for two days. Stated as the stop distance it cannot drift again.
        # A long replay does not support a uniform ceiling, so this is a
        # live-trading judgment rather than a measured rule. 0 turns it off.
        # bracket_vol_ceiling_pct is the older key and still works, read as
        # a cap on the expected move; when both are set the tighter wins.
        # (08-28)
        "sl_ceiling_pct": max(0.0,
                              float(policy.get("bracket_sl_ceiling_pct", 0))),
        "vol_ceiling_pct": max(0.0,
                               float(policy.get("bracket_vol_ceiling_pct", 0))),
        # Adverse distance at which a position is left early, at our own
        # price, without waiting for the stop. 0 turns it off. (08-28)
        "early_cut_pct": max(0.0,
                             float(policy.get("bracket_early_cut_pct", 0))),
        # What the operator said they wanted working, in dollars. Slots and
        # per-pick size come out of it: $300 at $50 a pick is six positions.
        # Percent-of-account is what a spreadsheet understands; a person
        # answering "how much do you want to trade with" says a number.
        "budget_usd": max(0.0, float(policy.get("bracket_budget_usd", 0))),
        # symbols the operator wants left alone, whatever the seal says.
        # Empty by default: this is a manual veto, not a measured rule.
        "skip_syms": {str(s).upper()
                      for s in (policy.get("bracket_skip_syms") or [])},
        "horizon_h": int(policy.get("bracket_horizon_h", 24)),
        # How the expiry closes. "limit" rests a reduce-only limit at the
        # mark and re-posts it every wait window until it fills; "market"
        # is the old behaviour. The 2026-08-26 geometry (TP 1.0x / SL 1.3x)
        # takes the expiry on about one trade in five instead of one in
        # thirty, so the taker fee on that exit stopped being a rounding
        # error: taker is 0.040% a side against 0.015% maker, and paying
        # that on one exit in five is worth avoiding. The position keeps its
        # exchange-side stop while the limit rests, so an unfilled chase is
        # bounded by the same stop it always had. (2026-08-26 user decision)
        "expiry_exit": str(policy.get("bracket_expiry_exit", "limit")).lower(),
        "expiry_wait_s": max(10, int(policy.get("bracket_expiry_wait_s", 60))),
        # fixed per-pick notional in USD; 0 keeps proportional sizing.
        # Added 2026-08-14 (user): their own account trades a fixed $30 a
        # pick while the shipped default stays proportional to the account.
        "notional_usd": max(0.0, float(policy.get("bracket_notional_usd", 0))),
    }


def tp_distance_pct(cfg: dict, exp_move_pct: float) -> float:
    """Target distance in %, the one place both modes agree on.

    Base multiplies the pick's expected move by bracket_tp_mult (1.0x
    since 08-26, 0.3x when this was written); hard fixes the target near
    3% regardless of the pick. Grading's fallback estimate must use the
    same number as the order, so both call this.
    """
    if cfg["tp_pct"] > 0:
        return cfg["tp_pct"]
    return cfg["tp_mult"] * exp_move_pct


def latest_seal() -> dict | None:
    """The newest seal from the CURRENT pick pipeline only.

    The legacy forecast process also writes 내일예측 files with trade ranks;
    its picks come from a different, rejected selection rule, so the bot
    must never consume them. The current sealer stamps a rule string that
    starts with the class-score marker, and every pick carries slip_pct.
    """
    files = sorted(glob.glob(os.path.join(OUTPUTS_DIR, "내일예측_*.json")))
    for path in reversed(files):
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        # A seal with an empty pick list is an answer, not a broken file:
        # the operator's rules can say nothing on this board will be
        # traded. any() is False on an empty list, so asking it here made
        # the loop walk back to an OLDER seal and trade names the rules
        # had just refused. The 386 seals on disk confirm the two halves
        # do different jobs: the rule string alone separates the 21 files
        # from the retired forecast process, and every 자산군 seal that
        # has picks carries trade_rank on them. So ask for the shape of a
        # seal, not for a pick inside it. (2026-09-03)
        if ("picks" in rec
                and str(rec.get("rule", "")).startswith("자산군")):
            rec["_path"] = path
            return rec
    return None


def seal_key(rec: dict) -> str:
    """Content-based identity (BR10): renaming or moving the file must not
    make a seal enterable twice."""
    return str(rec.get("made_at", ""))


def select_picks(rec: dict, cfg: dict) -> list[dict]:
    """Top slots by trade_rank; a pick under the vol floor is skipped and the
    next rank takes its seat (the floor is cost-derived: TP must clear 3x the
    round-trip fee)."""
    picks = sorted(rec.get("picks", []), key=lambda p: p.get("trade_rank", 9))
    out = []
    for p in picks:
        if p["sym"].upper() in cfg.get("skip_syms", ()):
            log(f"제외 목록이라 건너뜀: {p['sym']} (bracket_skip_syms)")
            continue
        if p.get("exp_move_pct", 0) < cfg["vol_floor_pct"]:
            log(f"변동폭 하한 미달로 건너뜀: {p['sym']} "
                f"({p.get('exp_move_pct')}% < {cfg['vol_floor_pct']}%)")
            continue
        # Both caps end up as a limit on the expected move, because that is
        # what a pick carries. The stop cap is converted through whatever
        # actually sets the stop, so it keeps meaning "the stop may not sit
        # past this" however the geometry changes. Tighter of the two wins.
        #
        # 09-03: that conversion still used sl_mult after be1a93a moved the
        # stop onto the cut. With a 2% cut the stop no longer grows with the
        # expected move at all, so a pick was being refused for a stop it was
        # never going to get: LIT read as 4.0% x 1.3 = 5.20% against the 5.00%
        # ceiling, while its real stop was the 2% cut. The gate now asks the
        # same question bracket_prices answers.
        _mv = p.get("exp_move_pct", 0)
        _slm = cfg.get("sl_mult", 1.0) or 1.0
        _cut = float(cfg.get("early_cut_pct", 0) or 0)
        _slc = cfg.get("sl_ceiling_pct", 0)
        if _cut > 0:
            # 손절 거리가 예상변동과 무관하게 고정이다. 상한을 넘는지는
            # 픽마다 달라지지 않으므로 이 관문은 여기서 할 일이 없다.
            _sl_cap = 0 if _cut <= _slc or not _slc else 1e-9
        else:
            _sl_cap = (_slc / _slm) if _slc else 0
        _caps = [c for c in (cfg.get("vol_ceiling_pct", 0), _sl_cap) if c]
        ceil = min(_caps) if _caps else 0
        if ceil and _mv > ceil:
            _shown = _cut if _cut > 0 else _mv * _slm
            log(f"변동폭 상한 초과로 건너뜀: {p['sym']} "
                f"(예상변동 {_mv}% · 손절선 {_shown:.2f}%, "
                f"상한 {_slc:.2f}%)")
            continue
        out.append(p)
        if len(out) == cfg["slots"]:
            break
    return out


def bracket_prices(px: float, direction: str, mv: float, cfg: dict,
                   tick: float) -> tuple[str, str] | None:
    """Tick-rounded TP/SL, validated to sit on the correct sides of entry.

    With a tight target and a coarse tick, rounding can collapse the TP
    onto the entry price or past it (BR8). The multiple has widened since
    that was first hit, so it bites less often now, but a wide multiple on
    a quiet pick still lands in the same place. A
    bracket that fails validation is a reason to skip the pick, not to enter
    it naked.

    Deliberate: the bracket is anchored to the LIVE price at entry (px), not
    to the seal's own entry price. The fill happens minutes to hours after
    the seal was made; anchoring to the stale seal price could place the TP
    on a level the market has already passed (instant fee-only exit) or push
    the SL further than its measured distance. The seal contributes the
    direction and expected move only; the geometry is recomputed from what
    was actually paid. Do not "fix" this by copying seal prices.
    """
    long_ = direction == "long"
    tp_d = tp_distance_pct(cfg, mv * 100) / 100
    tp = px * (1 + tp_d) if long_ else px * (1 - tp_d)
    # The stop IS the cut, and the exchange holds it. 09-02 user decision.
    #
    # It used to be 1.3 x expected move, sitting outside a 2% cut that the
    # loop ran itself. That put the line which actually decided the trade
    # inside the bot: it could be late by up to a poll, it could miss while
    # the process was restarting, and on a thin overnight book it could
    # fail to fill and let the price run to the far line. On 09-02 SAMSUNG
    # stopped at 1.64% and SKHYNIX at 1.89%, both before 2% was ever seen.
    #
    # One line now, at the cut, registered with the venue. It cannot be
    # late and it does not need the bot awake. On a quiet name where 1.3 x
    # move used to sit inside 2%, the stop is now slightly WIDER than it
    # was; that is the price of having one rule instead of two.
    #
    # The loop's cut and its chase are left in place: they still cover the
    # requested-close path and any position opened before this change.
    _cut = float(cfg.get("early_cut_pct", 0) or 0) / 100.0
    _sl_d = _cut if _cut > 0 else cfg["sl_mult"] * mv
    sl = px * (1 - _sl_d) if long_ else px * (1 + _sl_d)
    tp_s, sl_s = _round_to_tick(tp, tick), _round_to_tick(sl, tick)
    tp_f, sl_f = float(tp_s), float(sl_s)
    ok = (sl_f < px < tp_f) if long_ else (tp_f < px < sl_f)
    if not ok:
        return None
    return tp_s, sl_s


def _recorded_distances(pos: dict, cfg: dict) -> tuple[float, float]:
    """This position's own take-profit and stop distances, in percent.

    Read off the prices the orders were actually placed at, not recomputed
    from today's multipliers. On a day the geometry changes the book holds
    two generations, and reading one with the other's ruler is wrong. It
    matters for
    the slippage breaker, which asks whether a stop filled worse than its own
    distance, and for the estimated-PnL cap.

    Tick rounding alone already moves these, so a position's real distances
    are read off its own recorded prices rather than recomputed. Across a
    geometry change that gap is the whole change.

    Falls back to the config when a position predates the recorded prices or
    carries an unusable entry.
    """
    entry = pos.get("entry_fill") or pos.get("entry_intent") or 0
    tp, sl = pos.get("tp"), pos.get("sl")
    try:
        if entry > 0 and tp and sl:
            tp_d = abs(float(tp) / entry - 1.0) * 100
            sl_d = abs(float(sl) / entry - 1.0) * 100
            if tp_d > 0 and sl_d > 0:
                return tp_d, sl_d
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    exp = pos.get("exp_move_pct") or 0
    if exp > 0:
        return tp_distance_pct(cfg, exp), cfg["sl_mult"] * exp
    # Nothing to measure against, so say so instead of answering (0, 0). The
    # caller caps its estimate at min(move, tp_d) and max(move, -sl_d), so
    # zeros would book every close, win or loss, as exactly zero and make
    # the slippage counter read every stop as slipped. No position on file
    # lacks these fields; this is a guard, not a path.
    return None, None


def _is_strategy(rec: dict) -> bool:
    """Did the strategy produce this close, or did something else?

    The breaker and the status line grade the strategy, so an exit the
    operator or the venue made has no business in either. --close-all labels
    itself manual_close, but a position closed by hand in the exchange app
    comes back with the venue's own label and used to pass straight through.

    Kept: the exchange bracket firing, a liquidation, and the bot's own expiry
    close. Dropped: manual_close, and anything the code could only guess at,
    which is what the 추정 prefix marks. A guess is not evidence either way,
    and 60 rows in the 08-21 book carried ten guesses.
    """
    c = str(rec.get("cause") or "")
    if not c or c == "manual_close":
        return False
    # 추정 anywhere in the label, not only at the front: "만기:추정" is a real
    # strategy exit whose amount the code had to guess, and a guessed amount
    # is what makes a row unusable for grading, whatever caused the exit.
    return "추정" not in c


def _close_row(sym: str, pos: dict, est, cause: str) -> dict:
    """One shape for every booked close, carrying its own geometry.

    The book used to keep eight fields, none of them the bracket the trade
    actually wore, so a row could not be re-read later: one loss figure might be a
    stop hit cleanly or a stop slipped through, and nothing on the row said
    which. A book that spans a geometry change has to carry its own ruler,
    and the analysis side was corrected while the writing side was not.
    Additive, so nothing that reads the old eight fields changes.
    """
    row = {"sym": sym, "dir": pos.get("dir", ""),
           "pnl_pct_est": est, "cause": cause,
           "opened_at": pos.get("opened_at"),
           "closed_at": _now().isoformat(),
           "trade_rank": pos.get("trade_rank"),
           "basis": pos.get("basis")}
    for k in ("tp", "sl", "entry_fill", "amount", "exp_move_pct", "leverage",
              "pred_input"):
        v = pos.get(k)
        if v is not None:
            row[k] = v
    # Which fee convention priced this row. Rows written before 08-21 09:44
    # summed closing rows only, so they are optimistic by one side's fee;
    # rows written after include both sides. A book
    # that spans the change cannot be averaged without saying which is which,
    # and the earlier rows cannot all be repaired because the venue's history
    # only reaches back about fifty round trips.
    row["fee_basis"] = "round_trip"
    return row


def _settings_confirmed(client, sym: str, lev: int) -> bool:
    """Does the exchange itself say this market is isolated at `lev`?

    Called only after update_margin_mode/update_leverage failed, to separate
    "already set" (fine) from 429/signature/network errors (must never enter
    on cross margin or at a stale leverage). The settings payload is a dict
    whose per-symbol rows sit under "margin_settings", each row carrying
    "symbol", "isolated" (bool) and "leverage" (int). Anything unreadable
    counts as not confirmed: fail closed, but say WHY, so a silently skipped
    symbol can be diagnosed from the log. (review H2, N2)
    """
    try:
        resp = client.get_account_settings()
    except PacificaError as e:
        log(f"⚠️ {sym}: 계좌 설정 조회 실패({str(e)[:80]}), 확인 불가로 "
            f"진입 거부")
        return False
    rows = resp.get("margin_settings") if isinstance(resp, dict) else resp
    if not isinstance(rows, list):
        log(f"⚠️ {sym}: 계좌 설정 응답에 margin_settings 목록이 없음 "
            f"(응답 형식 {type(resp).__name__}), 확인 불가로 진입 거부")
        return False
    for s in rows:
        if not isinstance(s, dict) or s.get("symbol") != sym:
            continue
        iso, lev_raw = s.get("isolated"), s.get("leverage")
        if iso is None or lev_raw is None:
            log(f"⚠️ {sym}: margin_settings 행에 isolated/leverage 키가 없음 "
                f"(행 키: {sorted(s.keys())}), 확인 불가로 진입 거부")
            return False
        try:
            cur_lev = int(float(lev_raw))
        except (TypeError, ValueError):
            log(f"⚠️ {sym}: leverage 값 해석 불가({lev_raw!r}), 확인 불가로 "
                f"진입 거부")
            return False
        if not (bool(iso) and cur_lev == lev):
            log(f"⚠️ {sym}: 거래소 설정 불일치 (isolated={iso}, "
                f"leverage={cur_lev}, 요구 격리 {lev}배), 진입 거부")
            return False
        return True
    log(f"⚠️ {sym}: margin_settings 에 해당 종목 행이 없어 확인 불가, "
        f"진입 거부")
    return False


# One raw order row is logged per process when open orders exist but none
# matched the order_type values this code expects, so the real field names
# can be read from the log without spamming every loop. (review N2)
_raw_order_logged = False


def _log_raw_order_once(sym: str, orders: list) -> None:
    global _raw_order_logged
    if _raw_order_logged or not orders:
        return
    _raw_order_logged = True
    try:
        log(f"주문 필드 확인용 원본 1건 ({sym}): "
            + json.dumps(orders[0], ensure_ascii=False)[:500])
    except (TypeError, ValueError):
        pass


def _exchange_brackets(client, sym: str) -> tuple[bool, bool, list]:
    """(TP present, SL present, sym's open orders) from the exchange.

    The entry order carries take_profit/stop_loss subfields, but nothing in
    its response proves they were accepted; this asks the order book itself.
    The raw order list is returned so the caller can tell "no orders at all"
    apart from "orders exist but none matched the expected type fields":
    only the first case is safe to re-attach into, the second may already BE
    the bracket under unknown field names. (review H1, N2)
    """
    has_tp = has_sl = False
    orders = [o for o in client.get_open_orders() if o.get("symbol") == sym]
    for o in orders:
        ot = str(o.get("order_type", ""))
        if ot.startswith("take_profit"):
            has_tp = True
        elif ot.startswith("stop"):
            has_sl = True
    return has_tp, has_sl, orders


def _clear_pending(st: dict, sym: str) -> None:
    """Drop the pending-entry intent for a symbol once its outcome is booked
    (either as a position or as a completed round trip). (review H9)"""
    pend = st.get("pending_entries")
    if pend:
        st["pending_entries"] = [q for q in pend if q.get("sym") != sym]


def reconcile_pending(client, st: dict) -> None:
    """Adopt or drop entry intents that never got booked. (review H9)

    A pending record means a market order may have gone out right before a
    crash, so the exchange could hold a position no ledger remembers. If the
    exchange holds it, book it flagged (unprotected + recheck_fill) so the
    watcher re-validates the fill and the user can see it; if the exchange
    does not, the order never filled and the record is dropped. An
    unreadable exchange keeps every record for the next attempt: fail
    closed, never guess.
    """
    pend = st.get("pending_entries") or []
    if not pend:
        return
    try:
        live = {p.get("symbol"): p for p in client.get_positions()}
    except PacificaError as e:
        log(f"⚠️ 미결 진입 기록 {len(pend)}건 확인 실패({str(e)[:80]}), "
            f"다음 회차에 재시도")
        return
    keep = []
    for q in pend:
        sym = q.get("sym")
        if not sym or sym in st["positions"]:
            continue
        try:
            at = dt.datetime.fromisoformat(q.get("at"))
            age_min = (_now() - at).total_seconds() / 60
        except (TypeError, ValueError):
            age_min = 1e9
        lp = live.get(sym)
        if lp is None:
            # Absent from the exchange is ambiguous: never filled, not yet
            # visible, or filled AND already closed by its bracket. Young
            # records get a grace pass; old ones are asked about via the
            # exchange's own close history before being let go, so a trade
            # that lived and died while the bot was down still reaches
            # st["closed"] and the circuit breakers. (review P3, case B)
            if age_min < 5:
                keep.append(q)
                continue
            try:
                since = int(at.timestamp() * 1000) - 60_000
                done = realized_close(client, sym, since)
            except Exception:
                keep.append(q)      # unreadable history: retry next cycle
                continue
            if done:
                notional = float(q.get("amount") or 0) * \
                    float(q.get("entry_intent") or 0)
                _rc = done.get("cause") or ""
                if _rc in ("", "normal"):
                    # The venue labels every close "normal", so the sign of the
                    # result is a guess, and watch_positions was taught not to
                    # state a guess as fact: a hand-closed loss became a
                    # "stop_loss", which fed _stop_streak and blocked that
                    # direction for the rest of the day. This path kept the old
                    # certainty, so the hole reopened every time the bot came
                    # back from a crash. Same names as the other path, so the
                    # streak counter can tell a guess from a stop.
                    _rc = ("추정:이익" if float(done.get("pnl_usd") or 0) >= 0
                           else "추정:손실")
                # Same row shape as every other close, so the geometry
                # travels with it: q carries tp, sl, amount, exp_move_pct and
                # leverage, and a recovered trade is exactly the one a later
                # analysis will want to re-read.
                row = _close_row(
                    sym, {**q, "opened_at": q.get("at"),
                          "entry_fill": q.get("entry_intent")},
                    (float(done.get("pnl_usd") or 0) / notional * 100
                     if notional > 0 else 0.0), _rc)
                row.update({"pnl_usd": done.get("pnl_usd"),
                            "seal": q.get("seal"), "recovered": True})
                st["closed"].append(row)
                msg = (f"브래킷 복구: {sym} 은 봇이 꺼진 사이 이미 청산됨 "
                       f"({done.get('cause')}, "
                       f"${float(done.get('pnl_usd') or 0):+.2f}). "
                       f"장부에 기록했습니다.")
                log("⚠️ " + msg)
                notify.send("⚠️ " + msg)
            else:
                log(f"미결 진입 {sym}: 거래소 미보유 + 청산 이력 없음, "
                    f"미체결로 판단하고 기록을 버립니다")
            continue
        # Adopt only a position that matches the recorded intent: same
        # direction, roughly the ordered size. A mismatch means someone
        # else's trade (manual, another tool) and BR2 says never touch it.
        # (review P3, case C)
        want_side = "bid" if q.get("dir") == "long" else "ask"
        lp_side = str(lp.get("side") or "")
        try:
            lp_amt = abs(float(lp.get("amount") or 0))
            q_amt = float(q.get("amount") or 0)
        except (TypeError, ValueError):
            lp_amt, q_amt = 0.0, 0.0
        size_ok = q_amt > 0 and lp_amt > 0 and lp_amt <= q_amt * 1.05
        if lp_side != want_side or not size_ok:
            msg = (f"브래킷 복구 보류: {sym} 거래소 포지션이 기록과 다릅니다 "
                   f"(방향 {lp_side or '?'} vs 기대 {want_side}, 수량 "
                   f"{lp_amt} vs 주문 {q_amt}). 남의 포지션일 수 있어 "
                   f"건드리지 않습니다. 직접 확인해 주세요.")
            log("⚠️ " + msg)
            notify.send("⚠️ " + msg)
            continue
        try:
            entry = float(lp.get("entry_price") or q.get("entry_intent") or 0)
        except (TypeError, ValueError):
            entry = float(q.get("entry_intent") or 0)
        st["positions"][sym] = {
            # book what the exchange actually holds, not what we meant to
            # order: a partial fill must size the close and the pnl math
            "dir": q.get("dir"), "amount": lp_amt or q.get("amount"),
            "entry_intent": q.get("entry_intent"), "entry_fill": entry,
            "fill_confirmed": False, "tp": q.get("tp"), "sl": q.get("sl"),
            "exp_move_pct": q.get("exp_move_pct"),
            "pred_input": q.get("pred_input") or PRED_INPUT_TAG,
            "leverage": q.get("leverage"),
            "opened_at": q.get("at") or _now().isoformat(),
            "seal": q.get("seal"), "trade_rank": q.get("trade_rank"),
            "basis": q.get("basis"),
            # bracket presence is unknown after a crash: keep it visibly
            # unprotected and let the watcher re-validate the fill once
            "unprotected": True, "recheck_fill": True,
        }
        msg = (f"브래킷 복구: 중단됐던 진입 {sym} {q.get('dir')} 을 장부에 "
               f"복원했습니다 (거래소 보유 확인). TP/SL 부착 여부는 미확인, "
               f"봇이 매 회차 감시합니다.")
        log("⚠️ " + msg)
        notify.send("⚠️ " + msg)
    st["pending_entries"] = keep
    save_state(st)


def _cool_down() -> None:
    """Hold off the seal poll after a pass that entered nothing."""
    global _retry_not_before
    _retry_not_before = time.time() + ENTRY_RETRY_COOLDOWN_SEC


def apply_budget(cfg: dict) -> dict:
    """Turn a dollar answer into slots and per-pick size.

    Asked once when auto trading starts and changeable any time, so the
    number a person gave is the number that trades: $300 with the standard
    $50 a pick is six positions, not a share of whatever the balance
    happens to be that morning.
    """
    budget = cfg.get("budget_usd", 0)
    if budget <= 0:
        return cfg
    per = cfg.get("notional_usd") or 50.0
    # A budget smaller than one pick used to still open one pick at full size:
    # $30 with a $50 notional traded $50, 167% of what was asked for. Shrink
    # the pick instead, and say so, because silently trading more than the
    # number a person typed is the one thing this function must not do.
    if budget < per:
        log(f"예산 ${budget:,.0f} 가 픽 하나({per:,.0f})보다 작아 "
            f"픽 크기를 ${budget:,.0f} 로 줄입니다")
        per = budget
    cfg["notional_usd"] = per
    cfg["slots"] = max(1, min(int(budget // per), 8))
    # capital_pct is deliberately replaced, not merged: the budget is an
    # absolute limit, so a percentage of the balance on top of it would mean
    # two different answers to "how much". policy.yaml's bracket_capital_pct
    # is therefore ignored whenever a budget is set, which was happening
    # silently before.
    if cfg.get("capital_pct", 100.0) != 100.0:
        log(f"예산이 설정돼 있어 bracket_capital_pct "
            f"{cfg['capital_pct']:.0f}% 는 쓰지 않습니다 "
            f"(예산 ${budget:,.0f} 가 상한)")
    cfg["capital_pct"] = 100.0
    return cfg


def enter_positions(client, policy, st, cfg, dry: bool) -> None:
    rec = latest_seal()
    if not rec:
        log("진입 대기: 봉인(내일예측_*.json) 이 아직 없다. 픽 생성이 만들면 "
            "다음 회차에 자동 진입한다.")
        return
    key = seal_key(rec)
    made = dt.datetime.fromisoformat(rec["made_at"])
    if key in st["entered_seals"] or (dry and key in _dry_logged):
        return
    age_h = (_now() - made).total_seconds() / 3600
    if age_h > 6:
        # a stale seal is not an entry signal; wait for tonight's fresh one.
        # dry leaves no trace (BR1): only a live run marks the seal consumed.
        log(f"봉인이 {age_h:.1f}시간 지나 진입 생략 (다음 봉인 대기)")
        if dry:
            _dry_logged.add(key)
        else:
            st["entered_seals"].append(key)
            save_state(st)
        return

    acct = client.get_account()
    # Spendable margin, not equity: equity counts unrealized pnl and does not
    # subtract margin already locked by other positions (BR2, same as the old
    # bot's S4 fix).
    funds = float(acct.get("available_to_spend") or 0)
    if funds <= 0:
        # Cooling down matters as much as the message: without it the seal
        # stays unconsumed, the 30-second poll wakes on it forever, and the
        # same telegram goes out twice a minute. 08-19 ran 388 cycles in
        # four hours that way.
        _warn_once(st, "funds", "가용 증거금을 읽지 못해 진입을 건너뜁니다.")
        _cool_down()
        return
    # only the share of that spendable margin the user allows (default all of
    # it); deploy_pct and the slot split then divide this share, so $1000 at
    # 50% sizes exactly like $500 at 100%
    funds *= cfg["capital_pct"] / 100
    picks = select_picks(rec, cfg)
    if not picks:
        # every pick filtered out (vol floor, skip list): the seal holds
        # nothing for us, so stop re-reading it every thirty seconds
        _cool_down()
        return
    if cfg["notional_usd"] > 0:
        # A fixed notional is an absolute answer to "how much per pick", so
        # it is not a share of anything: it is not divided by the slot count.
        # Dividing it as well made every pick a fraction of a fraction, and
        # the size the account holder asked for was never actually reached.
        # No ceiling on top of it either. Trimming the asked-for size to
        # whatever the balance happened to allow was the bot deciding for
        # the account holder; the number they typed is the answer, and
        # budget_slices below simply opens fewer picks when the margin does
        # not stretch to more (2026-08-31 user instruction: do not block,
        # let people pick their own size).
        margin_per = cfg["notional_usd"] / max(1, cfg["leverage"])
    else:
        # Divide by the whole plan, not by what happens to be free. With the
        # old divisor a single empty slot took the entire spendable margin:
        # seven held plus one free put 97% of the account into that one
        # entry. Anyone running the shipped defaults (no fixed notional)
        # met that on their first refill.
        margin_per = funds * cfg["deploy_pct"] / max(1, cfg["slots"])
    mkts = {m["symbol"]: m for m in client.get_markets()}
    prices = {p["symbol"]: p for p in client.get_prices()}
    # any live exchange position blocks that symbol, whoever opened it:
    # the old bot, a manual trade, or ourselves (BR2)
    live_syms = {p.get("symbol") for p in client.get_positions()}

    opened = []
    entered_n = 0
    # Transient failures (price feed, settings, rejected order) counted so
    # the seal is only consumed below when something real happened; a pick
    # skipped deliberately (streak, slots, invalid bracket) does not count
    # as a failure. Too small to trade does count: the account may be topped
    # up within the seal's window, and burning the seal hides the reason.
    attempt_failed = 0
    too_small = 0
    held_start = len(st["positions"])
    # After two consecutive stops in the same direction, stop chasing that
    # side. A win, a direction flip, OR a day of quiet clears the block:
    # before 2026-08-25 a blocked symbol could stay blocked forever (it
    # cannot win because it cannot enter), and the user asked for a 24h
    # expiry ("하루정도 지나면 해제해").
    def _stop_streak(sym, direction):
        n = 0
        for rec in reversed(st["closed"]):
            if rec.get("sym") != sym:
                continue
            cause = str(rec.get("cause") or "")
            # A guessed loss is not proof the stop was hit: a position the
            # operator closed by hand at a loss used to block that direction
            # for the rest of the day (VVV, 08-19).
            if rec.get("dir") != direction or cause != "stop_loss":
                break
            if n == 0:
                try:
                    seen = dt.datetime.fromisoformat(rec["closed_at"])
                    now = (dt.datetime.now(seen.tzinfo) if seen.tzinfo
                           else dt.datetime.now())
                    if (now - seen).total_seconds() > 24 * 3600:
                        return 0          # the last stop is a day old
                except (KeyError, ValueError, TypeError):
                    pass
            n += 1
        return n

    # Empty special seats (the seal filled fewer than two funding specials)
    # lend their margin to conviction: a pick whose strong 1h vote agrees
    # with its entry direction gets double notional, one seat per bonus
    # (08-24 user decision, operator opt-in via bracket_strong_bonus).
    n_special = sum(1 for q in picks
                    if str(q.get("basis", "")).startswith("특별"))
    bonus_left = max(0, 2 - n_special) if (_strong_bonus and not dry) else 0
    # A bonus may never cost a seat (08-24 user order "놓치지 말게").
    # The budget is this cycle's deployable funds in units of one seat
    # slice; a double is granted only when, after paying two slices here,
    # every remaining seat can still afford its one. Otherwise the pick
    # simply enters at 1x. With a fixed notional margin_per is a flat size
    # rather than a share of funds, so this count is simply how many of
    # those sizes the spendable margin covers this cycle.
    budget_slices = (int(funds * cfg["deploy_pct"] // margin_per)
                     if margin_per > 0 else cfg["slots"])
    used_slices = 0

    for p in picks:
        # Eight picks mean eight order round-trips and their retries; ten
        # minutes of that used to read as a dead bot and invited a second
        # one onto the same margin.
        if not dry:
            write_heartbeat()
        if _stop_streak(p["sym"], p["dir"]) >= 2:
            log(f"{p['sym']} {p['dir']} 연속 손절 2회, 같은 방향 추격 중단")
            continue
        # Give up on a name the market has left behind. The anchor below is
        # held for the whole seal on purpose, so a price that has walked away
        # is missed at every retry, five minutes apart, for as long as the
        # seal lasts. That is a seat sitting empty rather than a pick being
        # patient. After this many misses the name is dropped for this seal
        # and the seat goes to the next pick. A fresh seal starts over.
        # (08-28 user decision: two or three misses and let it go)
        _misses = st.setdefault("entry_misses", {})
        if _misses.get(f"{key}|{p['sym']}", 0) >= _entry_max_tries:
            log(f"{p['sym']}: 진입 {_entry_max_tries}회 연속 미체결, 이 "
                f"봉인에서는 포기하고 자리를 다음 픽에 넘긴다")
            continue
        # total-slot cap. Before 2026-08-14 the bot entered one seal per day,
        # so the pick list itself was the cap; with hourly refills a fresh
        # seal of six names must not stack on top of held ones (user: refill
        # emptied slots, never exceed the slot count).
        if held_start + entered_n >= cfg["slots"]:
            # A seat_priority pick may claim a seat: when the book is
            # full, the worst-performing non-priority holding is closed
            # (maker chase, the early-cut machinery) and the pick enters
            # on a later pass once its seat is empty.
            if p.get("seat_priority") and not dry:
                victim, vpnl = None, 0.0
                for vsym, vpos in st["positions"].items():
                    if vpos.get("seat_priority") or vpos.get("evict_req"):
                        continue
                    ve = float(vpos.get("entry_fill") or 0)
                    vm = float(prices.get(vsym, {}).get("mark")
                               or prices.get(vsym, {}).get("mid") or 0)
                    if ve <= 0 or vm <= 0:
                        continue
                    pnl = ((vm / ve - 1) if vpos.get("dir") == "long"
                           else (1 - vm / ve)) * 100
                    if victim is None or pnl < vpnl:
                        victim, vpnl = vsym, pnl
                if victim is not None:
                    st["positions"][victim]["evict_req"] = "priority"
                    save_state(st)
                    log(f"{victim}: 우선 픽({p['sym']})에 자리를 내주려고 "
                        f"청산을 예약한다 (보유 중 최저 {vpnl:+.2f}%)")
                continue
            break
        sym, direction = p["sym"], p["dir"]
        touch_dir = direction
        seat_src = _side_source
        # A pick may pin its own side: the seal set it deliberately, so
        # the overrides below leave it alone and the record says so.
        if p.get("pin_side"):
            seat_src = "seal_pinned"
        elif _side_source == "touch2h":
            s2 = _touch2h_side(sym)
            if s2 is None:
                log(f"{sym}: 2시간 도달률 계산 불가(캐시 없음·오래됨) → "
                    f"봉인 방향({direction})으로 진입")
                seat_src = "no_bars_touch24"
            else:
                if s2 != direction:
                    log(f"{sym}: 방향 교체 {direction} → {s2} "
                        f"(2시간 도달률)")
                direction = s2
        if _side_source == "signal" and not dry and not p.get("pin_side"):
            sig_dir = _signal_side(client, sym)
            if sig_dir is None:
                # Only reachable when bars could not be fetched at all: the
                # escalating vote (1h -> 4h/8h/12h -> RSI) otherwise always
                # returns a side. The seat still trades, on the seal's
                # direction, and the record says the signals never spoke.
                log(f"{sym}: 신호 계산 불가(봉 조회 실패) → 봉인 방향"
                    f"({direction})으로 진입")
                seat_src = "no_bars_touch"
            else:
                direction = sig_dir
        # The operator's rules file may own the side. It answers 'long',
        # 'short', or None; None leaves whatever the rules above decided,
        # so a rules file without this hook changes nothing.
        if (_op_rules is not None and not p.get("pin_side")
                and hasattr(_op_rules, "side_for")):
            try:
                _od = _op_rules.side_for(sym, direction)
            except Exception:                           # noqa: BLE001
                _od = None
            if _od in ("long", "short"):
                if _od != direction:
                    log(f"{sym}: 방향 교체 {direction} → {_od} (운영자 규칙)")
                direction = _od
                seat_src = "op_side"
            else:
                # 왜 침묵했는지 물어본다. 규칙 파일이 안 알려주면 예전처럼
                # 사유 없이 찍는다. 침묵이 캐시가 낡아서인지 판단이 갈려서
                # 인지 로그에서 구분이 안 되던 자리다.
                _r = ""
                if hasattr(_op_rules, "side_reason"):
                    try:
                        _r = str(_op_rules.side_reason() or "")[:120]
                    except Exception:                   # noqa: BLE001
                        _r = ""
                log(f"{sym}: 운영자 방향 규칙 침묵"
                    f"{f' ({_r})' if _r else ''} → {seat_src} 방향"
                    f"({direction}) 유지")
        if _op_rules is not None:
            try:
                _why = _op_rules.entry_veto(sym, direction)
            except Exception:                           # noqa: BLE001
                _why = None
            if _why:
                log(f"{sym}: {_why}")
                continue
        if sym in st["positions"]:
            continue
        if sym in live_syms:
            log(f"{sym}: 거래소에 이미 포지션이 있어 건너뜀 (타 봇/수동?)")
            continue
        m = mkts.get(sym) or {}
        px = float(prices.get(sym, {}).get("mark")
                   or prices.get(sym, {}).get("mid") or 0)
        if px <= 0:
            log(f"{sym}: 가격 조회 실패, 건너뜀")
            attempt_failed += 1
            continue
        # Retry attempts reuse the FIRST attempt's anchor for this seal
        # (audit 0.4.53): re-anchoring each retry at the then-current mark
        # is the "chase the book" style the entry comparison rejected; the
        # style that won waits at one price for the hour. Keyed by seal, so
        # a fresh seal starts a fresh anchor.
        anchors = st.setdefault("entry_anchor", {})
        akey = f"{key}|{sym}"
        if akey in anchors:
            px = float(anchors[akey])
        else:
            anchors[akey] = px
            # prune anchors from older seals so the state never grows
            for old in [k for k in anchors if not k.startswith(f"{key}|")]:
                anchors.pop(old, None)
            save_state(st)
        lev = min(cfg["leverage"], int(m.get("max_leverage") or cfg["leverage"]))
        lot = float(m.get("lot_size") or 0.0001)
        tick = float(m.get("tick_size") or 0.01)
        bonus = 1
        if bonus_left > 0:
            seats_after = max(0, cfg["slots"] - held_start - entered_n - 1)
            if used_slices + 2 + seats_after <= budget_slices:
                sv = _strong_vote(client, sym)
                if sv == direction:
                    bonus = 2
                    bonus_left -= 1
                    seat_src = f"{seat_src}+strong2x"
                    log(f"{sym}: 강신호 일치({sv}) → 빈 특별석 사용, 명목 2배")
            else:
                log(f"{sym}: 증거금 여유 부족, 강신호 보너스 생략하고 1배 "
                    f"진입 (남은 자리 {seats_after} 보호)")
        # The asked-for size is never trimmed to fit (the account holder
        # chose it), but a seat the margin cannot carry must not be tried
        # either: the venue answers 422 and the seal is retried every few
        # minutes, so one unaffordable pick becomes a stream of failures.
        # Stop taking seats for this cycle instead, and say so once.
        # (2026-09-01, live: balance $21 against a $50 seat, four 422s)
        if used_slices + bonus > budget_slices:
            log(f"가용 증거금이 자리 하나(증거금 ${margin_per:,.0f})에 못 "
                f"미쳐 이번 회차는 여기서 멈춘다. 크기는 줄이지 않는다. "
                f"자리가 비거나 입금이 있으면 다음 회차에 들어간다")
            break
        amount = _round_down_to_lot(margin_per * lev * bonus / px, lot)
        if amount <= 0 or amount * px < float(m.get("min_order_size") or 10):
            # Counted as a failed attempt, not as a decision: the account is
            # simply too small for this seat size. Without this the seal was
            # marked used and the same silence repeated every hour, with a
            # single log line as the only trace.
            log(f"{sym}: 최소 주문 미달 (명목 ${amount*px:.2f}), 건너뜀")
            attempt_failed += 1
            too_small += 1
            continue
        mv = p["exp_move_pct"] / 100
        long_ = direction == "long"
        pr = bracket_prices(px, direction, mv, cfg, tick)
        if pr is None:
            notify.send(f"브래킷 스킵 {sym}: 익절/손절가가 틱 반올림 후 "
                        f"진입가와 어긋남 (틱 {tick}, 변동폭 {mv:.4f})")
            continue
        tp_s, sl_s = pr

        if dry:
            log(f"[DRY] {sym} {direction} 명목 ${amount*px:.0f} ({lev}배) "
                f"TP {tp_s} SL {sl_s}")
            entered_n += 1
            used_slices += bonus
            continue
        try:
            # A swallowed failure here used to mean "assume already set", but
            # a 429 and "already isolated" raise the same PacificaError. On
            # any failure, read the settings back and enter only on confirmed
            # isolated margin at the intended leverage. (review H2)
            mode_ok = lev_ok = True
            try:
                client.update_margin_mode(sym, True)          # isolated
            except PacificaError:
                mode_ok = False
            try:
                client.update_leverage(sym, lev)
            except PacificaError:
                lev_ok = False
            if not (mode_ok and lev_ok) and \
                    not _settings_confirmed(client, sym, lev):
                log(f"⚠️ {sym}: 격리마진/{lev}배 설정을 확인하지 못해 진입 "
                    f"생략 (크로스·엉뚱한 레버리지로 열지 않음)")
                notify.send(f"브래킷 스킵 {sym}: 격리마진/레버리지 설정 실패 "
                            f"후 확인 불가, 진입하지 않음")
                attempt_failed += 1
                continue
            # Persist the intent BEFORE the order goes out: a crash between
            # the order and the fill booking below would otherwise leave a
            # live exchange position that no ledger remembers. Startup
            # reconciles this record against the exchange. (review H9)
            st.setdefault("pending_entries", []).append({
                "sym": sym, "dir": direction, "amount": amount,
                "touch_dir": touch_dir, "side_source": seat_src,
                "entry_intent": px, "tp": float(tp_s), "sl": float(sl_s),
                "exp_move_pct": p["exp_move_pct"], "leverage": lev,
                # which statistic sized this trade, so the 09-01 switchover
                # can be graded later rather than argued about. The SEAL's
                # value wins: exp_move_pct came from the seal, and the tag
                # has to name what produced that number. PRED_INPUT_TAG is
                # only this process's current setting, which is a different
                # thing the moment the setting changes mid-book.
                "pred_input": p.get("pred_input") or PRED_INPUT_TAG,
                "at": _now().isoformat(), "seal": key,
                "trade_rank": p.get("trade_rank"),
                # why the seal picked this trade; carried into the closed
                # record so grading can be read against the original thesis
                "basis": p.get("basis"),
            })
            save_state(st)
            # SL as trigger-limit with a buffer: trigger at sl_s, limit a bit
            # worse, so the fill is near-certain while slippage is capped.
            # Buffer 0 keeps the stop a pure market trigger.
            sl_lim = ""
            if _sl_maker:
                # Trigger and limit at the same price: the stop sells where
                # we said or it waits. A take profit rests in the book from
                # the start and is lifted at its price; a stop is created
                # only once price arrives and takes whatever is left, and
                # that difference, not the fee, is what a stop costs. This
                # refuses to pay it. The cost is that the order can be left
                # behind, and what the chase below does not catch runs to
                # the expiry exit. (08-27 decision)
                sl_lim = _round_to_tick(float(sl_s), tick)
            elif _sl_buf > 0:
                _slb = float(sl_s) * (1 - _sl_buf * mv) if long_                     else float(sl_s) * (1 + _sl_buf * mv)
                sl_lim = _round_to_tick(_slb, tick)
            if _entry_limit:
                # Resting limit at the same anchor the brackets were built
                # on. Fills are maker; a runaway price means no fill, and
                # after _entry_wait seconds the order is cancelled and the
                # seat is skipped this cycle. Missing a runner is accepted:
                # the pick was priced at px, not at wherever it ran to.
                _res = client.create_limit_order(
                    sym, "bid" if long_ else "ask", str(amount),
                    _round_to_tick(px, tick), tif="GTC",
                    builder_code=policy.get("builder_code", ""),
                    take_profit_price=tp_s, stop_loss_price=sl_s,
                    take_profit_limit=_tp_limit,
                    stop_loss_limit_price=sl_lim)
                _oid = (_res or {}).get("order_id")
                _deadline = time.time() + _entry_wait
                _filled = False
                while time.time() < _deadline:
                    # 10s, not 5: eight seats polling serially at 5s were
                    # up to 192 position reads per cycle and half an hour's
                    # cycle could spend 16 minutes waiting (review 15-3);
                    # the wait itself is bracket_entry_wait_sec (operator
                    # shortened to 60s the same day).
                    time.sleep(10)
                    try:
                        if any(pp.get("symbol") == sym
                               for pp in client.get_positions()):
                            _filled = True
                            break
                    except PacificaError:
                        pass
                if not _filled:
                    # A failed cancel does NOT mean the order is gone. On
                    # 2026-08-27 a rate limit ate one cancel, the bot logged
                    # "cancelled, skipping" and cleared the pending record,
                    # and the order it had not cancelled filled six seconds
                    # later. That position was on the exchange for three
                    # hours with no take profit, no stop and no owner: the
                    # ledger did not hold it, so the watcher never expired it
                    # and every later cycle skipped the symbol as "someone
                    # else's". It ended down 4%.
                    #
                    # So: try the cancel more than once, and only forget the
                    # intent when the venue actually confirmed the cancel.
                    # An unconfirmed cancel keeps the pending record, which
                    # is what reconcile_pending reads at the top of the next
                    # cycle to adopt whatever appeared. Keeping a record for
                    # an order that truly never filled costs nothing, since
                    # reconcile drops it after its grace window.
                    _cancelled = False
                    for _try in range(3):
                        try:
                            client.cancel_order(sym, order_id=_oid)
                            _cancelled = True
                            break
                        except PacificaError as _ce:
                            log(f"{sym}: 진입 취소 실패 {_try + 1}/3 ({_ce})")
                            time.sleep(2)
                    try:
                        _filled = any(pp.get("symbol") == sym
                                      for pp in client.get_positions())
                    except PacificaError:
                        pass
                    if not _filled:
                        if _cancelled:
                            _clear_pending(st, sym)
                        else:
                            log(f"{sym}: 취소를 확인하지 못했다. 주문이 살아 "
                                f"있을 수 있어 미결 기록을 남기고, 다음 "
                                f"회차가 체결분을 입양한다")
                        _mk = f"{key}|{sym}"
                        _misses[_mk] = _misses.get(_mk, 0) + 1
                        # prune misses from older seals so state stays small
                        for _old in [k for k in _misses
                                     if not k.startswith(f"{key}|")]:
                            _misses.pop(_old, None)
                        save_state(st)
                        log(f"{sym}: 지정가 진입 미체결 {_entry_wait}s "
                            f"({_misses[_mk]}/{_entry_max_tries}회), "
                            f"이번 자리 건너뜀")
                        attempt_failed += 1
                        continue
            else:
                client.create_market_order(
                    sym, "bid" if long_ else "ask", str(amount), "0.5",
                    builder_code=policy.get("builder_code", ""),
                    take_profit_price=tp_s, stop_loss_price=sl_s,
                    take_profit_limit=_tp_limit,
                    stop_loss_limit_price=sl_lim)
            # read back the fill so the record holds reality, not intent.
            # a few retries (BR5); if it still cannot be read, record the
            # intent but say so, and let later loops repair it.
            fill, confirmed = px, False
            for _ in range(3):
                time.sleep(2)
                try:
                    _poss = client.get_positions()
                except PacificaError:
                    _poss = []          # order is out; keep recording intent
                for pos in _poss:
                    if pos.get("symbol") == sym:
                        fill = float(pos.get("entry_price") or px)
                        confirmed = True
                        break
                if confirmed:
                    break
            # The order allows 0.5% slippage while the TP can sit less than
            # that away, so a bad fill can land beyond its own bracket. A
            # bracket invalid against the real fill (inverted TP/SL) is
            # closed at market immediately, never held. The check runs even
            # when the fill could not be read back (px is then the best
            # estimate); an unconfirmed fill is additionally flagged so the
            # next watch loop re-reads the real entry and re-validates once.
            # (review H3, N5)
            fill_ok = (float(sl_s) < fill < float(tp_s)) if long_ \
                else (float(tp_s) < fill < float(sl_s))
            if not fill_ok:
                msg = (f"브래킷 역전 {sym}: 체결가 {fill} 가 TP {tp_s} / "
                       f"SL {sl_s} 범위 밖, 즉시 시장가 청산")
                log("⚠️ " + msg)
                try:
                    # Close what the exchange actually holds, exactly like
                    # close_market does; the intended amount is only the
                    # fallback when the read fails. (review N8)
                    close_amt = amount
                    try:
                        for _lp in client.get_positions():
                            if _lp.get("symbol") == sym:
                                close_amt = abs(float(_lp.get("amount")
                                                      or amount))
                                break
                    except (PacificaError, TypeError, ValueError):
                        pass
                    client.create_market_order(
                        sym, "ask" if long_ else "bid", str(close_amt),
                        "0.5", reduce_only=True,
                        builder_code=policy.get("builder_code", ""))
                    notify.send(msg)
                    # Book the round trip so the breaker average sees it:
                    # two taker fills cost about 0.16% of notional, a
                    # deliberately conservative estimate in the same
                    # percent-of-notional units every other closed record
                    # uses. (review N3)
                    st["closed"].append(_close_row(
                        sym, {**p, "dir": direction,
                              "opened_at": _now().isoformat()},
                        -0.16, "역전 즉시청산"))
                    _clear_pending(st, sym)     # round trip fully booked (H9)
                    save_state(st)
                    continue        # opened and closed, booked above
                except PacificaError as e:
                    notify.send(msg + f" ...청산 주문도 실패"
                                f"({str(e)[:80]}), 포지션으로 기록해 "
                                f"감시를 계속합니다")
                    # fall through: the record below keeps it watched and
                    # watch_positions force-closes on either line breach
            st["positions"][sym] = {
                "dir": direction, "amount": amount, "entry_intent": px,
                "entry_fill": fill, "fill_confirmed": confirmed,
                "tp": float(tp_s), "sl": float(sl_s),
                "exp_move_pct": p["exp_move_pct"], "leverage": lev,
                # which statistic sized this trade, so the 09-01 switchover
                # can be graded later rather than argued about. The SEAL's
                # value wins: exp_move_pct came from the seal, and the tag
                # has to name what produced that number. PRED_INPUT_TAG is
                # only this process's current setting, which is a different
                # thing the moment the setting changes mid-book.
                "pred_input": p.get("pred_input") or PRED_INPUT_TAG,
                "opened_at": _now().isoformat(), "seal": key,
                "trade_rank": p.get("trade_rank"),
                "basis": p.get("basis"),
                "seat_priority": bool(p.get("seat_priority")),
            }
            if not confirmed:
                # watch_positions re-reads the real entry once and
                # re-validates the bracket against it (review N5)
                st["positions"][sym]["recheck_fill"] = True
            _clear_pending(st, sym)     # intent became a booked position (H9)
            save_state(st)          # persist each fill immediately (BR7)
            # Trust but verify: is the TP/SL really on the exchange? Nothing
            # here changes what watch_positions does: its line checks cover
            # EVERY position, protected or not. The unprotected flag only
            # feeds status() and the alert, so the user can see which
            # positions have no exchange-side lines. Re-attaching is done
            # ONLY when the symbol has no open orders at all: if orders
            # exist but none matched the expected type fields, they may BE
            # the bracket under unknown field names, and a blind re-attach
            # could stack a duplicate stop. (review H1, N2, N6)
            has_tp = has_sl = checked = False
            sym_orders: list = []
            try:
                has_tp, has_sl, sym_orders = _exchange_brackets(client, sym)
                checked = True
            except PacificaError:
                pass
            if checked and not (has_tp and has_sl):
                if not sym_orders:
                    try:
                        client.set_position_tpsl(
                            sym, "bid" if long_ else "ask",
                            take_profit_price="" if has_tp else tp_s,
                            stop_loss_price="" if has_sl else sl_s,
                            take_profit_limit=_tp_limit,
                            stop_loss_limit_price="" if has_sl else sl_lim)
                        has_tp, has_sl, sym_orders = \
                            _exchange_brackets(client, sym)
                    except PacificaError:
                        pass
                else:
                    _log_raw_order_once(sym, sym_orders)
            if not checked or not (has_tp and has_sl):
                st["positions"][sym]["unprotected"] = True
                save_state(st)
                what = "조회 실패로 확인 불가" if not checked else (
                    f"TP {'있음' if has_tp else '없음'} / "
                    f"SL {'있음' if has_sl else '없음'}")
                msg = (f"브래킷 미보호 {sym}: 거래소 TP/SL {what}. "
                       f"봇이 매 회차 감시해 손절선 이탈 시 직접 청산합니다.")
                log("⚠️ " + msg)
                notify.send("⚠️ " + msg)
            entered_n += 1
            used_slices += bonus
            slip = (fill - px) / px * 100 * (1 if long_ else -1)
            opened.append(f"{sym} {direction} {lev}배 체결 {fill} "
                          f"(슬리피지 {slip:+.3f}%"
                          f"{'' if confirmed else ', 체결가 미확인'})")
        except PacificaError as e:
            notify.send(f"브래킷 진입 실패 {sym}: {str(e)[:120]}")
            attempt_failed += 1
    if dry:
        _dry_logged.add(key)
    else:
        # Consume the seal only when at least one entry went out, or when
        # every pick was skipped deliberately. If nothing was entered and
        # every attempt failed on transient errors (429, network, feed), the
        # seal stays open so the next loop retries it inside its 6h window;
        # the old unconditional append burned the whole day's picks on one
        # bad minute of API weather.
        if entered_n == 0 and too_small == len(picks) and picks:
            # One key, not one per seal. The condition is a property of the
            # account, not of the hour's picks, so keying on the seal added an
            # entry to `warned` every hour, twenty-four a day, forever. The
            # six-hour cooldown gives the repetition that was wanted.
            _warn_once(st, "too_small",
                       f"진입 0건: 계좌가 이 자리 크기에 못 미칩니다 "
                       f"(픽 {len(picks)}개 전부 최소 주문 미달). "
                       f"입금하거나 픽당 금액을 낮추세요.")
        if attempt_failed == 0:
            st["entered_seals"].append(key)
        else:
            # Unfilled picks get retried at the SAME anchor while this seal
            # is current (every ENTRY_RETRY_COOLDOWN_SEC), not once per
            # hour. Before 2026-08-25 a single fill marked the whole seal
            # consumed, so every 60s-cancelled sibling lost its hour, while
            # the entry style this bot uses assumes the full hour of
            # chances at the anchor. Same price, same maker fee,
            # no chasing: only more attempts. Held symbols are skipped by
            # the position check, so retries touch only the unfilled.
            global _retry_not_before
            _retry_not_before = time.time() + ENTRY_RETRY_COOLDOWN_SEC
            log(f"봉인 미소진: 진입 {entered_n}건, 미체결 {attempt_failed}건, "
                f"{ENTRY_RETRY_COOLDOWN_SEC // 60}분 뒤 같은 기준가로 재시도")
        save_state(st)
        if opened:
            notify.send("브래킷 진입:\n" + "\n".join(opened))


def realized_close(client, sym: str, since_ms: int) -> dict | None:
    """The exchange's own record of how a position ended (BR3/BR11).

    positions/history carries pnl in dollars and a cause, including
    "liquidation", so grading and the liquidation halt rest on facts rather
    than a mark-price guess.

    Returns None only when the history genuinely holds no close for this
    position. A failed or unparseable query raises PacificaError instead:
    swallowing it here let a 429 on the same cycle as a real liquidation
    silently disable the liquidation halt. (review H7)

    Opening rows count too. The venue reports every row with cause "normal"
    (100 of 100 on 08-21), so the cause branch never fired and only close
    rows were summed. But an open row's pnl IS the opening fee as a negative
    number, and dropping it made every trade look better than it was by one
    side of the round trip. The round trip is both sides, not one.

    The cause and price still come from the last close, because an open row
    describes an entry, not an ending.

    The window ends at this position's own close. since_ms opens it, but the
    same symbol is often re-entered minutes later, and those rows belong to
    the next position: counting them pulled a second opening fee in and
    doubled the fee charged against a single round trip.
    """
    # limit, or the venue returns its default 100 rows, which on 08-21 was
    # only two days: eight slots refilling hourly reach that quickly, and
    # a position whose close fell off the end grades as an estimate.
    # 200 covered seven weeks on the same account.
    rows = client._get("positions/history",
                       {"account": client.address, "limit": 200})
    try:
        seq = []
        for h in rows:
            if h.get("symbol") != sym:
                continue
            t = int(h.get("created_at") or 0)
            if t < since_ms:
                continue
            side = str(h.get("side", ""))
            cause = h.get("cause") or ""
            ending = (side.startswith("close")
                      or cause in ("take_profit", "stop_loss", "liquidation"))
            if ending or side.startswith("open"):
                seq.append((t, float(h.get("pnl") or 0), ending, cause,
                            float(h.get("price") or 0)))
    except Exception as e:
        raise PacificaError(
            f"positions/history 파싱 실패 ({type(e).__name__}: {e})") from e
    seq.sort()
    total, last = 0.0, None
    for t, pnl, ending, cause, price in seq:
        if last is not None and not ending:
            break                       # a re-entry: the next position's row
        total += pnl
        if ending:
            last = (cause, price)
    if last is None:
        return None
    return {"pnl_usd": total, "cause": last[0], "price": last[1]}


def _flat_pct(client, sym: str, pos: dict, since_ms: int) -> float:
    """Percent for a position closed by --close-all, read from the venue.

    Booking these at 0.0 kept the count honest and the number a lie, and it
    is the same number the running average warns on.
    """
    try:
        real = realized_close(client, sym, since_ms)
        notional = float(pos.get("entry_fill") or 0) * float(pos.get("amount") or 0)
        if real and notional > 0:
            return round(float(real["pnl_usd"]) / notional * 100, 3)
    except Exception:                                       # noqa: BLE001
        pass
    return 0.0


def close_limit(client, policy, sym: str, pos: dict, live: dict,
                mark: float, tick: float):
    """Rest a reduce-only limit at the mark and return its order id.

    Priced one tick to our side of the mark so the order rests instead of
    crossing: a sell sits a tick above, a buy a tick below. That is the
    whole point, since an exit that crosses pays the taker fee this
    replaces. Unfilled orders are cancelled and re-posted at the refreshed
    mark by the caller.
    """
    amt = pos["amount"]
    lp = live.get(sym)
    if lp:
        try:
            amt = abs(float(lp.get("amount") or amt))
        except (TypeError, ValueError):
            pass
    long_ = pos["dir"] == "long"
    px = mark + tick if long_ else mark - tick
    if px <= 0:
        px = mark
    res = client.create_limit_order(
        sym, "ask" if long_ else "bid", str(amt),
        _round_to_tick(px, tick), tif="GTC", reduce_only=True,
        builder_code=policy.get("builder_code", ""))
    return (res or {}).get("order_id")


def _chase_stop_maker(client, policy, st, sym: str, pos: dict, live: dict,
                      mark: float) -> None:
    """Follow the price with a resting order instead of paying to catch it.

    The stop-limit sits at the line and fills there or not at all. When the
    market leaves it behind, this puts a reduce-only limit one tick to our
    side of the mark and moves it every pass, so the exit keeps asking for
    a price we chose rather than taking whatever the book has.

    The exchange stop is deliberately NOT cancelled. Both orders are
    reduce-only, so they cannot between them flip the position, and leaving
    it alone means a price that comes back finds the stop still standing.
    Cancelling would trade a fill we might get for a position with no stop
    at all. (08-27 user decision: maker, always, and keep re-posting)
    """
    ch = pos.get("stop_chase") or {}
    tick = _tick_of(client, sym)
    oid = ch.get("oid")
    # Only move an order that is in the wrong place. Cancelling and
    # re-posting at the price it already sits at is two signed writes that
    # change nothing, and at a two second cadence that is a write a second
    # for as long as the chase lasts. Rate limits are real here: two 429s
    # today, and one of them cost a position its protection on 08-27. Price
    # standing still is exactly when the order needs no help.
    # (08-28 review A4)
    if oid is not None:
        try:
            _same = (_round_to_tick(mark, tick)
                     == _round_to_tick(float(ch.get("px") or 0), tick))
        except (TypeError, ValueError):
            _same = False
        # Standing still is only a reason to leave the order alone if the
        # order is still there. It can go while the mark does not move: the
        # exchange stop taking part of the position leaves this one over the
        # reduce-only size, and a venue that cancels it takes the chase with
        # it. Returning on price alone would end the chase silently, with
        # tries frozen so the thirty-miss warning never fires either, which
        # is the exact case that warning exists for. (08-28 review A6)
        if _same and _order_alive(client, oid):
            return
        try:
            client.cancel_order(sym, order_id=oid)
        except PacificaError:
            # Filled in the race, or never there. Only the book can tell the
            # two apart, and a live order must not be doubled.
            if _order_alive(client, oid):
                return
    tries = int(ch.get("tries", 0)) + 1
    new_oid = close_limit(client, policy, sym, pos, live, mark, tick)
    pos["stop_chase"] = {"oid": new_oid, "at": _now().isoformat(),
                         "px": mark, "tries": tries}
    save_state(st)
    if tries == 1:
        log(f"{sym}: 손절 지정가가 안 채워졌다. 마크({mark})에 지정가를 붙여 "
            f"따라간다. 거래소 손절은 그대로 살아 있다")
    elif tries % 30 == 0:
        log(f"{sym}: 손절 추격 지정가 {tries}회째 미체결, 계속 따라간다")
    # The chase has no retry limit on purpose: nothing on the way out is
    # allowed to take the market. The cost of that decision is a position
    # that keeps not selling while only the log says so, and a log nobody
    # reads is not a warning. Once, per position, when it has clearly
    # stopped being a moment's delay. (08-28 review A5)
    if tries >= 30:
        _warn_once(st, f"chase_long:{sym}",
                   f"{sym}: 손절 추격 지정가가 {tries}회 연속 미체결입니다. "
                   f"값이 계속 달아나고 있어 아직 못 팔았습니다. 시장가로 "
                   f"정리하려면 봇에게 말하세요.")


def _early_cut(client, policy, st, sym: str, pos: dict, live: dict,
               mark: float, adv: float, cut: float,
               quiet: bool = False) -> None:
    """Rest a reduce-only limit at the mark and follow it until it fills.

    0.4.64 made this take the market, on the argument that an order posted
    on the far side of a running market asks the market to come back. That
    measurement predated the lateness fixes that followed it: the cut then
    waited for the next cycle, so what was charged to the limit order
    included the cost of firing late.

    With the lateness gone, the market order still slips, by well more than
    the headroom the threshold was measured to have. A taker exit costs
    more than the threshold is worth.

    So it rests again, the same machinery as the stop chase and for the
    same reason: nothing on the way out takes the market. The failure mode
    0.4.64 named is real and unguarded here: an order that never fills
    leaves the position to the exchange stop. That stop is untouched
    throughout, so the position is never unprotected, only exited worse
    than intended. Thirty unfilled tries raise a warning.
    """
    ch = pos.get("early_cut") or {}
    if ch.get("done"):
        # left over from the market version across a restart; start a chase
        ch = {}
        pos.pop("early_cut", None)
    tick = _tick_of(client, sym)
    oid = ch.get("oid")
    if oid is not None:
        try:
            if _round_to_tick(mark, tick) == _round_to_tick(
                    float(ch.get("px") or 0), tick) and _order_alive(
                        client, oid):
                return                   # already resting in the right place
        except (TypeError, ValueError):
            pass
        try:
            client.cancel_order(sym, order_id=oid)
        except PacificaError:
            if _order_alive(client, oid):
                return
    tries = int(ch.get("tries", 0)) + 1
    new_oid = close_limit(client, policy, sym, pos, live, mark, tick)
    pos["early_cut"] = {"oid": new_oid, "at": _now().isoformat(),
                        "px": mark, "tries": tries}
    save_state(st)
    if tries == 1 and not quiet:
        log(f"{sym}: 진입가 대비 {adv:.2f}% 밀렸다(문턱 {cut}%). 손절선까지 "
            f"기다리지 않고 마크({mark})에 지정가를 걸어 정리한다. "
            f"거래소 손절은 그대로 살아 있다")
    elif tries % 30 == 0:
        log(f"{sym}: 조기 정리 지정가 {tries}회째 미체결, 계속 따라간다")
    if tries >= 30:
        _warn_once(st, f"early_long:{sym}",
                   f"{sym}: 조기 정리 지정가가 {tries}회 연속 미체결입니다. "
                   f"아직 못 팔았고 거래소 손절만 남아 있습니다.")

def _drop_stop_chase(client, st, sym: str, pos: dict) -> None:
    """Price came back over the line. Take the chaser off and forget it."""
    ch = pos.pop("stop_chase", None)
    pos.pop("sl_missed_at", None)
    if ch and ch.get("oid") is not None:
        try:
            client.cancel_order(sym, order_id=ch["oid"])
        except PacificaError:
            pass
    log(f"{sym}: 값이 손절선 위로 돌아왔다. 추격 지정가를 거둔다")
    save_state(st)


def _drop_early_cut(client, st, sym: str, pos: dict) -> None:
    """Price recovered past the cut. Take the resting order off.

    Without this a position that dipped, was marked for an early exit, and
    then came back would keep a reduce-only order sitting at the price it
    dipped to, and sell there on the next wobble. (08-28)
    """
    ch = pos.pop("early_cut", None)
    if not ch:
        return
    if ch.get("oid") is not None:
        try:
            client.cancel_order(sym, order_id=ch["oid"])
        except PacificaError:
            pass
    log(f"{sym}: 값이 조기 정리 문턱 위로 돌아왔다. 조기 정리 표시를 지운다")
    save_state(st)


def _cancel_resting(client, sym: str, pos: dict) -> None:
    """Take our own resting orders off the book before a market close.

    Two of them can be alive at once: an expiry limit this bot posted, and
    the stop-limit the exchange created when the trigger fired. Both are
    reduce-only, so the venue usually drops them with the position, but the
    ledger has not trusted "usually" since 08-26 and neither does this.
    Errors are swallowed on purpose: an order that cannot be cancelled is
    almost always one that is already gone. (08-27)
    """
    for _key, _tag in (("exit_limit", "만기"), ("stop_chase", "손절 추격"),
                       ("early_cut", "조기 정리")):
        _o = (pos.get(_key) or {}).get("oid")
        if _o is None:
            continue
        try:
            client.cancel_order(sym, order_id=_o)
            log(f"{sym}: 남아 있던 {_tag} 지정가를 취소했다")
        except PacificaError:
            pass
    try:
        for o in client.get_open_orders():
            if o.get("symbol") != sym:
                continue
            if not str(o.get("order_type", "")).startswith("stop"):
                continue
            client.cancel_order(sym, order_id=o.get("order_id"))
            log(f"{sym}: 남아 있던 손절 지정가를 취소했다")
    except PacificaError:
        pass


def close_market(client, policy, sym: str, pos: dict, live: dict,
                 slippage: str = "0.5") -> None:
    # close what the exchange actually holds, not what the ledger remembers
    # (BR6: partial fills or partial closes make the two diverge)
    amt = pos["amount"]
    lp = live.get(sym)
    if lp:
        try:
            amt = abs(float(lp.get("amount") or amt))
        except (TypeError, ValueError):
            pass
    side = "ask" if pos["dir"] == "long" else "bid"
    client.create_market_order(sym, side, str(amt), slippage, reduce_only=True,
                              builder_code=policy.get("builder_code", ""))


def watch_positions(client, policy, st, cfg, dry: bool) -> None:
    """Expiries, stuck exits, and disappeared positions, every loop."""
    if dry:
        # a dry run observes nothing it did not open, and must never write
        # closed records or flip the halt flag (BR4)
        return
    live = {p.get("symbol"): p for p in client.get_positions()}
    prices = {p["symbol"]: p for p in client.get_prices()}

    for sym in list(st["positions"]):
        pos = st["positions"][sym]
        opened = dt.datetime.fromisoformat(pos["opened_at"])
        mark = float(prices.get(sym, {}).get("mark")
                     or prices.get(sym, {}).get("mid") or 0)
        long_ = pos["dir"] == "long"
        entry = pos["entry_fill"]
        notional = entry * pos["amount"] if entry > 0 else 0

        if sym not in live:
            since = _since_open(pos)
            # A history query failure defers grading to the next loop rather
            # than falling straight to the estimate, so a real liquidation
            # still reaches the halt check once the query recovers. Only
            # after 3 straight failures does the estimate take over, loudly.
            # (review H7)
            try:
                real = realized_close(client, sym, since)
                pos.pop("grade_fails", None)
            except PacificaError as e:
                fails = int(pos.get("grade_fails", 0)) + 1
                pos["grade_fails"] = fails
                log(f"⚠️ 실현손익 조회 실패 {fails}회 {sym}: {e}")
                if fails < 3:
                    save_state(st)
                    continue
                notify.send(f"브래킷: {sym} 실현손익 조회 {fails}회 연속 실패, "
                            f"추정 채점으로 내려갑니다 (청산 여부 판정 불가)")
                real = None
            tp_d, sl_d = _recorded_distances(pos, cfg)
            if tp_d is None and not (real and notional > 0):
                # Distances are only needed to cap an estimate and to judge
                # slippage. When the venue has given a real number neither is
                # in play, so refusing to grade here would discard the most
                # accurate close we get. Defer only when the estimate is what
                # is left, and nothing writes a position without these fields
                # anyway, so this means a damaged record.
                log(f"⚠️ {sym}: 브래킷 거리를 못 읽어 채점을 미룹니다 "
                    f"(tp/sl/진입가·예상변동 없음)")
                continue
            if real and notional > 0:
                est = real["pnl_usd"] / notional * 100
                cause = real["cause"]
                if cause in ("", "normal"):
                    # The venue reports every close as "normal", so a label made from
                    # the sign of the result is a guess, and it was being read as fact:
                    # a hand-closed loss became a "stop", which then fed the stop-chase
                    # block and the slippage counter. The price decides when it can,
                    # and the guess is named as one when it cannot.
                    px_close = float(real.get("price") or 0)
                    ref_tp, ref_sl = pos.get("tp"), pos.get("sl")
                    hit = ""
                    if px_close and ref_tp and ref_sl:
                        tp_v, sl_v = float(ref_tp), float(ref_sl)
                        # Past the line counts as that line: a stop that
                        # slipped 1%p is still a stop, and it is precisely
                        # the slipped ones the counter needs to see.
                        if pos["dir"] == "long":
                            hit = ("take_profit" if px_close >= tp_v
                                   else "stop_loss" if px_close <= sl_v else "")
                        else:
                            hit = ("take_profit" if px_close <= tp_v
                                   else "stop_loss" if px_close >= sl_v else "")
                    cause = hit or ("추정:이익" if est >= 0 else "추정:손실")
                    if not hit and pos.get("exit_limit"):
                        # Our own resting expiry order is what filled: it
                        # sits one tick off the mark, far inside both
                        # bracket lines, so "hit" is empty by construction.
                        # Labelled so expiry exits can be graded on
                        # their own later.
                        cause = "만기:지정가"
                if cause == "liquidation" and HALT_ON_LIQUIDATION:
                    # Warned, not halted: every position carries an
                    # exchange-side stop, so a liquidation means that line
                    # was jumped rather than that the bot is unsupervised.
                    _warn_once(st, f"liq:{sym}",
                               f"거래소 청산 확인: {sym} ({est:+.2f}%). "
                               f"손절선을 건너뛴 체결입니다. 매매는 계속합니다.")
                # sl_d can be None here now that a venue-priced close grades
                # without it; the slippage question simply cannot be asked
                # for a record that lost its geometry.
                if (cause == "stop_loss" and sl_d is not None
                        and est < -sl_d - DEMOTE_SLIP_PCT):
                    st["slip_events"] = st.get("slip_events", 0) + 1
                    notify.send(f"브래킷 경고: {sym} 손절 이탈 "
                                f"({est:+.2f}% vs -{sl_d:.2f}%) "
                                f"[{st['slip_events']}회]")
            else:
                # history unavailable: fall back to the mark, and err to the
                # stop, never the target (the old guess scored anything a hair
                # above entry as a full TP win and fed that into the breaker)
                move = (mark / entry - 1) * 100 * (1 if long_ else -1) \
                    if mark > 0 and entry > 0 else 0.0
                est = min(move, tp_d) if move > 0 else max(move, -sl_d)
                cause = "추정(이력 조회 실패)"
            st["closed"].append(
                _close_row(sym, pos, round(est, 3), cause))
            # An expiry limit may still be resting when the exchange bracket
            # is what actually closed the position. A reduce-only order with
            # nothing to reduce is usually dropped by the venue, but leaving
            # one on the book means the next entry in this symbol could meet
            # its own stale exit. (2026-08-26)
            # A stop chase leaves the same kind of leftover as an expiry
            # limit, so both come off here. (08-27)
            _cancel_resting(client, sym, pos)
            del st["positions"][sym]
            save_state(st)
            notify.send(f"브래킷 청산: {sym} {est:+.2f}% ({cause})")
            continue

        # A fill that could not be read back at entry gets one re-validation
        # here: read the real entry price from the live position and, if it
        # inverts the bracket, close at market now instead of holding a
        # broken position to expiry. One shot: the flag is cleared whether
        # or not the read succeeds, the line checks below cover the rest.
        # (review N5)
        if pos.get("recheck_fill"):
            pos.pop("recheck_fill", None)
            try:
                real_entry = float((live.get(sym) or {})
                                   .get("entry_price") or 0)
            except (TypeError, ValueError):
                real_entry = 0.0
            if real_entry > 0:
                pos["entry_fill"] = real_entry
                pos["fill_confirmed"] = True
                entry = real_entry
                ok = (pos["sl"] < entry < pos["tp"]) if long_ \
                    else (pos["tp"] < entry < pos["sl"])
                if not ok:
                    msg = (f"브래킷 역전(재검증) {sym}: 체결가 {entry} 가 "
                           f"TP {pos['tp']} / SL {pos['sl']} 범위 밖, "
                           f"즉시 시장가 청산")
                    log("⚠️ " + msg)
                    try:
                        since_ms = _since_open(pos)
                        close_market(client, policy, sym, pos, live)
                        move, tag = _booked_pct(client, sym, pos, mark,
                                                since_ms)
                        st["closed"].append(
                            _close_row(sym, pos, move,
                                       "역전 즉시청산"
                                       + (":" + tag if tag else "")))
                        del st["positions"][sym]
                        save_state(st)
                        notify.send(msg)
                        continue
                    except PacificaError as e:
                        notify.send(msg + f" ...청산 실패({str(e)[:80]}), "
                                    f"감시를 계속합니다")
            save_state(st)

        held_h = (_now() - opened).total_seconds() / 3600
        # A live position past EITHER line means the exchange bracket did not
        # fire (missing or dead). The stop side is the one that bleeds, so it
        # is checked too, not only the TP. (review H1)
        hit_tp = mark > 0 and ((long_ and mark >= pos["tp"])
                               or (not long_ and mark <= pos["tp"]))
        hit_sl = mark > 0 and ((long_ and mark <= pos["sl"])
                               or (not long_ and mark >= pos["sl"]))
        if mark > 0 and not hit_sl and (pos.get("sl_missed_at")
                                        or pos.get("stop_chase")):
            # Price came back above the line on its own. The clock and the
            # chaser both go with it, or a later dip inherits a countdown
            # that already expired and a resting order at a stale price.
            _drop_stop_chase(client, st, sym, pos)
        # Leave before the stop, at our own price, once the trade has gone
        # far enough against us that it is almost certainly lost anyway.
        #
        # Measured 08-28 over several thousand replayed trades that fell
        # this far: about one in eight still reaches its take profit, more
        # than half go on to the stop, and the rest expire. Cutting them
        # all at this line throws that one win away and still comes out
        # ahead per ten, because the stops leave here instead of at the
        # stop line, which sits far wider. It wins at every expiry cost
        # tested, from a take profit's fee up to ten times it, and in both
        # windows.
        #
        # The exchange stop stays exactly where it was. This is a second,
        # nearer exit, not a narrower stop: measured as a narrower stop
        # (geometry 0.85) the same distance LOSES, because a triggered stop
        # concedes the spread and this does not. What is not yet known is
        # whether it truly fills as a maker; nothing has run live.
        # (08-28 user decision)
        # a position marked for requested close: close at the mark and
        # follow, the early-cut machinery with its own words
        if pos.get("evict_req") and mark > 0 and not hit_tp and not hit_sl:
            if not pos.get("early_cut"):
                log(f"{sym}: 자리 요청에 따라 마크({mark})에 지정가를 걸고 "
                    f"따라간다. 거래소 손절은 살려 둔다")
            try:
                _early_cut(client, policy, st, sym, pos, live, mark,
                           0.0, 0.0, quiet=True)
            except PacificaError as e:
                log(f"{sym}: 자리 양보 청산 지정가 실패({str(e)[:80]}), "
                    f"다음 회에 다시 건다")
            continue
        _cut = float(cfg.get("early_cut_pct", 0) or 0)
        # 09-04 user instruction: hand the cut to the exchange, do not run
        # it here. When early_cut_pct is on, bracket_prices puts the
        # exchange stop at exactly that distance (_sl_d = _cut), so this
        # loop cut can only race the exchange to the same line, never
        # reach it earlier. The exchange already holds it, with a trigger
        # limit and the chase, so the loop stands down.
        #
        # It is a distance test rather than a flat switch because a
        # position opened before the change may still carry a stop set
        # from sl_mult x mv, which sits farther out. There the loop cut is
        # what it was built to be: an exit that happens before the stop.
        _stop_d = 0.0
        if entry > 0 and pos.get("sl"):
            _stop_d = abs(float(pos["sl"]) / entry - 1) * 100
        if _cut > 0 and _stop_d and _cut >= _stop_d - 1e-9:
            _cut = 0.0          # 거래소 손절이 이미 그 선에 있다
        if (_cut > 0 and mark > 0 and not hit_tp and not hit_sl
                and entry > 0):
            _adv = ((entry - mark) / entry * 100 if long_
                    else (mark - entry) / entry * 100)
            if _adv < _cut and pos.get("early_cut"):
                _drop_early_cut(client, st, sym, pos)
            if _adv >= _cut:
                try:
                    _early_cut(client, policy, st, sym, pos, live, mark,
                               _adv, _cut)
                except PacificaError as e:
                    log(f"{sym}: 조기 정리 지정가 실패({str(e)[:80]}), "
                        f"다음 회에 다시 건다. 거래소 손절은 살아 있다")
                continue
        if held_h >= cfg["horizon_h"] or hit_tp or hit_sl:
            # A crossed line outranks the expiry. Before the limit exit
            # existed both causes ended in the same market close so the
            # order did not matter; now "만기" rests an order instead, and
            # a position that has jumped its stop with the exchange order
            # dead must not be left resting. (2026-08-26)
            why = ("손절 트리거 잔류" if hit_sl
                   else "익절 트리거 잔류" if hit_tp else "만기")
            # With a maker stop the order rests AT the line, so price being
            # through it is not yet evidence of anything: the limit may be
            # filling this second. Give it the grace it was put there for,
            # and then chase it with another resting order rather than
            # sweeping the book. Nothing here ever takes liquidity. The
            # clock starts on the record so a restart cannot reset it.
            # (08-27 user decision)
            if hit_sl and bool(policy.get("bracket_sl_maker", False)):
                _grace = max(1, int(policy.get("bracket_sl_chase_sec", 2)
                                    or 2))
                _seen = pos.get("sl_missed_at")
                if not _seen:
                    pos["sl_missed_at"] = _now().isoformat()
                    save_state(st)
                    log(f"{sym}: 값이 손절선을 지났다. 걸어둔 손절 지정가가 "
                        f"제 가격에 채워지는지 {_grace}초 기다린다")
                    continue
                try:
                    _waited = (_now() - dt.datetime.fromisoformat(
                        _seen)).total_seconds()
                except (TypeError, ValueError):
                    # A stamp this cannot read would raise here every pass
                    # while the watcher between cycles keeps
                    # coming back to it, which is a loop that never closes
                    # the position. An unreadable clock is restarted rather
                    # than trusted.
                    pos["sl_missed_at"] = _now().isoformat()
                    save_state(st)
                    log(f"{sym}: 추격 시계를 읽을 수 없어 다시 시작한다")
                    continue
                if _waited < _grace:
                    continue
                _chase_stop_maker(client, policy, st, sym, pos, live, mark)
                continue
            if why != "만기":
                # Price crossed a bracket line and the position is still open,
                # so the exchange-side order did not fire. That order is the
                # account's real protection, and this is the only signal that
                # it is missing or dead. The stop side is the one that bleeds.
                _warn_once(st, f"bracket_dead:{sym}",
                           f"{sym}: 거래소 브래킷이 걸리지 않아 봇이 대신 "
                           f"청산합니다 ({why}). 그 종목의 익절·손절 주문이 "
                           f"살아 있는지 확인하세요.")
            if why == "만기" and cfg.get("expiry_exit") == "limit" and mark > 0:
                # A resting limit instead of a market sweep. Only the expiry
                # takes this path: the two "트리거 잔류" causes mean the
                # exchange-side bracket is dead, and an unprotected position
                # gets out now, at any price. (2026-08-26 user decision)
                try:
                    _handle_expiry_limit(client, policy, st, cfg, sym, pos,
                                         live, mark)
                except PacificaError as e:
                    notify.send(f"브래킷 만기 지정가 실패 {sym}: {str(e)[:120]}")
                continue
            try:
                since_ms = _since_open(pos)
                # Anything of ours still resting has to come off the book
                # first. A reduce-only order the venue keeps after the
                # position is gone meets the next entry in this symbol as
                # its own stale exit, which is what the expiry path was
                # taught to avoid on 08-26. Under a maker stop this stopped
                # being a rare dead-bracket case and became the ordinary
                # one, so the same care belongs here. (08-27)
                _cancel_resting(client, sym, pos)
                close_market(client, policy, sym, pos, live)
                move, tag = _booked_pct(client, sym, pos, mark, since_ms)
                st["closed"].append(
                    _close_row(sym, pos, move,
                               why + (":" + tag if tag else "")))
                del st["positions"][sym]
                save_state(st)
                notify.send(f"브래킷 {why} 청산: {sym} {move:+.2f}%")
            except PacificaError as e:
                notify.send(f"브래킷 청산 실패 {sym}: {str(e)[:120]}")


_TICKS: dict = {}


def _tick_of(client, sym: str) -> float:
    """Tick size for one symbol. Cached for the life of the process."""
    if sym not in _TICKS:
        try:
            for m in client.get_markets():
                _TICKS[m["symbol"]] = float(m.get("tick_size") or 0.01)
        except (PacificaError, TypeError, ValueError, KeyError):
            pass
    return float(_TICKS.get(sym) or 0.01)


def _order_alive(client, oid) -> bool:
    """Is that order still on the book? Unknown counts as alive.

    A cancel can fail because the order already filled, because it never
    existed (a restart carrying stale state), or because the request itself
    failed. Only the middle case may be re-posted over, so anything this
    cannot establish is treated as still live and left alone.
    """
    if oid is None:
        return False
    try:
        return any(str(o.get("order_id")) == str(oid)
                   for o in client.get_open_orders())
    except PacificaError:
        return True


def _handle_expiry_limit(client, policy, st, cfg, sym, pos, live,
                         mark: float) -> None:
    """Rest, wait, re-post. The position keeps its stop the whole time.

    State lives on the position record as exit_limit {oid, at, px, tries}.
    Nothing here books a close: when the limit fills the position leaves
    the venue, and the disappeared-position branch above grades it off the
    venue's own history exactly as it does for a stop or a target.
    """
    tick = _tick_of(client, sym)
    el = pos.get("exit_limit")
    if el:
        waited = (_now() - dt.datetime.fromisoformat(el["at"])).total_seconds()
        if waited < cfg["expiry_wait_s"]:
            return                       # still resting, leave it alone
        try:
            client.cancel_order(sym, order_id=el.get("oid"))
        except PacificaError as e:
            # Filled in the race, or stale state from a restart. Re-posting
            # on top of a live order would put two reduce-only exits on the
            # book, so the book decides, not the exception text.
            if _order_alive(client, el.get("oid")):
                log(f"{sym}: 만기 지정가 취소 실패({str(e)[:60]}), "
                    f"주문이 살아 있어 그대로 둔다")
                return
            log(f"{sym}: 만기 지정가가 이미 사라졌다({str(e)[:60]}), 다시 건다")
    tries = int((el or {}).get("tries", 0)) + 1
    oid = close_limit(client, policy, sym, pos, live, mark, tick)
    pos["exit_limit"] = {"oid": oid, "at": _now().isoformat(),
                         "px": mark, "tries": tries}
    save_state(st)
    if tries == 1:
        log(f"{sym}: 만기 도달, 지정가 청산 대기 (기준가 {mark}, "
            f"{cfg['expiry_wait_s']}초마다 갱신). 손절선은 그대로 살아 있다")
    elif tries % 10 == 0:
        log(f"{sym}: 만기 지정가 {tries}회째 미체결, 계속 갱신 중")


def _since_open(pos: dict) -> int:
    """History window for one position, in ms, starting before it opened.

    The venue stamps its opening row when the order is sent; the book stamps
    opened_at once the fill is booked, about two seconds later (measured on
    all eight live positions: 1.8 to 2.0 seconds). Anchoring the window on
    opened_at therefore excluded the very row that carries the opening fee,
    which made yesterday's fee fix dead code. OPEN_SKEW_MS steps back far
    enough to include it while staying well inside one position's life.

    Falls back to a minute ago when a position carries no opened_at, which is
    the old behaviour and only reachable for a damaged record.
    """
    try:
        return int(dt.datetime.fromisoformat(
            pos["opened_at"]).timestamp() * 1000) - OPEN_SKEW_MS
    except (KeyError, TypeError, ValueError):
        return int(time.time() * 1000) - OPEN_SKEW_MS


def _booked_pct(client, sym: str, pos: dict, mark: float, since_ms: int):
    """What a self-initiated close is worth, preferring the venue to the mark.

    A close the bot observed was read from positions/history; a close the bot
    sent itself was booked straight off the mark with no fees and no slippage
    in it. Same ledger, two accounting standards, and the self-closed rows
    read better by a full round trip. Expiry is about to become the common
    case, so this takes the venue's number once the history has caught up and
    the mark less the round trip when it has not, marking which in the label.
    """
    try:
        real = realized_close(client, sym, since_ms)
    except PacificaError:
        real = None
    entry = pos.get("entry_fill") or 0
    notional = entry * (pos.get("amount") or 0)
    if real and notional > 0:
        return round(real["pnl_usd"] / notional * 100, 3), ""
    if entry > 0 and mark > 0:
        gross = (mark / entry - 1) * 100 * (1 if pos.get("dir") == "long"
                                            else -1)
        return round(gross - FEE_RT_PCT, 3), "추정"
    return 0.0, "추정"


def _warn_once(st, key: str, msg: str) -> None:
    """Say it loudly, once per condition, and carry on trading.

    These three conditions used to stop new entries until a person typed
    --resume. On 08-19 that turned one bad fill into an afternoon of holding
    positions and entering nothing, unnoticed, because a halt announces
    itself to a console window nobody is watching. The account's real
    protection is the exchange-side stop on every position, not this flag,
    so the conditions now warn instead of blocking.

    Suppressed per key on a cooldown, which is the middle these warnings need.
    Comparing message text fired every 30-minute pass, because the average
    warning carries the running average and any close moves it a thousandth;
    the state file held one running average while the number had walked on.
    Suppressing on the key alone went too far the other way: nothing in the
    codebase clears `warned`, --resume included, so a key that rang once went
    silent for the life of the state file. It had already happened, and with
    it the account had neither a halt nor a warning. This bot deliberately
    trades the halt away for the warning, so the warning has to keep working.

    Six hours, so a worsening account is told again up to four times a day.
    Old entries were plain strings; those count as "rang, time unknown" and
    are allowed to ring once more.
    """
    seen = st.setdefault("warned", {})
    now = time.time()
    prev = seen.get(key)
    if isinstance(prev, dict) and now - float(prev.get("at") or 0) < WARN_COOLDOWN_SEC:
        return
    seen[key] = {"msg": msg, "at": now}
    log(f"⚠️ {msg}")
    if _advisory_alerts:
        notify.send(f"브래킷 경고: {msg}")


def circuit_breakers(st, cfg) -> None:
    # 08-24 operator order ("이거 하지마"): with advisory alerts off, the
    # breaker checks themselves are skipped, not just their pings. The
    # account's real protection is the exchange-side stop on every
    # position; these checks only produced warnings anyway.
    # Shipped default keeps them (bracket_advisory_alerts: true).
    if not _advisory_alerts:
        return
    if st["halted"]:
        return
    # Operator closes are not strategy results. --close-all books its exits
    # here like any other, so a breaker meant to ask "is the strategy paying"
    # was averaging in whatever the operator did by hand, and the answer moved
    # with it. Count the last HALT_AVG_AFTER *strategy* closes rather
    # than filtering inside a fixed window, or the sample silently shrinks on
    # a day with many manual exits.
    closed = [c for c in st["closed"] if _is_strategy(c)]
    if len(closed) >= HALT_AVG_AFTER:
        recent = closed[-HALT_AVG_AFTER:]
        avg = sum(c["pnl_pct_est"] for c in recent) / len(recent)
        if avg < HALT_AVG_FLOOR:
            _warn_once(st, "avg",
                       f"누적 성적 미달: 최근 {HALT_AVG_AFTER}건 건당 "
                       f"{avg:+.3f}% < {HALT_AVG_FLOOR}%. 매매는 계속합니다.")
    if st.get("slip_events", 0) >= DEMOTE_SLIP_EVENTS:
        # The old remedy said "lower the leverage", which stopped meaning
        # anything on 08-14: notional is fixed per pick now, so leverage
        # moves the margin posted and not the loss a slipped stop takes.
        _warn_once(st, "slip",
                   f"손절 이탈 {st['slip_events']}회. 호가가 얇은 종목이 "
                   f"섞여 있습니다. 명목을 줄이거나 그 종목을 "
                   f"bracket_skip_syms 로 빼는 걸 검토하세요. 매매는 계속합니다.")


def status(st) -> str:
    lines = [f"[{MODE} 모드] 보유 {len(st['positions'])} · "
             f"청산 누적 {len(st['closed'])}건"]
    for sym, p in st["positions"].items():
        lines.append(f"  {sym} {p['dir']} {p['leverage']}배 "
                     f"진입 {p['entry_fill']} TP {p['tp']} SL {p['sl']}"
                     + (" ⚠️ 미보호(거래소 TP/SL 미확인)"
                        if p.get("unprotected") else ""))
    if st["closed"]:
        # Same separation the breaker makes, because this is the line a person
        # actually reads every cycle. Operator exits are not strategy
        # results, and they move both the score and the total, mostly because
        # --close-all books a run of fee-only closes. Per-trade return is the
        # honest unit, so that goes first and the sum second.
        strat = [c for c in st["closed"] if _is_strategy(c)]
        other = len(st["closed"]) - len(strat)
        if strat:
            wins = sum(1 for c in strat if c["pnl_pct_est"] > 0)
            tot = sum(c["pnl_pct_est"] for c in strat)
            lines.append(f"  전략 {wins}/{len(strat)} · 건당 "
                         f"{tot / len(strat):+.3f}%p · 합계 {tot:+.2f}%p"
                         + (f" (수동·추정 {other}건 제외)" if other else ""))
        elif other:
            lines.append(f"  전략 청산 0건 (수동·추정 {other}건뿐)")
    if st["halted"]:
        lines.append(f"  ⛔ 정지됨: {st['halt_reason']}")
    return "\n".join(lines)


# The equity curve. autonomous.py has written one since the first bot, but
# only autonomous.py ever called record_equity, so the file stopped the day
# the book moved to brackets and stayed stopped for weeks. Asked on 09-01
# where a day's money went, there was no series to answer with: the closed
# rows carry realized dollars, and equity carries the open ones, and only
# one of those was being kept.
#
# Throttled because a cycle is thirty seconds and the curve is not: one row
# every five minutes is enough to read a day, and it costs one account call.
# Never fatal. A curve is a report; aftercare is not, and the two must not
# share a failure.
EQUITY_REC_SEC = 300
_last_equity_rec: float = 0.0


def _record_equity_throttled(client) -> None:
    global _last_equity_rec
    now = time.time()
    if now - _last_equity_rec < EQUITY_REC_SEC:
        return
    _last_equity_rec = now
    try:
        eq = equity(client)
        if eq > 0:
            record_equity(eq)
    except Exception as e:                                  # noqa: BLE001
        log(f"자본 곡선 기록 실패(계속 진행): {type(e).__name__}")


def cycle(client, policy, st, cfg, dry: bool) -> None:
    if not dry:
        # entries that went out right before a crash are re-booked or
        # dropped before anything else runs; dry writes nothing (BR1)
        reconcile_pending(client, st)
    if st["halted"]:
        log(f"정지 상태: {st['halt_reason']} (--resume 으로 해제)")
        # A halt stops NEW entries only. Held positions still need expiry
        # closes, force-closes and grading, or one liquidation halt strands
        # every remaining position until expiry and beyond. Same pattern the
        # EV bot already uses while halted. (review H6)
        try:
            watch_positions(client, policy, st, cfg, dry)
        except Exception as e:
            log(f"정지 중 사후관리 스킵(일시 오류): {e}")
        if not dry:
            save_state(st)
        return
    held_before = len(st["positions"])
    watch_positions(client, policy, st, cfg, dry)
    circuit_breakers(st, cfg)
    if not st["halted"]:
        enter_positions(client, policy, st, cfg, dry)
    # A heartbeat says the process exists; these say it worked. The
    # watchdog and account_status read them, so a bot whose cycle
    # throws on every pass can no longer look healthy.
    st["last_cycle_ok_at"] = _now().isoformat(timespec="seconds")
    if len(st["positions"]) > held_before:
        st["last_entry_at"] = st["last_cycle_ok_at"]
    if not dry:
        save_state(st)
        _record_equity_throttled(client)
    # Housekeeping the operator's rules file wants on every cycle. It must
    # return immediately: anything slow belongs in a process of its own, not
    # in the loop that watches open positions.
    if _op_rules is not None and hasattr(_op_rules, "on_cycle"):
        try:
            _op_rules.on_cycle(dry)
        except Exception as e:                          # noqa: BLE001
            log(f"운영자 주기 작업 실패({e!r}), 매매는 그대로 진행")


SEAL_POLL_SEC = 30

# ── seal self-generation ─────────────────────────────────────────────────
# The trader consumes seals; seal_maker produces them. When no seal fresh
# enough exists, the loop generates one itself so a standalone install
# needs no external scheduler. At most one attempt per hour, and a failed
# generation only logs: position aftercare must never die with it.
# 2026-09-03 user decision: rebuild every half hour, not every hour.
# The board a seal carries is only as current as the seal, and with rules
# that read a fast signal the names go stale before the hour is out: on
# 09-03 the four Bollinger signals lit eight symbols at 12:48, five
# different ones by 13:30, and three by 17:24. A name that lights at 19:00
# and is dark again at 19:45 never reaches the file, so the seat is not
# entered late, it is lost.
#
# Late entry itself costs nothing measurable, which is why the interval and
# not the rule is what moves: replaying the signal with entry pushed one,
# two and three hours past the signal bar left both the fill price and the
# direction accuracy where they were. A band break is a mean-reversion
# call, so price drifts back toward the entry rather than away from it.
# What delay costs is seats, not price.
# How stale a seal may be before it is rebuilt. Two numbers, because the
# seal has two costs. When the operator's rules build the board themselves
# the seal skips its scoring and takes seconds (43.5s measured 09-04), and
# then the signal's own bar length is the right window: a 15m signal wants
# a quarter hour. When the board is scored the seal costs minutes (5m31s),
# and a quarter-hour window would leave it building a third of the time.
#
# Read off the last build rather than pinned, so switching modes cannot
# leave the wrong number behind. Half an hour until the first build tells
# us which mode we are in.
SEAL_FRESH_FAST_H = 0.25
SEAL_FRESH_SLOW_H = 0.5
SEAL_FAST_SECS = 90.0           # under this, the seal is the cheap kind
_last_seal_secs = 0.0


def seal_fresh_h() -> float:
    if _last_seal_secs and _last_seal_secs <= SEAL_FAST_SECS:
        return SEAL_FRESH_FAST_H
    return SEAL_FRESH_SLOW_H
SEAL_GEN_MIN_INTERVAL_SEC = 900
_last_seal_gen: float = 0.0
# An operator who runs an external seal generator sets
# bracket_selfgen_seal: false in policy.yaml so the bot never competes
# with it; the shipped default (no key) keeps self-generation on.
_selfgen_enabled: bool = True
_tp_limit: bool = False      # TP leg executes as limit when triggered (08-24)
_entry_limit: bool = False   # entry as resting limit at the anchor price
_entry_wait: int = 120       # seconds before an unfilled entry is cancelled
_entry_max_tries: int = 3    # misses before a name is dropped for this seal
_sl_buf: float = 0.0         # SL limit buffer, in units of expected move
_sl_maker: bool = False      # SL trigger and limit at the same price (08-27)
_side_source: str = "touch"  # who calls long/short: "touch" or "signal" (08-24)
_advisory_alerts: bool = True   # advisory warnings also go to Telegram; the
                                # log line always stays (08-24 operator ask)
_strong_bonus: bool = False     # empty special seats double a pick whose
                                # strong 1h vote agrees with its direction


def _touch2h_side(sym, per_hours=2, level=0.010, max_stale_h=6.0):
    """Direction from the SHORT-horizon touch rate.

    Live trades resolve in a few hours and rarely reach the 24h expiry, so
    a side read off a 24h touch rate was answering about a horizon the
    trade never lived through. A long replay favours the short horizon on
    the same seats and geometry. 2026-08-26 user decision to trade it.

    The seal ALSO calls the side this way now (seal_maker.SIDE_SOURCE moved
    to touch2h on 08-27), so this is no longer an override of a different
    rule but the same rule asked again at entry, minutes to hours later.
    They agree on about 92% of rows in the shadow ledger and the rest is
    that gap in time, not a disagreement about method. Kept because the
    price at entry is the price the trade actually gets. (08-28)

    Bars come from the hourly Pacifica cache the collector refreshes, so
    stock and RWA tokens are covered as well as coins. Returns "long" /
    "short", or None when the cache is missing or stale (the caller then
    keeps the seal's own direction and says so in the log)."""
    import gzip
    p = os.path.expanduser(
        f"~/.ocean_agent_bincache/pac_{sym}_1h_ohlc.json.gz")
    if not os.path.exists(p):
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            bars = json.load(fh)["bars"]
    except (OSError, ValueError, KeyError):
        return None
    if len(bars) < 300 + per_hours:
        return None
    if (time.time() * 1000 - float(bars[-1]["t"])) / 3.6e6 > max_stale_h:
        return None                      # stale cache: do not guess
    hi = [float(b["h"]) for b in bars]
    lo = [float(b["l"]) for b in bars]
    c = [float(b["c"]) for b in bars]
    n = len(c)
    up = dn = cnt = 0
    for i in range(max(0, n - 720 - per_hours), n - per_hours - 1):
        base = c[i]
        if base <= 0:
            continue
        cnt += 1
        if max(hi[i + 1:i + 1 + per_hours]) / base - 1.0 >= level:
            up += 1
        if 1.0 - min(lo[i + 1:i + 1 + per_hours]) / base >= level:
            dn += 1
    if not cnt:
        return None
    return "long" if up >= dn else "short"


_op_rules = None            # optional module from a local rules file the
                            # operator points at; absent, nothing changes


PRED_INPUT_TAG = "median"


def _apply_pred_input(policy, log) -> None:
    """Choose which statistic sizes a trade, and say so in the log.

    The shipped default sizes off the 30 day median move scaled by an ATR
    PERCENTILE. That percentile carries rank, not magnitude, and live
    trades show it missing regime: actual over expected runs 0.69 on calm
    entries against 1.61 on hot ones. Sizing straight off ATR flattens
    that to -0.02 and, at a matched width, earns 0.46 a day more in the
    replay and 0.15 a trade more live.

    k does not travel between candle sources. Fit it on the picks that get
    seated (their ATR28 median is 2.10% on Pacifica candles, twice the
    universe median) and not on the universe.

    Every position records which input sized it, so the switchover can be
    judged later instead of argued about.
    """
    global PRED_INPUT_TAG
    src = str(policy.get("seal_pred_input", "median") or "median").lower()
    if src not in ("median", "atr28"):
        log(f"알 수 없는 seal_pred_input '{src}', 기본값으로 둡니다")
        src = "median"
    k = float(policy.get("seal_pred_k", 1.5) or 1.5)
    os.environ["SEAL_PRED_INPUT"] = src
    os.environ["SEAL_PRED_K"] = str(k)
    PRED_INPUT_TAG = src if src == "median" else f"atr28x{k:g}"
    if src == "atr28":
        log(f"크기 결정: 예상변동 = {k:g} x ATR28 (09-01 사용자 결정). "
            f"30일 중앙값 x ATR분위 를 대신한다. 분위는 순위만 담아 국면을 "
            f"놓쳤고(잠잠 0.69 대 뜨거움 1.61), ATR 로 바꾸면 그 기울기가 "
            f"-0.02 로 눕는다. 폭은 3% 근처로 현행과 같게 맞췄다. "
            f"판정은 t 1.30 에서 내렸고 유의하지 않다. 거래마다 "
            f"pred_input 을 남기니 100건쯤 뒤에 다시 판정한다")
    else:
        log("크기 결정: 예상변동 = 30일 중앙값 x (0.7 + 0.6 x ATR분위) (현행)")


def _load_operator_rules(policy) -> None:
    """Load the operator's local rules file, if configured.

    The file is plain Python OUTSIDE the package (policy key
    `operator_rules`, or OCEAN_OPERATOR_RULES). What it decides lives in
    that file, not here; shipped code only knows the two call shapes it
    may answer: entry_veto(sym, direction) and, for the seal maker,
    front_picks(...). Broken or missing, trading is unchanged."""
    global _op_rules
    path = str(policy.get("operator_rules", "") or
               os.environ.get("OCEAN_OPERATOR_RULES", ""))
    if not path or not os.path.exists(path):
        return
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("operator_rules", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:                              # noqa: BLE001
        log(f"운영자 규칙 파일을 읽지 못함({e!r}), 없이 진행")
        return
    _op_rules = mod
    os.environ["OCEAN_OPERATOR_RULES"] = path
    log(f"운영자 규칙 적재: {os.path.basename(path)}")


def _vote_net(sigs, j, tf):
    """Raw net vote over the signal table, DELIBERATELY ungated.

    Review 15-4 asked for the matrix gate here. Applied and measured on
    08-24: the live matrix rejects 21 of 22 signals at 1h, which zeroes
    every vote and kills the strong-vote alarm and bonus the operator
    ordered that same day. The vote layer is the operator's declared bet
    on signals the matrix does NOT endorse ("no measured edge, operator's
    bet"), so gating it away contradicts its reason to exist. Raw counting
    also matches what the forward shadow ledger records. The correlated-
    family duplication (four sRSI variants = four votes) likewise stays,
    documented, until the forward record says the bet is worth refining."""
    net = 0
    for name, (side, fn) in sigs.items():
        try:
            if fn(j):
                net += 1 if side == "long" else -1
        except (TypeError, IndexError):
            pass
    return net


def _strong_vote(client, sym: str):
    """'long'/'short' only when the matrix-gated 1h net vote is at least
    ±2, the same bar the strong-vote Telegram alarm reads. None otherwise
    (weak, tied, or bars unavailable)."""
    from .signal_scanner import fetch_bars, _series, _signals
    try:
        bars = fetch_bars(client, sym, "1h", max_bars=400)
        if not bars or len(bars) < 260:
            return None
        c = [float(b[4]) for b in bars]
        h = [float(b[2]) for b in bars]
        lo = [float(b[3]) for b in bars]
        net = _vote_net(_signals(_series(c, h, lo)), len(c) - 1, "1h")
        if abs(net) < 2:
            return None
        return "long" if net > 0 else "short"
    except Exception:
        return None


def _signal_side(client, sym: str):
    """Signal-measured side: 'long' or 'short', decided by signals ALONE.

    08-24 evening order ("신호로 측정하라고"): silence must not empty the
    seat and must not be padded with the seal's touch direction either. So
    the vote escalates within the signal layer until it resolves:
      1. 22-signal net vote on the last closed 1h bar
      2. still tied -> add the 4h, 8h and 12h votes (same signals, higher
         timeframes, fetched exactly as the scanner would)
      3. still tied -> RSI(14) on the 1h closes: >=50 long, <50 short
         (an indicator verdict, never the touch rate)
    Returns None only when bars cannot be fetched at all; the caller then
    falls back to the seal direction and records it as such.
    """
    from .signal_scanner import fetch_bars, _series, _signals

    def vote(tf, n):
        bars = fetch_bars(client, sym, tf, max_bars=n)
        if not bars or len(bars) < 260:
            return None, None
        # fetch_bars returns (t, o, h, l, c) tuples, not dicts
        c = [float(b[4]) for b in bars]
        h = [float(b[2]) for b in bars]
        lo = [float(b[3]) for b in bars]
        net = _vote_net(_signals(_series(c, h, lo)), len(c) - 1, tf)
        return net, c

    try:
        net, c1 = vote("1h", 400)
        if net is None:
            return None
        if net == 0:
            for tf in ("4h", "8h", "12h"):
                try:
                    hi, _ = vote(tf, 300)
                except Exception:
                    hi = None
                if hi:
                    net += hi
        if net == 0:
            from .signal_scanner import rsi_series
            rs = rsi_series(c1, 14)
            if rs:
                return "long" if rs[-1] >= 50 else "short"
            return None
        return "long" if net > 0 else "short"
    except Exception:
        return None


def _newest_seal_age_h() -> float:
    """Age in hours of the newest seal file, by mtime; huge when none."""
    files = glob.glob(os.path.join(OUTPUTS_DIR, "내일예측_*.json"))
    if not files:
        return 1e9
    return (time.time() - max(os.path.getmtime(p) for p in files)) / 3600.0


def maybe_generate_seal() -> None:
    """Generate a fresh seal when the newest one is older than an hour.

    Every failure path logs and returns: the loop's job of watching and
    closing positions continues on the existing seal (or none).
    """
    global _last_seal_gen
    if not _selfgen_enabled:
        return
    try:
        age_h = _newest_seal_age_h()
        if age_h <= seal_fresh_h():
            return
        if time.time() - _last_seal_gen < SEAL_GEN_MIN_INTERVAL_SEC:
            return
        _last_seal_gen = time.time()
        ago = "없음" if age_h > 1e8 else f"{age_h:.1f}시간 지남"
        log(f"봉인 {ago}, 새로 만듭니다 (몇 분 걸릴 수 있음)")
        from . import seal_maker
        global _last_seal_secs
        _t_seal = time.time()
        path = seal_maker.make_seal(out_dir=OUTPUTS_DIR, log=log)
        _last_seal_secs = time.time() - _t_seal
        log(f"봉인 생성 {_last_seal_secs:.0f}초 · 다음 갱신 주기 "
            f"{seal_fresh_h()*60:.0f}분")
        if path is None:
            log("봉인 생성 보류(표본 부족), 다음 시간에 재시도합니다")
    except Exception as e:                 # noqa: BLE001
        log(f"봉인 생성 실패({type(e).__name__}: {str(e)[:120]}), "
            f"기존 봉인과 보유 관리로 계속합니다")


def fresh_seal_waiting(st: dict, dry: bool) -> bool:
    """Is there a seal this run has not entered yet, still inside its window?

    Cheap enough to ask every 30 seconds: it reads one small JSON file and
    touches no network. Guards mirror enter_positions exactly, so a True here
    means the next cycle will really act.
    """
    rec = latest_seal()
    if not rec:
        log("진입 대기: 봉인(내일예측_*.json) 이 아직 없다. 픽 생성이 만들면 "
            "다음 회차에 자동 진입한다.")
        return False
    key = seal_key(rec)
    if key in st.get("entered_seals", []) or (dry and key in _dry_logged):
        return False
    if time.time() < _retry_not_before:
        return False        # failed pass cooling down, no hot loop (P4)
    try:
        made = dt.datetime.fromisoformat(rec["made_at"])
    except (KeyError, ValueError):
        return False
    return (_now() - made).total_seconds() / 3600 <= 6


def stop_watch_pass(client, policy, st: dict):
    """Between cycles, keep the stop chase moving.

    Returns (wake, next_sec): whether the cycle should run now, and how long
    to wait before looking again.

    The interval is set by how close the nearest position is to its stop,
    because the cost this exists to avoid is made of seconds. Measured on
    the live book, our order size never eats past the top level of the book
    (ten symbols checked 08-28: eight fill in one level, the worst two cost
    0.03%), so the concession a stop pays is not depth. It is the distance
    price travels between crossing the line and our order arriving. On the
    first live chase that was 0.122% in ten seconds.

    So: far from the line this costs one prices call every half minute, and
    only inside the last stretch does it look every couple of seconds, which
    is where the seconds are worth paying for. (08-28)

    Runs on its own instead of waking the full cycle, because the cycle can
    spend minutes building a seal and this is the one job that cannot wait
    that long. It only ever rests orders; nothing here takes liquidity.

    Three states per held position: above the line (drop any chaser), just
    under it (start the grace the resting stop was given), and past the
    grace (move a reduce-only limit to the mark, every pass). The loop is
    woken only when a chased position has actually left the venue, so the
    cycle can book it the way it books every other close.

    One prices call per pass, and only while something is held under a
    maker stop. The positions call happens only when a chase is live.
    """
    slow = SEAL_POLL_SEC
    fast = max(1, int(policy.get("bracket_sl_chase_sec", 2) or 2))
    held = st.get("positions") or {}
    # This pass carries three jobs now, not one. The maker stop chase is
    # what it was written for, but the cut and the expiry also live here:
    # both are decided in the cycle, which is half an hour wide, so this is
    # what fetches them in time. Gating all of it on bracket_sl_maker would
    # mean turning the maker stop off silently restores a half-hour delay
    # on two exits that have nothing to do with it. Each job asks for
    # itself. (2026-09-01)
    _cut_on = float(policy.get("bracket_early_cut_pct", 0) or 0) > 0
    _exp_on = float(policy.get("bracket_horizon_h", 24) or 0) > 0
    _chase_on = bool(policy.get("bracket_sl_maker", False))
    if not held or not (_chase_on or _cut_on or _exp_on):
        return False, slow
    grace = fast
    try:
        prices = {p["symbol"]: p for p in client.get_prices()}
    except PacificaError:
        return False, slow               # the cycle will look properly
    under, nxt = {}, slow
    wake_now = False
    for sym, pos in list(held.items()):
        mk = float(prices.get(sym, {}).get("mark")
                   or prices.get(sym, {}).get("mid") or 0)
        if mk <= 0 or not pos.get("sl"):
            continue                     # a missing price proves nothing
        up = pos.get("dir") == "long"
        past = (mk <= pos["sl"] if up else mk >= pos["sl"])
        # How far the price still has to travel to reach the stop, as a
        # share of the whole distance the bracket allowed. Inside the last
        # sixth of it, look every couple of seconds.
        # Without an entry price there is no bracket width to take a share
        # of, and falling back to zero would silently make the test "within
        # 17% OF THE STOP PRICE", a different and much wider question that
        # the price-is-positive guard cannot catch. A record that lost its
        # entry gets the fast cadence outright: it is rare, and erring
        # toward looking more often is the safe direction here.
        # (08-28 review A3)
        _e = float(pos.get("entry_fill") or 0)
        span = abs(_e - pos["sl"]) if _e > 0 else 0.0
        left = (mk - pos["sl"]) if up else (pos["sl"] - mk)
        if span <= 0 or left / span <= 0.17:
            nxt = fast
        # The early cut is a line too, and it sits well inside the stop, so
        # the test above never reaches the fast cadence in time for it: at
        # the cut the price still has half the bracket to travel. The cut
        # then fired up to a slow interval late, and a market order sent
        # late leaves at a late price however fast it fills (measured
        # 2026-08-31: threshold 2.0%, actual triggers 2.06~2.73%).
        #
        # The cut itself belongs to the cycle, and the cycle is half an
        # hour apart, so crossing the line is not enough: this pass has to
        # go and fetch it. A 30s move is about 0.16%, so watch every couple
        # of seconds from half a point out, and wake the cycle the moment
        # the line is crossed. Once the cut is marked done the flag stops,
        # so a cut that could not be sent does not spin the loop.
        _cut = float(policy.get("bracket_early_cut_pct", 0) or 0)
        if _cut > 0 and _e > 0:
            adv = (_e - mk) / _e * 100 if up else (mk - _e) / _e * 100
            if adv >= _cut - 0.5:
                nxt = fast
            if adv >= _cut and not pos.get("early_cut"):
                wake_now = True
        # Same trap, second door: the expiry is judged in the cycle too, so
        # a position could sit half an hour past its horizon before the exit
        # limit was even posted. Whatever that delay costs was being read as
        # an expiry fill cost. Marked by exit_limit, so a posted exit does
        # not wake the loop again. (2026-09-01)
        # Third door: a seat asked for by a front pick. The request is
        # written by the cycle, but waiting for the next one to act on it
        # keeps the pick out of the seat for up to half an hour. Marked by
        # early_cut, which is the machinery the eviction uses. (2026-09-01)
        if pos.get("evict_req") and not pos.get("early_cut"):
            wake_now = True
        _hz = float(policy.get("bracket_horizon_h", 24) or 0)
        if _hz > 0 and not pos.get("exit_limit"):
            try:
                _op = dt.datetime.fromisoformat(pos["opened_at"])
                if (_now() - _op).total_seconds() / 3600 >= _hz:
                    wake_now = True
            except (KeyError, TypeError, ValueError):
                pass
        if not _chase_on:
            continue                     # cut and expiry only; venue keeps
            #                              the stop and fills it itself
        if not past:
            if pos.get("sl_missed_at") or pos.get("stop_chase"):
                try:
                    _drop_stop_chase(client, st, sym, pos)
                except PacificaError:
                    pass
            continue
        seen = pos.get("sl_missed_at")
        if not seen:
            pos["sl_missed_at"] = _now().isoformat()
            save_state(st)
            log(f"{sym}: 값이 손절선을 지났다. 걸어둔 손절 지정가가 제 "
                f"가격에 채워지는지 {grace}초 기다린다")
            continue
        try:
            waited = (_now()
                      - dt.datetime.fromisoformat(seen)).total_seconds()
        except (TypeError, ValueError):
            pos["sl_missed_at"] = _now().isoformat()   # never trust it twice
            save_state(st)
            continue
        if waited >= grace:
            under[sym] = pos
    if not under:
        return wake_now, nxt
    nxt = fast                           # a chase is live: stay on it
    try:
        live = {p.get("symbol"): p for p in client.get_positions()}
    except PacificaError:
        return wake_now, nxt
    wake = wake_now
    for sym, pos in under.items():
        if sym not in live:
            wake = True                  # gone: the cycle books it
            continue
        try:
            _chase_stop_maker(client, policy, st, sym, pos, live, 
                              float(prices[sym].get("mark")
                                    or prices[sym].get("mid") or 0))
        except PacificaError as e:
            log(f"{sym}: 손절 추격 지정가 실패({str(e)[:80]}), 다음 회에 "
                f"다시 건다. 거래소 손절은 그대로 살아 있다")
    return wake, nxt


def sleep_alive(seconds: int, dry: bool, st: dict | None = None,
                client=None, policy: dict | None = None) -> None:
    """Sleep between cycles, but wake early the moment a new seal appears.

    Two jobs. The lock window (10 min) is deliberately shorter than the loop
    (30 min), so a killed bot releases the account quickly and the heartbeat
    is renewed during the sleep rather than once per cycle.

    And the wait is interruptible. A user who asks for picks now expects the
    bot to act now, not to sit out the rest of a 30-minute timer; with seals
    no longer pinned to one evening hour, a fresh seal can land at any moment.
    Polling the seal file costs one local read, so the bot notices within
    half a minute instead of up to half an hour. Dry writes nothing.
    """
    # Elapsed, not nominal. This wait used to be pure sleep, so counting naps
    # was counting seconds. It now makes a network call per tick, and one 429
    # costs it over a minute of backoff, so a "300 second" heartbeat could
    # drift past the 10 minute freshness window and let the keeper start a
    # SECOND live bot on the same account. Nothing here may assume the clock
    # and the naps agree. (08-27)
    _end = time.monotonic() + seconds
    _next_hb = time.monotonic() + 300
    _tick = SEAL_POLL_SEC
    while True:
        left = _end - time.monotonic()
        if left <= 0:
            return
        # A held position under a maker stop is watched on the chase clock,
        # not the seal clock: an exit that is supposed to follow the price
        # cannot be moved once every half minute. The pass itself says how
        # soon to look again, so the fast cadence is spent only on a
        # position that is nearly at its line. With nothing held this is the
        # old cadence and costs nothing.
        #
        # The pass runs BEFORE the nap, not after. Written the other way it
        # slept the previous interval first, and since the interval starts
        # at the seal cadence, the first look after every cycle was half a
        # minute late no matter how close a position sat to its line. On
        # the measured 0.122% per ten seconds that is the whole point of
        # this code, spent. (08-28 review A2)
        # Same three jobs as the pass itself: the chase, the cut, and the
        # expiry. Gating this on the maker stop alone was the outer half of
        # the same trap. (2026-09-01)
        _p = policy or {}
        _watch = (not dry and client is not None and st is not None
                  and (st.get("positions") or {})
                  and (bool(_p.get("bracket_sl_maker", False))
                       or float(_p.get("bracket_early_cut_pct", 0) or 0) > 0
                       or float(_p.get("bracket_horizon_h", 24) or 0) > 0))
        if _watch:
            _woke, _tick = stop_watch_pass(client, policy or {}, st)
            if _woke:
                log("정리할 자리가 생겼다(추격 종료 · 조기 정리 문턱 · 만기). "
                    "대기를 끊고 사이클로 간다")
                return
        else:
            _tick = SEAL_POLL_SEC
        if st is not None and fresh_seal_waiting(st, dry):
            log("새 봉인 감지, 대기를 끊고 바로 진입 판단으로 갑니다")
            return
        if not dry and time.monotonic() >= _next_hb:
            write_heartbeat()
            _next_hb = time.monotonic() + 300
        time.sleep(min(_tick, max(0.0, _end - time.monotonic())))


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="1사이클만")
    ap.add_argument("--dry", action="store_true", help="판단만, 주문 안 함")
    ap.add_argument("--status", action="store_true", help="상태만 출력")
    ap.add_argument("--resume", action="store_true",
                    help="정지 해제 (옛 상태 파일용, 이제 멈추는 장치가 없음)")
    ap.add_argument("--close-all", action="store_true", help="전량 청산")
    args = ap.parse_args()

    # Starting is the operator saying "trade": it outranks a stop they asked
    # for earlier, and it is the only thing that clears one. Without this the
    # watchdog and a stop would argue with each other every hour.
    st_early = load_state()
    if st_early.get("stopped_by_user") and not (args.status or args.dry):
        st_early["stopped_by_user"] = False
        st_early["stopped_at"] = ""
        save_state(st_early)
        log("사용자 중지 상태를 시작과 함께 해제합니다")

    policy = load_policy()
    # A keyless start used to run a full cycle, hit GET /account with an
    # empty address, and report the venue's 400 as "rate limit, maybe"
    # (fresh-install simulation, 08-24). Say the actual problem instead.
    # The .env must be loaded FIRST: make_client loads it itself, so checking
    # the bare environment refused to start with a perfectly good .env on
    # disk (08-24 evening, operator restart). --status stays open.
    if not args.status:
        try:
            from dotenv import load_dotenv
            _envf = os.environ.get("PACIFICA_ENV_FILE")
            load_dotenv(_envf) if _envf else load_dotenv()
        except ImportError:
            pass
        from . import address_from_env as _addr
        if not _addr(os.environ.get("PACIFICA_BASE_URL", "")):
            log("계정이 연결돼 있지 않습니다. connect_pacifica 를 실행하거나 "
                ".env 에 ADDRESS 와 PACIFICA_API_KEY 를 넣고 다시 시작하세요.")
            return
    cfg = apply_budget(bracket_cfg(policy))
    global _selfgen_enabled, _tp_limit, _entry_limit, _entry_wait, _sl_buf
    global _side_source, _advisory_alerts, _sl_maker, _entry_max_tries
    _selfgen_enabled = bool(policy.get("bracket_selfgen_seal", True))
    _advisory_alerts = bool(policy.get("bracket_advisory_alerts", True))
    global _strong_bonus
    _strong_bonus = bool(policy.get("bracket_strong_bonus", False))
    if _strong_bonus:
        log("특별석 보너스: 빈 특별석 수만큼, 강신호(1h 순투표 ±2 이상)가 "
            "진입 방향과 일치하는 픽의 명목을 2배로 (08-24 사용자 결정)")
    _cfg_banner = bracket_cfg(policy)
    _tp_limit = bool(policy.get("bracket_tp_limit", False))
    _entry_limit = bool(policy.get("bracket_entry_limit", False))
    _entry_wait = int(policy.get("bracket_entry_wait_sec", 120) or 120)
    _entry_max_tries = max(1, int(policy.get("bracket_entry_max_tries", 3)
                                  or 3))
    _sl_buf = float(policy.get("bracket_sl_limit_buffer", 0) or 0)
    _sl_maker = bool(policy.get("bracket_sl_maker", False))
    modes = []
    if _entry_limit: modes.append(f"진입 지정가(미체결 {_entry_wait}s 후 취소, "
                                  f"{_entry_max_tries}회 놓치면 그 봉인에서 포기)")
    if _tp_limit: modes.append("익절 지정가")
    if _sl_maker:
        _chase = max(1, int(policy.get("bracket_sl_chase_sec", 2) or 2))
        modes.append(f"손절 지정가(트리거=지정가, 밀림 없음. 안 채워지면 "
                     f"{_chase}초마다 마크에 지정가를 옮겨 따라감, 시장가 없음)")
    elif _sl_buf > 0: modes.append(f"손절 트리거-지정가(버퍼 {_sl_buf}×변동)")
    _ec = float(_cfg_banner.get("early_cut_pct", 0) or 0)
    if _ec > 0:
        # The loop's own cut stands down when the exchange stop already sits
        # at the cut line, which a fixed early_cut_pct always makes true. Say
        # that, rather than describing machinery that no longer runs.
        modes.append(f"컷 {_ec}% 고정, 거래소가 들고 있음(손절선이 곧 그 선. "
                     f"봇이 따로 정리하지 않음)")
    if _cfg_banner.get("expiry_exit") == "limit":
        modes.append(f"만기 청산 지정가(미체결 {_cfg_banner['expiry_wait_s']}s"
                     f"마다 갱신)")
    if modes:
        log("체결 방식: " + " · ".join(modes) + " (08-24·08-26 사용자 결정)")
    _side_source = str(policy.get("bracket_side_source", "touch")).lower()
    if _side_source == "signal":
        log("부호 결정: 22신호 순투표, 동점이면 4h·8h·12h 신호 합산, 그래도 "
            "동점이면 RSI 판정 (08-24 사용자 결정). 신호가 항상 방향을 낸다")
    if _side_source == "touch2h":
        log("부호 결정: 2시간 ±1.0% 도달률 (08-26 사용자 결정). "
            "봉인의 24시간 도달률 대신, 실제 보유 시간에 맞춘 지평. "
            "캐시가 없거나 오래되면 봉인 방향으로 물러선다")
    _apply_pred_input(policy, log)
    _load_operator_rules(policy)
    if not _selfgen_enabled:
        log("봉인 자체 생성 꺼짐 (bracket_selfgen_seal: false), 외부 생성기를 기다립니다")
    use_mode(bracket_mode(policy), args.dry)   # before any file is touched
    # Every safety file (state, heartbeat, seals) lives under OUTPUTS_DIR.
    # If it cannot be written, the heartbeat and ledger the cross-bot guards
    # depend on cannot exist either, so refuse to run at all. (review N1)
    try:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        _probe = os.path.join(OUTPUTS_DIR, ".write_probe")
        with open(_probe, "w", encoding="utf-8") as _pf:
            _pf.write("ok")
        os.remove(_probe)
    except OSError as e:
        log(f"outputs 폴더({OUTPUTS_DIR})에 쓸 수 없어 시작을 거부합니다: {e}")
        return
    st = load_state()

    if args.status:
        print(status(st))
        return
    if args.resume:
        st["halted"], st["halt_reason"], st["slip_events"] = False, "", 0
        save_state(st)
        print("정지 해제됨")
        return

    # X1: the old EV bot and this one share one account's margin. A docstring
    # warning does not stop a double launch; a recent heartbeat on the old
    # bot's state file does. The EV bot's real state file lives under
    # data_file(), not cwd's state.json, which no longer exists and made this
    # guard never fire. Its mtime is renewed every EV cycle, so freshness is
    # judged against the loop interval. (review H4)
    os.environ["PACIFICA_BASE_URL"] = policy["base_url"]   # data_file() tag
    ev_state = data_file("autonomous.json")
    if os.path.exists(ev_state) and not args.close_all:
        fresh_min = max(10, int(policy.get("loop_interval_min", 30) or 30) * 2)
        age_min = (time.time() - os.path.getmtime(ev_state)) / 60
        if age_min < fresh_min:
            msg = (f"기존 EV 봇(autonomous)이 {age_min:.0f}분 전까지 살아있던 "
                   "흔적이 있어 시작을 거부합니다. 같은 계좌 증거금을 두 봇이 "
                   "나눠 쓰면 안 됩니다. EV 봇을 끄고 다시 실행하세요 "
                   f"(꺼진 봇의 흔적이면 {fresh_min}분 뒤 자동 해제).")
            log(msg)
            notify.send("브래킷: " + msg)
            return

    # The same rule between the two bracket modes: one account's margin, so
    # whichever mode is alive keeps it until it stops.
    running = live_bracket() if not args.close_all else None
    if running:
        other, age_min = running
        # No Ctrl+C here: nobody who installed this has a terminal window
        # to press it in. The bot is launched detached by the installer, the
        # Telegram bot, or the MCP tool, so the only handle a user has is
        # the same sentence they would say to turn it off.
        # (2026-08-26 user: "신규자들은 봇을 안 키는데 왜 넣어")
        msg = (f"{other} 모드 봇이 이미 돌고 있어 시작을 거부합니다 "
               f"({age_min:.0f}분 전 신호). 한 계좌를 두 봇이 나눠 쓰면 "
               "증거금이 겹칩니다. 이미 돌고 있으니 그대로 두시면 되고, "
               "끄고 싶으면 \"자동매매 꺼줘\" 라고 말하세요.")
        log(msg)
        notify.send("브래킷: " + msg)
        return

    # Claim the account the moment the lock check passes. Everything below
    # this line talks to the network first (client, reconcile), which took
    # tens of seconds on 08-20 morning: in that window this bot did not
    # exist yet, so the watchdog read "no heartbeat" and started a second
    # one. The claim has to be the first thing, not the first thing in the
    # loop. (--close-all skips the lock, so it must not claim either.)
    claimed = not args.dry and not args.close_all
    if claimed:
        write_heartbeat()

    try:
        client = make_client(policy)
    except Exception:
        # The claim above is written before the network is touched on purpose,
        # but a claim for a bot that never came up blocks a restart for the
        # whole freshness window. Hand it back on the way out; a crash later
        # still lets the heartbeat expire on its own.
        if claimed:
            clear_heartbeat()
        raise

    # Crash recovery must run before --close-all too, or a position that was
    # opened but never booked would survive a "close everything". (review H9)
    if not args.dry:
        reconcile_pending(client, st)

    if args.close_all:
        # Only a position whose close order actually went out leaves the
        # book. A failed close stays booked, loudly: wiping it would leave a
        # live exchange position that no ledger remembers and no watcher
        # ever force-closes. (review H8)
        live = {p.get("symbol"): p for p in client.get_positions()}
        failed = []
        for sym, pos in list(st["positions"].items()):
            try:
                since_ms = _since_open(pos)
                close_market(client, policy, sym, pos, live)
                # Book it the way every other close path does. Without this a
                # flatten erased those trades from the ledger, so the running
                # average that warns on a bad run never saw them, and neither
                # did any later measurement.
                # _close_row carries sym and dir like every other close path:
                # without them the merge key is (None, time), so a live bot's
                # own record of the same trade counts twice and the trade
                # drops out of per-symbol learning.
                row = _close_row(sym, pos,
                                 _flat_pct(client, sym, pos, since_ms),
                                 "manual_close")
                row["basis"] = ("--close-all 전량 청산 "
                                "(손익은 거래소 이력에서 확인)")
                st["closed"].append(row)
                del st["positions"][sym]
                log(f"청산: {sym}")
            except PacificaError as e:
                failed.append(sym)
                log(f"⚠️ 청산 실패 {sym}: {e} (장부에 남김, 재실행으로 재시도)")
        # Flattening is not a reason to stop trading: the operator asked
        # for a clean slate, and the next cycle fills it from the seal.
        st["halted"], st["halt_reason"] = False, ""
        save_state(st)
        if failed:
            msg = ("브래킷 --close-all: 청산 주문 실패로 장부에 남은 포지션: "
                   + ", ".join(failed) + ". 거래소에 살아 있을 수 있으니 "
                   "--close-all 재실행 또는 수동 청산 필요.")
            log("⚠️ " + msg)
            notify.send("⚠️ " + msg)
        return

    tp_txt = (f"TP {cfg['tp_pct']}% 고정" if cfg["tp_pct"] > 0
              else f"TP {cfg['tp_mult']}x")
    log(f"브래킷 트레이더 시작 · {MODE} 모드 · 슬롯 {cfg['slots']} · "
        f"{cfg['leverage']}배 · 자본 {cfg['capital_pct']:.0f}% · "
        f"{tp_txt} / SL "
        + (f"{float(cfg.get('early_cut_pct') or 0)}% 고정"
           if float(cfg.get("early_cut_pct") or 0) > 0
           else f"{cfg['sl_mult']}x") + " · "
        + f"하한 {policy.get('bracket_vol_floor_pct', 0)}% · "
        f"{'DRY RUN' if args.dry else '실주문'}")
    try:
        while True:
            if not args.dry:
                write_heartbeat()      # hold the account for this mode
            maybe_generate_seal()
            if not args.dry:
                write_heartbeat()      # generation can take minutes; renew
            try:
                cycle(client, policy, st, cfg, args.dry)
            except PacificaError as e:
                log(f"사이클 오류(다음 사이클에 재시도): {e}")
                if not args.dry:
                    save_state(st)     # keep whatever was booked before the error
            except Exception as e:                 # noqa: BLE001
                notify.send(f"브래킷 예상 밖 오류: {type(e).__name__}: {str(e)[:150]}")
                if not args.dry:
                    save_state(st)
            if args.once:
                break
            print(status(st))
            sleep_alive(LOOP_MIN * 60, args.dry, st, client, policy)
    finally:
        if not args.dry:
            clear_heartbeat()


if __name__ == "__main__":
    main()
