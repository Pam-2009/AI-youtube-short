import sys
import os
import re
import subprocess
import time
import anthropic
import yt_dlp
import shutil
import base64
import numpy as np
import cv2
import pytesseract
from deep_translator import GoogleTranslator, MyMemoryTranslator
from imageio_ffmpeg import get_ffmpeg_exe
from moviepy.editor import VideoFileClip, AudioFileClip, vfx
from pythainlp.tokenize import word_tokenize
from elevenlabs.client import ElevenLabs
from elevenlabs.types import VoiceSettings

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

# ElevenLabs Dubbing ไม่รองรับภาษาไทย จึงทำเอง:
# ถอดเสียงเป็นข้อความ -> แปลเป็นไทย -> ให้เสียงคนไทยของ ElevenLabs อ่าน
# -> ลบเสียงอังกฤษ + เบลอตัวหนังสืออังกฤษ -> ใส่ซับไทยแทน
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # ใช้เมื่อหาเสียงภาษาไทยไม่ได้
THAI_VOICE_PREFIX = "Thai - "


def transcribe(client, file_path):
    print("Transcribing speech with ElevenLabs...")
    with open(file_path, "rb") as f:
        result = client.speech_to_text.convert(file=f, model_id="scribe_v2")
    text = result.text.strip()
    if not text:
        raise Exception("No speech found in the video, nothing to translate.")
    print(f"Transcript ({result.language_code}): {text}")
    return text


def translate_free(text):
    # Google มักบล็อกเซิร์ฟเวอร์ของ GitHub ชั่วคราว: ลองใหม่ 3 ครั้ง แล้วค่อยใช้ MyMemory (ฟรีเหมือนกัน)
    for attempt in range(3):
        try:
            print("Translating to Thai with Google Translate (free)...")
            return GoogleTranslator(source="auto", target="th").translate(text)
        except Exception as e:
            print(f"Google Translate failed ({e.__class__.__name__}), retrying...")
            time.sleep(5 * (attempt + 1))

    # MyMemory รับได้ครั้งละ ~500 ตัวอักษร จึงแปลทีละประโยค
    print("Translating to Thai with MyMemory (free)...")
    translator = MyMemoryTranslator(source="english", target="thai")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    return " ".join(translator.translate(s) for s in sentences)


def translate_text_to_thai(text, duration_sec):
    # ใช้ Google Translate ฟรีเป็นค่าเริ่มต้น ถ้าตั้ง ANTHROPIC_API_KEY ไว้จะใช้ Claude (แปลเป็นธรรมชาติกว่า)
    if not os.getenv("ANTHROPIC_API_KEY"):
        thai = translate_free(text)
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


def pick_thai_voice(client):
    # ถ้าตั้ง ELEVENLABS_VOICE_ID ไว้ใช้อันนั้น ไม่งั้นหาเสียงคนไทยจาก Voice Library ของ ElevenLabs
    if os.getenv("ELEVENLABS_VOICE_ID"):
        return os.getenv("ELEVENLABS_VOICE_ID")
    try:
        mine = client.voices.search(search=THAI_VOICE_PREFIX, page_size=10).voices
        for v in mine:
            if (v.name or "").startswith(THAI_VOICE_PREFIX):
                print(f"Using Thai voice: {v.name}")
                return v.voice_id

        shared = client.voices.get_shared(language="th", sort="cloned_by_count", page_size=20).voices
        print(f"Found {len(shared)} Thai voices in the ElevenLabs library")
        # เรียงให้เสียงที่บัญชีฟรีใช้ได้มาก่อน
        for v in sorted(shared, key=lambda v: not v.free_users_allowed):
            try:
                added = client.voices.share(
                    public_user_id=v.public_owner_id,
                    voice_id=v.voice_id,
                    new_name=f"{THAI_VOICE_PREFIX}{v.name}",
                )
            except Exception as e:
                print(f"Can't add library voice {v.name}: {e}")
                break
            print(f"Added Thai voice from library: {v.name}")
            return added.voice_id
    except Exception as e:
        print(f"Could not search the voice library ({e})")
    print("Using the default voice (a Thai library voice needs a paid ElevenLabs plan)")
    return DEFAULT_VOICE_ID


MAX_VOICE_SPEEDUP = 1.2  # เร่งเสียงเกินนี้แล้วฟังไม่เป็นธรรมชาติ


def fit_voice_to_video(audio_path, video):
    # ถ้าเสียงไทยยาวกว่าวิดีโอ: เร่งเสียงได้ไม่เกิน 1.2 เท่า ส่วนที่เหลือให้วิดีโอช้าลงแทน
    duration = AudioFileClip(audio_path).duration
    if duration <= video.duration:
        return AudioFileClip(audio_path), video
    speed = min(duration / video.duration, MAX_VOICE_SPEEDUP)
    print(f"Thai voice is {duration:.1f}s for a {video.duration:.1f}s video, speeding voice up x{speed:.2f}")
    fitted_path = "thai_voice_fitted.mp3"
    subprocess.run(
        [get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", audio_path,
         "-filter:a", f"atempo={speed:.4f}", fitted_path],
        check=True,
    )
    voice = AudioFileClip(fitted_path)
    if voice.duration > video.duration:
        factor = video.duration / voice.duration
        print(f"Slowing the video to x{factor:.2f} so the voice fits")
        video = video.fx(vfx.speedx, factor).set_duration(voice.duration)
    return voice, video


def caption_masks(rgb):
    # ซับ TikTok มักเป็นตัวขาวหรือเหลืองขอบดำ: แยกเฉพาะพิกเซลสีขาว/เหลืองออกมา
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    white = (hsv[..., 2] > 200) & (hsv[..., 1] < 60)
    yellow = (hsv[..., 0] >= 18) & (hsv[..., 0] <= 35) & (hsv[..., 1] > 120) & (hsv[..., 2] > 170)
    return white, yellow


def find_caption_boxes(rgb, seen):
    # หาแถบตัวหนังสือ (กว้าง ๆ เตี้ย ๆ) แล้วให้ OCR ยืนยันว่ามีตัวอักษรภาษาอังกฤษจริง
    img_h, img_w = rgb.shape[:2]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 9))
    boxes = []
    for mask in caption_masks(rgb):
        mask8 = mask.astype(np.uint8) * 255
        merged = cv2.morphologyEx(mask8, cv2.MORPH_CLOSE, kernel)
        _, _, stats, _ = cv2.connectedComponentsWithStats(merged)
        for x, y, w, h, _ in stats[1:]:
            if not (15 <= h <= img_h * 0.15 and w >= 1.5 * h):
                continue
            fill = mask[y:y + h, x:x + w].mean()
            if not 0.08 <= fill <= 0.7:
                continue
            crop = 255 - mask8[max(0, y - 6):y + h + 6, max(0, x - 6):x + w + 6]
            crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
            text = pytesseract.image_to_string(crop, config="--psm 7").strip()
            if len(re.findall(r"[A-Za-z]", text)) >= 3:
                boxes.append((x, y, w, h))
                seen.add(text)
    return boxes


def find_english_text(video, step=0.5):
    # ตรวจทุก ๆ ครึ่งวินาทีว่ามีตัวหนังสืออังกฤษบนจอตรงไหนบ้าง
    print("Looking for English text on screen...")
    samples, seen = [], set()
    t = 0.0
    while t < video.duration:
        samples.append((t, find_caption_boxes(video.get_frame(t), seen)))
        t += step
    print(f"Found English text: {' | '.join(sorted(seen)) or '(none)'}")
    return samples, step


def log_preview_frames(clip, label):
    # พิมพ์ภาพตัวอย่างเล็ก ๆ ลง log (base64) ไว้ตรวจผลงานโดยไม่ต้องดาวน์โหลดวิดีโอ
    for frac in (0.2, 0.5, 0.8):
        t = clip.duration * frac
        frame = cv2.resize(cv2.cvtColor(clip.get_frame(t), cv2.COLOR_RGB2BGR), (270, 480))
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
        print(f"PREVIEW {label} t={t:.1f} {base64.b64encode(jpg.tobytes()).decode()}")


def keep_caption_boxes(samples, height):
    # ซับอยู่แถวเดียวกันตลอด: เก็บเฉพาะกล่องที่อยู่ใกล้แนวซับ (ตัดจุดที่ OCR อ่านผิดทิ้ง)
    centers = sorted(y + bh / 2 for _, bs in samples for (_, y, _, bh) in bs)
    if not centers:
        return samples, None
    line_y = centers[len(centers) // 2]
    kept = [(t, [b for b in bs if abs(b[1] + b[3] / 2 - line_y) < height * 0.1]) for t, bs in samples]
    return kept, line_y


def remove_english_text(video, samples, step):
    # เบลอทั้งแถบแนวนอนตรงที่มีซับอังกฤษ (กันตัวอักษรหลุดขอบ) และทำให้มืดลงนิดหน่อยให้ซับไทยอ่านง่าย
    def process(get_frame, t):
        frame = get_frame(t)
        boxes = [b for st, bs in samples if abs(st - t) <= step for b in bs]
        if not boxes:
            return frame
        h = frame.shape[0]
        y1 = max(0, min(b[1] for b in boxes) - 20)
        y2 = min(h, max(b[1] + b[3] for b in boxes) + 20)
        frame = frame.copy()
        strip = cv2.GaussianBlur(frame[y1:y2], (0, 0), 30)
        frame[y1:y2] = (strip * 0.7).astype(np.uint8)
        return frame
    return video.fl(process)


def split_thai_lines(text, max_chars=20):
    # ภาษาไทยไม่มีเว้นวรรคระหว่างคำ จึงตัดคำด้วย pythainlp แล้วจัดเป็นบรรทัดสั้น ๆ
    lines, current = [], ""
    for word in word_tokenize(text.replace("\n", " "), engine="newmm"):
        if word.isspace():
            if current:
                current += " "
            continue
        if current and len(current) + len(word) > max_chars:
            lines.append(current.strip())
            current = ""
        current += word
    if current.strip():
        lines.append(current.strip())
    return lines


def ass_time(sec):
    cs = int(round(sec * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def write_thai_subtitles(thai, voice_duration, size, line_y, path="thai_subs.ass"):
    w, h = size
    # วางซับไทยตรงที่ตัวหนังสืออังกฤษเคยอยู่ (ถ้าไม่เจอ วางไว้ช่วงล่างของจอ)
    y_pos = line_y if line_y is not None else h * 0.72
    font_size = int(h * 0.056)

    # เวลาแต่ละบรรทัดคิดตามจำนวนตัวอักษร (เสียงพูดเร็วพอ ๆ กันตลอด)
    lines = split_thai_lines(thai)
    total = sum(len(l) for l in lines) or 1
    events, start = [], 0.0
    for line in lines:
        end = start + voice_duration * len(line) / total
        events.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Thai,,0,0,0,,"
            f"{{\\an5\\pos({w // 2},{int(y_pos)})}}{line}"
        )
        start = end

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Thai,Loma,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,1,5,20,20,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""")
        f.write("\n".join(events) + "\n")
    print(f"Wrote {len(events)} Thai subtitle lines")
    return path


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
        voice_id=pick_thai_voice(client),
        text=thai,
        model_id="eleven_v3",
        output_format="mp3_44100_128",
        voice_settings=VoiceSettings(stability=0.5),  # "Natural" ของ eleven_v3
    )
    with open(voice_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)

    log_preview_frames(video, "original")
    # หาตัวหนังสือจากวิดีโอต้นฉบับก่อนเปลี่ยนความเร็ว
    samples, step = find_english_text(video)
    samples, line_y = keep_caption_boxes(samples, video.h)
    cleaned = remove_english_text(video, samples, step)
    thai_voice, cleaned = fit_voice_to_video(voice_path, cleaned)
    # ตัดเสียงต้นฉบับ (เสียงพูดภาษาอังกฤษ) ออกทั้งหมด ใช้เสียงไทยอย่างเดียว
    cleaned = cleaned.set_audio(thai_voice)
    cleaned.write_videofile("thai_no_subs.mp4", codec="libx264", audio_codec="aac")

    subs = write_thai_subtitles(thai, thai_voice.duration, video.size, line_y)
    ffmpeg = shutil.which("ffmpeg") or get_ffmpeg_exe()
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", "thai_no_subs.mp4",
         "-vf", f"ass={subs}", "-c:a", "copy", output_path],
        check=True,
    )
    log_preview_frames(VideoFileClip(output_path), "final")
    print("Thai translation complete!")

if __name__ == "__main__":
    video_url = sys.argv[1]
    start_time = sys.argv[2] if len(sys.argv) > 2 else 0
    end_time = sys.argv[3] if len(sys.argv) > 3 else 30
    
    raw_video = download_video(video_url)
    short_video = convert_to_vertical_short(raw_video, start_sec=start_time, end_sec=end_time)
    translate_to_thai(short_video)
