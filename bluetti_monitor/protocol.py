"""Bluetti Monitor Protocol Engine: Crypto, Modbus framing, and Register Map.
Ported from Sources/BluettiCore (Swift) and bluetti-bt-lib (Python).
"""

import hashlib
import struct
from typing import Dict, List, Optional, Tuple, Any

# Well-known constants for Bluetti V2 BLE encryption
LOCAL_AES_KEY = bytes.fromhex("459FC535808941F17091E0993EE3E93D")
APP_SIGNING_KEY = bytes.fromhex("4F19A16E3E87BDD9BD24D3E5495B88041511943CBC8B969ADE9641D0F56AF337")
DEVICE_SIGNING_KEY_DER = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d03010703420004"
    "A73ABF5D2232C8C1C72E68304343C272495E3A8FD6F30EA96DE2F4B3CE60B251"
    "EE21AC667CF8A71E18B46B664EAEFFE3C489F24F695B6411DB7E22CCC85A8594"
)


def crc16_modbus(data: bytes) -> int:
    """Calculates 16-bit Modbus RTU CRC."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def modbus_read_frame(start: int, count: int) -> bytes:
    """Constructs a Modbus RTU read frame (Unit ID 1, Function 3)."""
    f = bytes([0x01, 0x03, (start >> 8) & 0xFF, start & 0xFF, (count >> 8) & 0xFF, count & 0xFF])
    crc = crc16_modbus(f)
    return f + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def modbus_parse_read(frame: bytes, expected_count: int) -> List[int]:
    """Validates Modbus read response and returns register values."""
    if len(frame) < 5:
        raise ValueError("Modbus response frame too short")
    body = frame[:-2]
    expected_crc = crc16_modbus(body)
    got_crc = frame[-2] | (frame[-1] << 8)
    if expected_crc != got_crc:
        raise ValueError("Modbus CRC mismatch")
    if frame[1] & 0x80:
        raise ValueError(f"Modbus exception code {frame[2]}")
    n = frame[2] // 2
    if n != expected_count:
        raise ValueError(f"Register count mismatch: expected {expected_count}, got {n}")
    registers = []
    for i in range(n):
        reg = (frame[3 + i * 2] << 8) | frame[4 + i * 2]
        registers.append(reg)
    return registers


def checksum16(b: bytes) -> bytes:
    """Big-endian 2-byte sum of all bytes."""
    s = sum(b) & 0xFFFF
    return bytes([(s >> 8) & 0xFF, s & 0xFF])


def xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


# Cryptography integration with fallback
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    HAVE_CRYPTOGRAPHY = True
except ImportError:
    HAVE_CRYPTOGRAPHY = False


class BluettiCrypto:
    """Handles Bluetti BLE V2 key exchange and AES encryption."""

    def __init__(self, device_signing_der: bytes = DEVICE_SIGNING_KEY_DER):
        self.device_signing_der = device_signing_der
        self.unsecure_key: Optional[bytes] = None
        self.unsecure_iv: Optional[bytes] = None
        self.secure_key: Optional[bytes] = None
        self.peer_sig_valid: Optional[bool] = None
        self.my_private_key = None
        self.peer_public_key = None
        self.reset()

    def reset(self):
        self.unsecure_key = None
        self.unsecure_iv = None
        self.secure_key = None
        self.peer_sig_valid = None
        self.my_private_key = None
        self.peer_public_key = None

    @property
    def is_ready(self) -> bool:
        return self.secure_key is not None

    @property
    def header_size(self) -> int:
        """Length+IV header before the ciphertext: 2 while the per-connection IV is
        in use, 6 (length + 4-byte IV seed) once the session key is agreed."""
        return 2 if self.secure_key is None else 6

    def aes_cbc(self, encrypt: bool, data: bytes, key: bytes, iv: bytes) -> bytes:
        if len(data) % 16 != 0:
            raise ValueError(f"Data length {len(data)} not aligned to 16 bytes")
        if HAVE_CRYPTOGRAPHY:
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            engine = cipher.encryptor() if encrypt else cipher.decryptor()
            return engine.update(data) + engine.finalize()
        else:
            # Fallback for systems where cryptography is not yet installed
            raise NotImplementedError("Please install 'python3-cryptography' for hardware Bluetooth connectivity")

    def answer_challenge(self, data: bytes) -> bytes:
        """Type 1 challenge (4 bytes) -> reply with Type 2."""
        if len(data) != 4:
            raise ValueError(f"Challenge length {len(data)} != 4")
        iv = hashlib.md5(data[::-1]).digest()
        self.unsecure_iv = iv
        self.unsecure_key = xor_bytes(iv, LOCAL_AES_KEY)
        resp_data = bytes([0x02, 0x04]) + iv[8:12]
        return b"**" + resp_data + checksum16(resp_data)

    def answer_peer_pubkey(self, data: bytes) -> bytes:
        """Type 4 station public key -> ECDH and sign with app key -> Type 5."""
        if not HAVE_CRYPTOGRAPHY:
            raise RuntimeError("Hardware BLE key exchange requires python3-cryptography")
        if not self.unsecure_iv or not self.unsecure_key:
            raise RuntimeError("Challenge not completed before public key exchange")

        if len(data) != 128:
            raise ValueError(f"Expected 128 bytes, got {len(data)}")

        peer_pub_bytes = data[:64]
        peer_sig_bytes = data[64:]

        # Verify peer signature
        try:
            device_pub = serialization.load_der_public_key(self.device_signing_der)
            # Raw r, s to DER
            r = int.from_bytes(peer_sig_bytes[:32], "big")
            s = int.from_bytes(peer_sig_bytes[32:], "big")
            der_sig = encode_dss_signature(r, s)
            device_pub.verify(der_sig, peer_pub_bytes + self.unsecure_iv, ec.ECDSA(hashes.SHA256()))
            self.peer_sig_valid = True
        except Exception:
            self.peer_sig_valid = False

        # Generate our ECDH key pair
        my_key = ec.generate_private_key(ec.SECP256R1())
        my_pub = my_key.public_key()
        my_pub_bytes = my_pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)[1:]

        # Keep both keys; the session key is only derived once the station
        # accepts ours (type 6), which still arrives under the per-connection key.
        peer_point = b"\x04" + peer_pub_bytes
        self.peer_public_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), peer_point)
        self.my_private_key = my_key

        # Sign our public key with APP_SIGNING_KEY
        app_signer = ec.derive_private_key(int.from_bytes(APP_SIGNING_KEY, "big"), ec.SECP256R1())
        sig = app_signer.sign(my_pub_bytes + self.unsecure_iv, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(sig)
        raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")

        payload = bytes([0x05, 0x80]) + my_pub_bytes + raw_sig
        body = b"**" + payload + checksum16(payload)

        # Encrypt using unsecure key/iv
        return self.encrypt_unsecure(body)

    def accept_key(self, data: bytes) -> None:
        """Type 6: station accepted our key (data == 00). Derive the session key."""
        if len(data) != 1:
            raise ValueError(f"Key acceptance length {len(data)} != 1")
        if data[0] != 0:
            raise RuntimeError("Power station rejected our key")
        if self.my_private_key is None or self.peer_public_key is None:
            raise RuntimeError("Key acceptance before key exchange")
        self.secure_key = self.my_private_key.exchange(ec.ECDH(), self.peer_public_key)

    def encrypt_unsecure(self, data: bytes) -> bytes:
        pad_len = (16 - (len(data) % 16)) % 16
        padded = data + (b"\x00" * pad_len)
        enc = self.aes_cbc(True, padded, self.unsecure_key, self.unsecure_iv)
        return bytes([(len(data) >> 8) & 0xFF, len(data) & 0xFF]) + enc

    def encrypt_command(self, frame: bytes) -> bytes:
        if not self.secure_key:
            return frame
        import os
        seed = os.urandom(4)
        iv = hashlib.md5(seed).digest()
        pad_len = (16 - (len(frame) % 16)) % 16
        padded = frame + (b"\x00" * pad_len)
        enc = self.aes_cbc(True, padded, self.secure_key, iv)
        header = bytes([(len(frame) >> 8) & 0xFF, len(frame) & 0xFF]) + seed
        return header + enc

    def decrypt(self, message: bytes) -> bytes:
        if len(message) < 2:
            return message
        data_len = (message[0] << 8) | message[1]
        if self.secure_key:
            if len(message) < 6:
                raise ValueError("Secure message too short")
            iv = hashlib.md5(message[2:6]).digest()
            cipher = message[6:]
            plain = self.aes_cbc(False, cipher, self.secure_key, iv)
            return plain[:data_len]
        elif self.unsecure_key and self.unsecure_iv:
            cipher = message[2:]
            plain = self.aes_cbc(False, cipher, self.unsecure_key, self.unsecure_iv)
            return plain[:data_len]
        return message


def encode_dss_signature(r: int, s: int) -> bytes:
    rb = r.to_bytes((r.bit_length() + 8) // 8, "big")
    sb = s.to_bytes((s.bit_length() + 8) // 8, "big")
    if rb[0] & 0x80: rb = b"\x00" + rb
    if sb[0] & 0x80: sb = b"\x00" + sb
    seq = bytes([0x02, len(rb)]) + rb + bytes([0x02, len(sb)]) + sb
    return bytes([0x30, len(seq)]) + seq


def decode_dss_signature(sig: bytes) -> Tuple[int, int]:
    # DER decode
    if sig[0] != 0x30:
        raise ValueError("Invalid DER signature")
    idx = 2
    if sig[idx] != 0x02: raise ValueError("Invalid DER r")
    rlen = sig[idx + 1]
    r = int.from_bytes(sig[idx + 2 : idx + 2 + rlen], "big")
    idx = idx + 2 + rlen
    if sig[idx] != 0x02: raise ValueError("Invalid DER s")
    slen = sig[idx + 1]
    s = int.from_bytes(sig[idx + 2 : idx + 2 + slen], "big")
    return r, s


def modbus_frame_length(buf: bytes) -> Optional[int]:
    """Length of the complete Modbus frame at the start of buf, or None if incomplete."""
    if len(buf) < 3:
        return None
    fn = buf[1]
    if fn & 0x80:
        return 5                      # exception: id, fn|0x80, code, crc(2)
    if fn in (0x03, 0x04):
        return 3 + buf[2] + 2
    if fn in (0x06, 0x10):
        return 8
    return len(buf)


class BluettiLink:
    """Transport-independent protocol engine (same logic as the macOS app's
    BluettiLink, which is interop-tested against bluetti-bt-lib).

    Feed every BLE notification to receive(); it returns a list of events:
      ("write", bytes)    send to the write characteristic
      ("ready", None)     key exchange finished
      ("response", bytes) one complete, decrypted Modbus frame
      ("log", str) / ("failed", str)
    """

    def __init__(self, crypto: Optional[BluettiCrypto] = None):
        self.crypto = crypto or BluettiCrypto()
        self.encrypted = False
        self.saw_challenge = False
        self._cipher = bytearray()
        self._plain = bytearray()

    def reset(self):
        self.crypto.reset()
        self.encrypted = False
        self.saw_challenge = False
        self._cipher.clear()
        self._plain.clear()

    @property
    def is_ready(self) -> bool:
        return self.crypto.is_ready if self.encrypted else True

    def use_plaintext(self):
        """No challenge arrived: older stations talk plain Modbus."""
        self.encrypted = False

    def wrap(self, frame: bytes) -> bytes:
        if not self.encrypted:
            return frame
        if not self.crypto.is_ready:
            raise RuntimeError("Handshake not finished")
        return self.crypto.encrypt_command(frame)

    def clear_pending(self):
        self._plain.clear()

    @staticmethod
    def _kex(raw: bytes):
        """Parse a key-exchange message: 2a2a | type | len | data | checksum(2)."""
        if len(raw) < 6 or raw[:2] != b"**":
            return None
        body = raw[2:-2]
        return body[0], body[2:], checksum16(body) == raw[-2:]

    def receive(self, fragment: bytes) -> List[Tuple[str, Any]]:
        events: List[Tuple[str, Any]] = []
        fragment = bytes(fragment)

        # Before a key exists, key-exchange messages arrive in clear.
        kex = self._kex(fragment) if not self._cipher else None
        if kex is not None:
            mtype, data, ok = kex
            if not ok:
                events.append(("log", "Handshake checksum mismatch (continuing)"))
            if mtype == 1:
                self.encrypted = True
                self.saw_challenge = True
                try:
                    events.append(("write", self.crypto.answer_challenge(data)))
                    events.append(("log", "Challenge answered"))
                except Exception as e:
                    events.append(("failed", f"Challenge: {e}"))
            elif mtype == 3:
                events.append(("log", "Challenge accepted"))
            else:
                events.append(("log", f"Unexpected clear handshake message type {mtype}"))
            return events

        if not self.encrypted:
            self._plain += fragment
            events += self._drain_plain()
            return events
        if self.crypto.unsecure_key is None:
            events.append(("log", f"Encrypted data before handshake, dropped {len(fragment)} bytes"))
            return events

        self._cipher += fragment
        while len(self._cipher) >= 2:
            data_len = (self._cipher[0] << 8) | self._cipher[1]
            total = self.crypto.header_size + (data_len + 15) // 16 * 16
            if len(self._cipher) < total:
                break
            message = bytes(self._cipher[:total])
            del self._cipher[:total]
            try:
                plain = self.crypto.decrypt(message)
            except Exception as e:
                events.append(("log", f"Decrypt failed: {e}; buffer cleared"))
                self._cipher.clear()
                break

            kex = self._kex(plain)
            if kex is not None:
                mtype, data, _ = kex
                if mtype == 4:
                    try:
                        reply = self.crypto.answer_peer_pubkey(data)
                        verdict = "valid" if self.crypto.peer_sig_valid else "NOT verified"
                        events.append(("log", f"Station key received, signature {verdict}"))
                        events.append(("write", reply))
                    except Exception as e:
                        events.append(("failed", f"Key exchange: {e}"))
                elif mtype == 6:
                    try:
                        self.crypto.accept_key(data)
                        events.append(("ready", None))
                    except Exception as e:
                        events.append(("failed", f"Key accept: {e}"))
                else:
                    events.append(("log", f"Unexpected handshake message type {mtype}"))
                continue
            self._plain += plain
            events += self._drain_plain()
        return events

    def _drain_plain(self) -> List[Tuple[str, Any]]:
        events = []
        while True:
            n = modbus_frame_length(bytes(self._plain))
            if n is None or n <= 0 or len(self._plain) < n:
                break
            events.append(("response", bytes(self._plain[:n])))
            del self._plain[:n]
        return events


# Register map lives in registers.py (full port of the macOS app's map).
from .registers import (  # noqa: E402,F401  (re-exported for gui.py / ble.py)
    BLOCKS, FIELDS, decode_registers, is_private,
)
