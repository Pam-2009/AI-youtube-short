import sys
import os
import subprocess
import anthropic
import yt_dlp
from deep_translator import GoogleTranslator
from imageio_ffmpeg import get_ffmpeg_exe
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeAudioClip
from elevenlabs.client import ElevenLabs

def download_video(url, output_path="downloaded_video.mp4"):
    # Cobalt v7 API ถูกปิดไปแล้ว (พ.ย. 2024) จึงใช้ yt-dlp ดาวน์โหลดแทน (รองรับ YouTube / TikTok)
    print(f"Downloading video via yt-dlp: {url}")
    ydl_opts = {
        "outtmpl": output_path,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    print("Video downloaded successfully!")
    return output_path

def convert_to_vertical_short(input_path, output_path="short_clip.mp4", start_sec=0, end_sec=30):
    print(f"Trimming and cropping video ({start_sec}s to {end_sec}s)...")
    clip = VideoFileClip(input_path)
    # กันไม่ให้ end_sec เกินความยาววิดีโอ (เช่นคลิป TikTok ที่สั้นกว่า 30 วิ)
    end_sec = min(float(end_sec), clip.duration)
    clip = clip.subclip(float(start_sec), end_sec)
    
    # Crop to 9:16 vertical ratio
    w, h = clip.size
    target_w = int(h * (9 / 16))
    if target_w < w:
        x_center = w / 2
        clip = clip.crop(x1=x_center - target_w/2, x2=x_center + target_w/2, y1=0, y2=h)

    clip.write_videofile(output_path, codec="libx264", audio_codec="aac")
    return output_path

# ElevenLabs Dubbing ไม่รองรับภาษาไทย จึงทำ 3 ขั้นตอนเอง:
# ถอดเสียงเป็นข้อความ -> แปลเป็นไทยด้วย Claude -> ให้เสียง ElevenLabs อ่านภาษาไทยทับวิดีโอ
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # เปลี่ยนได้ด้วย ELEVENLABS_VOICE_ID


def transcribe(client, file_path):
    print("Transcribing speech with ElevenLabs...")
    with open(file_path, "rb") as f:
        result = client.speech_to_text.convert(file=f, model_id="scribe_v2")
    text = result.text.strip()
    if not text:
        raise Exception("No speech found in the video, nothing to translate.")
    print(f"Transcript ({result.language_code}): {text}")
    return text


def translate_text_to_thai(text, duration_sec):
    # ใช้ Google Translate ฟรีเป็นค่าเริ่มต้น ถ้าตั้ง ANTHROPIC_API_KEY ไว้จะใช้ Claude (แปลเป็นธรรมชาติกว่า)
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Translating to Thai with Google Translate (free)...")
        thai = GoogleTranslator(source="auto", target="th").translate(text)
        print(f"Thai: {thai}")
        return thai

    print("Translating to Thai with Claude...")
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        system=(
            "You translate short-video voice-overs into natural, casual spoken Thai. "
            "Reply with only the Thai translation, no notes or quotes. "
            f"It will be read aloud over a {duration_sec:.0f}-second clip, "
            "so keep it about as long as the original."
        ),
        messages=[{"role": "user", "content": text}],
    )
    if response.stop_reason == "refusal":
        raise Exception("Claude declined to translate this transcript.")
    thai = "".join(b.text for b in response.content if b.type == "text").strip()
    print(f"Thai: {thai}")
    return thai


def fit_audio_to_length(audio_path, max_sec):
    # ถ้าเสียงไทยยาวกว่าวิดีโอ ให้เร่งความเร็ว (atempo รับได้สูงสุด 2.0 ต่อครั้ง)
    duration = AudioFileClip(audio_path).duration
    if duration <= max_sec:
        return audio_path
    speed = duration / max_sec
    filters = []
    while speed > 2.0:
        filters.append("atempo=2.0")
        speed /= 2.0
    filters.append(f"atempo={speed:.4f}")
    fitted_path = "thai_voice_fitted.mp3"
    subprocess.run(
        [get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", audio_path,
         "-filter:a", ",".join(filters), fitted_path],
        check=True,
    )
    return fitted_path


def translate_to_thai(file_path, output_path="output_thai_short.mp4"):
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY environment variable is not set.")

    client = ElevenLabs(api_key=api_key)
    video = VideoFileClip(file_path)

    text = transcribe(client, file_path)
    thai = translate_text_to_thai(text, video.duration)

    print("Generating Thai voice with ElevenLabs...")
    voice_path = "thai_voice.mp3"
    audio = client.text_to_speech.convert(
        voice_id=os.getenv("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID,
        text=thai,
        model_id="eleven_v3",
        output_format="mp3_44100_128",
    )
    with open(voice_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)

    # เสียงไทยอยู่ด้านหน้า เสียงต้นฉบับ (เพลง/เอฟเฟกต์) เบาลงเหลือ 15%
    thai_voice = AudioFileClip(fit_audio_to_length(voice_path, video.duration))
    tracks = [thai_voice]
    if video.audio is not None:
        tracks.insert(0, video.audio.volumex(0.15))
    final = video.set_audio(CompositeAudioClip(tracks).set_duration(video.duration))
    final.write_videofile(output_path, codec="libx264", audio_codec="aac")
    print("Thai translation complete!")

if __name__ == "__main__":
    video_url = sys.argv[1]
    start_time = sys.argv[2] if len(sys.argv) > 2 else 0
    end_time = sys.argv[3] if len(sys.argv) > 3 else 30
    
    raw_video = download_video(video_url)
    short_video = convert_to_vertical_short(raw_video, start_sec=start_time, end_sec=end_time)
    translate_to_thai(short_video)
