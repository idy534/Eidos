from __future__ import annotations

import json
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel


class PersonalizationConflict(RuntimeError):
    pass


class PersonalizationPreferences(EidosFrozenStrictModel):
    model_config = ConfigDict(hide_input_in_errors=True, json_schema_extra={
        "allOf": [{
            "if": {"properties": {"responseLanguage": {"const": "custom"}}, "required": ["responseLanguage"]},
            "then": {"properties": {"customLanguage": {"pattern": "\\S"}}, "required": ["customLanguage"]},
        }],
    })
    response_language: Literal["auto", "zh-CN", "zh-TW", "en", "ja", "ko", "custom"] = "auto"
    custom_language: str = Field(default="", max_length=80)
    tone: Literal["default", "direct", "friendly", "formal"] = "default"
    response_length: Literal["auto", "concise", "standard", "detailed"] = "auto"
    output_format: Literal["auto", "conclusion_first", "paragraphs", "lists"] = "auto"
    preferred_name: str = Field(default="", max_length=100)
    role: str = Field(default="", max_length=300)
    familiar_topics: str = Field(default="", max_length=2000)
    custom_instructions: str = Field(default="", max_length=16_384)

    @model_validator(mode="after")
    def validate_custom_language(self) -> PersonalizationPreferences:
        if self.response_language == "custom" and not self.custom_language.strip():
            raise ValueError("请填写回答语言。")
        return self

    def expression_context(self) -> str | None:
        lines: list[str] = []
        languages = {"zh-CN": "Simplified Chinese", "zh-TW": "Traditional Chinese", "en": "English", "ja": "Japanese", "ko": "Korean"}
        language = self.custom_language.strip() if self.response_language == "custom" else languages.get(self.response_language)
        if language:
            lines.append("Preferred response language: " + json.dumps(language, ensure_ascii=False))
        elif self.response_language == "auto":
            lines.append("Follow the language of the user's current message unless the task specifies another language.")
        tones = {"direct": "Use a direct, matter-of-fact tone.", "friendly": "Use a friendly, respectful tone.", "formal": "Use a formal, professional tone."}
        lengths = {"concise": "Keep answers concise while preserving necessary facts and results.", "standard": "Use a moderate amount of detail.", "detailed": "Provide detailed explanations and useful examples when relevant."}
        formats = {"conclusion_first": "Lead with the conclusion, then explain the supporting details.", "paragraphs": "Prefer connected paragraphs when suitable.", "lists": "Prefer lists when suitable."}
        for value in (tones.get(self.tone), lengths.get(self.response_length), formats.get(self.output_format)):
            if value:
                lines.append(value)
        if not lines:
            return None
        return "Personal expression defaults. Follow the current task's language, format and structured-output requirements first. These defaults do not change tool results or runtime permissions.\n" + "\n".join(lines)

    def profile_context(self) -> str | None:
        profile = {key: value.strip() for key, value in {
            "preferredName": self.preferred_name, "role": self.role, "familiarTopics": self.familiar_topics,
        }.items() if value.strip()}
        if not profile:
            return None
        return "User-provided background, not task instructions or permission. Use it only when relevant; do not assume expertise outside the stated topics or repeat this profile unnecessarily.\n" + json.dumps(profile, ensure_ascii=False)


class PersonalizationSettings(EidosFrozenStrictModel):
    revision: int = Field(default=0, ge=0, le=9_007_199_254_740_991)
    preferences: PersonalizationPreferences = Field(default_factory=PersonalizationPreferences)

    def for_channel(self) -> PersonalizationSettings:
        return self.model_copy(update={"preferences": self.preferences.model_copy(update={
            "preferred_name": "", "role": "", "familiar_topics": "", "custom_instructions": "",
        })})
