import io
import cv2
import numpy as np

def convert_mjpeg_1080p_to_480p(input_mjpeg_path, output_mjpeg_path, jpeg_quality):
    target_resolution = (854, 480) # Standard 16:9 480p

    with open(input_mjpeg_path, "rb") as f_in, open(output_mjpeg_path, "wb") as f_out:
        frame_count = 0
        
        while True:
            # 1. Read the 5-byte header from the 1080p file
            header_bytes = f_in.read(5)
            if not header_bytes or len(header_bytes) < 5:
                break  # End of file reached
                
            # 2. Get the length of the 1080p frame
            frame_length = int(header_bytes.decode('ascii').strip())
            
            # 3. Read the 1080p JPEG payload
            jpeg_data_1080p = f_in.read(frame_length)
            if len(jpeg_data_1080p) < frame_length:
                break  # File is cut short / truncated
                
            # 4. Decode the JPEG bytes into a raw image matrix for OpenCV
            # np.frombuffer is incredibly fast because it doesn't copy memory
            np_arr = np.frombuffer(jpeg_data_1080p, dtype=np.uint8)
            frame_1080p = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            
            if frame_1080p is None:
                print(f"Skipping corrupted frame {frame_count}")
                continue

            # 5. Resize the frame down to 480p
            frame_480p = cv2.resize(frame_1080p, target_resolution, interpolation=cv2.INTER_AREA)
            
            # 6. Re-encode the 480p frame back into JPEG bytes
            success, jpeg_buffer = cv2.imencode('.jpg', frame_480p, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            if not success:
                print(f"Failed to encode frame {frame_count}")
                continue
                
            jpeg_data_480p = jpeg_buffer.tobytes()
            new_frame_length = len(jpeg_data_480p)
            
            # 7. Create the new 5-byte header for the 480p frame
            new_header_bytes = f"{new_frame_length:05d}".encode('ascii')
            
            # 8. Write the new header and the smaller 480p JPEG payload
            f_out.write(new_header_bytes)
            f_out.write(jpeg_data_480p)
            
            frame_count += 1
            if frame_count % 30 == 0:
                print(f"Resized {frame_count} MJPEG frames...")

    print(f"Successfully converted {frame_count} frames to 480p MJPEG!")

# === Example Run ===
convert_mjpeg_1080p_to_480p("no_sound.Mjpeg", "no_sound_480p.Mjpeg", 25)
