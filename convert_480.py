import cv2

def convert_1080p_to_480p_custom_mjpeg(input_mp4_path, output_mjpeg_path, jpeg_quality):
    # 1. Open the 1080p source MP4 file
    cap = cv2.VideoCapture(input_mp4_path)
    if not cap.isOpened():
        print(f"Error: Cannot open input video {input_mp4_path}")
        return

    # Define target 480p resolution (Width, Height)
    # 854x480 is standard for 16:9 widescreen video
    target_resolution = (854, 480)

    # Open the output file in binary write mode
    with open(output_mjpeg_path, "wb") as f_out:
        frame_count = 0
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break  # Video ended

            # 2. Resize the frame from 1080p to 480p
            # INTER_AREA interpolation provides the cleanest results when downscaling
            frame_480p = cv2.resize(frame, target_resolution, interpolation=cv2.INTER_AREA)

            # 3. Encode the resized 480p frame into JPEG bytes
            success, jpeg_buffer = cv2.imencode('.jpg', frame_480p, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            if not success:
                print(f"Failed to encode frame {frame_count}")
                continue

            jpeg_bytes = jpeg_buffer.tobytes()
            frame_length = len(jpeg_bytes)

            # 4. Create the 5-byte ASCII header
            header_str = f"{frame_length:05d}"
            header_bytes = header_str.encode('ascii')

            # Safety check
            if frame_length > 99999:
                print(f"Warning: Frame {frame_count} is {frame_length} bytes (exceeds 5-byte header limit)!")

            # 5. Write header and JPEG payload
            f_out.write(header_bytes)
            f_out.write(jpeg_bytes)

            frame_count += 1
            if frame_count % 30 == 0:
                print(f"Processed {frame_count} frames at 480p...")

    cap.release()
    print(f"Conversion complete! Saved 480p version to {output_mjpeg_path}")

# === Example Run ===
# convert_1080p_to_480p_custom_mjpeg("no_sound.mp4", "no_sound_480p.Mjpeg", 25)
