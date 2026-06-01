import argparse
import cv2
import numpy as np


HEADER_LEN = 5
TARGET_480P = (854, 480)


def _write_frame_with_header(out_file, jpeg_bytes):
    frame_length = len(jpeg_bytes)
    if frame_length > 99999:
        print(f"Warning: frame is too large ({frame_length} bytes) for 5-byte header")
    out_file.write(f"{frame_length:05d}".encode("ascii"))
    out_file.write(jpeg_bytes)


def convert_mp4_to_custom_mjpeg(input_mp4_path, output_mjpeg_path, jpeg_quality):
    """Convert MP4 to custom MJPEG stream (5-byte ASCII length + JPEG bytes)."""
    cap = cv2.VideoCapture(input_mp4_path)
    if not cap.isOpened():
        print(f"Error: Cannot open input video {input_mp4_path}")
        return

    with open(output_mjpeg_path, "wb") as f_out:
        frame_count = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            success, jpeg_buffer = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
            )
            if not success:
                print(f"Failed to encode frame {frame_count}")
                continue

            _write_frame_with_header(f_out, jpeg_buffer.tobytes())
            frame_count += 1
            if frame_count % 100 == 0:
                print(f"Processed {frame_count} frames...")

    cap.release()
    print(f"Conversion complete! Saved to {output_mjpeg_path}")


def convert_1080p_mp4_to_480p_custom_mjpeg(input_mp4_path, output_mjpeg_path, jpeg_quality):
    """Convert MP4 to 480p custom MJPEG stream."""
    cap = cv2.VideoCapture(input_mp4_path)
    if not cap.isOpened():
        print(f"Error: Cannot open input video {input_mp4_path}")
        return

    with open(output_mjpeg_path, "wb") as f_out:
        frame_count = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_480p = cv2.resize(frame, TARGET_480P, interpolation=cv2.INTER_AREA)
            success, jpeg_buffer = cv2.imencode(
                ".jpg", frame_480p, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
            )
            if not success:
                print(f"Failed to encode frame {frame_count}")
                continue

            _write_frame_with_header(f_out, jpeg_buffer.tobytes())
            frame_count += 1
            if frame_count % 30 == 0:
                print(f"Processed {frame_count} frames at 480p...")

    cap.release()
    print(f"Conversion complete! Saved 480p version to {output_mjpeg_path}")


def convert_mjpeg_1080p_to_480p(input_mjpeg_path, output_mjpeg_path, jpeg_quality):
    """Convert custom MJPEG (1080p) to custom MJPEG (480p)."""
    frame_count = 0
    with open(input_mjpeg_path, "rb") as f_in, open(output_mjpeg_path, "wb") as f_out:
        while True:
            header_bytes = f_in.read(HEADER_LEN)
            if not header_bytes or len(header_bytes) < HEADER_LEN:
                break

            frame_length = int(header_bytes.decode("ascii").strip())
            jpeg_data_1080p = f_in.read(frame_length)
            if len(jpeg_data_1080p) < frame_length:
                break

            np_arr = np.frombuffer(jpeg_data_1080p, dtype=np.uint8)
            frame_1080p = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if frame_1080p is None:
                print(f"Skipping corrupted frame {frame_count}")
                continue

            frame_480p = cv2.resize(frame_1080p, TARGET_480P, interpolation=cv2.INTER_AREA)
            success, jpeg_buffer = cv2.imencode(
                ".jpg", frame_480p, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
            )
            if not success:
                print(f"Failed to encode frame {frame_count}")
                continue

            _write_frame_with_header(f_out, jpeg_buffer.tobytes())
            frame_count += 1
            if frame_count % 30 == 0:
                print(f"Resized {frame_count} MJPEG frames...")

    print(f"Successfully converted {frame_count} frames to 480p MJPEG!")


def main():
    parser = argparse.ArgumentParser(description="Unified video conversion utilities")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["mp4_to_mjpeg", "mp4_to_mjpeg_480p", "mjpeg_1080_to_480"],
        help="Conversion mode",
    )
    parser.add_argument("--input", required=True, help="Input file path")
    parser.add_argument("--output", required=True, help="Output file path")
    parser.add_argument(
        "--quality", type=int, default=25, help="JPEG quality (0-100), default: 25"
    )
    args = parser.parse_args()

    if args.mode == "mp4_to_mjpeg":
        convert_mp4_to_custom_mjpeg(args.input, args.output, args.quality)
    elif args.mode == "mp4_to_mjpeg_480p":
        convert_1080p_mp4_to_480p_custom_mjpeg(args.input, args.output, args.quality)
    else:
        convert_mjpeg_1080p_to_480p(args.input, args.output, args.quality)


if __name__ == "__main__":
    main()
