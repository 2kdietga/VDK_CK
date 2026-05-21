from __future__ import annotations

import io
import math
import os
import struct
import urllib.parse
import urllib.request
import wave


SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
TTS_CHUNK_BYTES = 1024
DEFAULT_TTS_VOLUME_GAIN = 1.8
PCM_MIN = -32768
PCM_MAX = 32767


class AudioProcessingError(RuntimeError):
    pass


def pcm_chunks_to_wav_bytes(chunks: list[bytes]) -> bytes:
    if not chunks:
        raise AudioProcessingError('No audio chunks to convert.')

    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(b''.join(chunks))

    return buffer.getvalue()


def transcribe_pcm_chunks(chunks: list[bytes]) -> str:
    try:
        import speech_recognition as sr
    except ImportError as exc:
        raise AudioProcessingError('speech_recognition is not installed.') from exc

    language = os.environ.get('SPEECH_RECOGNITION_LANGUAGE', 'vi-VN')
    recognizer = sr.Recognizer()
    wav_buffer = io.BytesIO(pcm_chunks_to_wav_bytes(chunks))

    try:
        with sr.AudioFile(wav_buffer) as source:
            audio = recognizer.record(source)
        return recognizer.recognize_google(audio, language=language).strip()
    except sr.UnknownValueError as exc:
        raise AudioProcessingError('Could not understand the recorded audio.') from exc
    except sr.RequestError as exc:
        raise AudioProcessingError(f'Speech recognition request failed: {exc}') from exc


def voicerss_tts_pcm(text: str) -> bytes:
    api_key = os.environ.get('VOICERSS_API_KEY')
    if not api_key:
        raise AudioProcessingError('VOICERSS_API_KEY is not configured.')

    params = urllib.parse.urlencode(
        {
            'key': api_key,
            'hl': os.environ.get('VOICERSS_LANGUAGE', 'vi-vn'),
            'v': os.environ.get('VOICERSS_VOICE', 'Chi'),
            'src': text,
            'c': 'WAV',
            'f': '16khz_16bit_mono',
        }
    )
    url = f'http://api.voicerss.org/?{params}'

    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            wav_data = response.read()
    except Exception as exc:
        raise AudioProcessingError(f'VoiceRSS request failed: {exc}') from exc

    try:
        return amplify_pcm(wav_bytes_to_pcm(wav_data))
    except wave.Error as exc:
        raise AudioProcessingError(f'VoiceRSS did not return a valid WAV file: {exc}') from exc


def wav_bytes_to_pcm(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes), 'rb') as wav_file:
        return wav_file.readframes(wav_file.getnframes())


def amplify_pcm(pcm: bytes) -> bytes:
    gain = env_float('TTS_VOLUME_GAIN', DEFAULT_TTS_VOLUME_GAIN)
    if gain <= 0 or abs(gain - 1.0) < 0.01:
        return pcm

    sample_count = len(pcm) // SAMPLE_WIDTH
    if sample_count == 0:
        return pcm

    samples = struct.unpack(f'<{sample_count}h', pcm[: sample_count * SAMPLE_WIDTH])
    amplified = [
        max(PCM_MIN, min(PCM_MAX, round(sample * gain)))
        for sample in samples
    ]
    result = struct.pack(f'<{len(amplified)}h', *amplified)
    return result + pcm[sample_count * SAMPLE_WIDTH :]


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def fallback_tone_pcm() -> bytes:
    parts = [
        (600, 180),
        (0, 80),
        (500, 260),
    ]
    samples: list[int] = []
    for freq, duration_ms in parts:
        total_samples = int(SAMPLE_RATE * duration_ms / 1000)
        if freq == 0:
            samples.extend([0] * total_samples)
            continue

        attack = max(int(total_samples * 0.05), 1)
        release = max(int(total_samples * 0.1), 1)
        for index in range(total_samples):
            envelope = 1.0
            if index < attack:
                envelope = index / attack
            elif index > total_samples - release:
                envelope = (total_samples - index) / release

            value = envelope * 0.6 * math.sin(2 * math.pi * freq * index / SAMPLE_RATE)
            samples.append(int(value * 32767))

    return amplify_pcm(struct.pack(f'<{len(samples)}h', *samples))
