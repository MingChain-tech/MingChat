# Changelog

All notable changes to MingChat (铭信) will be documented in this file.

## [0.3.5] — 2026-05-17

### Added
- **MingDID on-chain resolution**: `MingDID.resolve()` fetches DID documents from BSV chain via WoC API
- **sender_hash160 → DID auto-mapping**: Incoming messages automatically resolve sender DID in Bridge
- **Bridge `GET /did/{did}` endpoint**: Real-time on-chain DID resolution via REST API
- **MCP `mingchat_did_resolve`**: Enhanced to return pubkey, address, KYC fields from chain
- **CompactSize/VarInt parsing**: `_read_varint()` in spv.py for proper Bitcoin protocol field parsing
- **CI version check**: GitHub Actions workflow now validates pyproject.toml == __init__.py == git tag consistency before publish
- **CI test step**: All 71 tests run as gate before PyPI publish

### Fixed
- **OP_RETURN extraction bug**: DID_REGISTER transactions with script_len ≥ 253 failed parsing (fixed VarInt support)
- **pyproject.toml version sync**: Version was stuck at 0.3.2 while code was 0.3.5, causing PyPI publish failures
- **SPV test failures**: 3 test_op_return tests fixed — hex data updated from 2-byte LE uint16 to CompactSize encoding
- **Bridge test timeout**: Fixed `_TestServer` to use `serve_forever()` instead of single `handle_request()`
- **PyPI auto-publish**: New token + fixed version → GitHub Actions publishes successfully

### Changed
- `pyproject.toml` version bumped to 0.3.5 (was erroneously 0.3.2)
- GitHub Secrets `PYPI_TOKEN` updated with fresh API token
- `extract_op_return()` now uses proper VarInt/CompactSize for transaction field parsing
- Tests: 71/71 passing (was 67/71)

## [0.3.3] — 2026-05-16

### Added
- **Message Fee Plan C**: Sender-decided `msg_fee` parameter in `send()`, UTXO output to receiver
- **4-tier priority**: 0 sat (free), 1-99 (low), 100-999 (medium), ≥1000 (high/VIP instant push)
- **Bridge Feishu push grading**: High-priority messages push instantly, others batched every 8s
- **API filtering**: `?priority=` and `?min_fee=` query parameters on `GET /messages`
- **Stats endpoint**: `GET /stats/msg-fee` for fee statistics
- **Message fee extraction**: `extract_msg_fee_from_tx()` in spv.py for SPV-level fee parsing
- **MCP msg_fee support**: `mingchat_send` tool accepts optional `msg_fee` parameter
- **Bridge `/notify-tx/{txid}`**: Manual transaction injection for SPV coverage completeness

## [0.3.2] — 2026-05-16

### Added
- **Reputation system**: Three message types — REPUTATION_SCORE (0x30), REPUTATION_REVIEW (0x31), REPUTATION_BOND (0x32)
- **Reputation data models**: `ReputationScore`, `ReputationReview`, `ReputationBond` with ECDSA signatures
- **ReputationStore**: Local caching of reputation data, auto-synced from chain via SPV listener
- **Bridge reputation API**: `GET /reputation/{did}/scores`, `/bonds`, `/stats` endpoints
- **MCP reputation tools**: `mingchat_rep_score`, `mingchat_rep_query`, `mingchat_rep_bond`
- **PyPI auto-publish**: GitHub Actions workflow triggers on `v*` tag push

### Changed
- `__init__.py` exports expanded to include all reputation classes
- `setup.py` removed duplicate `version` field (now sourced from `pyproject.toml`)

## [0.3.1] — 2026-05-16

### Added
- **Bridge daemon**: Production-ready systemd service with SPV WoC polling listener
- **Bridge REST API**: `GET /health`, `/status`, `/messages`, `POST /send` endpoints
- **Bridge config persistence**: JSON config file for webhook, settings
- **Bridge Feishu integration**: Push new messages to Feishu via Lark API

### Fixed
- hex-to-WIF auto-detection in bridge_server + mcp_server global variable fix

## [0.3.0] — 2026-05-15

### Added
- **MCH Protocol v0.3**: 86B fixed header (magic "MINC", version, type, sender, receiver, timestamp, payload hash)
- **21 message types**: CHAT, RPC_REQ/RESP, ACK, BROADCAST, PUBLISH, BID, ASSIGN, PROGRESS, DELIVER, ACCEPT, REJECT, ARBITRATE, SETTLE, CANCEL, DID_REGISTER/UPDATE/REVOKE
- **MingTask protocol**: Full task lifecycle from publish to settle, 10 operation codes
- **MingID (did:bsv)**: On-chain identity with DID document generation, register, resolve
- **MCP Server**: 14 tools for messaging, tasks, identity, SPV
- **SPV verification**: Merkle proof construction and verification, block header validation
- **CLI tool**: `mingchat` command for send/read/listen/status
- **Pure Python secp256k1**: No external cryptographic dependencies
- **BSV broadcast**: OP_RETURN transaction building and broadcast via WoC API
