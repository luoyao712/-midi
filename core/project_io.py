"""
项目 JSON 存档与读取
使用方式：
    from core.project_io import save_project, load_project
    save_project(project, "my_song.mep")
    project = load_project("my_song.mep")
文件扩展名推荐 .mep（Midi Editor Project）
"""
import json
from core.note_model import Project, Track, Note, TimeSignature, TempoChange

_VERSION = 1


def save_project(project: Project, path: str) -> None:
    """将 Project 序列化为 JSON 文件"""
    data = {
        "version":  _VERSION,
        "title":    project.title,
        "tempo":    project.tempo,
        "time_sig": {
            "numerator":   project.time_sig.numerator,
            "denominator": project.time_sig.denominator,
        },
        "key_sig": project.key_sig,
        "tempo_changes": [
            {"beat": tc.beat, "bpm": tc.bpm}
            for tc in project.tempo_changes
        ],
        "tracks": [
            {
                "name":       t.name,
                "color":      t.color,
                "instrument": t.instrument,
                "muted":      t.muted,
                "volume":     t.volume,
                "clef":       t.clef,
                "notes": [
                    {
                        "pitch":          n.pitch,
                        "start_beat":     n.start_beat,
                        "duration_beats": n.duration_beats,
                        "velocity":       n.velocity,
                    }
                    for n in t.notes
                ],
            }
            for t in project.tracks
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_project(path: str) -> Project:
    """从 JSON 文件读取 Project"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    ts_data = data.get("time_sig", {})
    project = Project(
        title   = data.get("title", "新建项目"),
        tempo   = data.get("tempo", 120),
        time_sig = TimeSignature(
            numerator   = ts_data.get("numerator", 4),
            denominator = ts_data.get("denominator", 4),
        ),
        key_sig = data.get("key_sig", 0),
    )

    for tc in data.get("tempo_changes", []):
        project.add_tempo_change(float(tc["beat"]), int(tc["bpm"]))

    for td in data.get("tracks", []):
        track = Track(
            name       = td.get("name", "轨道"),
            color      = td.get("color", "#4A9EFF"),
            instrument = td.get("instrument", 0),
            muted      = td.get("muted", False),
            volume     = td.get("volume", 100),
            clef       = td.get("clef", "auto"),
        )
        for nd in td.get("notes", []):
            track.notes.append(Note(
                pitch          = int(nd["pitch"]),
                start_beat     = float(nd["start_beat"]),
                duration_beats = float(nd["duration_beats"]),
                velocity       = int(nd.get("velocity", 80)),
            ))
        project.tracks.append(track)

    return project
