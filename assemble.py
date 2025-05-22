import os
import subprocess
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from functools import partial

def run_ffprobe(cmd, file_path):
    """Run ffprobe command and return output."""
    try:
        result = subprocess.run(
            cmd + [file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
            text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffprobe error: {e.stderr}")

def get_dimensions(file_path):
    """Get width and height of a media file using ffprobe."""
    cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', 
           '-show_entries', 'stream=width,height', '-of', 'csv=p=0']
    width, height = map(int, run_ffprobe(cmd, file_path).split(','))
    return width, height

def get_duration(file_path):
    """Get duration of a video file using ffprobe."""
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
           '-of', 'csv=p=0']
    return float(run_ffprobe(cmd, file_path))

def validate_16_9_aspect_ratio(width, height):
    """Validate that width and height form a 16:9 aspect ratio."""
    aspect_ratio = width / height
    if abs(aspect_ratio - 16/9) > 0.01:
        raise ValueError(f"Aspect ratio {aspect_ratio:.4f} is not 16:9")

def get_resolution_dimensions(resolution):
    """Return width and height for a given resolution preset."""
    presets = {'720p': (1280, 720), '1080p': (1920, 1080), '4k': (3840, 2160)}
    if resolution not in presets:
        raise ValueError(f"Invalid resolution: {resolution}. Choose from {list(presets.keys())}")
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

    filter_complex = []
    inputs = [f'-i "{temp_file}"' for temp_file in temp_files]
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

    # Corrected f-string: Properly escape and format the filter_complex
    filter_complex_str = ';'.join(filter_complex)
    cmd = (
        f'ffmpeg {" ".join(inputs)} -filter_complex "{filter_complex_str}" '
        f'-map "{last_video}" -map "{last_audio}" -c:v libx264 -pix_fmt yuv420p '
        f'-preset ultrafast -crf 23 -c:a aac -b:a 128k -movflags +faststart "{output_file}" -y'
    )
    try:
        subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg error: {e.stderr}")

def process_file(file, idx, args, width, height, fps):
    """Process a single file (image or video) to create a clip with zoomed background."""
    is_image = file.suffix.lower() in {'.jpg', '.jpeg', '.png'}
    temp_mp4 = f'temp_{idx:03d}.mp4'
    clip_duration = args.image_duration if is_image else min(get_duration(str(file)), args.max_video_duration)
    if idx < args.total_files - 1:
        clip_duration += args.transition_duration

    # Extract first frame and create background in one FFmpeg call
    scale_factor = 1.2
    larger_width, larger_height = int(width * scale_factor), int(height * scale_factor)
    total_frames = int(fps * clip_duration)
    zoom_expr = f"{args.zoom_start}+({args.zoom_end}-{args.zoom_start})*on/{total_frames}"
    x_expr, y_expr = f"(iw-iw*{zoom_expr})/2", f"(ih-ih*{zoom_expr})/2"
    
    # Calculate overlay dimensions
    w, h = get_dimensions(str(file))
    scale = min(width / w, height / h) * args.overlay_scale
    scaled_w, scaled_h = int(w * scale), int(h * scale)
    x, y = (width - scaled_w) // 2, (height - scaled_h) // 2

    # Single FFmpeg command for background and overlay
    input_cmd = f'-loop 1 -i "{file}"' if is_image else f'-i "{file}"'
    audio_cmd = '-f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100' if is_image else ''
    filter_complex = (
        f'[0:v]scale={larger_width}:{larger_height}:force_original_aspect_ratio=increase,'
        f'crop={larger_width}:{larger_height},gblur=sigma={args.blur_radius},'
        f'zoompan=z=\'{zoom_expr}\':x=\'{x_expr}\':y=\'{y_expr}\':d={total_frames}:s={width}x{height}:fps={fps},'
        f'format=yuv420p[bg];'
        f'[0:v]scale={scaled_w}:{scaled_h},format=yuv420p[overlay];'
        f'[bg][overlay]overlay={x}:{y}[v]'
    )
    audio_filter = f'[0:a]atrim=0:{clip_duration},asetpts=PTS-STARTPTS[a]' if not is_image else f'[1:a]atrim=0:{clip_duration},asetpts=PTS-STARTPTS[a]'
    
    cmd = (
        f'ffmpeg {input_cmd} {audio_cmd} -filter_complex "{filter_complex};{audio_filter}" '
        f'-map "[v]" -map "[a]" -t {clip_duration} -c:v libx264 -pix_fmt yuv420p '
        f'-preset ultrafast -crf 23 -c:a aac -b:a 128k -movflags +faststart "{temp_mp4}" -y'
    )
    try:
        subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return temp_mp4, clip_duration
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg error processing {file}: {e.stderr}")

def assemble(args):
    # Validate arguments
    if not 0.0 < args.overlay_scale <= 1.0:
        raise ValueError("Overlay scale must be between 0.0 and 1.0")
    if args.transition_duration < 0 or args.transition_duration >= min(args.image_duration, args.max_video_duration):
        raise ValueError("Transition duration must be non-negative and less than clip duration")

    # Handle resolution and dimensions
    width, height = args.resolution and get_resolution_dimensions(args.resolution) or (args.width or 1280, args.height or 720)
    validate_16_9_aspect_ratio(width, height)
    check_file_writable(args.output_file)

    # Scan and sort files
    input_dir = Path(args.input_dir)
    supported_exts = {'.jpg', '.jpeg', '.png', '.mp4'}
    files = sorted(f for f in input_dir.iterdir() if f.suffix.lower() in supported_exts)
    if not files:
        raise FileNotFoundError("No supported files found")
    args.total_files = len(files)

    # Process files in parallel
    temp_files, clip_durations = [], []
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        results = executor.map(lambda f, i: process_file(f, i, args=args, width=width, height=height, fps=30), files, range(len(files)))
        for temp_mp4, clip_duration in results:
            temp_files.append(temp_mp4)
            clip_durations.append(clip_duration)

    # Validate temporary files
    for temp_file in temp_files:
        if not os.path.exists(temp_file) or os.path.getsize(temp_file) == 0:
            raise FileNotFoundError(f"Temporary file {temp_file} is missing or empty")

    # Process clips in batches
    batch_size = 10
    batch_files = []
    for batch_idx, i in enumerate(range(0, len(temp_files), batch_size)):
        batch_temp_files = temp_files[i:i + batch_size]
        batch_clip_durations = clip_durations[i:i + batch_size]
        batch_output = f"batch_{batch_idx:03d}.mp4"
        concatenate_batch(batch_temp_files, batch_clip_durations, args.transition_duration, batch_output, width, height)
        batch_files.append(batch_output)

    # Final concatenation
    output_file = Path(args.output_file).resolve()
    if len(batch_files) == 1:
        os.rename(batch_files[0], output_file)
    else:
        list_file = 'batch_list.txt'
        with open(list_file, 'w') as f:
            f.writelines(f"file '{Path(batch_file).absolute()}'\n" for batch_file in batch_files)
        cmd = (
            f'ffmpeg -f concat -safe 0 -i "{list_file}" -c:v libx264 -pix_fmt yuv420p '
            f'-preset ultrafast -crf 23 -c:a aac -b:a 128k -movflags +faststart "{output_file}" -y'
        )
        subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        os.remove(list_file)

    # Clean up
    for temp in temp_files + batch_files:
        if os.path.exists(temp):
            os.remove(temp)
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
    parser.add_argument('--threads', type=int, default=4, help='Number of parallel processing threads')
    args = parser.parse_args()

    # Validate arguments
    if not 0.0 < args.overlay_scale <= 1.0:
        raise ValueError("Overlay scale must be between 0.0 and 1.0")
    if args.transition_duration < 0 or args.transition_duration >= min(args.image_duration, args.max_video_duration):
        raise ValueError("Transition duration must be non-negative and less than clip duration")

    # Handle resolution and dimensions
    width, height = args.resolution and get_resolution_dimensions(args.resolution) or (args.width or 1280, args.height or 720)
    validate_16_9_aspect_ratio(width, height)
    check_file_writable(args.output_file)

    # Scan and sort files
    input_dir = Path(args.input_dir)
    supported_exts = {'.jpg', '.jpeg', '.png', '.mp4'}
    files = sorted(f for f in input_dir.iterdir() if f.suffix.lower() in supported_exts)
    if not files:
        raise FileNotFoundError("No supported files found")
    args.total_files = len(files)

    # Process files in parallel
    temp_files, clip_durations = [], []
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        results = executor.map(partial(process_file, args=args, width=width, height=height, fps=30), files, range(len(files)))
        for temp_mp4, clip_duration in results:
            temp_files.append(temp_mp4)
            clip_durations.append(clip_duration)

    # Validate temporary files
    for temp_file in temp_files:
        if not os.path.exists(temp_file) or os.path.getsize(temp_file) == 0:
            raise FileNotFoundError(f"Temporary file {temp_file} is missing or empty")

    # Process clips in batches
    batch_size = 10
    batch_files = []
    for batch_idx, i in enumerate(range(0, len(temp_files), batch_size)):
        batch_temp_files = temp_files[i:i + batch_size]
        batch_clip_durations = clip_durations[i:i + batch_size]
        batch_output = f"batch_{batch_idx:03d}.mp4"
        concatenate_batch(batch_temp_files, batch_clip_durations, args.transition_duration, batch_output, width, height)
        batch_files.append(batch_output)

    # Final concatenation
    output_file = Path(args.output_file).resolve()
    if len(batch_files) == 1:
        os.rename(batch_files[0], output_file)
    else:
        list_file = 'batch_list.txt'
        with open(list_file, 'w') as f:
            f.writelines(f"file '{Path(batch_file).absolute()}'\n" for batch_file in batch_files)
        cmd = (
            f'ffmpeg -f concat -safe 0 -i "{list_file}" -c:v libx264 -pix_fmt yuv420p '
            f'-preset ultrafast -crf 23 -c:a aac -b:a 128k -movflags +faststart "{output_file}" -y'
        )
        subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        os.remove(list_file)

    # Clean up
    for temp in temp_files + batch_files:
        if os.path.exists(temp):
            os.remove(temp)
    print(f"Carousel video created: {output_file}")

if __name__ == '__main__':
    main()