# -*- coding: utf-8 -*-
"""Terms-of-use consent: ask the user once on an interactive console, then
approve the builder code with their own signature.

Rules (2026-08-09, user decisions):
  - The question is simply "Have you agreed to the Terms of Use?" in the
    user's OS language. The Terms on the site describe the developer fee
    (section 11); answering yes accepts the Terms and approves that fee.
  - NEVER approve silently. The prompt only appears on a real console
    (stdin isatty) - MCP servers and services never see it.
  - Installer (main below): declining ABORTS the install (exit code 3) and
    is not stored, so rerunning the installer asks again.
  - Bot console: declining is remembered and never blocks trading; orders
    simply go out without the code.
"""
import os
import sys

MARKER = os.path.join(os.path.expanduser("~"), ".ocean_agent_builder_consent")
MAX_FEE_RATE = "0.0001"          # 1 bps; published at oceanagent.fi

MESSAGES = {
    "en": {"q": "\nHave you agreed to the Terms of Use?"
                " (oceanagent.fi) [Y/n] ",
           "ok": "Thank you! Terms accepted.",
           "no": "No problem. Ocean Agent works the same without it.",
           "fail": "Could not record the acceptance ({err}); continuing.",
           "must": "The Terms of Use must be accepted to install. Installation cancelled."},
    "ko": {"q": "\n이용약관(oceanagent.fi)에 동의하셨습니까? [Y/n] ",
           "ok": "감사합니다! 약관에 동의하셨습니다.",
           "no": "괜찮습니다. 동의 없이도 모든 기능이 동일하게 동작합니다.",
           "fail": "동의 기록에 실패했습니다({err}). 설치는 계속됩니다.",
           "must": "설치하려면 이용약관 동의가 필요합니다. 설치를 중단합니다."},
    "ja": {"q": "\n利用規約(oceanagent.fi)に同意しましたか? [Y/n] ",
           "ok": "ありがとうございます!規約に同意しました。",
           "no": "問題ありません。同意なしでも全機能は同じように動作します。",
           "fail": "同意の記録に失敗しました({err})。セットアップは続行します。",
           "must": "インストールには利用規約への同意が必要です。中止します。"},
    "zh": {"q": "\n您是否已同意使用条款?(oceanagent.fi) [Y/n] ",
           "ok": "谢谢!您已同意条款。",
           "no": "没关系。不同意也不影响任何功能。",
           "fail": "无法记录同意({err}),安装将继续。",
           "must": "安装需要同意使用条款。已取消安装。"},
    "es": {"q": "\n¿Ha aceptado los Términos de Uso?"
                " (oceanagent.fi) [Y/n] ",
           "ok": "¡Gracias! Términos aceptados.",
           "no": "Sin problema. Ocean Agent funciona igual sin ello.",
           "fail": "No se pudo registrar la aceptación ({err}); continuando.",
           "must": "Para instalar es necesario aceptar los Términos de Uso. Instalación cancelada."},
    "fr": {"q": "\nAvez-vous accepté les Conditions d'Utilisation ?"
                " (oceanagent.fi) [Y/n] ",
           "ok": "Merci ! Conditions acceptées.",
           "no": "Pas de souci. Ocean Agent fonctionne pareil sans cela.",
           "fail": "Impossible d'enregistrer l'acceptation ({err});"
                   " on continue.",
           "must": "L'installation nécessite d'accepter les Conditions. Installation annulée."},
    "de": {"q": "\nHaben Sie den Nutzungsbedingungen zugestimmt?"
                " (oceanagent.fi) [Y/n] ",
           "ok": "Danke! Bedingungen akzeptiert.",
           "no": "Kein Problem. Ocean Agent funktioniert genauso ohne.",
           "fail": "Zustimmung konnte nicht gespeichert werden ({err});"
                   " weiter geht's.",
           "must": "Zur Installation müssen die Nutzungsbedingungen akzeptiert werden. Abgebrochen."},
    "pt": {"q": "\nVocê concordou com os Termos de Uso?"
                " (oceanagent.fi) [Y/n] ",
           "ok": "Obrigado! Termos aceitos.",
           "no": "Sem problema. O Ocean Agent funciona igual sem isso.",
           "fail": "Não foi possível registrar a aceitação ({err});"
                   " continuando.",
           "must": "Para instalar é preciso aceitar os Termos de Uso. Instalação cancelada."},
}


def _msgs() -> dict:
    """Pick messages by OS language, English fallback."""
    lang = ""
    try:
        import locale
        loc = locale.getdefaultlocale()[0] or ""
        lang = loc[:2].lower()
    except Exception:
        pass
    if not lang:
        lang = (os.environ.get("LANG", "")[:2] or "en").lower()
    return MESSAGES.get(lang, MESSAGES["en"])


def _marker() -> str:
    try:
        with open(MARKER, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _remember(value: str) -> None:
    try:
        with open(MARKER, "w", encoding="utf-8") as f:
            f.write(value)
    except OSError:
        pass


def ask_terms_only() -> str:
    """Ask the Terms question with no account attached.

    Returns 'approved' | 'declined' | 'skipped'. The answer goes in the same
    marker file, so the builder approval that runs once keys exist reads it
    instead of asking a second time.
    """
    remembered = _marker()
    if remembered in ("approved", "declined"):
        return remembered
    if not sys.stdin.isatty():
        return "skipped"
    m = _msgs()
    try:
        answer = input(m["q"]).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "skipped"
    if answer in ("", "y", "yes"):
        _remember("approved")
        print(m["ok"])
        return "approved"
    print(m["no"])
    return "declined"


def ensure_consent(client, builder_code: str,
                   remember_decline: bool = True) -> str:
    """Returns 'approved' | 'declined' | 'skipped'. Never raises.

    remember_decline=False (installer): a decline aborts the install and is
    NOT stored, so rerunning the installer asks again."""
    if not builder_code or not getattr(client, "keypair", None):
        return "skipped"
    remembered = _marker()
    if remembered in ("approved", "declined"):
        return remembered
    try:
        approved = {a.get("builder_code") for a in
                    client.get_builder_approvals()}
        if builder_code in approved:
            _remember("approved")
            return "approved"
    except Exception:
        pass                       # cannot check -> may still ask below
    if not sys.stdin.isatty():
        return "skipped"           # never prompt inside MCP or services
    m = _msgs()
    try:
        answer = input(m["q"]).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "skipped"
    if answer in ("", "y", "yes"):
        try:
            client.approve_builder_code(builder_code, MAX_FEE_RATE)
            _remember("approved")
            print(m["ok"])
            return "approved"
        except Exception as e:
            print(m["fail"].format(err=e))
            return "skipped"
    if remember_decline:
        _remember("declined")
        print(m["no"])
    return "declined"


def main() -> None:
    """`python -m ocean_agent.builder_consent` - used by the installer."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    try:                            # same .env discovery as mcp_server
        from dotenv import load_dotenv
        for cand in (os.environ.get("PACIFICA_ENV_FILE"),
                     os.path.join(os.path.expanduser("~"),
                                  ".ocean-agent", ".env"),
                     os.path.join(os.getcwd(), ".env")):
            if cand and os.path.exists(cand):
                load_dotenv(cand, override=False)
                break
    except ImportError:
        pass
    from .autonomous import load_policy, make_client
    policy = load_policy()
    os.environ.setdefault("PACIFICA_BASE_URL", policy["base_url"])
    try:
        client = make_client(policy)
    except Exception:
        # No keys yet is the normal case when the terms are asked before
        # the account is connected, which is the order that lets a decline
        # leave nothing on disk. The question is about the Terms of Use
        # and can be answered without an account; the on-chain approval
        # that follows needs one and happens later on its own.
        client = None
    if client is None:
        if ask_terms_only() == "declined":
            print(_msgs()["must"])
            sys.exit(3)
        return
    status = ensure_consent(client, policy.get("builder_code", ""),
                            remember_decline=False)
    if status == "declined":
        print(_msgs()["must"])
        sys.exit(3)


if __name__ == "__main__":
    main()
