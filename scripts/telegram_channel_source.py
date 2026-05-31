import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Telegram source discovery entry point. Requires Telethon credentials in local env for live use."
    )
    parser.add_argument("--channel", required=True)
    parser.add_argument("--search", default="")
    parser.add_argument("--message-id", default="")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--update-existing", action="store_true")
    args = parser.parse_args()

    try:
        import telethon  # noqa: F401
    except ImportError:
        print("Telethon is not installed. Install it only when you are ready for live Telegram sourcing.")
        print(f"Planned channel={args.channel} search={args.search!r} message_id={args.message_id!r} limit={args.limit}")
        return

    print("Telethon is installed; wire this script to the authenticated session before live channel pulls.")
    print(f"Requested channel={args.channel} search={args.search!r} message_id={args.message_id!r} download={args.download}")


if __name__ == "__main__":
    main()
