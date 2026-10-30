// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title PakVerifyRegistry
 * @notice Public, tamper-proof anchor for PakVerify police certificates.
 *
 * PakVerify keeps its full audit ledger in PostgreSQL (a SHA-256 hash chain).
 * For every issued certificate the backend also writes the certificate's
 * SHA-256 PDF hash and the matching ledger block hash to this contract, so
 * anyone can verify a certificate on-chain (for example with MetaMask)
 * without trusting the PakVerify database.
 *
 * No personal data (names, CNIC numbers) is ever written on-chain.
 */
contract PakVerifyRegistry {
    struct Anchor {
        bytes32 ledgerBlockHash;   // current_hash of the CERTIFICATE_ISSUE ledger block
        uint32 ledgerBlockIndex;   // block_index of that ledger block
        uint64 anchoredAt;         // block.timestamp when anchored
    }

    address public owner;
    uint256 public totalAnchored;
    mapping(bytes32 => Anchor) private anchors; // certificate SHA-256 => anchor

    event CertificateAnchored(
        bytes32 indexed certificateHash,
        bytes32 ledgerBlockHash,
        uint32 ledgerBlockIndex,
        string certificateNumber
    );
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    modifier onlyOwner() {
        require(msg.sender == owner, "PakVerify: caller is not the owner");
        _;
    }

    constructor() {
        owner = msg.sender;
        emit OwnershipTransferred(address(0), msg.sender);
    }

    function anchorCertificate(
        bytes32 certificateHash,
        bytes32 ledgerBlockHash,
        uint32 ledgerBlockIndex,
        string calldata certificateNumber
    ) external onlyOwner {
        require(certificateHash != bytes32(0), "PakVerify: empty hash");
        require(anchors[certificateHash].anchoredAt == 0, "PakVerify: already anchored");

        anchors[certificateHash] = Anchor(ledgerBlockHash, ledgerBlockIndex, uint64(block.timestamp));
        totalAnchored += 1;
        emit CertificateAnchored(certificateHash, ledgerBlockHash, ledgerBlockIndex, certificateNumber);
    }

    function verifyCertificate(bytes32 certificateHash)
        external
        view
        returns (bool anchored, bytes32 ledgerBlockHash, uint32 ledgerBlockIndex, uint64 anchoredAt)
    {
        Anchor memory a = anchors[certificateHash];
        return (a.anchoredAt != 0, a.ledgerBlockHash, a.ledgerBlockIndex, a.anchoredAt);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "PakVerify: zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
