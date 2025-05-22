import os
import subprocess
import argparse
import shutil
from pathlib import Path
import time
import random

def get_ffmpeg_path():
    """Get the FFmpeg path, either from PATH or from a specified location."""
    # Try to find ffmpeg in PATH first
    try:
        # On Windows, this will add .exe automatically
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            return ffmpeg_path
    except:
        pass
    
    # Check for common installation locations on Windows
    possible_paths = [
        r"C:\Program Files (x86)\ffmpeg-2025-05-19-git-c55d65ac0a-essentials_build\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        # Add more common paths if needed
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    # If we get here, prompt the user to specify the path
    print("FFmpeg not found in PATH or common locations.")
    ffmpeg_path = input("Please enter the full path to ffmpeg.exe: ")
    if os.path.exists(ffmpeg_path):
        return ffmpeg_path
    else:
        raise FileNotFoundError("FFmpeg executable not found. Please install FFmpeg or provide the correct path.")

def get_dimensions(file_path, ffmpeg_path):
    """Get width and height of a media file using ffprobe."""
    ffprobe_path = ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe") if ffmpeg_path.endswith("ffmpeg.exe") else ffmpeg_path.replace("ffmpeg", "ffprobe")
    cmd = [ffprobe_path, '-v', 'error', '-select_streams', 'v:0', 
           '-show_entries', 'stream=width,height', '-of', 'csv=p=0', file_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, check=True)
    width, height = map(int, result.stdout.strip().split(','))
    return width, height

def get_duration(file_path, ffmpeg_path):
    """Get duration of a video file using ffprobe."""
    ffprobe_path = ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe") if ffmpeg_path.endswith("ffmpeg.exe") else ffmpeg_path.replace("ffmpeg", "ffprobe")
    cmd = [ffprobe_path, '-v', 'error', '-show_entries', 'format=duration', 
           '-of', 'csv=p=0', file_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, check=True)
    return float(result.stdout.strip())

def ensure_directory_exists(file_path):
    """Ensure the directory for the given file path exists."""
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
        print(f"Created directory: {directory}")

def concat_segments_in_batches(temp_files, temp_dir, ffmpeg_path, output_file, batch_size=10):
    """Concatenate segments in batches to avoid command line limitations."""
    if len(temp_files) <= batch_size:
        # For small numbers of files, use the simple concat demuxer
        concat_with_concat_demuxer(temp_files, temp_dir, ffmpeg_path, output_file)
        return
    
    # For large numbers of files, process in batches
    batch_outputs = []
    
    for i in range(0, len(temp_files), batch_size):
        batch = temp_files[i:i+batch_size]
        batch_output = temp_dir / f"batch_{i//batch_size}.mp4"
        
        # Process this batch
        concat_with_concat_demuxer(batch, temp_dir, ffmpeg_path, batch_output)
        batch_outputs.append(batch_output)
    
    # Now concatenate the batch outputs
    if len(batch_outputs) == 1:
        # Only one batch, just rename it
        shutil.move(str(batch_outputs[0]), str(output_file))
    else:
        # Multiple batches need to be combined
        batch_list_path = temp_dir / 'batch_list.txt'
        with open(batch_list_path, 'w') as f:
            for out_file in batch_outputs:
                # Use absolute paths to avoid path resolution issues
                f.write(f"file '{out_file.resolve()}'\n")
        
        try:
            # Use absolute path for the list file
            subprocess.run([
                ffmpeg_path, 
                '-f', 'concat', 
                '-safe', '0',
                '-i', str(batch_list_path.resolve()), 
                '-c', 'copy',
                str(output_file), 
                '-y'
            ], check=True)
            print(f"Combined all batches into final output: {output_file}")
        except Exception as e:
            print(f"Error combining batches: {e}")
            # Attempt to copy at least the first batch as a fallback
            if os.path.exists(batch_outputs[0]):
                shutil.copy(str(batch_outputs[0]), str(output_file))
                print(f"Only copied the first batch as fallback: {output_file}")

def concat_with_concat_demuxer(temp_files, temp_dir, ffmpeg_path, output_file):
    """Use FFmpeg's concat demuxer to concatenate segments."""
    list_file_path = temp_dir / 'list.txt'
    with open(list_file_path, 'w') as f:
        for tf in temp_files:
            # Ensure we have the full path for each file
            if isinstance(tf, str):
                full_path = temp_dir / tf
            else:
                full_path = tf
            # Use absolute paths to avoid path resolution issues
            f.write(f"file '{full_path.resolve()}'\n")
    
    try:
        # Use absolute path for the list file
        subprocess.run([
            ffmpeg_path, 
            '-f', 'concat', 
            '-safe', '0',
            '-i', str(list_file_path.resolve()), 
            '-c', 'copy',
            str(output_file), 
            '-y'
        ], check=True)
        print(f"Concatenated segments into: {output_file}")
        return True
    except Exception as e:
        print(f"Error using concat demuxer: {e}")
        return False

def create_animated_background(ffmpeg_path, temp_frame_path, width, height, blur_radius, 
                             duration, temp_dir, bg_animation_style="zoom_in"):
    """
    Create an animated background with motion effect.
    
    Args:
        ffmpeg_path: Path to FFmpeg executable
        temp_frame_path: Path to the source frame
        width: Canvas width
        height: Canvas height
        blur_radius: Blur radius for the background
        duration: Duration of the animation in seconds
        temp_dir: Temporary directory
        bg_animation_style: Animation style ("zoom_in", "zoom_out", "pan_right", "pan_left", "random")
    
    Returns:
        Path to the animated background video file
    """
    # First create a blurred background image
    temp_blurred_img = temp_dir / 'temp_blurred_bg.jpg'
    
    # Make the blur image slightly larger than needed for movement
    scale_factor = 1.2  # 20% larger to allow for movement
    larger_width = int(width * scale_factor)
    larger_height = int(height * scale_factor)
    
    subprocess.run([
        ffmpeg_path, '-i', str(temp_frame_path),
        '-vf', f'scale={larger_width}:{larger_height}:force_original_aspect_ratio=increase,'
               f'crop={larger_width}:{larger_height},gblur=sigma={blur_radius}',
        str(temp_blurred_img), '-y'
    ], check=True)
    
    # Select animation style
    if bg_animation_style == "random":
        styles = ["zoom_in", "zoom_out", "pan_right", "pan_left", "pan_diagonal"]
        bg_animation_style = random.choice(styles)
    
    # Parameters for the animation
    fps = 30
    total_frames = int(fps * duration)
    animated_bg_path = temp_dir / 'temp_animated_bg.mp4'
    
    # Configure the animation based on style
    if bg_animation_style == "zoom_in":
        # Zoom in: start wide, end closer
        zoom_start, zoom_end = 1.0, 1.1
        zoom_expr = f"{zoom_start}+({zoom_end}-{zoom_start})*on/{total_frames}"
        x_expr = f"(iw-iw*{zoom_expr})/2"
        y_expr = f"(ih-ih*{zoom_expr})/2"
        
        filter_complex = f'zoompan=z=\'{zoom_expr}\':x=\'{x_expr}\':y=\'{y_expr}\':d={total_frames}:s={width}x{height}:fps={fps}'
        
    elif bg_animation_style == "zoom_out":
        # Zoom out: start close, end wider
        zoom_start, zoom_end = 1.1, 1.0
        zoom_expr = f"{zoom_start}+({zoom_end}-{zoom_start})*on/{total_frames}"
        x_expr = f"(iw-iw*{zoom_expr})/2"
        y_expr = f"(ih-ih*{zoom_expr})/2"
        
        filter_complex = f'zoompan=z=\'{zoom_expr}\':x=\'{x_expr}\':y=\'{y_expr}\':d={total_frames}:s={width}x{height}:fps={fps}'
        
    elif bg_animation_style == "pan_right":
        # Pan right: move from left to right
        x_expr = f"on*{larger_width-width}/{total_frames}"
        
        filter_complex = f'crop={width}:{height}:{x_expr}:0:s={width}x{height}:fps={fps}'
        
    elif bg_animation_style == "pan_left":
        # Pan left: move from right to left
        x_expr = f"{larger_width-width}-on*{larger_width-width}/{total_frames}"
        
        filter_complex = f'crop={width}:{height}:{x_expr}:0:s={width}x{height}:fps={fps}'
        
    elif bg_animation_style == "pan_diagonal":
        # Pan diagonally
        x_expr = f"on*{larger_width-width}/{total_frames}"
        y_expr = f"on*{larger_height-height}/{total_frames}"
        
        filter_complex = f'crop={width}:{height}:{x_expr}:{y_expr}:s={width}x{height}:fps={fps}'
    
    else:
        # Default to static if unknown style
        filter_complex = f'crop={width}:{height}:0:0:s={width}x{height}:fps={fps}'
    
    # Run FFmpeg to create the animated background
    subprocess.run([
        ffmpeg_path,
        '-loop', '1',
        '-i', str(temp_blurred_img),
        '-filter_complex', filter_complex,
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-t', str(duration),
        '-preset', 'fast', '-crf', '23',
        str(animated_bg_path), '-y'
    ], check=True)
    
    return animated_bg_path

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='Assemble a carousel video from images and videos.')
    parser.add_argument('--input-dir', required=True, help='Directory containing image and video assets')
    parser.add_argument('--output-file', required=True, help='Output MP4 file path')
    parser.add_argument('--width', type=int, default=1280, help='Canvas width (default: 1280)')
    parser.add_argument('--height', type=int, default=720, help='Canvas height (default: 720)')
    parser.add_argument('--image-duration', type=float, default=10, 
                        help='Duration for images in seconds (default: 5)')
    parser.add_argument('--max-video-duration', type=float, default=10, 
                        help='Max duration for videos in seconds (default: 10)')
    parser.add_argument('--blur-radius', type=float, default=20, 
                        help='Gaussian blur radius (default: 20)')
    parser.add_argument('--ffmpeg-path', help='Custom path to ffmpeg executable')
    parser.add_argument('--bg-animation', choices=['zoom_in', 'zoom_out', 'pan_right', 'pan_left', 'random', 'none'], 
                        default='zoom_in', help='Background animation style (default: zoom_in)')
    args = parser.parse_args()

    # Set up variables
    input_dir = Path(args.input_dir).resolve()
    output_file = Path(args.output_file).resolve()
    width, height = args.width, args.height
    image_duration = args.image_duration
    max_video_duration = args.max_video_duration
    blur_radius = args.blur_radius
    bg_animation_style = args.bg_animation
    
    # Get ffmpeg path
    ffmpeg_path = args.ffmpeg_path if args.ffmpeg_path else get_ffmpeg_path()
    print(f"Using FFmpeg from: {ffmpeg_path}")

    # Ensure output directory exists
    try:
        ensure_directory_exists(str(output_file))
    except Exception as e:
        print(f"Error creating output directory: {e}")
        return

    # Check if output file is locked or in use
    if output_file.exists():
        try:
            # Try to rename the file - this will fail if it's locked
            temp_name = output_file.with_name(f"{output_file.stem}_temp{output_file.suffix}")
            output_file.rename(temp_name)
            temp_name.rename(output_file)
            print("Output file is not locked, proceeding.")
        except PermissionError:
            print("Output file appears to be in use. Using a different filename.")
            # Create an alternative filename with timestamp
            timestamp = int(time.time())
            output_file = output_file.with_name(f"{output_file.stem}_{timestamp}{output_file.suffix}")
            print(f"New output file: {output_file}")

    # Supported file extensions
    image_exts = {'.jpg', '.jpeg', '.png'}
    video_exts = {'.mp4'}
    supported_exts = image_exts.union(video_exts)

    # Create a temporary working directory
    temp_dir = Path('temp_carousel_files').resolve()
    if temp_dir.exists():
        # Clean existing files in temp directory
        for old_file in temp_dir.glob('*'):
            try:
                old_file.unlink()
            except:
                print(f"Warning: Could not delete old temp file {old_file}")
    else:
        temp_dir.mkdir()

    # Scan and sort files
    files = sorted(f for f in input_dir.iterdir() if f.suffix.lower() in supported_exts)
    if not files:
        print("No supported files found in the input directory.")
        return

    temp_files = []
    
    # Use dedicated paths in temp directory
    temp_frame_path = temp_dir / 'temp_frame.jpg'

    # Process each file
    for idx, file in enumerate(files):
        print(f"Processing: {file.name} ({idx+1}/{len(files)})")
        is_image = file.suffix.lower() in image_exts
        
        input_file_path = file.resolve()

        # Extract first frame
        if is_image:
            shutil.copy(str(input_file_path), str(temp_frame_path))
        else:
            subprocess.run([ffmpeg_path, '-i', str(input_file_path), '-vframes', '1', str(temp_frame_path), 
                           '-y'], check=True)

        # Determine duration for this segment
        if is_image:
            segment_duration = image_duration
        else:
            try:
                duration = get_duration(str(input_file_path), ffmpeg_path)
                segment_duration = min(duration, max_video_duration)
            except:
                segment_duration = max_video_duration
                

        # Generate static blurred background (original behavior)
        bg_path = temp_dir / 'temp_bg.jpg'
        subprocess.run([
            ffmpeg_path, '-i', str(temp_frame_path),
            '-vf', f'scale={width}:{height}:force_original_aspect_ratio=increase,'
                    f'crop={width}:{height},gblur=sigma={blur_radius}',
            str(bg_path), '-y'
        ], check=True)

        # Calculate resized dimensions for overlay
        if is_image:
            # For images, use ffmpeg to get dimensions
            try:
                w, h = get_dimensions(str(input_file_path), ffmpeg_path)
            except:
                # Fallback using PIL if ffprobe fails
                from PIL import Image
                img = Image.open(input_file_path)
                w, h = img.size
                img.close()
        else:
            w, h = get_dimensions(str(input_file_path), ffmpeg_path)
            
        scale = min(width / w, height / h)
        scaled_w, scaled_h = int(w * scale), int(h * scale)
        x, y = (width - scaled_w) // 2, (height - scaled_h) // 2

        # Create scene - use numbers as filenames without paths
        temp_mp4 = f'segment_{idx:03d}.mp4'
        temp_mp4_path = temp_dir / temp_mp4
        
        if is_image:
            cmd = [
                ffmpeg_path,
                '-loop', '1', '-i', str(bg_path),
                '-loop', '1', '-i', str(input_file_path),
                '-filter_complex', 
                f'[1:v]scale={scaled_w}:{scaled_h}[overlay];'
                f'[0:v][overlay]overlay={x}:{y}[v]',
                '-map', '[v]',
                '-t', str(segment_duration),
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-an',  # No audio for images
                str(temp_mp4_path), '-y'
            ]
        else:
            # For videos
            try:
                # Check if the video has audio
                ffprobe_path = ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe") if ffmpeg_path.endswith("ffmpeg.exe") else ffmpeg_path.replace("ffmpeg", "ffprobe")
                audio_check_cmd = [
                    ffprobe_path, '-i', str(input_file_path), 
                    '-show_streams', '-select_streams', 'a', '-loglevel', 'error'
                ]
                audio_result = subprocess.run(audio_check_cmd, stdout=subprocess.PIPE, text=True)
                has_audio = bool(audio_result.stdout.strip())
                
                # Original static background method
                if has_audio:
                    cmd = [
                        ffmpeg_path,
                        '-loop', '1', '-t', str(segment_duration), '-i', str(bg_path),
                        '-i', str(input_file_path),
                        '-filter_complex',
                        f'[1:v]scale={scaled_w}:{scaled_h}[overlay];'
                        f'[0:v][overlay]overlay={x}:{y}[v];'
                        f'[1:a]atrim=0:{segment_duration},asetpts=PTS-STARTPTS[a]',
                        '-map', '[v]', '-map', '[a]',
                        '-t', str(segment_duration),
                        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                        '-c:a', 'aac', '-b:a', '128k',
                        str(temp_mp4_path), '-y'
                    ]
                else:
                        # Video without audio
                        cmd = [
                            ffmpeg_path,
                            '-loop', '1', '-t', str(segment_duration), '-i', str(bg_path),
                            '-i', str(input_file_path),
                            '-filter_complex',
                            f'[1:v]scale={scaled_w}:{scaled_h}[overlay];'
                            f'[0:v][overlay]overlay={x}:{y}[v]',
                            '-map', '[v]',
                            '-t', str(segment_duration),
                            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                            '-an',
                            str(temp_mp4_path), '-y'
                        ]
            except Exception as e:
                print(f"Error processing video: {e}")
                # Fallback to default duration and no audio
                cmd = [
                    ffmpeg_path,
                    '-loop', '1', '-t', str(max_video_duration), '-i', str(bg_path),
                    '-i', str(input_file_path),
                    '-filter_complex',
                    f'[1:v]scale={scaled_w}:{scaled_h}[overlay];'
                    f'[0:v][overlay]overlay={x}:{y}[v]',
                    '-map', '[v]',
                    '-t', str(max_video_duration),
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                    '-an',
                    str(temp_mp4_path), '-y'
                ]
                
        try:
            subprocess.run(cmd, check=True)
            temp_files.append(temp_mp4)  # Store just the filename, not the path
        except Exception as e:
            print(f"Error creating segment for {file.name}: {e}")
            continue

    if not temp_files:
        print("No segments were successfully created. Cannot create output video.")
        return

    # Use improved batch concatenation method
    try:
        concat_segments_in_batches(temp_files, temp_dir, ffmpeg_path, output_file, batch_size=10)
    except Exception as e:
        print(f"Error during final concatenation: {e}")
    
    # Clean up temp directory
    try:
        for temp_file in temp_dir.glob('*'):
            try:
                temp_file.unlink()
            except:
                print(f"Warning: Could not delete {temp_file}")
        try:
            temp_dir.rmdir()
        except:
            print(f"Warning: Could not remove temp directory. It may not be empty.")
    except Exception as e:
        print(f"Warning: Could not completely clean up temp directory: {e}")

if __name__ == '__main__':
    main()