from __future__ import annotations

import csv
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

from .utils import ROOT, timestamp_to_seconds


MATCH_FIELDS = [
    "match_id",
    "title",
    "date",
    "competition",
    "stage",
    "venue",
    "teams",
    "primary_emotion",
    "secondary_emotions",
    "mythology_score",
    "status",
    "source_url",
    "local_path",
    "notes",
]

MOMENT_FIELDS = [
    "moment_id",
    "match_id",
    "match_minute",
    "video_timestamp",
    "title",
    "event_type",
    "emotion",
    "narrative_function",
    "importance_score",
    "clip_start",
    "clip_end",
    "notes",
]

TIMELINE_FIELDS = [
    "timeline_id",
    "match_id",
    "sequence_order",
    "match_minute",
    "video_timestamp",
    "emotion",
    "label",
    "description",
]

CLIP_FIELDS = [
    "clip_id",
    "match_id",
    "moment_id",
    "clip_type",
    "start_time",
    "end_time",
    "duration_seconds",
    "series",
    "hook",
    "caption",
    "status",
]

MYTHOLOGY_FIELDS = [
    "match_id",
    "stakes",
    "crowd_emotion",
    "historical_impact",
    "narrative_arc",
    "cultural_memory",
    "total_score",
    "tier",
]

DATASETS = {
    "matches": ("data/matches.csv", MATCH_FIELDS),
    "moments": ("data/moments.csv", MOMENT_FIELDS),
    "emotional_timelines": ("data/emotional_timelines.csv", TIMELINE_FIELDS),
    "clip_windows": ("data/clip_windows.csv", CLIP_FIELDS),
    "mythology_scores": ("data/mythology_scores.csv", MYTHOLOGY_FIELDS),
}

ARCHIVE_DIRS = [
    "config",
    "data",
    "docs",
    "outputs/manifests",
    "outputs/captions",
    "outputs/scripts",
    "outputs/exports",
    "FootballArchive/RAW",
    "FootballArchive/CLIPS",
    "MATCHES/PREMIER_LEAGUE",
    "MATCHES/UCL",
    "MATCHES/MLS",
    "MATCHES/LIGA_MX",
    "MATCHES/WORLD_CUP",
]

SEED_MATCHES = [
    {
        "match_id": "brazil_germany_2014",
        "title": "Brazil 1-7 Germany",
        "date": "2014-07-08",
        "competition": "FIFA World Cup",
        "stage": "Semi-final",
        "venue": "Estadio Mineirao",
        "teams": "Brazil; Germany",
        "primary_emotion": "National Trauma",
        "secondary_emotions": "Collapse; Shock; Disbelief",
        "mythology_score": "99",
        "status": "seeded",
        "source_url": "",
        "local_path": "RAW/brazil_germany_2014.mp4",
        "notes": "The definitive modern collapse arc.",
    },
    {
        "match_id": "liverpool_milan_2005",
        "title": "Liverpool 3-3 Milan",
        "date": "2005-05-25",
        "competition": "UEFA Champions League",
        "stage": "Final",
        "venue": "Ataturk Olympic Stadium",
        "teams": "Liverpool; Milan",
        "primary_emotion": "Miracle",
        "secondary_emotions": "Belief; Resurrection; Shock",
        "mythology_score": "98",
        "status": "seeded",
        "source_url": "",
        "local_path": "RAW/liverpool_milan_2005.mp4",
        "notes": "The Istanbul miracle as football resurrection myth.",
    },
    {
        "match_id": "argentina_france_2022",
        "title": "Argentina 3-3 France",
        "date": "2022-12-18",
        "competition": "FIFA World Cup",
        "stage": "Final",
        "venue": "Lusail Stadium",
        "teams": "Argentina; France",
        "primary_emotion": "Destiny",
        "secondary_emotions": "Legacy; Panic; Catharsis",
        "mythology_score": "100",
        "status": "seeded",
        "source_url": "",
        "local_path": "RAW/argentina_france_2022.mp4",
        "notes": "Legacy, chaos, and coronation in one match.",
    },
    {
        "match_id": "barcelona_psg_2017",
        "title": "Barcelona 6-1 PSG",
        "date": "2017-03-08",
        "competition": "UEFA Champions League",
        "stage": "Round of 16",
        "venue": "Camp Nou",
        "teams": "Barcelona; PSG",
        "primary_emotion": "Impossibility",
        "secondary_emotions": "Chaos; Belief; Humiliation",
        "mythology_score": "97",
        "status": "seeded",
        "source_url": "",
        "local_path": "RAW/barcelona_psg_2017.mp4",
        "notes": "A comeback that feels like the stadium bending reality.",
    },
    {
        "match_id": "man_united_bayern_1999",
        "title": "Manchester United 2-1 Bayern Munich",
        "date": "1999-05-26",
        "competition": "UEFA Champions League",
        "stage": "Final",
        "venue": "Camp Nou",
        "teams": "Manchester United; Bayern Munich",
        "primary_emotion": "Late Fate",
        "secondary_emotions": "Disbelief; Treble; Collapse",
        "mythology_score": "96",
        "status": "seeded",
        "source_url": "",
        "local_path": "RAW/man_united_bayern_1999.mp4",
        "notes": "Football time turning against Bayern in stoppage time.",
    },
    {
        "match_id": "france_italy_2006",
        "title": "France 1-1 Italy",
        "date": "2006-07-09",
        "competition": "FIFA World Cup",
        "stage": "Final",
        "venue": "Olympiastadion Berlin",
        "teams": "France; Italy",
        "primary_emotion": "Tragedy",
        "secondary_emotions": "Legacy; Madness; Farewell",
        "mythology_score": "94",
        "status": "seeded",
        "source_url": "",
        "local_path": "RAW/france_italy_2006.mp4",
        "notes": "Zidane's final act turns a final into mythic tragedy.",
    },
    {
        "match_id": "portugal_netherlands_2006",
        "title": "Portugal 1-0 Netherlands",
        "date": "2006-06-25",
        "competition": "FIFA World Cup",
        "stage": "Round of 16",
        "venue": "Frankenstadion",
        "teams": "Portugal; Netherlands",
        "primary_emotion": "Madness",
        "secondary_emotions": "Violence; Chaos; Grudge",
        "mythology_score": "91",
        "status": "seeded",
        "source_url": "",
        "local_path": "RAW/portugal_netherlands_2006.mp4",
        "notes": "The Battle of Nuremberg as pure tournament disorder.",
    },
    {
        "match_id": "chelsea_bayern_2012",
        "title": "Chelsea 1-1 Bayern Munich",
        "date": "2012-05-19",
        "competition": "UEFA Champions League",
        "stage": "Final",
        "venue": "Allianz Arena",
        "teams": "Chelsea; Bayern Munich",
        "primary_emotion": "Defiance",
        "secondary_emotions": "Survival; Fate; Redemption",
        "mythology_score": "93",
        "status": "seeded",
        "source_url": "",
        "local_path": "RAW/chelsea_bayern_2012.mp4",
        "notes": "A survival story inside Bayern's own stadium.",
    },
    {
        "match_id": "italy_germany_2006",
        "title": "Italy 2-0 Germany",
        "date": "2006-07-04",
        "competition": "FIFA World Cup",
        "stage": "Semi-final",
        "venue": "Westfalenstadion",
        "teams": "Italy; Germany",
        "primary_emotion": "Late Poetry",
        "secondary_emotions": "Tension; Elegance; Heartbreak",
        "mythology_score": "92",
        "status": "seeded",
        "source_url": "",
        "local_path": "RAW/italy_germany_2006.mp4",
        "notes": "Extra-time pressure released by a perfect Italian ending.",
    },
    {
        "match_id": "netherlands_argentina_2022",
        "title": "Netherlands 2-2 Argentina",
        "date": "2022-12-09",
        "competition": "FIFA World Cup",
        "stage": "Quarter-final",
        "venue": "Lusail Stadium",
        "teams": "Netherlands; Argentina",
        "primary_emotion": "Rage",
        "secondary_emotions": "Revenge; Chaos; Nerve",
        "mythology_score": "90",
        "status": "seeded",
        "source_url": "",
        "local_path": "RAW/netherlands_argentina_2022.mp4",
        "notes": "Bad blood, tactical shock, and penalty pressure.",
    },
]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    dataset_status: dict[str, bool]
    errors: list[str]
    warnings: list[str]


def read_csv(path: str | Path) -> list[dict[str, str]]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: str | Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def ensure_csv(path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(",".join(fields) + "\n", encoding="utf-8")


def init_archive(root: Path = ROOT, *, seed: bool = True) -> None:
    for directory in ARCHIVE_DIRS:
        (root / directory).mkdir(parents=True, exist_ok=True)
    for _, (rel_path, fields) in DATASETS.items():
        ensure_csv(root / rel_path, fields)
    if seed:
        seed_data(root)


def seed_data(root: Path = ROOT) -> None:
    matches = read_csv(root / "data/matches.csv")
    existing_ids = {row.get("match_id") for row in matches}
    merged_matches = matches + [row for row in SEED_MATCHES if row["match_id"] not in existing_ids]
    write_csv(root / "data/matches.csv", merged_matches, MATCH_FIELDS)

    score_rows = read_csv(root / "data/mythology_scores.csv")
    existing_score_ids = {row.get("match_id") for row in score_rows}
    seeded_scores = []
    for match in SEED_MATCHES:
        if match["match_id"] in existing_score_ids:
            continue
        total_score = int(match["mythology_score"])
        seeded_scores.append(
            {
                "match_id": match["match_id"],
                "stakes": _component_score(total_score, 0),
                "crowd_emotion": _component_score(total_score, 1),
                "historical_impact": _component_score(total_score, 2),
                "narrative_arc": _component_score(total_score, 3),
                "cultural_memory": _component_score(total_score, 4),
                "total_score": str(total_score),
                "tier": tier_for_score(total_score),
            }
        )
    write_csv(root / "data/mythology_scores.csv", score_rows + seeded_scores, MYTHOLOGY_FIELDS)


def _component_score(total_score: int, offset: int) -> str:
    return str(max(0, min(20, round(total_score / 5) - (offset % 2))))


def validate_data(root: Path = ROOT) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    dataset_status: dict[str, bool] = {}
    rows_by_name: dict[str, list[dict[str, str]]] = {}

    for name, (rel_path, required_fields) in DATASETS.items():
        dataset_errors: list[str] = []
        path = root / rel_path
        if not path.exists():
            dataset_errors.append(f"Missing dataset: {rel_path}")
        else:
            with path.open("r", newline="", encoding="utf-8") as f:
                header = next(csv.reader(f), [])
            missing = [field for field in required_fields if field not in header]
            if missing:
                dataset_errors.append(f"{Path(rel_path).name} missing columns: {', '.join(missing)}")
            rows_by_name[name] = read_csv(path)
        errors.extend(dataset_errors)
        dataset_status[name] = not dataset_errors

    match_ids = {row.get("match_id", "") for row in rows_by_name.get("matches", []) if row.get("match_id")}

    for row in rows_by_name.get("moments", []):
        match_id = row.get("match_id", "")
        if match_id and match_id not in match_ids:
            errors.append(f"moments.csv references unknown match_id: {match_id}")
            dataset_status["moments"] = False
        _validate_zero_to_100(row, "importance_score", "moments.csv", errors, dataset_status, "moments")
        _validate_timestamp(row.get("video_timestamp", ""), "moments.csv", errors, dataset_status, "moments")

    for row in rows_by_name.get("emotional_timelines", []):
        match_id = row.get("match_id", "")
        if match_id and match_id not in match_ids:
            errors.append(f"emotional_timelines.csv references unknown match_id: {match_id}")
            dataset_status["emotional_timelines"] = False
        _validate_timestamp(row.get("video_timestamp", ""), "emotional_timelines.csv", errors, dataset_status, "emotional_timelines")

    for row in rows_by_name.get("clip_windows", []):
        match_id = row.get("match_id", "")
        if match_id and match_id not in match_ids:
            errors.append(f"clip_windows.csv references unknown match_id: {match_id}")
            dataset_status["clip_windows"] = False
        _validate_window(row, "clip_windows.csv", errors, dataset_status, "clip_windows")

    for row in rows_by_name.get("mythology_scores", []):
        match_id = row.get("match_id", "")
        if match_id and match_id not in match_ids:
            errors.append(f"mythology_scores.csv references unknown match_id: {match_id}")
            dataset_status["mythology_scores"] = False
        _validate_zero_to_100(row, "total_score", "mythology_scores.csv", errors, dataset_status, "mythology_scores")
        score = _to_float(row.get("total_score"))
        if score is not None and row.get("tier") and row["tier"] != tier_for_score(score):
            warnings.append(f"mythology_scores.csv tier mismatch for {match_id}: expected {tier_for_score(score)}")

    return ValidationResult(ok=not errors, dataset_status=dataset_status, errors=errors, warnings=warnings)


def _to_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _validate_zero_to_100(
    row: dict[str, str],
    field: str,
    source: str,
    errors: list[str],
    dataset_status: dict[str, bool],
    dataset_name: str,
) -> None:
    value = row.get(field, "")
    if value == "":
        return
    score = _to_float(value)
    if score is None:
        errors.append(f"{source} has non-numeric {field}: {value}")
        dataset_status[dataset_name] = False
        return
    if score < 0 or score > 100:
        errors.append(f"{source} has out-of-range {field}: {value}")
        dataset_status[dataset_name] = False


def _validate_timestamp(
    value: str,
    source: str,
    errors: list[str],
    dataset_status: dict[str, bool],
    dataset_name: str,
) -> None:
    if not value:
        return
    try:
        timestamp_to_seconds(value)
    except (TypeError, ValueError):
        errors.append(f"{source} has invalid timestamp: {value}")
        dataset_status[dataset_name] = False


def _validate_window(
    row: dict[str, str],
    source: str,
    errors: list[str],
    dataset_status: dict[str, bool],
    dataset_name: str,
) -> None:
    start = row.get("start_time", "")
    end = row.get("end_time", "")
    if not start and not end:
        return
    try:
        start_seconds = timestamp_to_seconds(start)
        end_seconds = timestamp_to_seconds(end)
    except (TypeError, ValueError):
        errors.append(f"{source} has invalid time window: {start}-{end}")
        dataset_status[dataset_name] = False
        return
    if end_seconds <= start_seconds:
        errors.append(f"{source} has non-positive time window: {start}-{end}")
        dataset_status[dataset_name] = False


def tier_for_score(score: float | int) -> str:
    score = float(score)
    if score >= 95:
        return "S"
    if score >= 85:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    return "Archive"


def classification_for_tier(tier: str) -> str:
    if tier == "S":
        return "Football Mythology"
    if tier == "A":
        return "Elite Archive"
    if tier == "B":
        return "Strong Story Source"
    if tier == "C":
        return "Contextual Archive"
    return "Archive"


def mythology_for_match(match_id: str, root: Path = ROOT) -> dict[str, Any]:
    rows = read_csv(root / "data/mythology_scores.csv")
    row = next((item for item in rows if item.get("match_id") == match_id), None)
    if row is None:
        raise KeyError(f"No mythology score found for match_id: {match_id}")
    total_score = int(float(row.get("total_score") or 0))
    tier = tier_for_score(total_score)
    return {
        "match_id": match_id,
        "total_score": total_score,
        "tier": tier,
        "classification": classification_for_tier(tier),
        "recommended_series": recommended_series(match_id, total_score, root=root),
    }


def recommended_series(match_id: str, total_score: int, root: Path = ROOT) -> list[str]:
    match = match_for_id(match_id, root=root)
    text = " ".join(
        [
            match.get("title", ""),
            match.get("primary_emotion", ""),
            match.get("secondary_emotions", ""),
            match.get("notes", ""),
        ]
    ).lower()
    if "collapse" in text or "trauma" in text or match_id == "brazil_germany_2014":
        return ["The Collapse", "National Trauma", "Football Cinema"]
    if "miracle" in text or "impossibility" in text or "comeback" in text:
        return ["Miracle Watch", "Impossible Nights", "Football Cinema"]
    if "madness" in text or "rage" in text or "chaos" in text:
        return ["The Madness", "Bad Blood", "Tournament Chaos"]
    if "legacy" in text or "destiny" in text or total_score >= 95:
        return ["Legacy Games", "Aura", "Football Cinema"]
    return ["Archive Stories", "Pressure Matches", "Football Cinema"]


def match_for_id(match_id: str, root: Path = ROOT) -> dict[str, str]:
    matches = read_csv(root / "data/matches.csv")
    return next((row for row in matches if row.get("match_id") == match_id), {"match_id": match_id})


def generate_story_arc(match_id: str, root: Path = ROOT) -> dict[str, Any]:
    match = match_for_id(match_id, root=root)
    clips = [row for row in read_csv(root / "data/clip_windows.csv") if row.get("match_id") == match_id]
    score = _to_float(match.get("mythology_score")) or _score_from_scores(match_id, root=root)
    arc_type = arc_type_for_match(match, score)
    title = match.get("title") or match_id
    emotion = match.get("primary_emotion") or "pressure"

    return {
        "match_id": match_id,
        "arc_type": arc_type,
        "hook": f"{title}: the match where {emotion.lower()} became the story.",
        "rising_action": rising_action_for_arc(arc_type, title),
        "turning_point": turning_point_for_arc(arc_type, title),
        "aftermath": aftermath_for_arc(arc_type, title),
        "payoff": payoff_for_arc(arc_type, title),
        "candidate_clip_ids": [row.get("clip_id", "") for row in clips if row.get("clip_id")],
    }


def write_story_arc(match_id: str, root: Path = ROOT) -> Path:
    arc = generate_story_arc(match_id, root=root)
    out_path = root / "outputs/scripts" / f"{match_id}_story_arc.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(arc, indent=2), encoding="utf-8")
    return out_path


def arc_type_for_match(match: dict[str, str], score: float) -> str:
    text = " ".join(
        [
            match.get("match_id", ""),
            match.get("title", ""),
            match.get("primary_emotion", ""),
            match.get("secondary_emotions", ""),
            match.get("notes", ""),
        ]
    ).lower()
    if "collapse" in text or "trauma" in text:
        return "Collapse Arc"
    if "miracle" in text or "impossibility" in text or "comeback" in text:
        return "Miracle Arc"
    if "madness" in text or "rage" in text or "chaos" in text:
        return "Madness Arc"
    if "revenge" in text:
        return "Revenge Arc"
    if "legacy" in text or "destiny" in text or score >= 95:
        return "Legacy Arc"
    if "aura" in text:
        return "Aura Arc"
    return "Straight Rise Arc"


def rising_action_for_arc(arc_type: str, title: str) -> str:
    if arc_type == "Collapse Arc":
        return "Start with normal match tension, then let the first cracks arrive faster than the viewer expects."
    if arc_type == "Miracle Arc":
        return "Establish the impossible deficit and the emotional distance between scoreboard and belief."
    if arc_type == "Madness Arc":
        return "Layer fouls, confrontations, crowd noise, and body language until the match feels unstable."
    if arc_type == "Legacy Arc":
        return "Frame the match around the player, team, or nation carrying history into the present."
    return f"Build {title} through pressure, consequence, and visible emotional escalation."


def turning_point_for_arc(arc_type: str, title: str) -> str:
    if arc_type == "Collapse Arc":
        return "The moment the match stops feeling competitive and starts feeling historic."
    if arc_type == "Miracle Arc":
        return "The first moment the impossible starts to look physically real."
    if arc_type == "Madness Arc":
        return "The incident where control leaves the referee, players, or crowd."
    return f"The decisive emotional beat that changes how {title} will be remembered."


def aftermath_for_arc(arc_type: str, title: str) -> str:
    if arc_type == "Collapse Arc":
        return "Hold on faces, silence, stunned body language, and the crowd processing humiliation."
    if arc_type == "Miracle Arc":
        return "Hold on disbelief turning into collective release."
    if arc_type == "Madness Arc":
        return "Show the residue: arguments, cards, exhausted players, and the crowd still buzzing."
    return f"Show what {title} leaves behind emotionally, not just the final score."


def payoff_for_arc(arc_type: str, title: str) -> str:
    if arc_type == "Collapse Arc":
        return "This is not a loss. It is a national memory."
    if arc_type == "Miracle Arc":
        return "This is why football never fully obeys probability."
    if arc_type == "Madness Arc":
        return "This is tournament football when pressure becomes disorder."
    return f"The payoff is why {title} still belongs in the archive."


def _score_from_scores(match_id: str, root: Path = ROOT) -> float:
    row = next((item for item in read_csv(root / "data/mythology_scores.csv") if item.get("match_id") == match_id), {})
    return _to_float(row.get("total_score")) or 0.0


def archive_root() -> str:
    return os.environ.get("FOOTBALL_ARCHIVE_ROOT") or ("C:\\FootballArchive" if os.name == "nt" else "FootballArchive")


def archive_path(*parts: str) -> str:
    root = archive_root()
    if "\\" in root or ":" in root:
        return str(PureWindowsPath(root, *parts))
    return str(Path(root, *parts))


def ffmpeg_commands(root: Path = ROOT, *, overwrite: bool = False) -> list[str]:
    clips = read_csv(root / "data/clip_windows.csv")
    matches = {row.get("match_id"): row for row in read_csv(root / "data/matches.csv")}
    commands: list[str] = []
    for clip in clips:
        match_id = clip.get("match_id", "")
        if not match_id:
            continue
        match = matches.get(match_id, {})
        local_path = match.get("local_path") or f"RAW/{match_id}.mp4"
        input_path = normalize_archive_media_path(local_path)
        output_name = f"{clip.get('clip_id') or match_id + '_clip'}.mp4"
        output_path = archive_path("CLIPS", output_name)
        commands.append(
            " ".join(
                [
                    "ffmpeg",
                    "-y" if overwrite else "-n",
                    "-ss",
                    clip.get("start_time", ""),
                    "-to",
                    clip.get("end_time", ""),
                    "-i",
                    quote_path(input_path),
                    "-c",
                    "copy",
                    quote_path(output_path),
                ]
            )
        )
    return commands


def normalize_archive_media_path(local_path: str) -> str:
    cleaned = local_path.replace("\\", "/").lstrip("/")
    if ":" in local_path:
        return local_path
    direct_path = Path(local_path)
    if direct_path.is_absolute():
        return str(direct_path)
    repo_path = ROOT / cleaned
    if repo_path.exists():
        return str(repo_path)
    if cleaned.startswith("FootballArchive/"):
        cleaned = cleaned.removeprefix("FootballArchive/")
    return archive_path(*cleaned.split("/"))


def quote_path(path: str) -> str:
    return f'"{path}"' if "\\" in path or ":" in path else shlex.quote(path)


def write_ffmpeg_commands(root: Path = ROOT, *, overwrite: bool = False) -> Path:
    ensure_clip_output_dir()
    out_path = root / "outputs/manifests/ffmpeg_clip_commands.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(ffmpeg_commands(root, overwrite=overwrite)) + "\n", encoding="utf-8")
    return out_path


def execute_ffmpeg_commands(commands: Iterable[str]) -> None:
    ensure_clip_output_dir()
    for command in commands:
        output_path = output_path_from_ffmpeg_command(command)
        if output_path and "-n" in shlex.split(command) and Path(output_path).exists():
            print(f"Skipping existing clip: {output_path}")
            continue
        subprocess.run(command, shell=True, check=True)


def ensure_clip_output_dir() -> None:
    clip_dir = archive_path("CLIPS")
    if "\\" not in clip_dir and ":" not in clip_dir:
        Path(clip_dir).mkdir(parents=True, exist_ok=True)


def output_path_from_ffmpeg_command(command: str) -> str:
    parts = shlex.split(command)
    if not parts:
        return ""
    return parts[-1]


def planned_match_rows_from_config(config_path: Path) -> list[dict[str, Any]]:
    if not config_path.exists():
        return []
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    matches = payload.get("matches", payload if isinstance(payload, list) else [])
    rows = []
    for item in matches:
        rows.append({field: item.get(field, "") for field in MATCH_FIELDS})
    return rows
