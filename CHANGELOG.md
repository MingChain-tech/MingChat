# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-01-01

### Added
- Initial release of MingChat
- BSV blockchain-based agent communication protocol
- OP_RETURN 86-byte fixed header protocol (MINC)
- 18 message types support (CHAT, RPC_REQ, RPC_RESP, BROADCAST, etc.)
- Pure Python secp256k1 signature implementation (no external crypto dependencies)
- MingChat client with send/receive/listen capabilities
- CLI tool with send, read, status, listen commands
- MCP Server integration (standard and enhanced versions)
- Hermes Agent integration support
- WIF private key management
- Address and Hash160 conversion utilities
- WhatsOnChain API integration for blockchain operations

### Features
- Decentralized messaging without centralized servers
- Privacy protection via Hash160 addresses
- Extremely low transaction fees (hundreds of satoshis)
- Real-time message listening via polling
- RPC call support for agent coordination

### Technical Details
- Protocol ID: 0x4D494E43 ("MINC")
- Header Size: 86 bytes fixed
- Supports mainnet and testnet
- Pure Python cryptographic implementation

## [Unreleased]

### Planned
- SPV wallet verification
- DID (Decentralized Identity) support
- Multi-signature transactions
- Encrypted message content
- Performance optimizations
