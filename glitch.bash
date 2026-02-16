#!/bin/bash

input_dir="/home/user/Documents/tvproject/toglitch"
output_dir="/home/user/Documents/tvproject/glitched"
mkdir -p "$output_dir"

for file in "$input_dir"/*.mp4; do
    base_name=$(basename "$file" .mp4)
    output_file="$output_dir/${base_name}_video_only.mp4"

    # Determine the number of frames (approximation using ffprobe)
    total_frames=$(ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of default=nokey=1:noprint_wrappers=1 "$file")
    
    # Generate a random shuffle pattern
    pattern=$(shuf -i 0-$((total_frames-1)) -n $total_frames | tr '\n' '|' | sed 's/|$//')

    # Apply the filter with the new complex shuffle pattern
    ffmpeg -y -i "$file" \
           -vf "shuffleframes=$pattern,noise=alls=20:allf=t+u,hue=h=60:s=1.5,eq=contrast=1.5:brightness=0.1,gblur=sigma=2" \
           -c:a copy "$output_file"

    echo "Processed $file to $output_file"
done

echo "All files have been processed."