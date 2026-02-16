#!/bin/bash

shopt -s globstar
declare -A dir_counters  # associative array to keep track of counters for each directory

# Create a temporary directory for safely renaming files to avoid conflicts
temp_dir=$(mktemp -d)
echo "Using temporary directory $temp_dir for safe renaming..."

for file in ./**/*; do
    if [[ -f "$file" ]]; then
        dirname="${file%/*}/"
        basename=$(basename -- "$file")
        extension="${basename##*.}"

        # Initialize or increment the counter for this directory
        if [[ -z "${dir_counters[$dirname]}" ]]; then
            dir_counters[$dirname]=1
        else
            ((dir_counters[$dirname]++))
        fi

        # Check if the file has an extension and rename accordingly
        if [[ "$basename" == *.* ]]; then
            new_name="${dir_counters[$dirname]}.$extension"
        else
            new_name="${dir_counters[$dirname]}"
        fi

        # Move files to a temporary directory first to avoid overwriting
        mv -- "$file" "$temp_dir/$new_name"
        echo "Moved $file to temporary as $new_name"
    fi
done

# Move files from temporary directory back to original directory structure with new names
for file in "$temp_dir"/*; do
    new_basename=$(basename -- "$file")
    original_dir="${file/$temp_dir/$PWD}"
    original_dir="${original_dir%/*}"
    mv -- "$file" "$original_dir/$new_basename"
    echo "Moved back to original directory as $original_dir/$new_basename"
done

# Optionally, remove the temporary directory if empty
rmdir "$temp_dir" && echo "Temporary directory removed."
