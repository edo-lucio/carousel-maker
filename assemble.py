import os
import subprocess
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import uuid
import tempfile

def run_ffprobe(file_path, entries, stream='v:0'):
    """Run ffprobe command and return parsed output."""
    cmd = ['ffprobe', '-v', 'error', '-select_streams', stream, 
           '-show_entries', entries, '-of', 'csv=p=0']
    try:
        result = subprocess.run(
            cmd + [file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
            text=True, check=True, encoding='utf-8'
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffprobe error: {e.stderr}")

def get_dimensions(file_path):
    """Get width and height of a media file using ffprobe."""
    width, height = map(int, run_ffprobe(file_path, 'stream=width,height').split(','))
    return width, height

def get_duration(file_path):
    """Get duration of a video file using ffprobe."""
    try:
        return float(run_ffprobe(file_path, 'format=duration', stream='v:0'))
    except ValueError:
        return 0.0

def validate_16_9_aspect_ratio(width, height):
    """Validate that width and height form a 16:9 aspect ratio."""
    if abs(width / height - 16/9) > 0.01:
        raise ValueError(f"Aspect ratio {width/height:.4f} is not 16:9")

def get_resolution_dimensions(resolution):
    """Return width and height for a given resolution preset."""
    presets = {'720p': (1280, 720), '1080p': (1920, 1080), '4k': (3840, 2160)}
    return presets[resolution]

def check_file_writable(file_path):
    """Ensure output file path is writable."""
    file_path = Path(file_path)
    os.makedirs(file_path.parent, exist_ok=True)
    if not os.access(file_path.parent, os.W_OK):
        raise PermissionError(f"No write permission for {file_path.parent}")

def concatenate_batch(temp_files, clip_durations, transition_duration, output_file, width, height):
    """Concatenate a batch of clips with crossfade transitions."""
    if len(temp_files) == 1:
        os.rename(temp_files[0], output_file)
        return

    inputs = [f'-i "{temp_file}"' for temp_file in temp_files]
    filter_complex = []
    current_offset = 0
    last_video, last_audio = "[0:v]", "[0:a]"
    for i in range(1, len(temp_files)):
        offset = max(0, current_offset + clip_durations[i-1] - transition_duration)
        filter_complex.append(
            f"{last_video}[{i}:v]xfade=transition=fade:duration={transition_duration}:offset={offset}[v{i}];"
            f"{last_audio}[{i}:a]acrossfade=d={transition_duration}[a{i}]"
        )
        last_video, last_audio = f"[v{i}]", f"[a{i}]"
        current_offset = offset

    cmd = (
        f'ffmpeg {" ".join(inputs)} -filter_complex "{';'.join(filter_complex)}" '
        f'-map "{last_video}" -map "{last_audio}" -c:v libx264 -pix_fmt yuv420p '
        f'-preset ultrafast -crf 28 -c:a aac -b:a 128k -movflags +faststart "{output_file}" -y'
    )
    try:
        subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg error: {e.stderr}")

def process_file(file, idx, args, width, height, fps, temp_dir):
    """Process a single file to create a clip with zoomed background and fading filename overlay."""
    is_image = file.suffix.lower() in {'.jpg', '.jpeg', '.png'}
    temp_mp4 = os.path.join(temp_dir, f'temp_{idx:03d}_{uuid.uuid4().hex}.mp4')
    clip_duration = args.image_duration if is_image else min(get_duration(str(file)), args.max_video_duration)
    if idx < args.total_files - 1:
        clip_duration += args.transition_duration

    scale_factor = 1.0
    larger_width, larger_height = int(width * scale_factor), int(height * scale_factor)
    total_frames = int(fps * clip_duration)
    zoom_expr = f"{args.zoom_start}+({args.zoom_end}-{args.zoom_start})*on/{total_frames}"
    x_expr, y_expr = f"(iw-iw*{zoom_expr})/2", f"(ih-ih*{zoom_expr})/2"
    
    w, h = get_dimensions(str(file))
    scale = min(width / w, height / h) * args.overlay_scale
    scaled_w, scaled_h = int(w * scale), int(h * scale)
    x, y = (width - scaled_w) // 2, (height - scaled_h) // 2

    filename = file.name.replace("'", "'\\''")
    font_size = 34
    text_y = height - int(height * 0.1)
    fade_in_end = args.text_fade_in
    fade_out_start = clip_duration - args.text_fade_out

    if fade_in_end >= fade_out_start:
        raise ValueError(f"Fade-in end time ({fade_in_end}s) must be less than fade-out start time ({fade_out_start}s)")

    input_cmd = f'-loop 1 -i "{file}"' if is_image else f'-i "{file}"'
    audio_cmd = '-f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100' if is_image else ''
    filter_complex = (
        f'[0:v]scale={larger_width}:{larger_height}:force_original_aspect_ratio=increase,'
        f'crop={larger_width}:{larger_height},gblur=sigma={args.blur_radius},'
        f'zoompan=z=\'{zoom_expr}\':x=\'{x_expr}\':y=\'{y_expr}\':d={total_frames}:s={width}x{height}:fps={fps},'
        f'colorchannelmixer=rr={args.background_opacity}:gg={args.background_opacity}:bb={args.background_opacity}[bg];'
        f'[0:v]scale={scaled_w}:{scaled_h},format=rgba,colorchannelmixer=aa=0.8[overlay];'
        f'[bg][overlay]overlay={x}:{y},'
        f'drawtext=text=\'{filename}\':fontfile=Anton-Regular.ttf:'
        f'fontcolor=yellow:fontsize={font_size}:x=(w-text_w)/2:y={text_y}:'
        f'borderw=2:bordercolor=black:'
        f'alpha=\'if(lt(t,{args.text_fade_in}),t/{args.text_fade_in},if(gt(t,{fade_out_start}),1-(t-{fade_out_start})/{args.text_fade_out},1))\'[v]'
    )
    audio_filter = f'[0:a]atrim=0:{clip_duration},asetpts=PTS-STARTPTS[a]' if not is_image else f'[1:a]atrim=0:{clip_duration},asetpts=PTS-STARTPTS[a]'
    
    cmd = (
        f'ffmpeg {input_cmd} {audio_cmd} -filter_complex "{filter_complex};{audio_filter}" '
        f'-map "[v]" -map "[a]" -t {clip_duration} -c:v libx264 -pix_fmt yuv420p '
        f'-preset ultrafast -crf 28 -c:a aac -b:a 128k -movflags +faststart -threads 1 "{temp_mp4}" -y'
    )
    try:
        subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return temp_mp4, clip_duration
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg error processing {file}: {e.stderr}")

def assemble(args):
    if not 0.0 < args.overlay_scale <= 1.0:
        raise ValueError("Overlay scale must be between 0.0 and 1.0")
    if args.transition_duration < 0 or args.transition_duration >= min(args.image_duration, args.max_video_duration):
        raise ValueError("Transition duration must be non-negative and less than clip duration")
    if args.text_fade_in < 0 or args.text_fade_out < 0:
        raise ValueError("Text fade-in and fade-out times must be non-negative")
    if args.text_fade_in + args.text_fade_out >= min(args.image_duration, args.max_video_duration):
        raise ValueError("Sum of text fade-in and fade-out times must be less than clip duration")
    if not 0.0 <= args.background_opacity <= 1.0:
        raise ValueError("Background opacity must be between 0.0 and 1.0")

    width, height = args.resolution and get_resolution_dimensions(args.resolution) or (args.width or 1280, args.height or 720)
    validate_16_9_aspect_ratio(width, height)
    check_file_writable(args.output_file)

    input_dir = Path(args.input_dir)
    supported_exts = {'.jpg', '.jpeg', '.png', '.mp4'}
    files = sorted(f for f in input_dir.iterdir() if f.suffix.lower() in supported_exts)
    if not files:
        raise FileNotFoundError("No supported files found")
    args.total_files = len(files)

    batch_size = max(1, min(10, args.threads * 2))  # Adjust batch size based on threads
    output_file = Path(args.output_file).resolve()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            for batch_idx, start_idx in enumerate(range(0, len(files), batch_size)):
                batch_files = files[start_idx:start_idx + batch_size]
                results = executor.map(
                    partial(process_file, args=args, width=width, height=height, fps=30, temp_dir=temp_dir),
                    batch_files, range(len(batch_files))
                )
                
                batch_temp_files, batch_clip_durations = [], []
                for temp_mp4, clip_duration in results:
                    if not os.path.exists(temp_mp4) or os.path.getsize(temp_mp4) == 0:
                        raise FileNotFoundError(f"Temporary file {temp_mp4} is missing or empty")
                    batch_temp_files.append(temp_mp4)
                    batch_clip_durations.append(clip_duration)
                
                batch_output = os.path.join(temp_dir, f"batch_{batch_idx:03d}_{uuid.uuid4().hex}.mp4")
                concatenate_batch(batch_temp_files, batch_clip_durations, args.transition_duration, batch_output, width, height)
                
                if batch_idx == 0:
                    if os.path.exists(output_file):
                        os.remove(output_file)
                    os.rename(batch_output, output_file)
                else:
                    temp_concat_file = os.path.join(temp_dir, f"temp_concat_{uuid.uuid4().hex}.mp4")
                    list_file = os.path.join(temp_dir, f"concat_list_{uuid.uuid4().hex}.txt")
                    with open(list_file, 'w') as f:
                        f.write(f"file '{output_file}'\n")
                        f.write(f"file '{batch_output}'\n")
                    cmd = (
                        f'ffmpeg -f concat -safe 0 -i "{list_file}" -c:v copy -c:a copy '
                        f'-movflags +faststart "{temp_concat_file}" -y'
                    )
                    try:
                        subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                        if os.path.exists(output_file):
                            os.remove(output_file)
                        os.rename(temp_concat_file, output_file)
                    except subprocess.CalledProcessError as e:
                        raise RuntimeError(f"FFmpeg error during concatenation: {e.stderr}")
    
    return f"Carousel video created: {output_file}"

def main():
    parser = argparse.ArgumentParser(description='Assemble a carousel video with zooming background and crossfade transitions (16:9).')
    parser.add_argument('--input-dir', required=True, help='Directory with image/video assets')
    parser.add_argument('--output-file', required=True, help='Output MP4 file path')
    parser.add_argument('--resolution', choices=['720p', '1080p', '4k'], help='Resolution preset')
    parser.add_argument('--width', type=int, help='Canvas width (16:9 ratio with height)')
    parser.add_argument('--height', type=int, help='Canvas height (16:9 ratio with width)')
    parser.add_argument('--image-duration', type=float, default=5, help='Image duration in seconds')
    parser.add_argument('--max-video-duration', type=float, default=10, help='Max video duration in seconds')
    parser.add_argument('--blur-radius', type=float, default=20, help='Gaussian blur radius')
    parser.add_argument('--zoom-start', type=float, default=1.0, help='Background zoom start')
    parser.add_argument('--zoom-end', type=float, default=1.2, help='Background zoom end')
    parser.add_argument('--overlay-scale', type=float, default=0.9, help='Overlay scale factor (0.0 to 1.0)')
    parser.add_argument('--transition-duration', type=float, default=1.0, help='Crossfade transition duration in seconds')
    parser.add_argument('--text-fade-in', type=float, default=0.5, help='Time to complete text fade-in from start of clip (seconds)')
    parser.add_argument('--text-fade-out', type=float, default=0.5, help='Time to complete text fade-out before end of clip (seconds)')
    parser.add_argument('--background-opacity', type=float, default=1.0, help='Background luminosity (0.0 to 1.0, lower is darker)')
    parser.add_argument('--threads', type=int, default=4, help='Number of parallel processing threads')
    args = parser.parse_args()
    print(assemble(args))

if __name__ == '__main__':
    main()