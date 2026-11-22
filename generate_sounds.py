import wave
import numpy as np
import os

SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
os.makedirs(SOUNDS_DIR, exist_ok=True)

def create_soft_chime(filename, freq, duration=0.10, decay=20.0):
    sample_rate = 44100
    n_samples = int(sample_rate * duration)
    t = np.linspace(0, duration, n_samples, False)
    
    # Fundamental + warm soft second harmonic
    signal = 0.75 * np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2 * t)
    # Smooth exponential decay with gentle 3ms attack to eliminate clicks
    attack_samples = int(sample_rate * 0.004)
    envelope = np.exp(-decay * t)
    envelope[:attack_samples] *= np.linspace(0, 1, attack_samples)
    
    # Soft volume level (gentle and discreet)
    audio = signal * envelope * 0.25
    pcm = (audio * 32767).astype(np.int16)
    
    out_path = os.path.join(SOUNDS_DIR, filename)
    with wave.open(out_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    print(f"Created {filename}")

def create_switch_chime(filename="switch.wav"):
    sample_rate = 44100
    duration = 0.12
    n_samples = int(sample_rate * duration)
    t = np.linspace(0, duration, n_samples, False)
    # Bright rising pitch transition from 660 Hz to 880 Hz
    freq_ramp = np.linspace(659.25, 880.0, n_samples)
    signal = 0.7 * np.sin(2 * np.pi * freq_ramp * t) + 0.3 * np.sin(4 * np.pi * freq_ramp * t)
    envelope = np.exp(-18.0 * t)
    attack_samples = int(sample_rate * 0.005)
    envelope[:attack_samples] *= np.linspace(0, 1, attack_samples)
    audio = signal * envelope * 0.22
    pcm = (audio * 32767).astype(np.int16)
    out_path = os.path.join(SOUNDS_DIR, filename)
    with wave.open(out_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    print(f"Created {filename}")

if __name__ == "__main__":
    # Start: Soft bright D5 note (587 Hz)
    create_soft_chime("start.wav", 587.33, duration=0.09, decay=24.0)
    # Stop: Soft warm A4 note (440 Hz)
    create_soft_chime("stop.wav", 440.00, duration=0.11, decay=20.0)
    # Switch: Rising tone cue (659 Hz -> 880 Hz)
    create_switch_chime("switch.wav")
