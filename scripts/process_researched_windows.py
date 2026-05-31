import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.stadium_signal import execute_ffmpeg_commands, ffmpeg_commands, write_ffmpeg_commands


def main() -> None:
    parser = argparse.ArgumentParser(description="Export FFmpeg-ready commands from researched clip windows.")
    parser.add_argument("--execute", action="store_true", help="Actually run the generated FFmpeg commands.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing exported clips when executing FFmpeg.")
    args = parser.parse_args()

    out_path = write_ffmpeg_commands(overwrite=args.overwrite)
    commands = ffmpeg_commands(overwrite=args.overwrite)
    print(f"FFmpeg commands written: {out_path}")
    if args.execute:
        execute_ffmpeg_commands(commands)


if __name__ == "__main__":
    main()
