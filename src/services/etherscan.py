"""Etherscan API service for Ethereum on-chain data (wallet balances, token transfers, contracts)."""
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ETHERSCAN_API = "https://api.etherscan.io/api"

# Well-known ERC-20 token contracts on Ethereum mainnet
TOKEN_CONTRACTS = {
    "USDC": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "USDT": "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "WETH": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "DAI": "0x6b175474e89094c44da98b954eedeac495271d0f",
}

# Decimals per token (used for converting raw amounts)
TOKEN_DECIMALS = {
    "USDC": 6,
    "USDT": 6,
    "WETH": 18,
    "DAI": 18,
}


def _format_amount(raw_value: str, decimals: int) -> float:
    """Convert a raw token amount string to a human-readable float."""
    try:
        return int(raw_value) / (10 ** decimals)
    except (ValueError, TypeError):
        return 0.0


def _short_address(address: str) -> str:
    """Shorten an Ethereum address for display."""
    if len(address) >= 10:
        return f"{address[:6]}...{address[-4:]}"
    return address


def _ts_to_str(timestamp: str) -> str:
    """Convert a unix timestamp string to a readable UTC datetime."""
    try:
        dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return timestamp


class EtherscanService:
    """
    Etherscan API client for Ethereum on-chain data.

    Note: Free tier is limited to 5 calls/sec. Add delays between rapid
    successive calls if needed.
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._client = httpx.Client(timeout=20.0)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _get(self, params: dict) -> dict:
        """Make authenticated GET request to Etherscan API."""
        params["apikey"] = self.api_key
        resp = self._client.get(ETHERSCAN_API, params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # 1. Token transfers
    # ------------------------------------------------------------------

    def get_wallet_token_transfers(self, address: str, token: str = "USDC") -> str:
        """
        Get recent ERC-20 token transfers for a wallet.

        Args:
            address: Ethereum wallet address.
            token: Token symbol to filter on (USDC, USDT, etc.).
                   Pass "ALL" to show all ERC-20 transfers.

        Returns:
            Formatted transfer report string for LLM consumption.
        """
        addr = address.strip().lower()
        token_upper = token.strip().upper()

        try:
            params = {
                "module": "account",
                "action": "tokentx",
                "address": addr,
                "sort": "desc",
                "page": "1",
                "offset": "20",
            }

            data = self._get(params)

            if data.get("status") != "1" or not data.get("result"):
                message = data.get("message", "No transfers found")
                return f"No ERC-20 token transfers found for {_short_address(addr)}: {message}"

            transfers = data["result"]

            # Filter by token if not "ALL"
            if token_upper != "ALL":
                contract = TOKEN_CONTRACTS.get(token_upper, "").lower()
                if contract:
                    transfers = [
                        tx for tx in transfers
                        if tx.get("contractAddress", "").lower() == contract
                    ]
                else:
                    # Try matching by symbol in the response
                    transfers = [
                        tx for tx in transfers
                        if tx.get("tokenSymbol", "").upper() == token_upper
                    ]

            if not transfers:
                return f"No {token_upper} transfers found for {_short_address(addr)} in the last 20 token transactions."

            lines = [f"--- Token Transfers for {_short_address(addr)} ({token_upper}) ---"]

            for tx in transfers:
                tx_from = tx.get("from", "").lower()
                tx_to = tx.get("to", "").lower()
                symbol = tx.get("tokenSymbol", "???")
                decimals = int(tx.get("tokenDecimal", TOKEN_DECIMALS.get(symbol.upper(), 18)))
                raw_value = tx.get("value", "0")
                amount = _format_amount(raw_value, decimals)
                ts = _ts_to_str(tx.get("timeStamp", ""))
                tx_hash = tx.get("hash", "")

                # Determine direction
                if tx_from == addr:
                    direction = "OUT"
                    counterparty = _short_address(tx_to)
                elif tx_to == addr:
                    direction = "IN"
                    counterparty = _short_address(tx_from)
                else:
                    direction = "???"
                    counterparty = f"{_short_address(tx_from)} -> {_short_address(tx_to)}"

                # Flag large transfers
                large_flag = ""
                if symbol.upper() in ("USDC", "USDT", "DAI") and amount > 10_000:
                    large_flag = " [LARGE]"
                elif symbol.upper() == "WETH" and amount > 5:
                    large_flag = " [LARGE]"

                lines.append(
                    f"  {direction} {amount:,.2f} {symbol}{large_flag} | "
                    f"{'to' if direction == 'OUT' else 'from'}: {counterparty} | {ts}"
                )

            lines.append("---")
            return "\n".join(lines)

        except httpx.HTTPError as e:
            msg = f"Etherscan API error fetching token transfers for '{address}': {e}"
            logger.error(msg)
            return msg
        except Exception as e:
            msg = f"Etherscan token transfer query failed for '{address}': {e}"
            logger.error(msg)
            return msg

    # ------------------------------------------------------------------
    # 2. Contract info
    # ------------------------------------------------------------------

    def get_contract_info(self, address: str) -> str:
        """
        Check if an address is a smart contract and retrieve basic contract metadata.

        Args:
            address: Ethereum address to inspect.

        Returns:
            Formatted contract info string for LLM consumption.
        """
        addr = address.strip()

        try:
            # First check if ABI is available (verified contract)
            abi_data = self._get({
                "module": "contract",
                "action": "getabi",
                "address": addr,
            })

            is_verified = abi_data.get("status") == "1"

            # Get source code info (includes contract name, compiler, etc.)
            source_data = self._get({
                "module": "contract",
                "action": "getsourcecode",
                "address": addr,
            })

            results = source_data.get("result", [])

            lines = [f"--- Contract Info for {_short_address(addr)} ---"]

            if not results or (isinstance(results, list) and len(results) == 0):
                lines.append("No contract data returned. Address may be an EOA (externally owned account).")
                lines.append("---")
                return "\n".join(lines)

            info = results[0] if isinstance(results, list) else results

            contract_name = info.get("ContractName", "")
            compiler = info.get("CompilerVersion", "")
            optimization = info.get("OptimizationUsed", "")
            proxy = info.get("Proxy", "0")
            implementation = info.get("Implementation", "")

            if not contract_name:
                lines.append("This address does not appear to be a verified contract.")
                lines.append("It may be an EOA (regular wallet) or an unverified contract.")
            else:
                lines.append(f"Contract Name: {contract_name}")
                lines.append(f"Verified: {'Yes' if is_verified else 'No'}")
                if compiler:
                    lines.append(f"Compiler: {compiler}")
                if optimization:
                    lines.append(f"Optimization: {'Yes' if optimization == '1' else 'No'}")
                if proxy == "1":
                    lines.append(f"Proxy Contract: Yes")
                    if implementation:
                        lines.append(f"Implementation: {implementation}")

            lines.append("---")
            return "\n".join(lines)

        except httpx.HTTPError as e:
            msg = f"Etherscan API error fetching contract info for '{address}': {e}"
            logger.error(msg)
            return msg
        except Exception as e:
            msg = f"Etherscan contract query failed for '{address}': {e}"
            logger.error(msg)
            return msg

    # ------------------------------------------------------------------
    # 3. ETH balance
    # ------------------------------------------------------------------

    def get_wallet_eth_balance(self, address: str) -> str:
        """
        Get ETH balance for a wallet address.

        Args:
            address: Ethereum wallet address.

        Returns:
            Formatted ETH balance string for LLM consumption.
        """
        addr = address.strip()

        try:
            data = self._get({
                "module": "account",
                "action": "balance",
                "address": addr,
                "tag": "latest",
            })

            if data.get("status") != "1":
                message = data.get("message", "Unknown error")
                return f"Could not fetch ETH balance for {_short_address(addr)}: {message}"

            raw_balance = data.get("result", "0")
            eth_balance = _format_amount(raw_balance, 18)

            lines = [
                f"--- ETH Balance for {_short_address(addr)} ---",
                f"Balance: {eth_balance:,.6f} ETH",
                "---",
            ]
            return "\n".join(lines)

        except httpx.HTTPError as e:
            msg = f"Etherscan API error fetching ETH balance for '{address}': {e}"
            logger.error(msg)
            return msg
        except Exception as e:
            msg = f"Etherscan balance query failed for '{address}': {e}"
            logger.error(msg)
            return msg
