"""한국어 음성 합성(무료).

- edge: Microsoft Edge 읽어주기 음성(edge-tts). 무료·API 키 불필요, 인터넷 연결 필요. 품질 가장 좋음.
- local: 완전 오프라인. Windows는 내장 한국어 음성(System.Speech, PowerShell), 그 외에는 espeak-ng(음질 낮음).
- auto: edge를 먼저 시도하고 실패하면 local.
같은 문장은 해시로 캐시해서 다시 만들지 않는다.
"""
import asyncio
import hashlib
import shutil
import subprocess
import wave
from pathlib import Path

from .config import IS_WINDOWS, ffmpeg_exe

_edge_down = False


def duration(path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def _to_wav(src: Path, dst: Path):
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "48000",
                    "-af", "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                           "silenceremove=start_periods=1:start_threshold=-50dB,areverse",
                    str(dst)], check=True)


def _edge(text, voice, rate, out_mp3: Path):
    import edge_tts

    async def run():
        await edge_tts.Communicate(text, voice, rate=rate).save(str(out_mp3))
    asyncio.run(run())


def _local(text, out_raw: Path, speed=1.0):
    if IS_WINDOWS:
        # Windows 내장 음성(System.Speech)을 PowerShell로 호출. 한국어 음성(Heami)이 있으면 그것을 쓴다.
        txt = out_raw.with_suffix(".txt")
        txt.write_text(text, encoding="utf-8")
        rate = max(-10, min(10, round((speed - 1) * 10)))
        ps = ("Add-Type -AssemblyName System.Speech; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'ko-KR' } | Select-Object -First 1; "
              "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; "
              f"$s.Rate = {rate}; "
              f"$s.SetOutputToWaveFile('{out_raw}'); "
              f"$s.Speak([IO.File]::ReadAllText('{txt}', [Text.Encoding]::UTF8)); $s.Dispose()")
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           capture_output=True, text=True, timeout=120)
        txt.unlink(missing_ok=True)
        if r.returncode != 0 or not out_raw.exists():
            raise RuntimeError("Windows 내장 음성 실패: " + (r.stderr.strip().splitlines() or ["원인 불명"])[-1])
        return
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise RuntimeError("오프라인 음성 엔진이 없습니다(espeak-ng 설치 필요).")
    subprocess.run([exe, "-v", "ko", "-s", str(int(170 * speed)), "-w", str(out_raw), text], check=True)


def synth(text: str, out_dir: Path, settings: dict, log=print) -> dict:
    """문장 하나를 wav로 만든다. 반환: {file, seconds, engine}."""
    global _edge_down
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = settings.get("tts_engine", "auto")
    speed = min(1.6, max(0.7, float(settings.get("tts_speed", 1.0))))
    key = hashlib.sha1(f"{engine}|{settings['tts_voice']}|{speed:.2f}|{text}".encode()).hexdigest()[:16]
    wav = out_dir / f"{key}.wav"
    meta = out_dir / f"{key}.engine"
    if wav.exists() and meta.exists():
        return {"file": str(wav), "seconds": duration(wav), "engine": meta.read_text()}

    errors = []
    order = {"auto": ["edge", "local"], "edge": ["edge"], "local": ["local"]}[engine]
    if engine == "auto" and _edge_down:
        order = ["local"]
    for eng in order:
        raw = out_dir / f"{key}.{eng}.{'mp3' if eng == 'edge' else 'wav'}"
        try:
            if eng == "edge":
                _edge(text, settings["tts_voice"], f"{round((speed - 1) * 100):+d}%", raw)
            else:
                _local(text, raw, speed)
            _to_wav(raw, wav)
            raw.unlink(missing_ok=True)
            meta.write_text(eng)
            return {"file": str(wav), "seconds": duration(wav), "engine": eng}
        except Exception as e:
            if eng == "edge":
                _edge_down = True  # 같은 실행 중에는 다시 기다리지 않음(앱 재시작 시 다시 시도)
            errors.append(f"{eng}: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
            log(f"음성 엔진 {eng} 실패 → {errors[-1]}")
    raise RuntimeError("음성 합성 실패: " + " / ".join(errors))
