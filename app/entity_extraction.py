"""
Entity extraction from memory content using GPT-4o-mini.
All errors are swallowed — extraction is best-effort and never blocks the request path.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

VALID_ENTITY_TYPES = {"person", "organization", "preference", "fact", "procedure"}

_EXTRACTION_PROMPT = """Extract entities and relationships from the following memory content.

Return JSON only, no commentary:
{
  "entities": [
    {"label": "<entity name or value>", "type": "<person|organization|preference|fact|procedure>"}
  ],
  "relations": [
    {"source": "<entity label>", "relation": "<verb phrase>", "target": "<entity label>"}
  ]
}

Rules:
- Only extract entities explicitly mentioned.
- "type" must be one of: person, organization, preference, fact, procedure.
- Relations must reference labels in the entities list.
- If nothing to extract, return {"entities": [], "relations": []}.

Memory content:
"""


@dataclass
class ExtractedEntity:
    label: str
    entity_type: str


@dataclass
class ExtractedRelation:
    source: str
    relation: str
    target: str


@dataclass
class ExtractionResult:
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)


def parse_extraction_response(raw: str) -> ExtractionResult:
    """Parse LLM JSON response. Never raises."""
    try:
        data = json.loads(raw)
        entities_raw = data.get("entities")
        relations_raw = data.get("relations")
        if not isinstance(entities_raw, list) or not isinstance(relations_raw, list):
            return ExtractionResult()
        entities = [
            ExtractedEntity(
                label=str(e["label"]),
                entity_type=e["type"] if e.get("type") in VALID_ENTITY_TYPES else "fact",
            )
            for e in entities_raw
            if isinstance(e, dict) and e.get("label")
        ]
        relations = [
            ExtractedRelation(
                source=str(r["source"]),
                relation=str(r["relation"]),
                target=str(r["target"]),
            )
            for r in relations_raw
            if isinstance(r, dict) and r.get("source") and r.get("relation") and r.get("target")
        ]
        return ExtractionResult(entities=entities, relations=relations)
    except Exception:
        return ExtractionResult()


async def extract_entities(content: str) -> ExtractionResult:
    """Call GPT-4o-mini to extract entities. Returns empty result on any failure."""
    client = AsyncOpenAI()
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": _EXTRACTION_PROMPT + content}],
            temperature=0.0,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        return parse_extraction_response(raw)
    except Exception as exc:
        logger.warning("Entity extraction failed (non-fatal): %s", exc)
        return ExtractionResult()
