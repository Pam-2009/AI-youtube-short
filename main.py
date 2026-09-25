import sys
import os
import time
import requests
from moviepy.editor import VideoFileClip
from elevenlabs.client import ElevenLabs

def download_video_via_cobalt(url, output_path="downloaded_video.mp4"):
    print(f"Fetching direct download link via Cobalt API for: {url}")
    
    # ส่ง Request ไปยัง Cobalt API Public Instance
    api_url = "https://api.cobalt.tools/api/json"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    payload = {
        "url": url,
        "vCodec": "h264"
    }
    
    response = requests.post(api_url, json=payload, headers=headers)
    data = response.json()
    
    # ตรวจสอบสถานะการดึงข้อมูล
    if data.get("status") in ["stream", "redirect"]:
        download_url = data.get("url")
    elif data.get("status") == "picker":
        download_url = data["picker"][0]["url"]
    else:
        raise Exception(f"Cobalt API failed: {data.get('text', 'Unknown error')}")

    print("Downloading video stream...")
    video_data = requests.get(download_url, stream=True)
    with open(output_path, "wb") as f:
        for chunk in video_data.iter_content(chunk_size=1024*1024):
            if chunk:
                f.write(chunk)
                
    print("Video downloaded successfully!")
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
    
    raw_video = download_video_via_cobalt(video_url)
    short_video = convert_to_vertical_short(raw_video, start_sec=start_time, end_sec=end_time)
    translate_to_thai(short_video)
