import sys
import os
import time
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip
from elevenlabs.client import ElevenLabs

def download_video(url, output_path="downloaded_video.mp4"):
    print(f"Downloading video from {url}...")
    ydl_opts = {'outtmpl': output_path, 'format': 'mp4/best'}
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return output_path

def convert_to_vertical_short(input_path, output_path="short_clip.mp4", start_sec=0, end_sec=30):
    print(f"Trimming and cropping video ({start_sec}s to {end_sec}s)...")
    clip = VideoFileClip(input_path).subclip(int(start_sec), int(end_sec))
    
    # Crop to 9:16 vertical ratio
    w, h = clip.size
    target_w = int(h * (9 / 16))
    if target_w < w:
        x_center = w / 2
        clip = clip.crop(x1=x_center - target_w/2, x2=x_center + target_w/2, y1=0, y2=h)

    clip.write_videofile(output_path, codec="libx264", audio_codec="aac")
    return output_path

def translate_to_thai(file_path, output_path="output_thai_short.mp4"):
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY environment variable is not set.")
    
    client = ElevenLabs(api_key=api_key)
    print("Sending video to ElevenLabs for Thai dubbing...")
    
    with open(file_path, "rb") as video_file:
        response = client.dubbing.dub_a_video_or_audio_file(
            file=video_file,
            target_lang="th",
            source_lang="auto"
        )
    
    dubbing_id = response.dubbing_id
    print(f"Dubbing started. Job ID: {dubbing_id}")
    
    # Poll for completion
    while True:
        status_info = client.dubbing.get_dubbing_project_metadata(dubbing_id)
        status = status_info.status
        print(f"Current Status: {status}")
        if status == "dubbed":
            break
        elif status == "failed":
            raise Exception("Dubbing job failed on ElevenLabs.")
        time.sleep(10)
        
    # Download dubbed result
    dubbed_file = client.dubbing.get_dubbed_file(dubbing_id, target_lang="th")
    with open(output_path, "wb") as f:
        for chunk in dubbed_file:
            f.write(chunk)
    print("Thai translation complete!")

if __name__ == "__main__":
    video_url = sys.argv[1]
    start_time = sys.argv[2] if len(sys.argv) > 2 else 0
    end_time = sys.argv[3] if len(sys.argv) > 3 else 30
    
    raw_video = download_video(video_url)
    short_video = convert_to_vertical_short(raw_video, start_sec=start_time, end_sec=end_time)
    translate_to_thai(short_video)






