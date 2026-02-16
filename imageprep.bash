#!/bin/bash

# Description:
# This script processes a batch of images in a specified input directory. It resizes and applies a radial gradient background
# to each image to ensure each has dimensions of 720x480 pixels. After processing, the script can optionally convert
# each image into a 23-second video with a resolution of 720x480 pixels using the h264 codec. The images can be
# optionally deleted after conversion based on user input.
#
# Usage:
# ./script_name.sh <input-directory> <output-directory>
#
# Parameters:
# <input-directory>: Directory containing the images to process.
# <output-directory>: Directory where processed images and videos will be saved.
#
# The script checks if the output directory exists and prompts the user if it should continue if the directory is present,
# potentially overwriting existing files. After processing the images, it prompts the user to decide whether to convert
# the images into videos and whether to delete the original images after conversion.
#
# Dependencies:
# Ensure ImageMagick and ffmpeg are installed and available in your system's PATH to run this script.
#
# Example:
# To process images in /path/to/input and save the results in /path/to/output, use:
# ./script_name.sh /path/to/input /path/to/output

# Check for correct number of arguments
if [ $# -ne 2 ]; then
  echo "Usage: $0 <input-directory> <output-directory>"
  exit 1
fi

# Input and output directories from arguments
INPUT_DIR=$1
OUTPUT_DIR=$2

# Check if output directory exists
if [ -d "$OUTPUT_DIR" ]; then
  read -p "Output directory exists. Continue and overwrite existing files? (y/n): " yn
  case $yn in
    [Yy]* ) ;;
    * ) echo "Exiting."; exit;;
  esac
else
  # Create output directory if it doesn't exist
  mkdir -p "$OUTPUT_DIR"
fi

# Process each image in the input directory
for img in "$INPUT_DIR"/*; do
  # File base name and extension handling
  filename=$(basename -- "$img")
  extension="${filename##*.}"
  filename="${filename%.*}"

  # Sanitize the filename by removing special characters and whitespaces
  safe_filename=$(echo "$filename" | sed 's/[^a-zA-Z0-9]//g')

  # Create a background image and overlay the resized image on it directly
  convert -size 720x480 radial-gradient:white-black \
          \( "$img" -resize 720x480 \) \
          -gravity center -composite \
          "$OUTPUT_DIR/${safe_filename}_processed.$extension"
done

# Ask user if they want to convert images to video
read -p "Convert images to videos? (y/n): " convert_to_video
if [ "$convert_to_video" = "y" ]; then
  for img in "$OUTPUT_DIR"/*; do
    # Convert each image to a 23-second video
    ffmpeg -framerate 1/23 -i "$img" -c:v libx264 -t 23 -pix_fmt yuv420p -vf "scale=720:480" "${img%.*}.mp4"
    # Delete the image after converting
    rm "$img"
  done
fi

exit 0