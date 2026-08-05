"""Response schemas for the purpose vocabulary and its templates.

Read-only, and deliberately so: a template is data in the repository
(`app/resources/purpose_templates.json`), loaded and validated at startup. The
endpoint exists because the planning UI needs to know what the criteria editor
should be pre-filled with and which axes a session will be judged on — the
same information the server used, rather than a second copy of it in the
frontend.
"""

from pydantic import BaseModel

from app.api.schemas.criteria import SuccessCriterionSchema
from app.domain.athlete import Discipline
from app.domain.purpose import Purpose
from app.domain.templates import ScoringAxis


class PurposeTemplateRead(BaseModel):
    """One purpose, with what it starts with and how it is judged."""

    purpose: Purpose
    discipline: Discipline
    #: The scoring axes WP-7 will compute for a session of this purpose.
    axes: list[ScoringAxis]
    #: The criteria a session of this purpose is created with. Editable
    #: afterwards — this is a default, not a rule.
    default_criteria: list[SuccessCriterionSchema]


class PurposeTemplatesRead(BaseModel):
    """The whole vocabulary, in the order the build plan states it."""

    items: list[PurposeTemplateRead]
