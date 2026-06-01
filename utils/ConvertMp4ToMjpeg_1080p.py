import cv2

def convert_mp4_to_custom_mjpeg(input_mp4_path, output_mjpeg_path, jpeg_quality):
    # 1. Open the source MP4 file using OpenCV
    cap = cv2.VideoCapture(input_mp4_path)
    if not cap.isOpened():
        print(f"Error: Cannot open input video {input_mp4_path}")
        return

    # Open the output file in binary write mode
    with open(output_mjpeg_path, "wb") as f_out:
        frame_count = 0
        
        while True:
            # Read a raw frame from the MP4
            ret, frame = cap.read()
            if not ret:
                break  # Video ended

            # 2. Encode the raw frame into JPEG memory bytes
            # [1] is the raw byte buffer, [0] is a boolean success flag
            success, jpeg_buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            if not success:
                print(f"Failed to encode frame {frame_count}")
                continue

            # Convert the buffer to raw bytes
            jpeg_bytes = jpeg_buffer.tobytes()
            frame_length = len(jpeg_bytes)

            # 3. Create the 5-byte ASCII header
            # '{:05d}' ensures it is padded with leading zeros (e.g., 150 -> '00150')
            header_str = f"{frame_length:05d}"
            header_bytes = header_str.encode('ascii')

            # Ensure safety check (if a frame is 100,000+ bytes, it breaks the 5-byte rule)
            if frame_length > 99999:
                print(f"Warning: Frame {frame_count} is too large ({frame_length} bytes) for a 5-byte header!")

            # 4. Write header followed immediately by the JPEG payload
            f_out.write(header_bytes)
            f_out.write(jpeg_bytes)

            frame_count += 1
            if frame_count % 100 == 0:
                print(f"Processed {frame_count} frames...")

    cap.release()
    print(f"Conversion complete! Saved to {output_mjpeg_path}")

# === Example Run ===
# convert_mp4_to_custom_mjpeg("no_sound.mp4", "no_sound.Mjpeg", 25)