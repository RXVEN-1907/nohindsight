#!/usr/bin/env bash
# gen_keys.sh
# Sign and verify a nohindsight backtest artifact with Ed25519.
#
# Usage: gen_keys.sh [work_dir] [artifact]
#   work_dir  where keys and signatures land, default: current directory
#   artifact  the file to sign, default: report.json, a small example is
#             written if the file does not exist
#
# Where this plugs in: `nh audit` writes report.json. This script signs it
# and pins its sha256. The signature rides along with the report, so a
# shared screenshot can be checked against the file: anyone holding
# ed25519_public.pem verifies the signature, then matches the hash in the
# report footer. The private key never leaves the box that signs.
#
# Why Ed25519: 64-byte signatures, milliseconds to verify, no parameter
# tuning, no certificate ceremony. It is the modern default in OpenSSL 3
# and the right primitive for "prove this file is the file".

set -euo pipefail

work_dir="${1:-$PWD}"
artifact="${2:-report.json}"

cd "$work_dir"

if ! command -v openssl >/dev/null 2>&1; then
    echo "openssl not found in PATH" >&2
    exit 1
fi

# Example artifact so the script runs standalone. In real use, pass the
# report the audit produced.
if [ ! -f "$artifact" ]; then
    printf '{\n "meta": {"note": "example artifact for gen_keys.sh"},\n "verdict": "SUSPICIOUS",\n "sharpe": 0.691\n}\n' > "$artifact"
    echo "wrote example artifact: $artifact"
fi

# 1. Keypair. Ed25519 keys are 32 bytes each, private and public.
openssl genpkey -algorithm ed25519 -out ed25519_private.pem
openssl pkey -in ed25519_private.pem -pubout -out ed25519_public.pem
chmod 600 ed25519_private.pem

# 2. Sign. Ed25519 is a pure signing algorithm, not a digest-and-sign one,
#    so -rawin tells pkeyutl the input is the raw message (the algorithm
#    hashes it internally, per RFC 8032). Omitting -rawin on OpenSSL 3
#    fails for Ed25519 keys.
openssl pkeyutl -sign -rawin -inkey ed25519_private.pem \
    -in "$artifact" -out "$artifact.sig"

# 3. Verify. This is the command a reader of the shared screenshot runs.
openssl pkeyutl -verify -rawin -pubin -inkey ed25519_public.pem \
    -in "$artifact" -sigfile "$artifact.sig"

# 4. Pin the artifact's content hash. The signature proves authorship,
#    the sha256 proves the screenshot came from this exact file.
sha256sum "$artifact"

# 5. Optional: encrypt the artifact to a recipient public key, for handing
#    the report to one person over an untrusted channel. PGP without PGP.
#    Their pkey must be an X25519 (key agreement) key, which openssl
#    generates with: openssl genpkey -algorithm x25519 -out recipient.pem
#
# openssl pkeyutl -encrypt -pubin -inkey recipient_public.pem \
#     -in "$artifact" -out "$artifact.enc"
# # recipient side:
# openssl pkeyutl -decrypt -inkey recipient_private.pem \
#     -in "$artifact.enc" -out "$artifact.dec"
