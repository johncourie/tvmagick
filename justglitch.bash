#!/bin/bash

# Function to apply combined glitch effects to each video file
apply_combined_glitch_effects() {
    local input_dir=$1  # Directory containing the video chunks

    # Loop through each video file in the directory
    for file in "${input_dir}"/*.mp4; do
        echo "Processing file: $file"

        # Define intermediate and final filenames
        local shuffled_file="${file%.*}_shuffled.mp4"
        local noised_file="${file%.*}_noised.mp4"
        local final_file="${file%.*}_final.mp4"

        # Apply shuffle frames effect
        local pattern=$(shuf -i 0-9 -n 10 | tr '\n' '|' | sed 's/|$//')
        ffmpeg -y -i "$file" -vf "shuffleframes=$pattern" -c:a copy "$shuffled_file"
        echo "Shuffle frames applied to $file"

        # Apply intense noise effect
        ffmpeg -y -i "$shuffled_file" -vf "noise=c0s=50:c1s=30:c2s=30:c0f=t+p:c1f=t+p:c2f=t+p" -c:a copy "$noised_file"
        echo "Intense noise applied to $shuffled_file"

        # Apply negate effect
        ffmpeg -y -i "$noised_file" -vf "negate" -c:a copy "$final_file"
        echo "Negate effect applied to $noised_file"

        # Optionally delete intermediate files
        rm -f "$shuffled_file" "$noised_file"
        echo "Intermediate files removed, final output in: $final_file"
    done
}

# Main script execution
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <directory_with_video_chunks>"
    exit 1
fi

# Directory containing video chunks
input_dir="$1"

# Ensure the directory exists
if [ ! -d "$input_dir" ]; then
    echo "Error: Directory does not exist: $input_dir"
    exit 1
fi

# Apply glitch effects to each file in the directory
apply_combined_glitch_effects "$input_dir"
