"""Conservative recording matching; manual decisions persist across snapshots."""
import re
import unicodedata
from difflib import SequenceMatcher


def normalized(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    value="".join(character for character in value if not unicodedata.combining(character))
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def version(value):
    text = normalized(value)
    return tuple(marker for marker in ["extended", "radio", "remix", "live", "instrumental", "acoustic", "edit", "sped", "slowed", "remaster", "remastered", "clean", "explicit", "dub", "vip"] if marker in text.split())


def candidates(catalogue, track):
    available = {}
    for row in catalogue.files():
        if row["available"] and row["recording_id"] not in available:
            path = catalogue.resolve(row)
            if path and path.is_file():
                available[row["recording_id"]] = row
    ranked = []
    for row in available.values():
        title = bool(normalized(row["title"])) and normalized(row["title"]) == normalized(track["title"])
        artist = bool(normalized(row["artist"])) and normalized(row["artist"]) == normalized(track["artist"])
        time_ok = bool(row["duration"] and track["duration"] and abs(row["duration"] - track["duration"]) <= 2.5)
        same_version = version(row["title"]) == version(track["title"])
        if version(row["title"]) or version(track["title"]):
            same_version = same_version and title  # Different remix/edit names require review, even with the same ISRC.
        isrc = bool(track.get("isrc") and row["isrc"] and row["isrc"].upper() == track["isrc"].upper())
        conflicting_isrc = bool(track.get("isrc") and row["isrc"] and not isrc)
        score = (0.55 * SequenceMatcher(None, normalized(row["title"]), normalized(track["title"])).ratio()
                 + 0.25 * SequenceMatcher(None, normalized(row["artist"]), normalized(track["artist"])).ratio()
                 + (0.2 if time_ok else 0))
        if not same_version:
            score *= 0.45
        strong = same_version and time_ok and not conflicting_isrc and (isrc or (title and artist))
        if strong:
            score = 0.99 if isrc else 0.94
        if score >= 0.45:
            ranked.append({"recording_id": row["recording_id"], "score": score, "strong": strong,
                           "label": f"{row['artist']} — {row['title']} ({row['relative_path']})",
                           "evidence": f"ISRC {'matches' if isrc else 'not confirmed'}; duration {'matches' if time_ok else 'differs'}; version {'matches' if same_version else 'differs'}"})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def match_track(catalogue, track):
    with catalogue.connect() as db:
        saved = db.execute("SELECT * FROM matches WHERE spotify_id=?", (track["id"],)).fetchone()
    if saved and saved["recording_id"] and catalogue.available_recording(saved["recording_id"]):
        return {"recording_id": saved["recording_id"], "state": "matched"}
    if saved and saved["recording_id"]:
        return {"recording_id": saved["recording_id"], "state": "unavailable"}
    ranked = candidates(catalogue, track)
    if ranked and ranked[0]["strong"] and (len(ranked) == 1 or ranked[0]["score"] - ranked[1]["score"] >= 0.08):
        catalogue.save_remote(track)
        catalogue.decide_match(track["id"], ranked[0]["recording_id"], "automatic", ranked[0]["evidence"])
        return {"recording_id": ranked[0]["recording_id"], "state": "matched"}
    return {"recording_id": None, "state": "review" if ranked else ("missing" if track.get("playable",True) else "unavailable")}
