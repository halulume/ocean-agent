"""Setup check: run this after installing to see whether everything is ready.

    python -m ocean_agent.doctor

Everything printed here is read by the person who ran it, not by Claude, so
it is written in English. The rest of the package answers in Korean on
purpose: those are tool results, and Claude restates them in whatever
language the user is writing. This file has no such translator in front of
it. A beta user ran it on 2026-09-03 and was handed a screen of Korean.
"""

import os
import shutil
import sys


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "[OK]" if ok else "[X] "
    print(f"{mark} {name}" + (f", {detail}" if detail else ""))
    return ok


def _claude_entry():
    """(config path, the ocean-agent entry). The entry is None when absent."""
    import json
    cands = []
    if sys.platform == "win32":
        cands.append(os.path.join(os.environ.get("APPDATA", ""),
                                  "Claude", "claude_desktop_config.json"))
    else:
        cands.append(os.path.expanduser(
            "~/Library/Application Support/Claude/"
            "claude_desktop_config.json"))
    # The installer (website/install.ps1) writes claude_desktop_config.json
    # above. The two below are developer layouts, checked as well so the
    # answer is not a false negative for someone running from a checkout.
    cands.append(os.path.expanduser("~/.claude.json"))
    cands.append(os.path.join(os.getcwd(), ".mcp.json"))
    first = ""
    for p in cands:
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        servers = d.get("mcpServers") or {}
        for name, ent in servers.items():
            if "ocean" in name.lower() or "ocean_agent" in str(ent):
                return p, ent if isinstance(ent, dict) else {}
        first = first or p          # first file that exists but lacks it
    return first, None


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    print("=== Ocean Agent setup check ===\n")
    all_ok = True

    # 1. Python version
    v = sys.version_info
    all_ok &= check("Python 3.10+", v >= (3, 10),
                    f"{v.major}.{v.minor}.{v.micro}")

    # 2. Python packages
    missing = []
    for mod in ("requests", "solders", "base58", "yaml", "dotenv"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    all_ok &= check("Python packages", not missing,
                    "all installed" if not missing
                    else f"missing: {missing} -> "
                         f"pip install -r requirements.txt")

    # 3. Node.js. Only needed for Pacifica's own MCP server. Our tools are
    # Python and do not touch it. On 09-03 a user read this cross as the
    # reason they were stuck, so the wording now says outright that a miss
    # here is not a problem.
    npx = shutil.which("npx")
    node_ok = npx is not None
    check("Node.js/npx", node_ok,
          npx or "not found. Only used in Pacifica MCP mode, so this does "
                 "not stop you from using Ocean Agent in Claude")

    # 4. Is it registered with Claude. For an MCP user this is the only line
    # that matters.
    #
    # 09-03: a beta user ran this from their home folder, got three crosses
    # and stopped. None of the three were their problem. config.yaml belongs
    # to the standalone bot and is absent for MCP users by design, the .env
    # is written elsewhere by the installer while this only looked in the
    # current folder, and Node.js is for Pacifica MCP mode. The one question
    # that mattered, "can Claude see the tools", was never asked.
    cfg_path, entry = _claude_entry()
    reg_ok = entry is not None
    check("Registered with Claude", reg_ok,
          f"found in {os.path.basename(cfg_path)}" if reg_ok
          else ("ocean-agent is not in your Claude config. Run the install "
                "command again" if cfg_path else "could not find a Claude "
                "config file. Open Claude once, then try again"))
    all_ok &= reg_ok

    # 5. Config file. When registered as MCP, having no config.yaml is the
    # normal case, so it is only counted against you when it is needed.
    cfg_ok = os.path.exists("config.yaml")
    if reg_ok:
        if cfg_ok:
            check("config.yaml", True, "")
    else:
        all_ok &= check("config.yaml", cfg_ok,
                        "" if cfg_ok else "check that you are running this "
                                          "from the bot folder")

    import yaml
    from dotenv import load_dotenv
    # Look for the installer's .env in the same order the MCP server does.
    # Checking only the current folder tells someone running from their home
    # directory that they have no keys, which is a lie.
    env_used = None
    for _cand in (os.environ.get("PACIFICA_ENV_FILE"),
                  ((entry or {}).get("env") or {}).get("PACIFICA_ENV_FILE"),
                  os.path.join(os.path.dirname(os.path.dirname(
                      os.path.abspath(__file__))), ".env"),
                  os.path.join(os.getcwd(), ".env")):
        if _cand and os.path.exists(_cand):
            load_dotenv(_cand, override=False)
            env_used = _cand
            break
    if env_used:
        print(f"     (.env found at: {env_used})")
    cfg = {}
    if cfg_ok:
        with open("config.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

    from . import address_from_env, api_key_from_env
    # Assume mainnet when there is no config.yaml. This used to be an empty
    # string, which left the network undecided, so an MCP user with working
    # keys was told they had none.
    _net_url = cfg.get("base_url", "https://api.pacifica.fi")
    address = address_from_env(_net_url)
    key = api_key_from_env(_net_url)
    net_label = "testnet" if "test-api" in _net_url else "mainnet"
    check(f".env, wallet address ({net_label})", bool(address),
          f"{address[:6]}...{address[-4:]}" if address
          else "not set. Needed for account lookups and live orders "
               "(prices still work without it)")
    check(f".env, API key ({net_label})", bool(key),
          "set" if key else "not set. Dry runs work, live orders do not "
                            "(create one at app.pacifica.fi/apikey)")

    # 6. A real connection test. Runs without config.yaml. For an MCP user
    # this is the only line that shows whether the keys actually work.
    base_url = cfg.get("base_url", "https://api.pacifica.fi")
    api_mode = cfg.get("api_mode", "rest")
    print(f"\nConnection test ({api_mode.upper()} / {base_url}) ...")
    try:
        if api_mode == "mcp" and node_ok and cfg_ok:
            from .mcp_client import PacificaMCPClient
            client = PacificaMCPClient(base_url, address=address)
        else:
            from .api_client import PacificaClient
            client = PacificaClient(base_url, address=address)
        prices = client.get_prices()
        check("Prices", True, f"{len(prices)} markets received")
        if address:
            try:
                acct = client.get_account()
                check("Account", True,
                      f"balance {acct.get('balance', '?')} USDC, "
                      f"equity {acct.get('account_equity', '?')}")
            except Exception as e:                      # noqa: BLE001
                check("Account", False, str(e)[:120])
    except Exception as e:                              # noqa: BLE001
        all_ok = False
        check("Connection", False, str(e)[:150])

    if not reg_ok:
        print("\n[!] Not registered with Claude. Run the install command "
              "again, then quit Claude completely and reopen it. Closing "
              "the window is not enough: on Windows, right-click the tray "
              "icon next to the clock and choose Quit.")
    elif all_ok:
        print("\n[OK] Ready. Quit Claude completely, open it again, and say "
              "\"show me today's picks\" in Claude's chat box.")
    else:
        print("\n[!] Fix the items marked [X] above, then run this again.")


if __name__ == "__main__":
    main()
