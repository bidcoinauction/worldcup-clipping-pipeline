import argparse

from pipeline.clip_manifest import FIELDNAMES, write_clip_manifest
from pipeline.utils import ROOT


def main():
    parser = argparse.ArgumentParser(description="Convert GPT JSON analysis into clip manifest CSV.")
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--league", required=True)
    parser.add_argument("--match-name", required=True)
    parser.add_argument("--source-video", default="")
    parser.add_argument("--profile", default="football",
                        help="Registered sport/project profile (default: football)")
    args = parser.parse_args()

    result = write_clip_manifest(
        args.analysis,
        league=args.league,
        match_name=args.match_name,
        source_video=args.source_video,
        profile=args.profile,
        output_root=ROOT,
    )
    print(f"Manifest written: {result['output_path']}")


if __name__ == "__main__":
    main()
