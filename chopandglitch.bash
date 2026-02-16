#!/bin/bash

# Description:
# This script provides tools to manage video files, specifically to snip segments,
# apply glitch effects, and merge video files. It's designed to operate based on
# command-line switches that determine the action.

# Usage:
# --snip, --glitch, and --merge. It accepts either a video file or a directory as input.

# Examples:
# ./process_video.sh --snip /path/to/video_file /path/to/output_directory
# ./process_video.sh --glitch /path/to/directory_with_video_chunks
# ./process_video.sh --merge /path/to/directory_with_video_segments /path/to/output_directory

snip_videos() {
    local input_dir=$1
    local output_dir=$2

    # Check if the output directory exists, if not create it
    [ ! -d "$output_dir" ] && mkdir -p "$output_dir"

    # Loop through all mp4 files in the input directory
    for video_file in "$input_dir"/*.mp4; do
        local base_name=$(basename "${video_file%.*}")
        # Command to segment the video
        ffmpeg -i "$video_file" -c:v libx264 -x264-params keyint=60:min-keyint=60 -s 720x480 -f segment -segment_time 2 -reset_timestamps 1 "${output_dir}/${base_name}_chunk%03d.mp4"
        echo "Video has been segmented: $video_file"
    done
}

glitch_videos() {
    local input_dir=$1
    local output_dir=$2  # Assuming second argument is the output directory
    mkdir -p "$output_dir"  # Ensure output directory exists

    for file in "$input_dir"/*.mp4; do
        echo "Processing file: $file"
        local base_name=$(basename "$file" .mp4)
        local final_file="$output_dir/${base_name}_glitched.mp4"
        local pattern=$(shuf -i 0-9 -n 10 | tr '\n' '|' | sed 's/|$//')
        
        # Apply the filter with the new complex shuffle pattern
        ffmpeg -y -i "$file" \
               -vf "shuffleframes=$pattern,noise=alls=20:allf=t+u,hue=h=60:s=1.5,eq=contrast=1.5:brightness=0.1,gblur=sigma=2" \
               -c:a copy "$final_file"

        echo "Glitch effects applied and output to: $final_file"
    done
}


merge_videos() {
    local input_dir=$(realpath "$1")
    local output_dir=$(realpath "$2")
    local max_size=$((700 * 1024 * 1024))  # Max file size
    local current_size=0
    local file_counter=1
    local file_list=""

    mkdir -p "$output_dir"
    local files=($(find "$input_dir" -type f -name '*.mp4'))
    local num_files=${#files[@]}

    while [ $num_files -gt 0 ]; do
        local index=$(( RANDOM % num_files ))
        local current_file="${files[$index]}"

        # Get file size
        local file_size=$(stat -c%s "$current_file")

        if [[ $((current_size + file_size)) -gt $max_size ]]; then
            ffmpeg -f concat -safe 0 -i <(echo -e "$file_list") -c copy "${output_dir}/merged_output_${file_counter}.mp4"
            echo "Output file ${output_dir}/merged_output_${file_counter}.mp4 created."

            ((file_counter++))
            file_list=""
            current_size=0
        fi

        file_list+="file '$current_file'\n"
        current_size=$((current_size + file_size))

        # Remove the current file efficiently from the array
        files[$index]=${files[num_files-1]}
        unset files[num_files-1]
        ((num_files--))
    done

    # Process any remaining files
    if [[ -n "$file_list" ]]; then
        ffmpeg -f concat -safe 0 -i <(echo -e "$file_list") -c copy "${output_dir}/merged_output_${file_counter}.mp4"
        echo "Output file ${output_dir}/merged_output_${file_counter}.mp4 created."
    fi
}





main() {
    local operation=""
    local input_path=""
    local output_dir=""

    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --snip|--glitch|--merge)
                operation="${1#--}"
                shift
                input_path="$1"
                shift
                output_dir="$1"
                shift
                ;;
            *)
                echo "Error: Invalid option or too many arguments: $1"
                echo "Usage: $0 (--snip|--glitch|--merge) <input_path> [output_directory]"
                exit 1
                ;;
        esac
    done

    if [[ -z "$operation" || -z "$input_path" || -z "$output_dir" ]]; then
        echo "Error: Missing required parameters."
        echo "Usage: $0 (--snip|--glitch|--merge) <input_path> [output_directory]"
        exit 1
    fi

    "${operation}_videos" "$input_path" "$output_dir"
}

main "$@"
