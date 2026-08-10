"""Pacifica 요청 서명, 공식 python-sdk common/utils.py 방식을 그대로 따름.

서명 대상 메시지는 {헤더 필드..., "data": 페이로드}를 키 정렬 후
compact JSON으로 직렬화한 문자열이다.
"""

import json
import time

import base58
from solders.keypair import Keypair


def _sort_json_keys(value):
    if isinstance(value, dict):
        return {k: _sort_json_keys(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [_sort_json_keys(item) for item in value]
    return value


def _prepare_message(header: dict, payload: dict) -> str:
    if not {"type", "timestamp", "expiry_window"} <= header.keys():
        raise ValueError("Header must have type, timestamp, and expiry_window")
    data = {**header, "data": payload}
    return json.dumps(_sort_json_keys(data), separators=(",", ":"))


def sign_operation(op_type: str, payload: dict, keypair: Keypair,
                   expiry_window: int = 5_000) -> dict:
    """페이로드에 서명하고, API 요청 body에 합칠 공통 필드를 반환한다."""
    timestamp = int(time.time() * 1_000)
    header = {"timestamp": timestamp, "expiry_window": expiry_window, "type": op_type}
    message = _prepare_message(header, payload)
    signature = keypair.sign_message(message.encode("utf-8"))
    return {
        "signature": base58.b58encode(bytes(signature)).decode("ascii"),
        "timestamp": timestamp,
        "expiry_window": expiry_window,
    }


def keypair_from_base58(private_key: str) -> Keypair:
    return Keypair.from_base58_string(private_key)
