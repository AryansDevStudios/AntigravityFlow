import os
import struct
import hashlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

# App-specific master AES-256 key
_SECRET = b"AntigravityFlow_Encrypted_Audio_Storage_Vault_2026"
AES_KEY = hashlib.sha256(_SECRET).digest()

def pcm_to_wav_bytes(pcm_data: bytes, sample_rate: int = 16000, num_channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """Wraps raw linear PCM mono audio into standard 44-byte RIFF WAV format."""
    data_size = len(pcm_data)
    byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)
    chunk_size = 36 + data_size
    
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        chunk_size,
        b'WAVE',
        b'fmt ',
        16,                # Subchunk1Size (16 for PCM)
        1,                 # AudioFormat (1 for PCM)
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data',
        data_size
    )
    return header + pcm_data

def wav_to_pcm_bytes(wav_data: bytes) -> bytes:
    """Extracts raw linear PCM audio from a RIFF WAV container."""
    if len(wav_data) < 44 or not wav_data.startswith(b'RIFF'):
        return wav_data
    # Look for 'data' subchunk
    idx = wav_data.find(b'data')
    if idx != -1 and idx + 8 <= len(wav_data):
        return wav_data[idx + 8:]
    return wav_data[44:]

def encrypt_audio_data(data_bytes: bytes) -> bytes:
    """Encrypts audio bytes with AES-256-CBC and prepends 16-byte random IV."""
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(data_bytes) + padder.finalize()
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()
    return iv + ciphertext

def decrypt_audio_data(enc_bytes: bytes) -> bytes:
    """Decrypts AES-256-CBC ciphertext using the prepended 16-byte IV."""
    if len(enc_bytes) <= 16:
        raise ValueError("Corrupted or empty encrypted audio payload")
    iv = enc_bytes[:16]
    ciphertext = enc_bytes[16:]
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded_data = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded_data) + unpadder.finalize()

def save_encrypted_recording(pcm_bytes: bytes, file_path: str, sample_rate: int = 16000) -> int:
    """Converts PCM to WAV, encrypts it, and writes to disk."""
    parent_dir = os.path.dirname(os.path.abspath(file_path))
    os.makedirs(parent_dir, exist_ok=True)
    wav_bytes = pcm_to_wav_bytes(pcm_bytes, sample_rate=sample_rate)
    enc_data = encrypt_audio_data(wav_bytes)
    with open(file_path, "wb") as f:
        f.write(enc_data)
    return len(enc_data)

def load_decrypted_wav(file_path: str) -> bytes:
    """Reads encrypted audio file and returns decrypted WAV bytes."""
    with open(file_path, "rb") as f:
        enc_bytes = f.read()
    return decrypt_audio_data(enc_bytes)

def load_decrypted_pcm(file_path: str) -> bytes:
    """Reads encrypted audio file and returns raw PCM bytes for re-transcription."""
    wav_bytes = load_decrypted_wav(file_path)
    return wav_to_pcm_bytes(wav_bytes)
