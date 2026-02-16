#!/bin/bash
# Description:
#narrative points to make 
# 4:3 is standard for older CRTs 640x480.
# no spaces in filenames 
# format sizes (1.44m, 666m, 2.35g)
#reason for format sizes (note 1.44 is ~23 seconds) 

#Programattic restrictions  
#	Need to precreate directories ./photo ./video ./output
#	Need to run script from ./
#  pre-put all photos in ./photo and videos in ./video 
#  NORMALIZE must be: frame rate AND resolution. 

#---

#initially process the videos 

chop video() { #also normalizes
	normalize video to resolution and frame rate 
	save new video in ./video 
	delete original video
	chop new video into new smaller-videos $cuttime long  
	If new video is a portrait video, superimpose a static video background
	after all new smaller-videos are created delete new video
}

glitch video(){
	use ffmpeg to randomly apply a $specified series of glitches 
	save the new video in ./video 
	delete old videos
}

-------

animate photo(){	
	shrink photo to correct dimensions in image magick 
	with ffmepg turn photo in to video of legnth $splicetime
		If portrait photo, superimpose a static video background
	save new video in /video
	delete processed photo from /photo 
	}
	
	animate_photo() {
    local photo=$1
    width=$(identify -format "%w" "$photo")
    height=$(identify -format "%h" "$photo")
    
    if [ "$height" -gt "$width" ]; then
        # It's a portrait photo
        ffmpeg -i static_noise.mp4 -i "$photo" -filter_complex "[1]scale=480x640[photo];[0][photo]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2" -c:v libx264 -t 10 ./video/$(basename "$photo" .jpg).mp4
    else
        # It's not a portrait photo, simple conversion to video
        ffmpeg -loop 1 -i "$photo" -c:v libx264 -t $splicetime -pix_fmt yuv420p ./video/$(basename "$photo" .jpg).mp4
    fi
    # Remove the processed photo
    rm "$photo"
}


-------
	
genrate video(){
	randomly merge all videos into a series of videos of size $format
	delete original videos
}
	




main() {)
main "$@"

