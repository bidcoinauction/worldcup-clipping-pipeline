from pathlib import Path
import csv
from pipeline.utils import ROOT

CAPTIONS = [
    ["EMOTION", "American audiences are not emotionally prepared for this sport.", "The World Cup turns normal people into patriots for 90 minutes.", "#worldcup #football #soccer"],
    ["AURA", "Some players do not walk into stadiums. They change the temperature.", "This is the part of football Americans are about to understand.", "#football #worldcup #soccer"],
    ["CHAOS", "This sport can go from beautiful to completely unhinged in ten seconds.", "That is why the World Cup is different.", "#worldcup #football #soccer"],
    ["AMERICA", "America is about to find out why the rest of the world loses its mind over this.", "Football is not just a game during the World Cup.", "#worldcup #usa #soccer"],
]

def main():
    out = ROOT / "CAPTIONS/caption_bank.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["angle", "line_1", "line_2", "hashtags"])
        writer.writerows(CAPTIONS)
    print(f"Caption bank written: {out}")

if __name__ == "__main__":
    main()
