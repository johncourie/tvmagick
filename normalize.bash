#!/bin/bash

# Script Name: NormalizeVideoResolution.sh
# Description: This script processes all MP4 video files in a specified directory,
#              normalizing them to a uniform resolution of 720x480 pixels. The script 
#              removes audio tracks and adjusts the frame rate to 30 FPS to ensure
#              consistency across all output videos. The videos are encoded using the 
#              H.264 codec with a CRF (Constant Rate Factor) of 28, balancing quality
#              with file size, using a 'fast' preset for quicker encoding.
#
# Usage: ./NormalizeVideoResolution.sh
#        Ensure that the 'input_dir' and 'output_dir' variables are set to the 
#        appropriate paths before running the script.
#
# Input Directory: /home/user/Documents/tvproject/deploy
# Output Directory: /home/user/Documents/tvproject/deploy/normalized
# Dependencies: This script requires ffmpeg to be installed and accessible in the
#               system's PATH.
#
# Author: [Your Name]
# Created on: [Creation Date]
# Last updated: [Last Updated Date]
#
# Note: This script does not maintain the original aspect ratio of the videos. If 
#       maintaining the original aspect ratio is required, additional ffmpeg options
#       or filters need to be applied.

input_dir="/home/user/Documents/tvproject/tomerge"
output_dir="/home/user/Documents/tvproject/normalized"
mkdir -p "$output_dir"

for file in "$input_dir"/*.mp4; do
    base_name=$(basename "$file" .mp4)
    output_file="$output_dir/${base_name}_video_only.mp4"

    # Force the resolution to 720x480, adjust framerate to 25 FPS, and encode using libx264 without audio.
    ffmpeg -y -i "$file" -r 30 -s 720x480 -c:v libx264 -preset fast -crf 28 -an "$output_file"

    echo "Processed $file to $output_file"
done

echo "All files have been processed."
