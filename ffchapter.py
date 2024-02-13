#!/usr/bin/python3

import argparse
import subprocess
import json
import os
import platform

from datetime import datetime, timedelta
from glob import glob

# ---Helper functions---
def get_abs_path(relative_path):
    return os.path.join(os.getcwd(), relative_path)

def ensure_directories_exist():
    directories = ["tmp", "log"]
    for dir in directories:
        abs_dir_path = get_abs_path(dir)
        os.makedirs(abs_dir_path, exist_ok=True)
        print(f"[INFO] '{abs_dir_path}' directory is ready.")

def user_confirmation():
    """Prompts user for confirmation to proceed with cleanup."""
    user_input = input("Proceed with cleanup? (yes/no): ").lower()
    if user_input in ['yes', 'y']:
        return True
    return False

def cleanup_directories():
    for folder in ["log", "tmp", "__pycache__"]:
        abs_folder_path = get_abs_path(folder)
        for root, dirs, files in os.walk(abs_folder_path, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        print(f"[INFO] Cleaned up '{abs_folder_path}' folder.")
    ffjob_json_path = get_abs_path("ffjob.json")
    if os.path.exists(ffjob_json_path):
        os.remove(ffjob_json_path)
        print(f"[INFO] '{ffjob_json_path}' file removed.")

def verify_chapter_files():
    """Verify that all chapter files exist in the tmp folder."""
    chapter_files = glob("tmp/*.mkv")
    if not chapter_files:
        print("[ERROR] No chapter files found in 'tmp' directory.")
        exit(1)
    print("[INFO] All chapter files found. Proceeding to concatenation.")

# ---Main functions---
def run_vmaf(original_file, encoded_file):
    """Runs VMAF test on the first 1 minute of the original and encoded files."""
    try:
        print("[INFO] Running VMAF test on the first 1 minute of video...")
        vmaf_command = [
            "ffmpeg",
            "-i", encoded_file,
            "-i", original_file,
            "-filter_complex", "[0:v]setpts=PTS-STARTPTS[reference];[1:v]setpts=PTS-STARTPTS[distorted];[distorted][reference]libvmaf=model_path=/usr/local/share/model/vmaf_v0.6.1.json:log_path=vmaf_log.json:log_fmt=json",
            "-t", "60",
            "-f", "null",
            "-"
        ]
        result = subprocess.run(vmaf_command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"VMAF test error: {result.stderr}")
        with open("vmaf_log.json", 'r') as log_file:
            vmaf_results = json.load(log_file)
        vmaf_score = vmaf_results['VMAF_score']
        print(f"[INFO] VMAF score for the first 1 minute: {vmaf_score}")
        return vmaf_score
    except Exception as e:
        print(f"[ERROR] Failed to run VMAF test: {e}")
        exit(1)

def concatenate_chapters():
    try:
        print("[INFO] Concatenating chapter files...")
        tmp_dir = get_abs_path("tmp")
        chapter_files = sorted(glob(os.path.join(tmp_dir, "*.mkv")))
        concat_file_path = os.path.join(tmp_dir, "concat.txt")
        with open(concat_file_path, "w") as concat_file:
            for file_path in chapter_files:
                abs_path = os.path.abspath(file_path)
                formatted_path = abs_path.replace("\\", "/") if os.name == 'nt' else abs_path
                concat_file.write(f"file '{formatted_path}'\n")

        concat_command = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file_path,
            "-c", "copy",
            os.path.join(os.getcwd(), "output.mkv")
        ]
        subprocess.run(concat_command, check=True)
        print("[SUCCESS] Chapters concatenated successfully into 'output.mkv'.")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to concatenate chapter files: {e}")
        exit(1)

def run_ffprobe(input_file):
    """Runs ffprobe on the input file and returns JSON output."""
    try:
        print(f"[INFO] Running ffprobe for '{input_file}' to extract chapter information...")
        command = [
            "ffprobe",
            "-i", get_abs_path(input_file),
            "-print_format", "json",
            "-show_chapters",
            "-show_format",
            "-loglevel", "error"
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"ffprobe error: {result.stderr}")
        print("[SUCCESS] Chapter information extracted successfully.")
        return json.loads(result.stdout)
    except Exception as e:
        print(f"[ERROR] Failed to run ffprobe: {e}")
        exit(1)

def generate_ffmpeg_commands(chapters, input_file, svt_av1_params, preset, crf):
    """Generates ffmpeg commands for each chapter."""
    ffmpeg_commands = []
    for chapter in chapters:
        title = chapter['tags']['title'].replace(" ", "_")
       
        tmp_dir = get_abs_path("tmp")
        output_file = os.path.join(tmp_dir, f"{title}.mkv")
       
        base_command = [
            "ffmpeg",
            "-ss", chapter['start_time'],
            "-to", chapter['end_time'],
            "-i", get_abs_path(input_file),
            "-c:v", "libsvtav1",
            "-preset", preset,
            "-crf", crf,
            "-g", "360",
            "-pix_fmt", "yuv420p10le",
            "-svtav1-params", svt_av1_params,
            "-c:a", "libopus",
            "-ac",  "6",
            "-b:a", "256K",
            "-vbr:a", "2",
            "-sn",
            "-reset_timestamps", "1",
            output_file
        ]
       
        ffmpeg_commands.append({
            "title": title,
            "length_in_seconds": float(chapter['end_time']) - float(chapter['start_time']),
            "command": base_command
        })
    print(ffmpeg_commands)
    return ffmpeg_commands

def save_ffjob_info(ffjob_info, input_file, total_length):
    """Saves the ffjob information to a JSON file."""
    try:
        ffjob_info["total_length_in_seconds"] = total_length
        ffjob_info["executed_datetime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open("ffjob.json", 'w') as f:
            json.dump(ffjob_info, f, indent=4)
        print("[SUCCESS] Saved job information to ffjob.json")
    except Exception as e:
        print(f"[ERROR] Failed to save job information: {e}")

def execute_ffmpeg_command(chapter_info):
    """Executes the ffmpeg command in a separate process without waiting for it to complete."""
    try:
        title = chapter_info['title']
        command = chapter_info['command']

        log_file_path = os.path.join(get_abs_path("log"), f"{title}.log")
        with open(log_file_path, 'a') as log_file:
            subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT)
        print(f"[INFO] Encoding for '{title}' is running in the background. Log: {log_file_path}")
    except Exception as e:
        print(f"[ERROR] Failed to start encoding for '{title}': {e}")

def get_ffjob_info():
    """Reads the ffjob information from the JSON file."""
    try:
        with open("ffjob.json", 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to read job information: {e}")
        exit(1)


def parse_log_file(log_filename):
    """Parses the log file to get the last reported encoding speed and time encoded."""
    with open(log_filename, 'r') as f:
        lines = f.readlines()
    if lines:
        last_line = lines[-1]
        fps = last_line.split("fps=")[1].split(" ")[0]
        time_encoded = last_line.split("time=")[1].split(" ")[0]
        return fps, time_encoded
    return "N/A", "00:00:00.00"

def format_time_delta(total_seconds):
    """Formats seconds into HH:MM:SS format."""
    return str(timedelta(seconds=int(total_seconds)))

def check_encoding_status(ffjob_info):
    """Checks and prints the status of the current encoding tasks."""
    total_encoded_time_seconds = 0
    for chapter in ffjob_info["chapters"]:
        title = chapter["title"]
        command = chapter["command"]
        print(f"\nChapter: {title}")
        print(f"Command: {command}")
       
        log_filename = os.path.join(get_abs_path("log"), f"{title}.log")
        fps, time_encoded = parse_log_file(log_filename)
        encoded_time_seconds = sum(x * float(t) for x, t in zip([3600, 60, 1], time_encoded.split(":")))
        total_encoded_time_seconds += encoded_time_seconds
       
        print(f"Encoding Speed: {fps} fps")
        print(f"Time Encoded: {time_encoded} (hh:mm:ss)")
   
    total_video_length = ffjob_info["total_length_in_seconds"]
    print(f"\nTotal Encoded Time: {format_time_delta(total_encoded_time_seconds)} (hh:mm:ss)")
    print(f"Total Video Length: {format_time_delta(total_video_length)} (hh:mm:ss)")
    print(f"Completion: {total_encoded_time_seconds / total_video_length * 100:.2f}%")
   
    executed_datetime = datetime.strptime(ffjob_info["executed_datetime"], "%Y-%m-%d %H:%M:%S")
    elapsed_time = datetime.now() - executed_datetime
    print(f"Total Encoding Time: {elapsed_time}")

def main(input_file, svt_av1_params, preset, crf, info, complete):
    """Main function to process the input file and generate ffmpeg commands."""

    if complete:
        verify_chapter_files()
        concatenate_chapters()
        run_vmaf(input_file, "output.mkv")
        if user_confirmation():
            #cleanup_directories()
            pass
        else:
            print("[INFO] Cleanup aborted by user.")
    elif info:
        ffjob_info = get_ffjob_info()
        check_encoding_status(ffjob_info)
    else:
        print("[INFO] Script started. Preparing to process video file...")
        ensure_directories_exist()
        json_output = run_ffprobe(input_file)
        chapters = json_output['chapters']
        total_length = float(json_output['format']['duration'])
   
        print(f"[INFO] Found {len(chapters)} chapters. Generating ffmpeg commands...")
        ffmpeg_commands = generate_ffmpeg_commands(chapters, input_file, svt_av1_params, preset, crf)
        ffjob_info = {"chapters": ffmpeg_commands}

        for chapter_info in ffmpeg_commands:
            title = chapter_info["title"]
            print(f"Generated command for {title} encoding")
            execute_ffmpeg_command(chapter_info)

        save_ffjob_info(ffjob_info, input_file, total_length)
        print("All ffmpeg commands have been generated and dispatched for encoding.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process video file or check encoding status.")
    parser.add_argument("input_file", nargs='?', default=None, help="The input video file to process.")
    parser.add_argument("--svt_av1_params", default=" tune=0:enable-overlays=1:scm=0:scd=1:lookahead=120:keyint=360:film-grain=3:input-depth=10:irefresh-type=1:lp=4", help="SVT-AV1 specific parameters as a string.")
    parser.add_argument("--preset", default="1", help="Encoding preset.")
    parser.add_argument("--crf", default="16", help="Constant Rate Factor for encoding quality.")
    parser.add_argument("-info", action="store_true", help="Check the status of the current encoding tasks.")
    parser.add_argument("-complete", action="store_true", help="Complete the encoding process by concatenating chapters and cleaning up.")
    args = parser.parse_args()

    main(args.input_file, args.svt_av1_params, args.preset, args.crf, args.info, args.complete)