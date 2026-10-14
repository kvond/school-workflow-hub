"""Configuration: which Agenda, which courses, when they meet, and pacing assumptions."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CourseConfig:
    name: str  # matches the Agenda tab without its emoji: Forensics, A&P, Biology
    tab: str  # exact Agenda tab name
    meets: list[str]  # weekdays the course meets, e.g. ["Mon", "Wed"]
    friday_side: str  # "A" or "B": which Fridays this course meets
    marker_prefix: str  # first letter of the deck marker code: F08, A07, C06
    students: int = 0  # for print counts
    groups: int = 0  # lab groups, for per-group printing and supplies
    sections: list[str] = field(default_factory=list)  # Schoology section ids
    drive_root: str = ""  # folder holding this course's week folders


@dataclass
class Pacing:
    block_minutes: int = 90
    overhead_minutes: int = 10  # attendance, transitions, cleanup
    over_tolerance: int = 10  # flag when the estimate exceeds the block by more than this
    under_minutes: int = 50  # flag a meeting day planned this thin
    minutes_per_response_box: float = 3.0
    minutes_per_bank_term: float = 1.0
    minutes_per_content_slide: float = 1.5
    minutes_per_external_activity: float = 12.0


@dataclass
class Config:
    agenda_id: str
    school_year_start: int
    courses: list[CourseConfig]
    weeks_tab: str = "🗂 Weeks"
    worksheets_tab: str = "📝 Worksheets"
    turnin_tab: str = "🗂 TurnIn Map"
    lms_name: str = "Schoology"
    output_folder_id: str = ""  # Drive folder for the preflight Doc and print packet
    gcs_bucket: str = ""  # for the VT baseline between runs
    pacing: Pacing = field(default_factory=Pacing)
    schoology_domain: str = ""
    llm_review: bool = False
    vertex_project: str = ""
    vertex_region: str = "global"

    def course(self, name: str) -> CourseConfig:
        for c in self.courses:
            if c.name == name:
                return c
        raise KeyError(name)


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or os.environ.get("PREFLIGHT_CONFIG", "config.yaml"))
    raw = yaml.safe_load(path.read_text())
    courses = [CourseConfig(**c) for c in raw.pop("courses")]
    pacing = Pacing(**raw.pop("pacing", {}))
    return Config(courses=courses, pacing=pacing, **raw)
