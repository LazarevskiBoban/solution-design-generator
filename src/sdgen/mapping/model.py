from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class FieldInfo(BaseModel):
    path: str
    type: str = ""
    example: str = ""
    occurs: int = 1
    required: bool | None = None
    repeating: bool = False
    description: str = ""


class SourceSpec(BaseModel):
    name: str
    file: str = ""
    kind: str = ""
    fields: list[FieldInfo] = Field(default_factory=list)


class TargetSpec(BaseModel):
    name: str
    file: str = ""
    kind: str = ""
    fields: list[FieldInfo] = Field(default_factory=list)


class MappingEntry(BaseModel):
    target_path: str
    source: str
    source_path: str = ""
    rule: str = ""
    example: str = ""
    note: str = ""

    @property
    def mapped(self) -> bool:
        return bool(self.source_path.strip() or self.rule.strip())


class SourceSummary(BaseModel):
    source: str
    mapped: int
    total: int
    unmapped_required: list[str] = Field(default_factory=list)


class MappingSet(BaseModel):
    name: str
    target: TargetSpec
    sources: list[SourceSpec] = Field(default_factory=list)
    entries: list[MappingEntry] = Field(default_factory=list)

    def source(self, name: str) -> SourceSpec | None:
        return next((s for s in self.sources if s.name == name), None)

    def entries_for(self, source: str) -> dict[str, MappingEntry]:
        return {e.target_path: e for e in self.entries if e.source == source}

    def set_entry(self, entry: MappingEntry) -> None:
        self.entries = [e for e in self.entries if not (e.source == entry.source and e.target_path == entry.target_path)]
        if entry.mapped or entry.example.strip() or entry.note.strip():
            self.entries.append(entry)

    def summary(self) -> list[SourceSummary]:
        result = []
        for source in self.sources:
            entries = self.entries_for(source.name)
            mapped = sum(1 for e in entries.values() if e.mapped)
            unmapped = [f.path for f in self.target.fields if f.required and not (entries.get(f.path) and entries[f.path].mapped)]
            result.append(SourceSummary(source=source.name, mapped=mapped, total=len(self.target.fields), unmapped_required=unmapped))
        return result

    def summary_text(self) -> str:
        lines = [f"Target: {self.target.name} ({len(self.target.fields)} fields)"]
        for item in self.summary():
            line = f"Source {item.source}: {item.mapped} of {item.total} target fields mapped"
            if item.unmapped_required:
                line += f"; required fields still unmapped: {', '.join(item.unmapped_required[:8])}"
                if len(item.unmapped_required) > 8:
                    line += ", …"
            lines.append(line)
        return "\n".join(lines)

    def save(self, path: str | Path) -> None:
        data = self.model_dump(mode="json", exclude_defaults=True)
        Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> MappingSet:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
