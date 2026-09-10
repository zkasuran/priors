"""Base (Sepolia) integration.

Two things, both serving the product's actual function of vetting on-chain
counterparties:

  1. read priors  -- `onchain_profile` reads a counterparty's live state on
     Base Sepolia (balance, transaction count, contract-or-EOA). A brand-new
     address with no on-chain footprint is a caution prior, folded into the
     verdict by the engine. This needs no gas and runs in the demo live.

  2. attest       -- `attest_outcome` writes a graded outcome to the Ethereum
     Attestation Service on Base, turning the local reputation record into a
     public, verifiable on-chain trail. This needs a funded key; when the key
     is unfunded the call returns a prepared, unsent attestation and the exact
     funding step, so it is a one-command finish rather than a dead end.

Addresses were verified live against Base Sepolia: EAS.getSchemaRegistry() at
0x42..21 returns 0x42..20, so 0x42..21 is EAS and 0x42..20 is the registry.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

BASE_SEPOLIA_RPCS = [
    os.environ.get("BASE_SEPOLIA_RPC", "").strip() or "https://sepolia.base.org",
    "https://base-sepolia-rpc.publicnode.com",
]
CHAIN_ID = 84532
EAS_ADDRESS = "0x4200000000000000000000000000000000000021"
SCHEMA_REGISTRY_ADDRESS = "0x4200000000000000000000000000000000000020"
EXPLORER = "https://sepolia.basescan.org"

# The attestation schema Priors publishes for every graded outcome.
EAS_SCHEMA = "string svc_category,address counterparty,string verdict,uint8 score,string evidence"
_SCHEMA_TYPES = ["string", "address", "string", "uint8", "string"]

_EAS_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "schema", "type": "bytes32"},
                    {
                        "components": [
                            {"name": "recipient", "type": "address"},
                            {"name": "expirationTime", "type": "uint64"},
                            {"name": "revocable", "type": "bool"},
                            {"name": "refUID", "type": "bytes32"},
                            {"name": "data", "type": "bytes"},
                            {"name": "value", "type": "uint256"},
                        ],
                        "name": "data",
                        "type": "tuple",
                    },
                ],
                "name": "request",
                "type": "tuple",
            }
        ],
        "name": "attest",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "payable",
        "type": "function",
    }
]

_REGISTRY_ABI = [
    {
        "inputs": [
            {"name": "schema", "type": "string"},
            {"name": "resolver", "type": "address"},
            {"name": "revocable", "type": "bool"},
        ],
        "name": "register",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

ZERO_BYTES32 = "0x" + "00" * 32
ZERO_ADDR = "0x0000000000000000000000000000000000000000"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_w3(timeout: float = 10.0):
    from web3 import Web3

    last = None
    for url in BASE_SEPOLIA_RPCS:
        if not url:
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
            if w3.is_connected():
                return w3
        except Exception as e:  # pragma: no cover - network
            last = e
    raise RuntimeError(f"no Base Sepolia RPC reachable ({last!r})")


def onchain_profile(address: str, timeout: float = 10.0) -> dict[str, Any]:
    """Read a counterparty's live state on Base Sepolia. No gas, read-only."""
    from web3 import Web3

    w3 = get_w3(timeout)
    addr = Web3.to_checksum_address(address)
    balance = w3.eth.get_balance(addr)
    tx_count = w3.eth.get_transaction_count(addr)
    code = w3.eth.get_code(addr)
    return {
        "chain": "base-sepolia",
        "chain_id": CHAIN_ID,
        "address": addr,
        "balance_eth": float(Web3.from_wei(balance, "ether")),
        "tx_count": tx_count,
        "is_contract": len(code) > 0,
        "age_days": _age_days(addr, timeout),
        "checked_at": _now_iso(),
    }


def _age_days(address: str, timeout: float) -> float | None:
    """Best-effort account age from Basescan, if an API key is set. Optional."""
    key = os.environ.get("BASESCAN_API_KEY")
    if not key:
        return None
    try:  # pragma: no cover - network + optional
        import httpx

        url = (
            f"https://api-sepolia.basescan.org/api?module=account&action=txlist"
            f"&address={address}&startblock=0&endblock=99999999&page=1&offset=1"
            f"&sort=asc&apikey={key}"
        )
        r = httpx.get(url, timeout=timeout)
        rows = r.json().get("result") or []
        if rows:
            first_ts = int(rows[0]["timeStamp"])
            return round((time.time() - first_ts) / 86400.0, 1)
    except Exception:
        return None
    return None


def cache_onchain_prior(mem, address: str) -> dict[str, Any]:
    """Read Base Sepolia and fold the profile into the counterparty's card."""
    prof = onchain_profile(address)
    from .memory import norm_addr

    cp = mem.get_counterparty(address) or {"address": norm_addr(address)}
    cp["onchain"] = prof
    mem.put_counterparty(address, cp)
    return prof


# ---- EAS attestation (write path) ---------------------------------------
def encode_attestation_data(svc_category: str, counterparty: str, verdict: str, score: int, evidence: str) -> bytes:
    """ABI-encode the attestation payload for our schema. Pure, offline, tested."""
    from eth_abi import encode
    from web3 import Web3

    return encode(
        _SCHEMA_TYPES,
        [svc_category, Web3.to_checksum_address(counterparty), verdict, int(max(0, min(255, score))), evidence],
    )


def account_address(private_key: str) -> str:
    from eth_account import Account

    return Account.from_key(private_key).address


def register_schema(private_key: str, timeout: float = 30.0) -> dict[str, Any]:
    """Register the Priors schema on Base Sepolia. Needs a funded key. Returns tx info."""
    from web3 import Web3

    w3 = get_w3(timeout)
    acct = w3.eth.account.from_key(private_key)
    reg = w3.eth.contract(address=Web3.to_checksum_address(SCHEMA_REGISTRY_ADDRESS), abi=_REGISTRY_ABI)
    fn = reg.functions.register(EAS_SCHEMA, ZERO_ADDR, True)
    tx = fn.build_transaction(
        {"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address),
         "chainId": CHAIN_ID, "gas": 400000, "maxFeePerGas": w3.eth.gas_price * 2,
         "maxPriorityFeePerGas": w3.to_wei(0.01, "gwei")}
    )
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=timeout)
    # schema UID is the first topic of the Registered event; compute deterministically instead
    uid = schema_uid(EAS_SCHEMA, ZERO_ADDR, True)
    return {"tx_hash": h.hex(), "schema_uid": uid, "explorer": f"{EXPLORER}/tx/{h.hex()}"}


def schema_uid(schema: str = EAS_SCHEMA, resolver: str = ZERO_ADDR, revocable: bool = True) -> str:
    """EAS derives a schema UID as keccak256(abi.encodePacked(schema, resolver, revocable))."""
    from eth_utils import keccak
    from web3 import Web3
    from eth_abi.packed import encode_packed

    packed = encode_packed(["string", "address", "bool"], [schema, Web3.to_checksum_address(resolver), revocable])
    return "0x" + keccak(packed).hex()


def attest_outcome(
    address: str,
    svc_category: str,
    verdict: str,
    score: int,
    evidence: str,
    private_key: str | None = None,
    schema_uid_hex: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Attest a graded outcome on Base via EAS.

    With a funded key it sends the transaction and returns the on-chain hash.
    Without a key or funds it returns the fully prepared, unsent attestation and
    the exact funding command, so finishing is one step.
    """
    data = encode_attestation_data(svc_category, address, verdict, score, evidence)
    uid = schema_uid_hex or os.environ.get("EAS_SCHEMA_UID") or schema_uid()
    prepared = {
        "sent": False,
        "eas": EAS_ADDRESS,
        "schema": EAS_SCHEMA,
        "schema_uid": uid,
        "recipient": address,
        "data_hex": "0x" + data.hex(),
        "chain": "base-sepolia",
    }

    pk = private_key or os.environ.get("PRIORS_ATTEST_KEY")
    if not pk:
        prepared["status"] = "no key: set PRIORS_ATTEST_KEY to a funded Base Sepolia key, then re-run"
        return prepared

    from web3 import Web3

    w3 = get_w3(timeout)
    acct = w3.eth.account.from_key(pk)
    prepared["from"] = acct.address
    bal = w3.eth.get_balance(acct.address)
    if bal == 0:
        prepared["status"] = (
            f"unfunded: send Base Sepolia ETH to {acct.address} "
            f"(faucet https://portal.cdp.coinbase.com/products/faucet), then re-run"
        )
        return prepared

    eas = w3.eth.contract(address=Web3.to_checksum_address(EAS_ADDRESS), abi=_EAS_ABI)
    request = (
        Web3.to_bytes(hexstr=uid),
        (Web3.to_checksum_address(address), 0, True, Web3.to_bytes(hexstr=ZERO_BYTES32), data, 0),
    )
    fn = eas.functions.attest(request)
    tx = fn.build_transaction(
        {"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address),
         "chainId": CHAIN_ID, "gas": 350000, "maxFeePerGas": w3.eth.gas_price * 2,
         "maxPriorityFeePerGas": w3.to_wei(0.01, "gwei"), "value": 0}
    )
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=timeout)
    att_uid = "0x" + rcpt["logs"][0]["data"].hex()[-64:] if rcpt.get("logs") else None
    prepared.update({
        "sent": True,
        "tx_hash": h.hex(),
        "attestation_uid": att_uid,
        "explorer": f"{EXPLORER}/tx/{h.hex()}",
        "status": "attested",
    })
    return prepared
